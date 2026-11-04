"""The benchmark scenarios: one class per scenario id of docs/METHOD.md, and their runner.

Every operation of a concurrent or count-based scenario runs in a worker process. The aggregate
scenario runs in the runner process, as a single client, because it compares the results of the
engines. Every measured run is settled first, and the runner records its position in the order and
its diagnostics.
"""

from __future__ import annotations

import statistics
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import partial
from itertools import batched
from typing import TYPE_CHECKING, ClassVar, Final, Literal

from dbbench.config import NS_PER_MS, BenchConfig
from dbbench.dataset import OrderGenerator
from dbbench.diagnostics import RunProbe
from dbbench.engines import (
    POSTGRES_ENGINE,
    Engine,
    EngineCatalog,
    EngineFactory,
    EngineSpec,
    LoadRow,
    PostgresEngineSpec,
    StatusRow,
    TopUserRow,
)
from dbbench.errors import DbBenchError
from dbbench.histogram import LatencyHistogram
from dbbench.order import EngineOrder
from dbbench.results import MetricValue, RawRecord
from dbbench.settle import Settler, SettleRecord
from dbbench.workers import WorkerJob, WorkerPool, WorkerResult, WorkloadKind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from datetime import datetime

    from dbbench.diagnostics import RunReading
    from dbbench.postgres_server import PostgresServer

RunMode = Literal["full", "quick"]
SINGLE_WORKER: Final = 1
LATENCY_PERCENTILES: Final = {
    "p50_ms": 50,
    "p90_ms": 90,
    "p95_ms": 95,
    "p99_ms": 99,
    "p999_ms": 99.9,
}
P99: Final = 99


class BenchmarkError(DbBenchError):
    """Raised when a scenario cannot produce a valid result, for example mismatched aggregates."""


@dataclass(frozen=True, slots=True)
class RunEvidence:
    """What was read around one measured run: the settle record and the diagnostics."""

    settle: SettleRecord
    diagnostics: dict[str, MetricValue]


@dataclass(frozen=True, slots=True)
class Observed[T]:
    """The value a measured run produced, with the evidence taken around it."""

    value: T
    evidence: RunEvidence


@dataclass(frozen=True, slots=True)
class _AggregateRun:
    """The timings and the answers of both aggregate queries, repeated, on one engine."""

    status_ms: list[float]
    top_ms: list[float]
    outputs: list[tuple[list[StatusRow], list[TopUserRow]]]


class RunContext:
    """What every scenario needs: the config, the mode, the server, the record sink and helpers."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        config: BenchConfig,
        mode: RunMode,
        server: PostgresServer | None,
        sink: Callable[[RawRecord], None],
        settler: Settler | None = None,
        probe: RunProbe | None = None,
    ) -> None:
        """Remembers the run settings and creates the catalog, factory and run helpers.

        The settler and the probe default to the real ones. Tests pass their own.
        """
        self.config = config
        self.mode = mode
        self.records: list[RawRecord] = []
        self.settler = settler if settler is not None else Settler(config.settle)
        self.probe = probe if probe is not None else RunProbe()
        self.order = EngineOrder(config.run.order_seed)
        self._server = server
        self._sink = sink
        self._catalog = EngineCatalog(config)
        self._factory = EngineFactory()

    def emit(self, record: RawRecord) -> None:
        """Keeps one measured record and passes it to the sink."""
        self.records.append(record)
        self._sink(record)

    def require_server(self) -> PostgresServer:
        """Returns the PostgreSQL server or raises when the run has none."""
        if self._server is None:
            message = "this scenario needs the PostgreSQL server, which the run does not have"
            raise BenchmarkError(message)
        return self._server

    def engine_spec(self, name: str) -> EngineSpec:
        """Returns the spec of one engine name."""
        return self._catalog.spec(name)

    def build_engine(self, name: str) -> Engine:
        """Returns an unconnected engine for one engine name."""
        return self._factory.build(self._catalog.spec(name))

    def open_engine(self, name: str) -> Engine:
        """Returns a connected engine for one engine name."""
        engine = self.build_engine(name)
        engine.connect()
        return engine


class Scenario(ABC):
    """Base of every scenario. A scenario measures and returns records, the runner emits them."""

    scenario_id: ClassVar[str]

    def __init__(self, ctx: RunContext) -> None:
        """Remembers the run context."""
        self._ctx = ctx

    @abstractmethod
    def measure(self, repeat: int) -> list[RawRecord]:
        """Runs the scenario once and returns its records."""

    def _engines(self) -> tuple[str, ...]:
        """Returns the engine names this scenario measures, from the config."""
        return self._ctx.config.engines_by_scenario[self.scenario_id]

    def _ordered(self, repeat: int, workers: int) -> list[str]:
        """Returns this scenario's engines in the order of one repeat and worker level."""
        return self._ctx.order.shuffled(self._engines(), self.scenario_id, repeat, workers)

    def _record(  # noqa: PLR0913
        self,
        *,
        engine: str,
        workers: int,
        repeat: int,
        metrics: dict[str, MetricValue],
        phase: str = "",
        position: int | None = None,
        evidence: RunEvidence | None = None,
    ) -> RawRecord:
        """Builds one raw record for this scenario, with the evidence of a measured run."""
        return RawRecord(
            scenario=self.scenario_id,
            phase=phase,
            engine=engine,
            workers=workers,
            repeat=repeat,
            metrics=metrics,
            order_position=position,
            settle=evidence.settle if evidence is not None else None,
            diagnostics=evidence.diagnostics if evidence is not None else {},
        )

    def _observe[T](self, name: str, body: Callable[[], T], *, reset_writes: bool) -> Observed[T]:
        """Runs one measured run: reset, settle, read, the body, read again.

        The reset of orders_writes comes first, so the settle covers its writes too. Both reads
        run in the runner process, outside the body.
        """
        spec = self._ctx.engine_spec(name)
        if reset_writes:
            self._reset_writes(name)
        settle = self._ctx.settler.settle(spec)
        before: RunReading = self._ctx.probe.before(spec)
        value = body()
        diagnostics = self._ctx.probe.after(spec, before)
        return Observed(value=value, evidence=RunEvidence(settle=settle, diagnostics=diagnostics))

    def _new_histogram(self) -> LatencyHistogram:
        """Returns an empty histogram with the configured shape."""
        latency = self._ctx.config.run.latency
        return LatencyHistogram(latency.bucket_ns, latency.range_ns)

    def _merge(self, parts: Iterable[LatencyHistogram]) -> LatencyHistogram:
        """Returns one histogram holding the sum of the given histograms."""
        merged = self._new_histogram()
        for part in parts:
            merged.merge(part)
        return merged

    def _reset_writes(self, name: str) -> None:
        """Empties orders_writes on one engine before a write run."""
        engine = self._ctx.open_engine(name)
        try:
            engine.reset_writes()
        finally:
            engine.close()

    def _job(
        self,
        name: str,
        workload: WorkloadKind,
        *,
        index: int,
        timed: bool,
        operations: int,
    ) -> WorkerJob:
        """Builds the job of one worker from the config."""
        config = self._ctx.config
        return WorkerJob(
            engine=self._ctx.engine_spec(name),
            workload=workload,
            worker_index=index,
            dataset=config.dataset,
            latency=config.run.latency,
            read_share=config.run.mixed_read_share,
            timed=timed,
            operations=operations,
            warmup_ops=config.run.count_warmup_ops,
            duration_seconds=config.run.duration_seconds,
            warmup_seconds=config.run.warmup_seconds,
        )

    def _throughput(self, results: Sequence[WorkerResult]) -> dict[str, MetricValue]:
        """Returns ops, errors, ops per second and the latency percentiles of all workers.

        Ops per second is the successes divided by the longest worker's measured time. The error
        rate is the errors divided by the attempts.
        """
        ops = sum(result.ops for result in results)
        errors = sum(result.errors for result in results)
        attempts = ops + errors
        elapsed = max((result.elapsed_seconds for result in results), default=0.0)
        total = self._merge(r.histogram for r in results if r.histogram is not None)
        metrics: dict[str, MetricValue] = {
            "ops": ops,
            "errors": errors,
            "error_rate": errors / attempts if attempts else 0.0,
            "ops_per_s": ops / elapsed if elapsed > 0 else 0.0,
        }
        for key, percent in LATENCY_PERCENTILES.items():
            metrics[key] = total.percentile(percent)
        metrics["max_ms"] = total.max_ms()
        return metrics


class FootprintScenario(Scenario):
    """Start time, idle memory, file sizes, and memory again after the runs.

    The start phase is not settled and not shuffled: the server must start before the SQLite
    connect is timed, so its records carry no order position.
    """

    scenario_id = "footprint"

    def measure(self, repeat: int) -> list[RawRecord]:
        """Restarts the server, reads the idle memory and the SQLite connect time."""
        server = self._ctx.require_server()
        server.stop()
        start_seconds = server.start()
        time.sleep(self._ctx.config.run.footprint_settle_seconds)
        rss_mb, pss_mb = server.memory()
        records = [
            self._record(
                engine=POSTGRES_ENGINE,
                workers=SINGLE_WORKER,
                repeat=repeat,
                phase="start",
                metrics={
                    "start_seconds": start_seconds,
                    "idle_rss_mb": rss_mb,
                    "idle_pss_mb": pss_mb,
                },
            )
        ]
        for name in self._engines():
            spec = self._ctx.engine_spec(name)
            if isinstance(spec, PostgresEngineSpec):
                continue
            engine = self._ctx.build_engine(name)
            started = time.perf_counter_ns()
            engine.connect()
            engine.ping()
            connect_ms = (time.perf_counter_ns() - started) / NS_PER_MS
            engine.close()
            records.append(
                self._record(
                    engine=name,
                    workers=SINGLE_WORKER,
                    repeat=repeat,
                    phase="start",
                    metrics={"sqlite_connect_ms": connect_ms},
                )
            )
        return records

    def sizes(self) -> list[RawRecord]:
        """Reads the file sizes. The runner calls this after the seed scenario."""
        server = self._ctx.require_server()
        records: list[RawRecord] = []
        for name in self._engines():
            spec = self._ctx.engine_spec(name)
            engine = self._ctx.open_engine(name)
            try:
                size = engine.size_bytes()
            finally:
                engine.close()
            if isinstance(spec, PostgresEngineSpec):
                metrics: dict[str, MetricValue] = {
                    "pg_database_bytes": size,
                    "data_dir_bytes": server.data_dir_bytes(),
                }
            else:
                metrics = {"sqlite_file_bytes": size}
            records.append(
                self._record(
                    engine=name, workers=SINGLE_WORKER, repeat=0, phase="sizes", metrics=metrics
                )
            )
        return records

    def memory_after(self) -> RawRecord:
        """Reads the server memory after all runs, when the server is still up."""
        rss_mb, pss_mb = self._ctx.require_server().memory()
        return self._record(
            engine=POSTGRES_ENGINE,
            workers=SINGLE_WORKER,
            repeat=0,
            phase="after_runs",
            metrics={"rss_mb": rss_mb, "pss_mb": pss_mb},
        )


class SeedScenario(Scenario):
    """Loads the data set into every engine, then builds the index and runs ANALYZE."""

    scenario_id = "seed"

    def measure(self, repeat: int) -> list[RawRecord]:
        """Settles, loads, indexes and analyzes each engine in order. Generation is not timed."""
        records: list[RawRecord] = []
        for position, name in enumerate(self._ordered(repeat, SINGLE_WORKER)):
            observed = self._observe(name, partial(self._seed_engine, name), reset_writes=False)
            records.append(
                self._record(
                    engine=name,
                    workers=SINGLE_WORKER,
                    repeat=repeat,
                    position=position,
                    metrics=observed.value,
                    evidence=observed.evidence,
                )
            )
        return records

    def _seed_engine(self, name: str) -> dict[str, MetricValue]:
        """Creates the schema, loads every batch, builds the index and analyzes one engine."""
        dataset = self._ctx.config.dataset
        engine = self._ctx.open_engine(name)
        try:
            engine.create_schema()
            load_seconds = self._load(engine)
            index_seconds = _timed(engine.build_indexes)
            analyze_seconds = _timed(engine.analyze)
        finally:
            engine.close()
        return {
            "load_seconds": load_seconds,
            "rows_per_second": dataset.rows / load_seconds,
            "index_seconds": index_seconds,
            "analyze_seconds": analyze_seconds,
        }

    def _load(self, engine: Engine) -> float:
        """Inserts every batch, timing only the inserts, and returns the total seconds."""
        dataset = self._ctx.config.dataset
        generator = OrderGenerator(dataset, dataset.seed)
        rows = ((order_id, generator.next_order()) for order_id in range(1, dataset.rows + 1))
        total = 0.0
        for batch in batched(rows, dataset.batch_size):
            batch_rows: list[LoadRow] = list(batch)
            started = time.perf_counter()
            engine.load_batch(batch_rows)
            total += time.perf_counter() - started
        return total


def _timed(action: Callable[[], None]) -> float:
    """Runs an action and returns how many seconds it took."""
    started = time.perf_counter()
    action()
    return time.perf_counter() - started


class _CountedScenario(Scenario, ABC):
    """Shared shape of the single worker, count-based scenarios."""

    workload: ClassVar[WorkloadKind]
    resets_writes: ClassVar[bool]

    def measure(self, repeat: int) -> list[RawRecord]:
        """Runs one worker per engine, in this repeat's order, for the configured operations."""
        records: list[RawRecord] = []
        operations = self._operations()
        for position, name in enumerate(self._ordered(repeat, SINGLE_WORKER)):
            observed = self._observe(
                name,
                partial(self._count_run, name, operations),
                reset_writes=self.resets_writes,
            )
            records.append(
                self._record(
                    engine=name,
                    workers=SINGLE_WORKER,
                    repeat=repeat,
                    position=position,
                    metrics=observed.value,
                    evidence=observed.evidence,
                )
            )
        return records

    def _count_run(self, name: str, operations: int) -> dict[str, MetricValue]:
        """Runs one worker process for the operations and returns the throughput block."""
        job = self._job(name, self.workload, index=0, timed=False, operations=operations)
        return self._throughput(WorkerPool().run([job]))

    @abstractmethod
    def _operations(self) -> int:
        """Returns the configured operation count for this scenario."""


class SingleInsertScenario(_CountedScenario):
    """One worker, one commit per insert."""

    scenario_id = "single_insert"
    workload = WorkloadKind.INSERT
    resets_writes = True

    def _operations(self) -> int:
        """Returns single_insert_ops."""
        return self._ctx.config.run.single_insert_ops


class PointReadScenario(_CountedScenario):
    """One worker, point reads by id."""

    scenario_id = "point_read"
    workload = WorkloadKind.POINT_READ
    resets_writes = False

    def _operations(self) -> int:
        """Returns point_read_ops."""
        return self._ctx.config.run.point_read_ops


class UserReadScenario(_CountedScenario):
    """One worker, the newest orders of one user."""

    scenario_id = "user_read"
    workload = WorkloadKind.USER_READ
    resets_writes = False

    def _operations(self) -> int:
        """Returns user_read_ops."""
        return self._ctx.config.run.user_read_ops


class AggregateScenario(Scenario):
    """Both aggregate queries on each engine, run in the runner process, with results compared."""

    scenario_id = "aggregate"

    def measure(self, repeat: int) -> list[RawRecord]:
        """Runs both queries aggregate_runs times per engine and compares every result.

        Each engine is settled and read in its order. The comparison happens after all engines,
        and the run stops when the answers differ.
        """
        cutoff = self._ctx.config.dataset.cutoff()
        runs = self._ctx.config.run.aggregate_runs
        names = self._ordered(repeat, SINGLE_WORKER)
        observed: dict[str, Observed[_AggregateRun]] = {}
        for name in names:
            observed[name] = self._observe(
                name,
                partial(self._aggregate_run, name, cutoff, runs),
                reset_writes=False,
            )
        reference = observed[self._engines()[0]].value.outputs
        results_match = all(observed[name].value.outputs == reference for name in self._engines())
        if not results_match:
            message = "the aggregate results differ between the engines, the run stops"
            raise BenchmarkError(message)
        records: list[RawRecord] = []
        for position, name in enumerate(names):
            run = observed[name].value
            records.append(
                self._record(
                    engine=name,
                    workers=SINGLE_WORKER,
                    repeat=repeat,
                    position=position,
                    metrics={
                        "by_status_median_ms": statistics.median(run.status_ms),
                        "by_status_min_ms": min(run.status_ms),
                        "top_users_median_ms": statistics.median(run.top_ms),
                        "top_users_min_ms": min(run.top_ms),
                        "results_match": results_match,
                    },
                    evidence=observed[name].evidence,
                )
            )
        return records

    def _aggregate_run(self, name: str, cutoff: datetime, runs: int) -> _AggregateRun:
        """Runs both queries on one engine, runs times, and keeps the timings and the answers."""
        engine = self._ctx.open_engine(name)
        status_ms: list[float] = []
        top_ms: list[float] = []
        outputs: list[tuple[list[StatusRow], list[TopUserRow]]] = []
        try:
            for _ in range(runs):
                started = time.perf_counter_ns()
                status_rows = engine.aggregate_by_status(cutoff)
                middle = time.perf_counter_ns()
                top_rows = engine.aggregate_top_users()
                finished = time.perf_counter_ns()
                status_ms.append((middle - started) / NS_PER_MS)
                top_ms.append((finished - middle) / NS_PER_MS)
                outputs.append((status_rows, top_rows))
        finally:
            engine.close()
        return _AggregateRun(status_ms=status_ms, top_ms=top_ms, outputs=outputs)


class _ConcurrentScenario(Scenario, ABC):
    """Shared shape of the scenarios that run several workers for a fixed duration."""

    workload: ClassVar[WorkloadKind]
    resets_writes: ClassVar[bool]

    def measure(self, repeat: int) -> list[RawRecord]:
        """Runs every worker level, and every engine in its order for that level."""
        records: list[RawRecord] = []
        for workers in self._levels():
            for position, name in enumerate(self._ordered(repeat, workers)):
                observed = self._observe(
                    name,
                    partial(self._pool_run, name, workers),
                    reset_writes=self.resets_writes,
                )
                records.append(
                    self._record(
                        engine=name,
                        workers=workers,
                        repeat=repeat,
                        position=position,
                        metrics=observed.value,
                        evidence=observed.evidence,
                    )
                )
        return records

    def _pool_run(self, name: str, workers: int) -> dict[str, MetricValue]:
        """Starts the worker processes of one level and returns the metrics of the run."""
        jobs = [
            self._job(name, self.workload, index=index, timed=True, operations=0)
            for index in range(workers)
        ]
        return self._metrics(WorkerPool().run(jobs))

    @abstractmethod
    def _levels(self) -> tuple[int, ...]:
        """Returns the worker counts to run."""

    def _metrics(self, results: Sequence[WorkerResult]) -> dict[str, MetricValue]:
        """Returns the metrics of one run. The default is the throughput block."""
        return self._throughput(results)


class ConcurrentReadScenario(_ConcurrentScenario):
    """Several workers doing point reads for the duration."""

    scenario_id = "concurrent_read"
    workload = WorkloadKind.POINT_READ
    resets_writes = False

    def _levels(self) -> tuple[int, ...]:
        """Returns the configured concurrency levels."""
        return self._ctx.config.run.concurrency_levels


class ConcurrentWriteScenario(_ConcurrentScenario):
    """Several workers inserting for the duration, with lock errors counted."""

    scenario_id = "concurrent_write"
    workload = WorkloadKind.INSERT
    resets_writes = True

    def _levels(self) -> tuple[int, ...]:
        """Returns the configured concurrency levels."""
        return self._ctx.config.run.concurrency_levels


class MixedScenario(_ConcurrentScenario):
    """Several workers, each operation a read or an insert, with the latencies kept apart."""

    scenario_id = "mixed"
    workload = WorkloadKind.MIXED
    resets_writes = True

    def _levels(self) -> tuple[int, ...]:
        """Returns the single mixed worker count from the config."""
        return (self._ctx.config.run.mixed_workers,)

    def _metrics(self, results: Sequence[WorkerResult]) -> dict[str, MetricValue]:
        """Adds the read and write p99 latencies to the throughput block."""
        metrics = self._throughput(results)
        reads = self._merge(r.read_histogram for r in results if r.read_histogram is not None)
        writes = self._merge(r.write_histogram for r in results if r.write_histogram is not None)
        metrics["read_p99_ms"] = reads.percentile(P99)
        metrics["write_p99_ms"] = writes.percentile(P99)
        return metrics


class ScenarioRunner:
    """Runs the selected scenarios in table order, with the repeats, and the footprint phases."""

    ORDER: ClassVar[tuple[type[Scenario], ...]] = (
        FootprintScenario,
        SeedScenario,
        SingleInsertScenario,
        PointReadScenario,
        UserReadScenario,
        AggregateScenario,
        ConcurrentReadScenario,
        ConcurrentWriteScenario,
        MixedScenario,
    )

    def __init__(self, ctx: RunContext) -> None:
        """Remembers the run context."""
        self._ctx = ctx

    @classmethod
    def scenario_ids(cls) -> tuple[str, ...]:
        """Returns every scenario id in table order."""
        return tuple(scenario.scenario_id for scenario in cls.ORDER)

    def run(self, selected: Sequence[str]) -> None:
        """Runs the selected scenarios, or all of them when none is selected.

        The footprint sizes are read right after the seed scenario, and the memory after the runs
        at the end, when the footprint scenario is selected.
        """
        chosen = frozenset(selected) or frozenset(self.scenario_ids())
        footprint = FootprintScenario(self._ctx)
        sizes_due = FootprintScenario.scenario_id in chosen
        repeats = self._ctx.config.run.repeats
        for scenario_type in self.ORDER:
            if scenario_type.scenario_id not in chosen:
                continue
            scenario: Scenario = (
                footprint if scenario_type is FootprintScenario else scenario_type(self._ctx)
            )
            for repeat in range(repeats):
                for record in scenario.measure(repeat):
                    self._ctx.emit(record)
            if scenario_type is SeedScenario and sizes_due:
                self._emit_sizes(footprint)
                sizes_due = False
        if sizes_due:
            self._emit_sizes(footprint)
        if FootprintScenario.scenario_id in chosen:
            self._ctx.emit(footprint.memory_after())

    def _emit_sizes(self, footprint: FootprintScenario) -> None:
        """Emits the footprint sizes."""
        for record in footprint.sizes():
            self._ctx.emit(record)
