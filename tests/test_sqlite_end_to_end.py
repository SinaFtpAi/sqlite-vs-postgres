"""SQLite only, end to end: seed, point and user reads, the aggregate, and concurrent writers.

No PostgreSQL is needed for this test. It runs in the tmp_path given by the small_config fixture,
with a fake meminfo and no rest, so it stays fast and does not depend on the disk.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from dbbench.config import TOOL_ROOT
from dbbench.machine import MachineInfo
from dbbench.report import MarkdownReport
from dbbench.results import RawRecord, Summarizer
from dbbench.scenarios import (
    AggregateScenario,
    ConcurrentWriteScenario,
    PointReadScenario,
    RunContext,
    Scenario,
    SeedScenario,
    UserReadScenario,
)

if TYPE_CHECKING:
    from dbbench.config import BenchConfig
    from dbbench.settle import Settler

SQLITE_ENGINE = "sqlite_wal_normal"


def _run(ctx: RunContext, scenario: Scenario) -> list[RawRecord]:
    """Runs one scenario once, emits its records into the context and returns them."""
    records = scenario.measure(0)
    for record in records:
        ctx.emit(record)
    return records


def test_sqlite_scenarios_run_end_to_end(small_config: BenchConfig, quiet_settler: Settler) -> None:
    """Every scenario finishes, the rows are all there, and the aggregate results agree."""
    small_config.paths.work_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(small_config, "quick", None, lambda _record: None, settler=quiet_settler)

    (seeded,) = _run(ctx, SeedScenario(ctx))
    database = small_config.paths.work_dir / small_config.sqlite.file_name
    with sqlite3.connect(database) as conn:
        (row_count,) = conn.execute("SELECT COUNT(*) FROM orders").fetchone()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert seeded.metrics["rows_per_second"] is not None
    assert row_count == small_config.dataset.rows
    assert journal == "wal"

    (point,) = _run(ctx, PointReadScenario(ctx))
    assert point.metrics["ops"] == small_config.run.point_read_ops
    assert point.metrics["errors"] == 0
    assert point.metrics["p50_ms"] is not None
    assert point.metrics["p999_ms"] is not None

    (user,) = _run(ctx, UserReadScenario(ctx))
    assert user.metrics["ops"] == small_config.run.user_read_ops

    (aggregate,) = _run(ctx, AggregateScenario(ctx))
    assert aggregate.metrics["results_match"] is True

    (concurrent,) = _run(ctx, ConcurrentWriteScenario(ctx))
    assert concurrent.workers == small_config.run.concurrency_levels[0]
    ops = concurrent.metrics["ops"]
    errors = concurrent.metrics["errors"]
    assert isinstance(ops, int)
    assert isinstance(errors, int)
    assert ops + errors > 0


def test_every_measured_run_carries_settle_position_and_diagnostics(
    small_config: BenchConfig, quiet_settler: Settler
) -> None:
    """Each measured record has its settle record, its order position and the SQLite WAL size."""
    small_config.paths.work_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(small_config, "quick", None, lambda _record: None, settler=quiet_settler)
    _run(ctx, SeedScenario(ctx))
    ctx.records.clear()
    records = _run(ctx, PointReadScenario(ctx))
    (record,) = records
    assert record.order_position == 0
    assert record.settle is not None
    assert record.settle.dirty_kib == 0
    assert record.settle.timed_out is False
    assert "sqlite_wal_bytes" in record.diagnostics
    assert record.engine == SQLITE_ENGINE


def test_the_report_has_the_concurrent_table_and_the_method(
    small_config: BenchConfig, quiet_settler: Settler
) -> None:
    """The report built from a SQLite run has its tables, its spread lines and its method notes."""
    small_config.paths.work_dir.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(small_config, "quick", None, lambda _record: None, settler=quiet_settler)
    _run(ctx, SeedScenario(ctx))
    _run(ctx, PointReadScenario(ctx))
    _run(ctx, ConcurrentWriteScenario(ctx))
    groups = Summarizer().summarize(ctx.records)
    machine = MachineInfo(
        small_config, "quick", "postgres (PostgreSQL) test", "pg_config test", TOOL_ROOT
    ).collect()
    text = MarkdownReport(groups, machine).render()
    assert "## Concurrent inserts" in text
    assert "## Point read by id (1 worker)" in text
    assert "SQLite (WAL, synchronous NORMAL)" in text
    assert "Spread: see summary.json (min and max)." in text
    assert "## How it was measured" in text
