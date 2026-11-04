"""Worker processes. Each one opens its own connection, runs one workload and reports back.

Concurrency uses processes (spawn), never threads, so the GIL is not part of what is measured.
worker_entry is the one module-level function here: multiprocessing needs an importable callable as
the process target, and a class method cannot be pickled for the spawn method.
"""

from __future__ import annotations

import multiprocessing
import queue
import random
import time
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from dbbench.config import NS_PER_S, DatasetSettings, LatencySettings
from dbbench.dataset import OrderGenerator
from dbbench.engines import Engine, EngineFactory, EngineSpec
from dbbench.errors import DbBenchError
from dbbench.histogram import LatencyHistogram

if TYPE_CHECKING:
    from collections.abc import Sequence
    from multiprocessing.process import BaseProcess
    from multiprocessing.queues import Queue
    from multiprocessing.synchronize import Barrier

READ_STREAM_OFFSET: Final = 1
WRITE_STREAM_OFFSET: Final = 1000


class WorkerError(DbBenchError):
    """Raised when a worker fails or exits without reporting."""


class WorkloadKind(StrEnum):
    """Which operations a worker performs."""

    POINT_READ = "point_read"
    USER_READ = "user_read"
    INSERT = "insert"
    MIXED = "mixed"


class OperationKind(StrEnum):
    """Whether one operation was a read or a write, for the split latencies of the mixed run."""

    READ = "read"
    WRITE = "write"


class WorkerJob(BaseModel):
    """Everything one worker needs. It is sent to the process as JSON."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine: EngineSpec
    workload: WorkloadKind
    worker_index: int = Field(ge=0)
    dataset: DatasetSettings
    latency: LatencySettings
    read_share: float = Field(ge=0, le=1)
    timed: bool
    operations: int = Field(ge=0)
    warmup_ops: int = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    warmup_seconds: float = Field(ge=0)


class WorkerResult(BaseModel):
    """What one worker reports back to the runner."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    worker_index: int
    ops: int = 0
    errors: int = 0
    elapsed_seconds: float = 0.0
    histogram: LatencyHistogram | None = None
    read_histogram: LatencyHistogram | None = None
    write_histogram: LatencyHistogram | None = None
    error: str | None = None

    @classmethod
    def failed(cls, worker_index: int, message: str) -> WorkerResult:
        """Returns the result of a worker that stopped with an error."""
        return cls(worker_index=worker_index, error=message)


class Workload(ABC):
    """One kind of operation, performed against an open engine."""

    @abstractmethod
    def perform(self, engine: Engine) -> OperationKind:
        """Performs one operation and reports whether it was a read or a write."""


class PointReadWorkload(Workload):
    """Reads one order by a random id."""

    def __init__(self, rows: int, rng: random.Random) -> None:
        """Remembers the row count and the random stream for the ids."""
        self._rows = rows
        self._rng = rng

    def perform(self, engine: Engine) -> OperationKind:
        """Reads one order by id."""
        engine.point_read(self._rng.randint(1, self._rows))
        return OperationKind.READ


class UserReadWorkload(Workload):
    """Reads the newest orders of one random user."""

    def __init__(self, users: int, rng: random.Random) -> None:
        """Remembers the user count and the random stream for the user ids."""
        self._users = users
        self._rng = rng

    def perform(self, engine: Engine) -> OperationKind:
        """Reads the newest orders of one user."""
        engine.user_read(self._rng.randint(1, self._users))
        return OperationKind.READ


class InsertWorkload(Workload):
    """Inserts one generated order into orders_writes."""

    def __init__(self, generator: OrderGenerator) -> None:
        """Remembers the generator of the rows to insert."""
        self._generator = generator

    def perform(self, engine: Engine) -> OperationKind:
        """Inserts one order."""
        engine.insert_one(self._generator.next_order())
        return OperationKind.WRITE


class MixedWorkload(Workload):
    """Chooses a read with the given probability, otherwise an insert."""

    def __init__(
        self,
        reads: Workload,
        writes: Workload,
        read_share: float,
        rng: random.Random,
    ) -> None:
        """Remembers both workloads, the read share and the stream that makes the choice."""
        self._reads = reads
        self._writes = writes
        self._read_share = read_share
        self._rng = rng

    def perform(self, engine: Engine) -> OperationKind:
        """Performs a read or a write, chosen at random."""
        if self._rng.random() < self._read_share:
            return self._reads.perform(engine)
        return self._writes.perform(engine)


class WorkloadFactory:
    """Builds the workload of one worker, with its random streams."""

    def __init__(self, job: WorkerJob) -> None:
        """Remembers the job the workload is built for."""
        self._job = job

    def build(self) -> Workload:
        """Returns the workload, seeded from the data set seed and the worker index."""
        dataset = self._job.dataset
        index = self._job.worker_index
        read_rng = random.Random(dataset.seed + READ_STREAM_OFFSET + index)  # noqa: S311
        write_seed = dataset.seed + WRITE_STREAM_OFFSET + index
        kind = self._job.workload
        if kind is WorkloadKind.POINT_READ:
            return PointReadWorkload(dataset.rows, read_rng)
        if kind is WorkloadKind.USER_READ:
            return UserReadWorkload(dataset.users, read_rng)
        if kind is WorkloadKind.INSERT:
            return InsertWorkload(OrderGenerator(dataset, write_seed))
        return MixedWorkload(
            reads=PointReadWorkload(dataset.rows, read_rng),
            writes=InsertWorkload(OrderGenerator(dataset, write_seed)),
            read_share=self._job.read_share,
            rng=read_rng,
        )


class _Recorder:
    """Collects the latencies and the counts of one measured window."""

    def __init__(self, latency: LatencySettings, *, split: bool) -> None:
        """Creates empty histograms. split adds separate read and write histograms."""
        self._latency = latency
        self._total = self._new_histogram()
        self._reads = self._new_histogram() if split else None
        self._writes = self._new_histogram() if split else None
        self._ops = 0
        self._errors = 0

    def _new_histogram(self) -> LatencyHistogram:
        """Returns an empty histogram with the configured shape."""
        return LatencyHistogram(self._latency.bucket_ns, self._latency.range_ns)

    def success(self, kind: OperationKind, elapsed_ns: int) -> None:
        """Records one successful operation."""
        self._ops += 1
        self._total.record_ns(elapsed_ns)
        if kind is OperationKind.READ and self._reads is not None:
            self._reads.record_ns(elapsed_ns)
        if kind is OperationKind.WRITE and self._writes is not None:
            self._writes.record_ns(elapsed_ns)

    def failure(self) -> None:
        """Counts one lock error. Its latency is not recorded."""
        self._errors += 1

    def result(self, worker_index: int, elapsed_seconds: float) -> WorkerResult:
        """Returns the worker result for this window."""
        return WorkerResult(
            worker_index=worker_index,
            ops=self._ops,
            errors=self._errors,
            elapsed_seconds=elapsed_seconds,
            histogram=self._total,
            read_histogram=self._reads,
            write_histogram=self._writes,
        )


class WorkerTask:
    """Runs the job inside one worker process."""

    def __init__(self, job: WorkerJob) -> None:
        """Remembers the job."""
        self._job = job

    def execute(self, barrier: Barrier) -> WorkerResult:
        """Connects, waits for the other workers, runs the warmup and the measured window."""
        engine = EngineFactory().build(self._job.engine)
        engine.connect()
        try:
            workload = WorkloadFactory(self._job).build()
            barrier.wait()
            if self._job.timed:
                return self._run_timed(engine, workload)
            return self._run_counted(engine, workload)
        finally:
            engine.close()

    def _recorder(self) -> _Recorder:
        """Returns an empty recorder. Mixed runs keep reads and writes apart."""
        split = self._job.workload is WorkloadKind.MIXED
        return _Recorder(self._job.latency, split=split)

    def _run_timed(self, engine: Engine, workload: Workload) -> WorkerResult:
        """Runs for the warmup time without recording, then for the duration with recording."""
        warm_until = time.perf_counter_ns() + self._ns(self._job.warmup_seconds)
        scratch = self._recorder()
        while time.perf_counter_ns() < warm_until:
            self._attempt(engine, workload, scratch)
        recorder = self._recorder()
        started = time.perf_counter_ns()
        until = started + self._ns(self._job.duration_seconds)
        while time.perf_counter_ns() < until:
            self._attempt(engine, workload, recorder)
        elapsed = (time.perf_counter_ns() - started) / NS_PER_S
        return recorder.result(self._job.worker_index, elapsed)

    def _run_counted(self, engine: Engine, workload: Workload) -> WorkerResult:
        """Runs the untimed warmup operations, then the counted operations."""
        scratch = self._recorder()
        for _ in range(self._job.warmup_ops):
            self._attempt(engine, workload, scratch)
        recorder = self._recorder()
        started = time.perf_counter_ns()
        for _ in range(self._job.operations):
            self._attempt(engine, workload, recorder)
        elapsed = (time.perf_counter_ns() - started) / NS_PER_S
        return recorder.result(self._job.worker_index, elapsed)

    def _attempt(self, engine: Engine, workload: Workload, recorder: _Recorder) -> None:
        """Performs one operation. A lock error is counted and the worker goes on."""
        started = time.perf_counter_ns()
        try:
            kind = workload.perform(engine)
        except Exception as error:
            if not engine.is_lock_error(error):
                raise
            recorder.failure()
            return
        recorder.success(kind, time.perf_counter_ns() - started)

    @staticmethod
    def _ns(seconds: float) -> int:
        """Converts seconds to whole nanoseconds."""
        return round(seconds * NS_PER_S)


class WorkerPool:
    """Starts the worker processes for one measurement and collects their results."""

    _POLL_SECONDS: ClassVar[float] = 1.0

    def __init__(self) -> None:
        """Selects the spawn start method, so no thread or fork state is inherited."""
        self._context = multiprocessing.get_context("spawn")

    def run(self, jobs: Sequence[WorkerJob]) -> list[WorkerResult]:
        """Runs all jobs at once, returns their results by worker index, or raises WorkerError."""
        barrier = self._context.Barrier(len(jobs))
        results: Queue[WorkerResult] = self._context.Queue()
        processes = [
            self._context.Process(
                target=worker_entry, args=(job.model_dump_json(), barrier, results)
            )
            for job in jobs
        ]
        for process in processes:
            process.start()
        collected: list[WorkerResult] = []
        try:
            while len(collected) < len(jobs):
                collected.append(self._next_result(results, processes))
        finally:
            for process in processes:
                process.join()
        failures = [result.error for result in collected if result.error is not None]
        if failures:
            raise WorkerError(failures[0])
        return sorted(collected, key=lambda result: result.worker_index)

    def _next_result(
        self, results: Queue[WorkerResult], processes: Sequence[BaseProcess]
    ) -> WorkerResult:
        """Waits for the next result. Raises when every process has ended without one."""
        while True:
            try:
                return results.get(timeout=self._POLL_SECONDS)
            except queue.Empty:
                if any(process.is_alive() for process in processes):
                    continue
                try:
                    return results.get_nowait()
                except queue.Empty:
                    message = "a worker process ended without reporting a result"
                    raise WorkerError(message) from None


def worker_entry(job_json: str, barrier: Barrier, results: Queue[WorkerResult]) -> None:
    """Runs one worker process and puts its result on the queue, even when it fails.

    A failure aborts the barrier, so the other workers stop instead of waiting forever.
    """
    job = WorkerJob.model_validate_json(job_json)
    try:
        result = WorkerTask(job).execute(barrier)
    except Exception as error:  # noqa: BLE001
        barrier.abort()
        result = WorkerResult.failed(job.worker_index, str(error))
    results.put(result)
