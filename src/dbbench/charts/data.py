"""Chart data: summary.json and machine.json turned into chart models, and the charts.json file.

A chart model holds what one chart shows, in plain numbers and words, and is written to
charts.json as it is. A draft adds the ratio note of each row, which the SVG shows and charts.json
leaves out.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict

from dbbench.charts.format import ValueFormatter
from dbbench.charts.units import COUNT, MILLISECONDS, SHARE, ChartForm, ChartScale, ChartUnit

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from dbbench.config import ChartsSettings
    from dbbench.machine import MachineRecord
    from dbbench.results import SummaryGroup

SOURCE_FILE: Final = "summary.json"
JSON_INDENT: Final = 2
VERSION_PATTERN: Final = re.compile(r"\d+(?:\.\d+)+")

SQLITE_NORMAL: Final = "sqlite_wal_normal"
SQLITE_FULL: Final = "sqlite_wal_full"
SQLITE_NOWAIT: Final = "sqlite_wal_nowait"
POSTGRES: Final = "postgres"
POSTGRES_UNIX: Final = "postgres_unix"

POINT_READ: Final = "point_read"
USER_READ: Final = "user_read"
SINGLE_INSERT: Final = "single_insert"
CONCURRENT_READ: Final = "concurrent_read"
CONCURRENT_WRITE: Final = "concurrent_write"
AGGREGATE: Final = "aggregate"

OPS_METRIC: Final = "ops_per_s"
ERROR_RATE_METRIC: Final = "error_rate"
BY_STATUS_METRIC: Final = "by_status_median_ms"
TOP_USERS_METRIC: Final = "top_users_median_ms"
LATENCY_STEPS: Final = (
    ("p50", "p50_ms"),
    ("p90", "p90_ms"),
    ("p99", "p99_ms"),
    ("p99.9", "p999_ms"),
    ("max", "max_ms"),
)

CHART_SINGLE_CLIENT_READS: Final = "single-client-reads"
CHART_SINGLE_INSERT: Final = "single-insert"
CHART_CONCURRENT_READS: Final = "concurrent-reads"
CHART_CONCURRENT_WRITES: Final = "concurrent-writes"
CHART_WRITE_FAILURES: Final = "write-failures"
CHART_WRITE_LATENCY: Final = "write-latency-16"
CHART_AGGREGATES: Final = "aggregates"


class ChartPoint(BaseModel):
    """One value of a series: its category, the median and the min and max over the repeats."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    value: float
    min: float
    max: float


class ChartSeries(BaseModel):
    """One engine's values in one chart, with its colors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine: str
    label: str
    color_dark: str
    color_light: str
    hollow: bool
    points: list[ChartPoint]


class ChartModel(BaseModel):
    """One chart, as charts.json holds it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    subtitle: str
    form: ChartForm
    unit: ChartUnit
    scale: ChartScale
    higher_is_better: bool
    categories: list[str]
    series: list[ChartSeries]


class ChartMachine(BaseModel):
    """The machine and the software versions a chart set was measured on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu: str
    filesystem: str
    sqlite: str
    postgres: str


class ChartSet(BaseModel):
    """The whole charts.json: where the numbers come from, the machine and every chart."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    repeats: int
    machine: ChartMachine
    charts: list[ChartModel]


@dataclass(frozen=True, slots=True)
class ChartDraft:
    """A chart model with the ratio note of each row, which only the SVG shows."""

    model: ChartModel
    row_notes: dict[str, str]


@dataclass(frozen=True, slots=True)
class _Cell:
    """One measured value with the min and max over its repeats."""

    value: float
    low: float
    high: float


@dataclass(frozen=True, slots=True)
class _Spec:
    """What makes one chart: its id, its form and units, its engines and its ratio pair."""

    chart_id: str
    form: ChartForm
    unit: ChartUnit
    scale: ChartScale
    higher_is_better: bool
    engines: tuple[str, ...]
    ratio_pair: tuple[str, str] | None = None


_Columns = list[tuple[str, dict[str, _Cell]]]


class _DatasetFacts(BaseModel):
    """The part of the run's dataset that the chart text names."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    rows: int


class _QueryFacts(BaseModel):
    """The row limits of the two read queries, as the run used them."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    user_read_limit: int
    top_users_limit: int


class _RunFacts(BaseModel):
    """What the run's config says about the data, for the chart titles and row labels."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    dataset: _DatasetFacts
    queries: _QueryFacts


class ChartDataBuilder:
    """Builds the chart models of one results folder, and writes charts.json."""

    def __init__(
        self,
        groups: list[SummaryGroup],
        machine: MachineRecord,
        labels: dict[str, str],
        settings: ChartsSettings,
    ) -> None:
        """Indexes the summary groups and reads the run's facts. Nothing is built yet."""
        self._labels = labels
        self._settings = settings
        self._wording = settings.wording
        self._formatter = ValueFormatter(settings.wording)
        self._machine = machine
        self._facts = _RunFacts.model_validate(machine.config)
        self._repeats = max((group.repeats for group in groups), default=0)
        self._groups = {(group.scenario, group.engine, group.workers): group for group in groups}
        self._levels = {
            scenario: sorted({group.workers for group in groups if group.scenario == scenario})
            for scenario in (CONCURRENT_READ, CONCURRENT_WRITE)
        }

    def drafts(self) -> tuple[list[ChartDraft], list[str]]:
        """Returns the drafts of every chart that has data, and the ids of the charts with none."""
        builders: tuple[tuple[str, Callable[[], ChartDraft | None]], ...] = (
            (CHART_SINGLE_CLIENT_READS, self._single_client_reads),
            (CHART_SINGLE_INSERT, self._single_insert),
            (CHART_CONCURRENT_READS, self._concurrent_reads),
            (CHART_CONCURRENT_WRITES, self._concurrent_writes),
            (CHART_WRITE_FAILURES, self._write_failures),
            (CHART_WRITE_LATENCY, self._write_latency),
            (CHART_AGGREGATES, self._aggregates),
        )
        drafts: list[ChartDraft] = []
        skipped: list[str] = []
        for chart_id, build in builders:
            draft = build()
            if draft is None:
                skipped.append(chart_id)
            else:
                drafts.append(draft)
        return drafts, skipped

    def chart_set(self, drafts: list[ChartDraft]) -> ChartSet:
        """Returns the charts.json model of the drafts, with the machine they were measured on."""
        machine = self._machine
        return ChartSet(
            source=SOURCE_FILE,
            repeats=self._repeats,
            machine=ChartMachine(
                cpu=machine.cpu_model,
                filesystem=machine.work_dir_filesystem,
                sqlite=machine.sqlite,
                postgres=_postgres_version(machine.postgres),
            ),
            charts=[draft.model for draft in drafts],
        )

    def write_json(self, chart_set: ChartSet, path: Path) -> None:
        """Writes charts.json with the same numbers as the SVG files."""
        path.write_text(chart_set.model_dump_json(indent=JSON_INDENT) + "\n", encoding="utf-8")

    def _single_client_reads(self) -> ChartDraft | None:
        """Point and user reads from one client, in queries per second, with the SQLite ratio."""
        spec = _Spec(
            CHART_SINGLE_CLIENT_READS,
            form="dot",
            unit=COUNT,
            scale="log",
            higher_is_better=True,
            engines=(SQLITE_NORMAL, POSTGRES, POSTGRES_UNIX),
            ratio_pair=(SQLITE_NORMAL, POSTGRES),
        )
        columns = [
            (self._row_label(scenario), self._row_cells(spec.engines, scenario, 1, OPS_METRIC))
            for scenario in (POINT_READ, USER_READ)
        ]
        return self._assemble(spec, columns)

    def _single_insert(self) -> ChartDraft | None:
        """Inserts with one commit each, one row per engine, in inserts per second."""
        spec = _Spec(
            CHART_SINGLE_INSERT,
            form="dot",
            unit=COUNT,
            scale="log",
            higher_is_better=True,
            engines=(SQLITE_NORMAL, SQLITE_FULL, POSTGRES, POSTGRES_UNIX),
        )
        columns = [
            (
                self._labels.get(engine, engine),
                self._row_cells((engine,), SINGLE_INSERT, 1, OPS_METRIC),
            )
            for engine in spec.engines
        ]
        return self._assemble(spec, columns)

    def _concurrent_reads(self) -> ChartDraft | None:
        """Reads as the worker count grows, in total queries per second."""
        spec = _Spec(
            CHART_CONCURRENT_READS,
            form="line",
            unit=COUNT,
            scale="linear",
            higher_is_better=True,
            engines=(SQLITE_NORMAL, POSTGRES),
        )
        return self._assemble(spec, self._worker_columns(spec.engines, CONCURRENT_READ, OPS_METRIC))

    def _concurrent_writes(self) -> ChartDraft | None:
        """Inserts as the worker count grows, in total inserts per second."""
        spec = _Spec(
            CHART_CONCURRENT_WRITES,
            form="line",
            unit=COUNT,
            scale="linear",
            higher_is_better=True,
            engines=(SQLITE_NORMAL, SQLITE_FULL, POSTGRES),
        )
        return self._assemble(
            spec, self._worker_columns(spec.engines, CONCURRENT_WRITE, OPS_METRIC)
        )

    def _write_failures(self) -> ChartDraft | None:
        """The share of inserts that failed with a lock error, with no busy timeout."""
        spec = _Spec(
            CHART_WRITE_FAILURES,
            form="bar",
            unit=SHARE,
            scale="linear",
            higher_is_better=False,
            engines=(SQLITE_NOWAIT,),
        )
        return self._assemble(
            spec, self._worker_columns(spec.engines, CONCURRENT_WRITE, ERROR_RATE_METRIC)
        )

    def _write_latency(self) -> ChartDraft | None:
        """Insert latency percentiles at the highest worker count, in ms."""
        levels = self._levels[CONCURRENT_WRITE]
        if not levels:
            return None
        spec = _Spec(
            CHART_WRITE_LATENCY,
            form="percentile",
            unit=MILLISECONDS,
            scale="log",
            higher_is_better=False,
            engines=(SQLITE_NORMAL, SQLITE_FULL, POSTGRES),
        )
        highest = levels[-1]
        columns = [
            (label, self._row_cells(spec.engines, CONCURRENT_WRITE, highest, metric))
            for label, metric in LATENCY_STEPS
        ]
        return self._assemble(spec, columns, facts={"workers": str(highest)})

    def _aggregates(self) -> ChartDraft | None:
        """The two report queries in median ms, with the ratio between the two engines."""
        spec = _Spec(
            CHART_AGGREGATES,
            form="dot",
            unit=MILLISECONDS,
            scale="linear",
            higher_is_better=False,
            engines=(SQLITE_NORMAL, POSTGRES),
            ratio_pair=(SQLITE_NORMAL, POSTGRES),
        )
        columns = [
            (
                self._row_label("status"),
                self._row_cells(spec.engines, AGGREGATE, 1, BY_STATUS_METRIC),
            ),
            (
                self._row_label("top_users"),
                self._row_cells(spec.engines, AGGREGATE, 1, TOP_USERS_METRIC),
            ),
        ]
        return self._assemble(spec, columns)

    def _worker_columns(self, engines: tuple[str, ...], scenario: str, metric: str) -> _Columns:
        """One column per worker level of a scenario, labelled with the level."""
        return [
            (str(level), self._row_cells(engines, scenario, level, metric))
            for level in self._levels[scenario]
        ]

    def _row_cells(
        self, engines: tuple[str, ...], scenario: str, workers: int, metric: str
    ) -> dict[str, _Cell]:
        """The measured cells of the engines that have this metric, keyed by engine."""
        cells: dict[str, _Cell] = {}
        for engine in engines:
            group = self._groups.get((scenario, engine, workers))
            if group is None:
                continue
            value = _number(group.metrics.get(metric))
            if value is None:
                continue
            low = _number(group.metrics_min.get(metric))
            high = _number(group.metrics_max.get(metric))
            cells[engine] = _Cell(
                value=value,
                low=value if low is None else low,
                high=value if high is None else high,
            )
        return cells

    def _row_label(self, key: str) -> str:
        """Returns the wording of a row label, with the run's query limits filled in."""
        return self._wording.row_labels[key].format(
            user_read_limit=self._facts.queries.user_read_limit,
            top_users_limit=self._facts.queries.top_users_limit,
        )

    def _assemble(
        self,
        spec: _Spec,
        columns: _Columns,
        facts: dict[str, str] | None = None,
    ) -> ChartDraft | None:
        """Builds one chart from its columns, or returns None when no column has a value."""
        present = [(label, cells) for label, cells in columns if cells]
        if not present:
            return None
        text = self._wording.titles[spec.chart_id]
        values = {
            "rows": self._formatter.count(self._facts.dataset.rows),
            "runs": self._formatter.runs(self._repeats),
            **(facts or {}),
        }
        model = ChartModel(
            id=spec.chart_id,
            title=text.title.format(**values),
            subtitle=text.subtitle.format(**values),
            form=spec.form,
            unit=spec.unit,
            scale=spec.scale,
            higher_is_better=spec.higher_is_better,
            categories=[label for label, _ in present],
            series=[
                series
                for series in (self._series(engine, present) for engine in spec.engines)
                if series.points
            ],
        )
        return ChartDraft(
            model=model,
            row_notes=self._ratio_notes(
                present, spec.ratio_pair, higher_is_better=spec.higher_is_better
            ),
        )

    def _series(self, engine: str, present: _Columns) -> ChartSeries:
        """One engine's points over the columns that have a value for it."""
        style = self._settings.series[engine]
        points = [
            ChartPoint(category=label, value=cell.value, min=cell.low, max=cell.high)
            for label, cells in present
            if (cell := cells.get(engine)) is not None
        ]
        return ChartSeries(
            engine=engine,
            label=self._labels.get(engine, engine),
            color_dark=style.dark,
            color_light=style.light,
            hollow=style.hollow,
            points=points,
        )

    def _ratio_notes(
        self,
        present: _Columns,
        ratio_pair: tuple[str, str] | None,
        *,
        higher_is_better: bool,
    ) -> dict[str, str]:
        """Returns the ratio note of each row where both engines of the pair have a value.

        The note names the faster engine. The ratio is the larger value over the smaller, so it is
        never below one.
        """
        if ratio_pair is None:
            return {}
        first, second = ratio_pair
        notes: dict[str, str] = {}
        for label, cells in present:
            a, b = cells.get(first), cells.get(second)
            if a is None or b is None or a.value <= 0 or b.value <= 0:
                continue
            first_is_faster = a.value >= b.value if higher_is_better else a.value <= b.value
            faster = first if first_is_faster else second
            ratio = max(a.value, b.value) / min(a.value, b.value)
            notes[label] = self._wording.ratio_faster.format(
                ratio=self._formatter.ratio(ratio),
                engine=self._wording.engine_ratio_names[faster],
            )
        return notes


def _number(value: object) -> float | None:
    """Returns a metric as a float, or None when it is missing, not a number or not finite."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return float(value)


def _postgres_version(line: str) -> str:
    """Returns the version number from the server's version line, for example 18.6."""
    match = VERSION_PATTERN.search(line)
    return match.group(0) if match else line
