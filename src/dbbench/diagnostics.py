"""Per run readings taken by the runner process, outside the timed window.

For PostgreSQL the readings are the checkpoint count and the WAL position, from the cumulative
statistics. For SQLite the reading is the size of the WAL file right after the run. Both are kept
next to each measured record, so a slow run can be checked against the writes behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import psycopg

from dbbench.engines import PostgresEngineSpec, SqliteEngineSpec
from dbbench.errors import DbBenchError

if TYPE_CHECKING:
    from dbbench.results import MetricValue

_HAS_CHECKPOINTER_VIEW: Final = "SELECT to_regclass('pg_catalog.pg_stat_checkpointer') IS NOT NULL"
_CHECKPOINTS_CHECKPOINTER: Final = "SELECT num_timed + num_requested FROM pg_stat_checkpointer"
_CHECKPOINTS_BGWRITER: Final = "SELECT checkpoints_timed + checkpoints_req FROM pg_stat_bgwriter"
_WAL_LSN: Final = "SELECT pg_current_wal_lsn()::text"
_WAL_DIFF: Final = "SELECT pg_wal_lsn_diff(%s::pg_lsn, %s::pg_lsn)"
_SQLITE_WAL_SUFFIX: Final = "-wal"


class DiagnosticsError(DbBenchError):
    """Raised when a diagnostics query returns no row."""


@dataclass(frozen=True, slots=True)
class RunReading:
    """What PostgreSQL reported before a run: its checkpoint count and WAL position."""

    checkpoints: int = 0
    wal_lsn: str = "0/0"


class RunProbe:
    """Reads the diagnostics of one engine before and after a run."""

    def before(self, spec: SqliteEngineSpec | PostgresEngineSpec) -> RunReading:
        """Takes the PostgreSQL reading. SQLite needs none, so it returns an empty reading."""
        if isinstance(spec, PostgresEngineSpec):
            with psycopg.connect(spec.dsn, autocommit=True) as conn:
                return self._reading(conn)
        return RunReading()

    def after(
        self,
        spec: SqliteEngineSpec | PostgresEngineSpec,
        before: RunReading,
    ) -> dict[str, MetricValue]:
        """Returns the diagnostics of the run that just ended, keyed as raw.jsonl keeps them."""
        if isinstance(spec, SqliteEngineSpec):
            return {"sqlite_wal_bytes": self._sqlite_wal_bytes(spec)}
        with psycopg.connect(spec.dsn, autocommit=True) as conn:
            now = self._reading(conn)
            row = self._row(conn, _WAL_DIFF, (now.wal_lsn, before.wal_lsn))
        return {
            "pg_checkpoints": now.checkpoints - before.checkpoints,
            "pg_wal_bytes": int(row[0]),
        }

    def _reading(self, conn: psycopg.Connection[tuple[Any, ...]]) -> RunReading:
        """Reads the checkpoint count and the WAL position over one open connection."""
        has_view = bool(self._row(conn, _HAS_CHECKPOINTER_VIEW)[0])
        query = _CHECKPOINTS_CHECKPOINTER if has_view else _CHECKPOINTS_BGWRITER
        checkpoints = int(self._row(conn, query)[0])
        wal_lsn = str(self._row(conn, _WAL_LSN)[0])
        return RunReading(checkpoints=checkpoints, wal_lsn=wal_lsn)

    @staticmethod
    def _row(
        conn: psycopg.Connection[tuple[Any, ...]],
        query: str,
        params: tuple[str, ...] = (),
    ) -> tuple[Any, ...]:
        """Runs a query that returns one row, and raises DiagnosticsError when it returns none."""
        row = conn.execute(query, params).fetchone()
        if row is None:
            message = f"the diagnostics query returned no row: {query}"
            raise DiagnosticsError(message)
        return row

    @staticmethod
    def _sqlite_wal_bytes(spec: SqliteEngineSpec) -> int:
        """Returns the size of the WAL file next to the database, or 0 when there is none."""
        wal = spec.path.with_name(spec.path.name + _SQLITE_WAL_SUFFIX)
        return wal.stat().st_size if wal.exists() else 0
