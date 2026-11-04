"""Charts: the chart models from a small hand written summary, the number text and the files."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dbbench.charts import ChartRunner
from dbbench.charts.data import ChartDataBuilder
from dbbench.charts.format import ValueFormatter
from dbbench.charts.theme import THEME_MODES
from dbbench.config import TOOL_ROOT
from dbbench.errors import DbBenchError
from dbbench.machine import MachineRecord
from dbbench.results import ResultWriter, SummaryFile

if TYPE_CHECKING:
    from dbbench.config import BenchConfig

FIXTURE = Path(__file__).resolve().parent / "data" / "charts_summary.json"
ALL_CHART_IDS = (
    "single-client-reads",
    "single-insert",
    "concurrent-reads",
    "concurrent-writes",
    "write-failures",
    "write-latency-16",
    "aggregates",
)
EXPECTED_ROW_NOTES = {
    "Read one order by id": "20.0x faster in SQLite",
    "Newest 20 orders of one user": "2.0x faster in PostgreSQL",
}


def _machine(rows: int) -> MachineRecord:
    """Returns a machine record whose config names the row count and the query limits."""
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
        sqlite="3.53.1",
        psycopg="3.3.8",
        postgres="postgres (PostgreSQL) 18.6",
        git_commit=None,
        config={
            "dataset": {"rows": rows},
            "queries": {"user_read_limit": 20, "top_users_limit": 10},
        },
    )


def _builder(bench_config: BenchConfig, rows: int = 50_000) -> ChartDataBuilder:
    """Returns a builder over the fixture summary, with the labels and charts of the real config."""
    groups = SummaryFile.model_validate_json(FIXTURE.read_text(encoding="utf-8")).groups
    return ChartDataBuilder(groups, _machine(rows), bench_config.labels, bench_config.charts)


def test_ratio_notes_name_the_faster_engine_with_one_decimal(bench_config: BenchConfig) -> None:
    """The note reads faster in the winner: 20.0x in SQLite, 2.0x in PostgreSQL, 1.6x for status."""
    drafts, skipped = _builder(bench_config).drafts()
    assert skipped == []
    single = next(draft for draft in drafts if draft.model.id == "single-client-reads")
    assert single.row_notes == EXPECTED_ROW_NOTES
    aggregates = next(draft for draft in drafts if draft.model.id == "aggregates")
    assert aggregates.row_notes == {
        "Count and sum by status, half the table": "1.6x faster in PostgreSQL",
        "Top 10 users by spend, full table": "2.9x faster in PostgreSQL",
    }


def test_series_keep_the_engine_order_colors_and_hollow_markers(bench_config: BenchConfig) -> None:
    """Series follow the order of the chart; the Unix socket line is the hollow one."""
    drafts, _ = _builder(bench_config).drafts()
    single = next(draft for draft in drafts if draft.model.id == "single-client-reads")
    engines = [series.engine for series in single.model.series]
    assert engines == ["sqlite_wal_normal", "postgres", "postgres_unix"]
    sqlite, postgres, unix = single.model.series
    assert (sqlite.color_dark, sqlite.color_light) == ("#D95926", "#EB6834")
    assert (postgres.color_dark, postgres.color_light) == ("#3987E5", "#2A78D6")
    assert (sqlite.hollow, postgres.hollow, unix.hollow) == (False, False, True)
    assert sqlite.label == bench_config.labels["sqlite_wal_normal"]


def test_titles_fill_the_row_count_worker_count_and_repeats(bench_config: BenchConfig) -> None:
    """The placeholders are filled from the data: 50k rows, the highest worker level, 3 runs."""
    drafts, _ = _builder(bench_config).drafts()
    by_id = {draft.model.id: draft.model for draft in drafts}
    assert by_id["aggregates"].title == "Reports over 50k rows"
    assert by_id["write-latency-16"].title == "Write latency with 16 writers"
    assert by_id["concurrent-reads"].subtitle.endswith("the min and max of 3 runs")
    assert by_id["concurrent-reads"].categories == ["1", "4", "16"]


def test_missing_scenarios_skip_their_charts(bench_config: BenchConfig) -> None:
    """A folder without the concurrent scenarios draws the others and names what it skipped."""
    groups = SummaryFile.model_validate_json(FIXTURE.read_text(encoding="utf-8")).groups
    kept = [group for group in groups if group.scenario != "concurrent_write"]
    builder = ChartDataBuilder(kept, _machine(50_000), bench_config.labels, bench_config.charts)
    drafts, skipped = builder.drafts()
    assert set(skipped) == {"concurrent-writes", "write-failures", "write-latency-16"}
    assert "aggregates" in {draft.model.id for draft in drafts}


def test_chart_set_json_has_the_documented_shape(bench_config: BenchConfig, tmp_path: Path) -> None:
    """charts.json holds the source, the repeats, the machine and the charts, with their keys."""
    builder = _builder(bench_config)
    drafts, _ = builder.drafts()
    chart_set = builder.chart_set(drafts)
    path = tmp_path / "charts.json"
    builder.write_json(chart_set, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source"] == "summary.json"
    assert payload["repeats"] == 3
    assert payload["machine"] == {
        "cpu": "Test CPU",
        "filesystem": "ext4",
        "sqlite": "3.53.1",
        "postgres": "18.6",
    }
    assert [chart["id"] for chart in payload["charts"]] == list(ALL_CHART_IDS)
    first = payload["charts"][0]
    assert set(first) == {
        "id",
        "title",
        "subtitle",
        "form",
        "unit",
        "scale",
        "higher_is_better",
        "categories",
        "series",
    }
    assert set(first["series"][0]["points"][0]) == {"category", "value", "min", "max"}


def test_every_chart_writes_a_dark_and_a_light_svg(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """Each chart id has both SVG files, plus charts.json and embed.md, all drawn with Agg."""
    results = tmp_path / "results" / "2026-11-04-quick"
    results.mkdir(parents=True)
    shutil.copy(FIXTURE, results / "summary.json")
    ResultWriter(results).write_machine(_machine(50_000))
    outcome = ChartRunner(bench_config.charts, bench_config.labels, results).run()
    assert outcome.written == ALL_CHART_IDS
    assert outcome.skipped == ()
    for chart_id in ALL_CHART_IDS:
        for mode in THEME_MODES:
            svg = outcome.charts_dir / f"{chart_id}-{mode}.svg"
            assert svg.read_text(encoding="utf-8").lstrip().startswith("<?xml")
    embed = (outcome.charts_dir / "embed.md").read_text(encoding="utf-8")
    assert embed.count("<picture>") == len(ALL_CHART_IDS)
    assert "results/2026-11-04-quick/charts/aggregates-dark.svg" in embed


def test_png_previews_outside_the_repository_are_written(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """With a preview folder, every chart is also saved as a PNG in that folder."""
    results = tmp_path / "results" / "run"
    results.mkdir(parents=True)
    shutil.copy(FIXTURE, results / "summary.json")
    ResultWriter(results).write_machine(_machine(50_000))
    preview = tmp_path / "previews"
    ChartRunner(bench_config.charts, bench_config.labels, results, preview).run()
    assert len(list(preview.glob("*.png"))) == len(ALL_CHART_IDS) * len(THEME_MODES)


def test_preview_inside_the_repository_is_refused(bench_config: BenchConfig) -> None:
    """A PNG preview folder under the repository is an error, so no PNG lands in the project."""
    runner = ChartRunner(
        bench_config.charts,
        bench_config.labels,
        TOOL_ROOT / "results" / "nowhere",
        TOOL_ROOT / "results" / "previews",
    )
    with pytest.raises(DbBenchError, match="outside the repository"):
        runner.run()


def test_compact_counts_round_the_way_the_labels_need(bench_config: BenchConfig) -> None:
    """199,839 reads as 200k, 7,191 keeps one decimal, and a million is 1M."""
    formatter = ValueFormatter(bench_config.charts.wording)
    assert formatter.count(199_839) == "200k"
    assert formatter.count(7_191) == "7.2k"
    assert formatter.count(50_000) == "50k"
    assert formatter.count(1_000_000) == "1M"
    assert formatter.count(1_000) == "1k"
    assert formatter.count(100) == "100"


def test_milliseconds_labels_and_ticks(bench_config: BenchConfig) -> None:
    """5008.0 ms reads as 5,008 ms, 0.003 ms keeps its decimals, and ticks drop trailing zeros."""
    formatter = ValueFormatter(bench_config.charts.wording)
    assert formatter.label("ms", 5008.0) == "5,008 ms"
    assert formatter.label("ms", 0.003) == "0.003 ms"
    assert formatter.label("ms", 16.7) == "16.7 ms"
    assert formatter.milliseconds_tick(0.01) == "0.01"
    assert formatter.milliseconds_tick(1000.0) == "1,000"


def test_run_count_is_singular_for_one_run(bench_config: BenchConfig) -> None:
    """One run reads as 1 run, and more as 5 runs."""
    formatter = ValueFormatter(bench_config.charts.wording)
    assert formatter.runs(1) == "1 run"
    assert formatter.runs(5) == "5 runs"
