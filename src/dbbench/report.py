"""Renders summary.md, with numbers formatted for reading and engines named as readers know them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from dbbench.machine import MachineRecord
    from dbbench.results import MetricValue, SummaryGroup

MS_DECIMALS_BELOW_ONE: Final = 3
MS_DECIMALS_ABOVE_ONE: Final = 1
MS_THRESHOLD: Final = 1.0
RAM_DECIMALS: Final = 1
PERCENT_SCALE: Final = 100
ERROR_RATE_DECIMALS: Final = 2
SECONDS_DECIMALS: Final = 3
MB_DECIMALS: Final = 1

LATENCY_COLUMNS: Final = (
    ("p50 ms", "p50_ms"),
    ("p90 ms", "p90_ms"),
    ("p95 ms", "p95_ms"),
    ("p99 ms", "p99_ms"),
    ("p99.9 ms", "p999_ms"),
    ("max ms", "max_ms"),
)
OPS_COLUMN: Final = ("Ops/s", "ops_per_s")
ERROR_COLUMNS: Final = (("Errors", "errors"), ("Error rate", "error_rate"))

# (scenario id, heading, columns as (header, metric key)). Engine and Workers come first.
SECTIONS: Final = (
    (
        "seed",
        "Seed (load of the data set)",
        (
            ("Load s", "load_seconds"),
            ("Rows/s", "rows_per_second"),
            ("Index s", "index_seconds"),
            ("Analyze s", "analyze_seconds"),
        ),
    ),
    (
        "single_insert",
        "Single insert (1 worker, one commit each)",
        (
            OPS_COLUMN,
            *LATENCY_COLUMNS,
        ),
    ),
    ("point_read", "Point read by id (1 worker)", (OPS_COLUMN, *LATENCY_COLUMNS)),
    ("user_read", "Newest orders of one user (1 worker)", (OPS_COLUMN, *LATENCY_COLUMNS)),
    (
        "aggregate",
        "Aggregates (single client, median and min of each query)",
        (
            ("Status median ms", "by_status_median_ms"),
            ("Status min ms", "by_status_min_ms"),
            ("Top users median ms", "top_users_median_ms"),
            ("Top users min ms", "top_users_min_ms"),
        ),
    ),
    ("concurrent_read", "Concurrent point reads", (OPS_COLUMN, *LATENCY_COLUMNS)),
    (
        "concurrent_write",
        "Concurrent inserts",
        (
            OPS_COLUMN,
            *LATENCY_COLUMNS,
            *ERROR_COLUMNS,
        ),
    ),
    (
        "mixed",
        "Mixed workload (point reads and inserts in one pool)",
        (
            OPS_COLUMN,
            ("Read p99 ms", "read_p99_ms"),
            ("Write p99 ms", "write_p99_ms"),
            ("Errors", "errors"),
        ),
    ),
)

METHOD_NOTES: Final = (
    (
        "Both stores sit on the same disk under the work folder. A RAM filesystem is refused, "
        "because it would hide the fsync cost."
    ),
    (
        "PostgreSQL runs with the production settings of the author's site (a 256 MB, 1 core "
        "server running PostgreSQL 18), on PostgreSQL 18.6 built from the official source. "
        "max_connections is raised so 16 workers fit. fsync and synchronous_commit stay on."
    ),
    (
        "SQLite runs in WAL mode with a 64 MiB page cache, the same memory budget as PostgreSQL "
        "shared_buffers. SQLite 3.54.1 is built from the official amalgamation and used through "
        "pysqlite3. synchronous NORMAL is the usual production choice; FULL matches the durability "
        "of PostgreSQL."
    ),
    (
        "Each worker is a separate process with one persistent connection. Every write is its own "
        "transaction. The seed puts one batch of rows in one explicit transaction."
    ),
    (
        "Before every measured run the disk is settled: a checkpoint, an operating system sync, a "
        "wait for Dirty memory to fall, and a rest of one second. Each run's settle record is in "
        "raw.jsonl."
    ),
    (
        "The engine order is shuffled for each scenario, repeat and worker level, from a fixed "
        "seed, so no engine always goes first. The position is in raw.jsonl."
    ),
    (
        "Each scenario is repeated, every repeat starts after an untimed warmup, and the tables "
        "show the median. summary.json also has the min and max of each metric."
    ),
    (
        "Latency is taken per operation with a nanosecond clock and kept in a histogram with "
        "1 microsecond buckets up to 100 ms. Percentiles use the nearest rank. A failed "
        "operation has no latency."
    ),
    (
        "Ops per second is the successes divided by the longest worker's measured time. The error "
        "rate is errors divided by attempts."
    ),
    (
        "Latencies are closed loop service times: a worker waits for each operation before it "
        "sends the next one. A stalled worker sends nothing, so the tails are understated. No open "
        "loop run is done."
    ),
    (
        "Worker counts above the CPU count oversubscribe the machine, and the concurrent numbers "
        "include that contention."
    ),
    (
        "SQLite runs inside the benchmark process. PostgreSQL is reached over TCP, and the Unix "
        "socket rows show the same server without the TCP stack."
    ),
    (
        "The two aggregate queries must return identical results on both engines. This is checked "
        "on every run (results_match), and the run stops if they differ."
    ),
    (
        "In the concurrent scenarios a SQLite lock error (database is locked or busy) is counted, "
        "not retried, and has no latency."
    ),
    (
        "Diagnostics in raw.jsonl: PostgreSQL checkpoints and WAL bytes, and the SQLite WAL file "
        "size after each run. They are read outside the timed window. The server flushes its "
        "statistics periodically, so a checkpoint near the end of a run may show in the next one."
    ),
)


def format_int(value: float) -> str:
    """Formats a count or a rate with thousands separators and no decimals."""
    return f"{round(value):,}"


def format_ms(value: float) -> str:
    """Formats milliseconds: 3 decimals below 1 ms, 1 decimal from 1 ms up."""
    if value < MS_THRESHOLD:
        return f"{value:.{MS_DECIMALS_BELOW_ONE}f}"
    return f"{value:,.{MS_DECIMALS_ABOVE_ONE}f}"


def format_value(key: str, value: MetricValue) -> str:
    """Formats one metric by its name: booleans and text as they are, numbers by their unit."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    return _format_number(key, value)


def _format_number(key: str, value: float) -> str:
    """Formats a number by its metric name: ms, MB, seconds, error rate, or a plain count."""
    if key.endswith("_ms"):
        return format_ms(value)
    if key.endswith("_mb"):
        return f"{value:,.{MB_DECIMALS}f}"
    if key.endswith("_seconds"):
        return f"{value:,.{SECONDS_DECIMALS}f}"
    if key == "error_rate":
        return f"{value * PERCENT_SCALE:.{ERROR_RATE_DECIMALS}f}%"
    return format_int(value)


class MarkdownReport:
    """Builds the text of summary.md from the summary groups and the machine record."""

    def __init__(self, groups: list[SummaryGroup], machine: MachineRecord) -> None:
        """Remembers the groups, the machine facts and the reader-facing engine names."""
        self._groups = groups
        self._machine = machine
        self._labels = machine.engine_labels()

    def _engine(self, name: str) -> str:
        """Returns the reader-facing name of an engine, or its id when it has no label."""
        return self._labels.get(name, name)

    def render(self) -> str:
        """Returns the whole document."""
        lines = [
            f"# SQLite vs PostgreSQL results ({self._machine.mode} run)",
            "",
            f"Started at {self._machine.started_at} (UTC).",
            "",
            self._machine_line(),
            "",
            f"Aggregate results identical on both engines: {self._match_line()}",
            "",
            *self._footprint_section(),
        ]
        for scenario_id, heading, columns in SECTIONS:
            lines.extend(self._section(scenario_id, heading, columns))
        lines.extend(["## How it was measured", ""])
        lines.extend(f"- {note}" for note in METHOD_NOTES)
        return "\n".join(lines) + "\n"

    def _machine_line(self) -> str:
        """Describes the machine and the software versions in one line."""
        machine = self._machine
        ram = f"{machine.ram_gb:.{RAM_DECIMALS}f} GB RAM"
        return (
            f"Machine: {machine.cpu_model}, {machine.logical_cpus} logical CPUs, {ram}, "
            f"Linux kernel {machine.kernel}, work folder on {machine.work_dir_filesystem}. "
            f"Python {machine.python}, SQLite {machine.sqlite}, psycopg {machine.psycopg}, "
            f"{machine.postgres}."
        )

    def _match_line(self) -> str:
        """Returns yes, no, or not measured, from the aggregate groups."""
        for group in self._groups:
            if group.scenario == "aggregate":
                value = group.metrics.get("results_match")
                return format_value("results_match", value)
        return "not measured"

    def _ordered(self, scenario_id: str) -> list[SummaryGroup]:
        """Returns the groups of one scenario in table order.

        Rows follow the engine's place in the scenario's engine list of the config, then the worker
        count. The random run order of the engines never shows.
        """
        order = self._machine.engine_order(scenario_id)

        def place(group: SummaryGroup) -> tuple[int, int]:
            index = order.index(group.engine) if group.engine in order else len(order)
            return index, group.workers

        groups = [group for group in self._groups if group.scenario == scenario_id]
        return sorted(groups, key=place)

    def _footprint_section(self) -> list[str]:
        """Writes the footprint table: one row per phase, engine and measure."""
        rows = [
            [group.phase, self._engine(group.engine), name, format_value(name, value)]
            for group in self._ordered("footprint")
            for name, value in group.metrics.items()
        ]
        if not rows:
            return []
        return [
            "## Footprint",
            "",
            *_table(["Phase", "Engine", "Measure", "Value"], rows),
            "",
            _spread_line(self._runs_of(scenario_id="footprint")),
            "",
        ]

    def _runs_of(self, scenario_id: str) -> int:
        """Returns the largest repeat count among the groups of one scenario."""
        counts = [group.repeats for group in self._groups if group.scenario == scenario_id]
        return max(counts, default=0)

    def _section(
        self, scenario_id: str, heading: str, columns: tuple[tuple[str, str], ...]
    ) -> list[str]:
        """Writes one scenario table, or nothing when the scenario was not run."""
        groups = self._ordered(scenario_id)
        if not groups:
            return []
        headers = ["Engine", "Workers", *(header for header, _ in columns)]
        rows = [
            [
                self._engine(group.engine),
                str(group.workers),
                *(format_value(key, group.metrics.get(key)) for _, key in columns),
            ]
            for group in groups
        ]
        runs = self._runs_of(scenario_id)
        return [
            f"## {heading}",
            "",
            *_table(headers, rows),
            "",
            _spread_line(runs),
            "",
        ]


def _spread_line(runs: int) -> str:
    """Returns the line under a table that says how many runs the medians come from."""
    noun = "run" if runs == 1 else "runs"
    return f"Median of {runs} {noun}. Spread: see summary.json (min and max)."


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Returns a Markdown table."""
    header_line = "| " + " | ".join(headers) + " |"
    separator = "|" + "|".join("---" for _ in headers) + "|"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return [header_line, separator, *body]
