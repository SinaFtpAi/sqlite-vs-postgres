"""Waits for the disk to go quiet before each measured run.

Writes left over from the previous run would otherwise be flushed during the next one and show up as
its latency. The settle step checkpoints the database, syncs the operating system, waits until the
dirty page count is low, and then rests for a fixed time.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

import psycopg
from pydantic import BaseModel, ConfigDict

from dbbench.engines import PostgresEngineSpec, SqliteEngineSpec
from dbbench.sqlite_driver import SqliteDriver

if TYPE_CHECKING:
    from dbbench.config import SettleSettings

PROC_MEMINFO: Final = Path("/proc/meminfo")
_DIRTY_PREFIX: Final = "Dirty:"
_KIB_FIELD: Final = 1
_SQLITE_CHECKPOINT: Final = "PRAGMA wal_checkpoint(TRUNCATE)"
_POSTGRES_CHECKPOINT: Final = "CHECKPOINT"


class SettleRecord(BaseModel):
    """What one settle step found, as stored in raw.jsonl.

    dirty_kib is the last Dirty value read, or null where meminfo does not exist. waited_seconds is
    the time from the end of os.sync to the start of the run, polling and the rest included.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dirty_kib: int | None
    waited_seconds: float
    timed_out: bool


class Settler:
    """Runs the settle steps before a measured run."""

    def __init__(self, settings: SettleSettings, meminfo: Path = PROC_MEMINFO) -> None:
        """Remembers the settings and the meminfo file to poll. Tests pass their own file."""
        self._settings = settings
        self._meminfo = meminfo

    def settle(self, spec: SqliteEngineSpec | PostgresEngineSpec) -> SettleRecord:
        """Checkpoints the engine's database, syncs, waits for quiet and rests.

        Returns what the wait found, so the record shows whether the disk settled or timed out.
        """
        self._checkpoint(spec)
        os.sync()
        started = time.monotonic()
        dirty_kib, timed_out = self._wait_for_quiet()
        time.sleep(self._settings.min_seconds)
        return SettleRecord(
            dirty_kib=dirty_kib,
            waited_seconds=time.monotonic() - started,
            timed_out=timed_out,
        )

    @staticmethod
    def _checkpoint(spec: SqliteEngineSpec | PostgresEngineSpec) -> None:
        """Moves the WAL into the database: SQLite on a new connection, PostgreSQL by CHECKPOINT."""
        if isinstance(spec, SqliteEngineSpec):
            sqlite_conn = SqliteDriver(spec.driver).connect_default(spec.path)
            try:
                sqlite_conn.execute(_SQLITE_CHECKPOINT).fetchall()
            finally:
                sqlite_conn.close()
            return
        with psycopg.connect(spec.dsn, autocommit=True) as conn:
            conn.execute(_POSTGRES_CHECKPOINT)

    def _wait_for_quiet(self) -> tuple[int | None, bool]:
        """Polls Dirty until it is low enough or the timeout passes.

        Returns the last Dirty value read, and whether the wait timed out.
        """
        deadline = time.monotonic() + self._settings.timeout_seconds
        while True:
            dirty = self._read_dirty_kib()
            if dirty is None or dirty <= self._settings.dirty_max_kib:
                return dirty, False
            if time.monotonic() >= deadline:
                return dirty, True
            time.sleep(self._settings.poll_seconds)

    def _read_dirty_kib(self) -> int | None:
        """Returns the Dirty value of meminfo in kB, or None when the file or line is missing."""
        try:
            lines = self._meminfo.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            if line.startswith(_DIRTY_PREFIX):
                return int(line.split()[_KIB_FIELD])
        return None
