"""Number formatting and the layout of summary.md."""

from __future__ import annotations

from dbbench.machine import MachineRecord
from dbbench.report import MarkdownReport, format_int, format_ms, format_value
from dbbench.results import RawRecord, Summarizer


def test_counts_get_thousands_separators() -> None:
    """Large counts are easy to read: 1,234,567."""
    assert format_int(1234567) == "1,234,567"
    assert format_value("ops_per_s", 1234.4) == "1,234"


def test_milliseconds_have_three_decimals_below_one_and_one_above() -> None:
    """Below 1 ms: 0.500. From 1 ms up: 1,234.6."""
    assert format_ms(0.5) == "0.500"
    assert format_ms(0.0424) == "0.042"
    assert format_ms(1234.56) == "1,234.6"
    assert format_ms(1.0) == "1.0"


def test_error_rate_is_a_percentage_and_missing_values_say_so() -> None:
    """A rate of 0.0123 reads as 1.23%, and an empty percentile as n/a."""
    assert format_value("error_rate", 0.0123) == "1.23%"
    assert format_value("p99_ms", None) == "n/a"
    assert format_value("results_match", value=True) == "yes"


def _machine(config: dict[str, object] | None = None) -> MachineRecord:
    """Returns a machine record with fixed values, so the report text is predictable."""
    return MachineRecord(
        started_at="2026-11-04T10:00:00+00:00",
        mode="quick",
        cpu_model="Test CPU",
        logical_cpus=8,
        ram_gb=31.3,
        kernel="6.6.0",
        work_dir="~/work",
        work_dir_filesystem="ext4",
        python="3.12.1",
        sqlite="3.45.0",
        psycopg="3.3.8",
        postgres="postgres (PostgreSQL) 18.6",
        git_commit=None,
        config=config or {},
    )


def _point_read_records() -> list[RawRecord]:
    """Returns one aggregate record and one point read record."""
    return [
        RawRecord(
            scenario="aggregate",
            engine="sqlite_wal_normal",
            workers=1,
            repeat=0,
            metrics={"by_status_median_ms": 12.25, "results_match": True},
        ),
        RawRecord(
            scenario="point_read",
            engine="sqlite_wal_normal",
            workers=1,
            repeat=0,
            metrics={"ops_per_s": 25000.0, "p50_ms": 0.0312, "p99_ms": 1.5},
        ),
    ]


def test_report_has_the_header_and_one_table_per_scenario() -> None:
    """The header names the mode and the UTC start, and each scenario that ran gets a table."""
    groups = Summarizer().summarize(_point_read_records())
    text = MarkdownReport(groups, _machine()).render()
    assert text.startswith(
        "# SQLite vs PostgreSQL results (quick run)\n\n"
        "Started at 2026-11-04T10:00:00+00:00 (UTC).\n"
    )
    assert "Aggregate results identical on both engines: yes" in text
    assert "## Point read by id (1 worker)" in text
    assert "| sqlite_wal_normal | 1 | 25,000 | 0.031 | n/a | n/a | 1.5 | n/a | n/a |" in text
    assert "## Concurrent inserts" not in text
    assert "## How it was measured" in text


def test_each_table_names_the_runs_behind_its_medians() -> None:
    """Under each table: the number of repeats, and where the spread is. One run is singular."""
    groups = Summarizer().summarize(_point_read_records())
    text = MarkdownReport(groups, _machine()).render()
    assert "Median of 1 run. Spread: see summary.json (min and max)." in text


def test_engines_show_their_reader_facing_labels() -> None:
    """With labels in the config, the table shows the label; without one, the engine id."""
    labels = {"sqlite_wal_normal": "SQLite (WAL, synchronous NORMAL)"}
    groups = Summarizer().summarize(_point_read_records())
    text = MarkdownReport(groups, _machine({"labels": labels})).render()
    assert "| SQLite (WAL, synchronous NORMAL) | 1 | 25,000 |" in text


def test_rows_follow_the_engine_order_of_the_config_then_the_workers() -> None:
    """Runs are shuffled, but tables list engines in config order, then workers ascending."""
    config = {
        "engines_by_scenario": {"concurrent_read": ["sqlite_wal_normal", "postgres"]},
        "labels": {"sqlite_wal_normal": "SQLite", "postgres": "PostgreSQL"},
    }
    records = [
        RawRecord(
            scenario="concurrent_read",
            engine="postgres",
            workers=16,
            repeat=0,
            metrics={"ops_per_s": 192135.0},
        ),
        RawRecord(
            scenario="concurrent_read",
            engine="sqlite_wal_normal",
            workers=16,
            repeat=0,
            metrics={"ops_per_s": 895216.0},
        ),
        RawRecord(
            scenario="concurrent_read",
            engine="postgres",
            workers=1,
            repeat=0,
            metrics={"ops_per_s": 10221.0},
        ),
        RawRecord(
            scenario="concurrent_read",
            engine="sqlite_wal_normal",
            workers=1,
            repeat=0,
            metrics={"ops_per_s": 256095.0},
        ),
    ]
    groups = Summarizer().summarize(records)
    text = MarkdownReport(groups, _machine(config)).render()
    rows = [
        line for line in text.splitlines() if line.startswith(("| SQLite | ", "| PostgreSQL | "))
    ]
    assert rows == [
        "| SQLite | 1 | 256,095 | n/a | n/a | n/a | n/a | n/a | n/a |",
        "| SQLite | 16 | 895,216 | n/a | n/a | n/a | n/a | n/a | n/a |",
        "| PostgreSQL | 1 | 10,221 | n/a | n/a | n/a | n/a | n/a | n/a |",
        "| PostgreSQL | 16 | 192,135 | n/a | n/a | n/a | n/a | n/a | n/a |",
    ]
