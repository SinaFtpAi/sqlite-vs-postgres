"""Validated benchmark settings, loaded from config/dbbench.yaml and the local overlay.

This module only parses and checks the files. It does not read the environment, and it keeps no
defaults in code: every value comes from the YAML. config/local.yaml, when it exists, is merged over
config/dbbench.yaml key by key. It is gitignored, so it holds only this machine's own values.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from psycopg.conninfo import make_conninfo
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    field_validator,
)

TOOL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = TOOL_ROOT / "config" / "dbbench.yaml"
LOCAL_OVERLAY_NAME: Final = "local.yaml"
_SOURCE_SUBDIR: Final = "src"
_WHEEL_SUBDIR: Final = "wheels"

NS_PER_US = 1_000
NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000

NonEmpty = Annotated[str, StringConstraints(min_length=1)]
HexDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _Frozen(BaseModel):
    """Base for every settings model: unknown keys are errors and values cannot change."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _expand_home(value: object) -> object:
    """Expands a leading ~ in a path given as text."""
    if isinstance(value, str):
        return Path(value).expanduser()
    return value


class PathSettings(_Frozen):
    """Where the stores and the results are, and which folders the guard must never touch."""

    work_dir: Path
    results_dir: Path
    protected_paths: list[Path]

    @field_validator("work_dir", mode="before")
    @classmethod
    def _expand(cls, value: object) -> object:
        """Expands a leading ~ in the work folder."""
        return _expand_home(value)

    @field_validator("protected_paths", mode="before")
    @classmethod
    def _expand_list(cls, value: object) -> object:
        """Expands a leading ~ in every protected folder."""
        if isinstance(value, list):
            return [_expand_home(item) for item in value]
        return value

    def results_root(self, tool_root: Path) -> Path:
        """Returns the results folder, resolved against the tool folder when it is relative."""
        if self.results_dir.is_absolute():
            return self.results_dir
        return tool_root / self.results_dir


class PostgresSettings(_Frozen):
    """The bench PostgreSQL cluster: binaries, how to reach it and its settings.

    A null bin_dir means the binaries that dbbench install built. Any other path is a PostgreSQL
    installation of the user's own.
    """

    bin_dir: Path | None
    host: NonEmpty
    port: PositiveInt
    user: NonEmpty
    database: NonEmpty
    cluster_dir: NonEmpty
    data_dir: NonEmpty
    log_file: NonEmpty
    settings: dict[NonEmpty, str | int | float]
    prepare_threshold: int = Field(ge=0)

    @field_validator("bin_dir", mode="before")
    @classmethod
    def _expand(cls, value: object) -> object:
        """Expands a leading ~ in the binary folder, when one is given."""
        return _expand_home(value)

    def dsn(self, database: str | None = None) -> str:
        """Returns the TCP connection string for the bench cluster, or for another database."""
        return make_conninfo(
            host=self.host,
            port=str(self.port),
            user=self.user,
            dbname=database or self.database,
        )

    def socket_dsn(self, socket_dir: Path, database: str | None = None) -> str:
        """Returns the Unix socket connection string: the socket folder replaces the host."""
        return make_conninfo(
            host=str(socket_dir),
            port=str(self.port),
            user=self.user,
            dbname=database or self.database,
        )


class SqliteVariant(_Frozen):
    """Per connection pragmas that tell one SQLite engine variant from another."""

    synchronous: Literal["NORMAL", "FULL"]
    busy_timeout_ms: int = Field(ge=0)


class SqliteSettings(_Frozen):
    """The SQLite file, its driver, its cache settings, the lock error texts and the variants.

    The driver is pysqlite3 (the build from dbbench install) or stdlib (Python's own sqlite3).
    """

    driver: Literal["pysqlite3", "stdlib"]
    file_name: NonEmpty
    cache_size_kib: PositiveInt
    mmap_size_bytes: int = Field(ge=0)
    cached_statements: int = Field(ge=0)
    lock_error_messages: tuple[NonEmpty, ...] = Field(min_length=1)
    variants: dict[NonEmpty, SqliteVariant]


class SettleSettings(_Frozen):
    """How the runner waits for the disk to go quiet before each measured run."""

    dirty_max_kib: int = Field(ge=0)
    poll_seconds: float = Field(gt=0)
    timeout_seconds: float = Field(ge=0)
    min_seconds: float = Field(ge=0)


class AmountRange(_Frozen):
    """Inclusive range for amount_cents."""

    minimum: PositiveInt
    maximum: PositiveInt


class DatasetSettings(_Frozen):
    """Size and shape of the generated orders."""

    rows: PositiveInt
    users: PositiveInt
    batch_size: PositiveInt
    note_length: PositiveInt
    note_alphabet: NonEmpty
    seed: int
    start: datetime
    span_days: PositiveInt
    amount_cents: AmountRange
    statuses: dict[NonEmpty, PositiveInt] = Field(min_length=1)

    def cutoff(self) -> datetime:
        """Returns the instant half way through the span, used by the status aggregate."""
        return self.start + timedelta(days=self.span_days) / 2


class QueriesSettings(_Frozen):
    """Row limits of the two read queries."""

    user_read_limit: PositiveInt
    top_users_limit: PositiveInt


class LatencySettings(_Frozen):
    """Bucket width and range of the latency histogram."""

    bucket_us: PositiveInt
    range_ms: PositiveInt

    @property
    def bucket_ns(self) -> int:
        """Returns the bucket width in nanoseconds."""
        return self.bucket_us * NS_PER_US

    @property
    def range_ns(self) -> int:
        """Returns the histogram range in nanoseconds."""
        return self.range_ms * NS_PER_MS


class RunSettings(_Frozen):
    """How many times and how long each scenario runs."""

    repeats: PositiveInt
    warmup_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    single_insert_ops: PositiveInt
    point_read_ops: PositiveInt
    user_read_ops: PositiveInt
    aggregate_runs: PositiveInt
    concurrency_levels: tuple[PositiveInt, ...] = Field(min_length=1)
    mixed_workers: PositiveInt
    mixed_read_share: float = Field(ge=0, le=1)
    count_warmup_ops: int = Field(ge=0)
    footprint_settle_seconds: float = Field(ge=0)
    order_seed: int
    latency: LatencySettings


class QuickDatasetOverrides(_Frozen):
    """Dataset values replaced by --quick. Unset values keep the file's value."""

    rows: PositiveInt | None = None
    users: PositiveInt | None = None


class QuickRunOverrides(_Frozen):
    """Run values replaced by --quick. Unset values keep the file's value."""

    repeats: PositiveInt | None = None
    duration_seconds: float | None = Field(default=None, gt=0)
    single_insert_ops: PositiveInt | None = None
    point_read_ops: PositiveInt | None = None
    user_read_ops: PositiveInt | None = None
    aggregate_runs: PositiveInt | None = None


class QuickSettings(_Frozen):
    """Everything --quick changes."""

    dataset: QuickDatasetOverrides = Field(default_factory=QuickDatasetOverrides)
    run: QuickRunOverrides = Field(default_factory=QuickRunOverrides)


class ChartFigureSettings(_Frozen):
    """Size and resolution of each chart, and the salt that keeps the SVG bytes the same."""

    width_in: float = Field(gt=0)
    dpi: PositiveInt
    margin_x: float = Field(ge=0, lt=1)
    svg_hashsalt: NonEmpty
    plot_height_in: float = Field(gt=0)
    dot_base_height_in: float = Field(gt=0)
    dot_row_height_in: float = Field(gt=0)


class ChartHeaderSettings(_Frozen):
    """Where the title, subtitle, legend and plot start, measured down from the top edge."""

    title_top_in: float = Field(ge=0)
    subtitle_top_in: float = Field(ge=0)
    legend_top_in: float = Field(ge=0)
    plot_top_in: float = Field(ge=0)
    plot_top_legend_in: float = Field(ge=0)
    legend_row_in: float = Field(gt=0)


class ChartFooterSettings(_Frozen):
    """The footer line: its height from the bottom edge, its text and the project link."""

    bottom_in: float = Field(ge=0)
    template: NonEmpty
    project_url: NonEmpty


class ChartAxesSettings(_Frozen):
    """Where the axes sit on the figure, and the ticks and headroom of the value axes."""

    plot_bottom_in: float = Field(ge=0)
    line_left_in: float = Field(ge=0)
    line_right_in: float = Field(ge=0)
    bar_right_in: float = Field(ge=0)
    dot_bottom_in: float = Field(ge=0)
    dot_left_in: float = Field(ge=0)
    dot_ratio_right_in: float = Field(ge=0)
    dot_plain_right_in: float = Field(ge=0)
    count_ticks: PositiveInt
    bar_ticks: PositiveInt
    dot_headroom: float = Field(ge=1)
    line_headroom: float = Field(ge=1)


class ChartMarkSettings(_Frozen):
    """Sizes of markers, lines, gaps and text wrapping, in pixels or characters."""

    marker_px: float = Field(gt=0)
    marker_ring_px: float = Field(ge=0)
    line_px: float = Field(gt=0)
    connector_px: float = Field(gt=0)
    spread_px: float = Field(gt=0)
    spread_cap_px: float = Field(gt=0)
    grid_px: float = Field(gt=0)
    legend_dot_px: float = Field(gt=0)
    legend_label_gap_px: float = Field(ge=0)
    legend_item_gap_px: float = Field(ge=0)
    value_gap_px: float = Field(ge=0)
    value_lane_gap_px: float = Field(ge=0)
    direct_offset_px: float = Field(ge=0)
    direct_gap_px: float = Field(ge=0)
    ratio_gap_px: float = Field(ge=0)
    bar_width: float = Field(gt=0, le=1)
    grid_alpha: float = Field(ge=0, le=1)
    axis_alpha: float = Field(ge=0, le=1)
    line_spacing: float = Field(gt=0)
    label_wrap_chars: PositiveInt
    direct_wrap_chars: PositiveInt


class ChartFontSettings(_Frozen):
    """The font family and the size in points of each kind of text, and the weights of two kinds."""

    family: NonEmpty
    title_pt: float = Field(gt=0)
    title_weight: NonEmpty
    subtitle_pt: float = Field(gt=0)
    legend_pt: float = Field(gt=0)
    footer_pt: float = Field(gt=0)
    tick_pt: float = Field(gt=0)
    axis_label_pt: float = Field(gt=0)
    row_label_pt: float = Field(gt=0)
    direct_label_pt: float = Field(gt=0)
    value_label_pt: float = Field(gt=0)
    bar_value_pt: float = Field(gt=0)
    ratio_pt: float = Field(gt=0)
    ratio_weight: NonEmpty


class ChartPalette(_Frozen):
    """The colors of one theme: surface, three text colors, muted, grid and axis."""

    surface: NonEmpty
    text: NonEmpty
    text_secondary: NonEmpty
    muted: NonEmpty
    grid: NonEmpty
    axis: NonEmpty


class ChartThemes(_Frozen):
    """The dark and the light palette. Each chart is drawn once in each."""

    dark: ChartPalette
    light: ChartPalette


class ChartSeriesStyle(_Frozen):
    """The color of one engine in each theme, and whether its markers are hollow."""

    dark: NonEmpty
    light: NonEmpty
    hollow: bool = False


class ChartTitleText(_Frozen):
    """The title and subtitle of one chart. Placeholders are filled from the data."""

    title: NonEmpty
    subtitle: NonEmpty


class ChartWording(_Frozen):
    """Chart text that is not specific to one chart: axis labels, unit words and row labels."""

    workers_label: NonEmpty
    runs_one: NonEmpty
    runs_many: NonEmpty
    ratio_faster: NonEmpty
    engine_ratio_names: dict[NonEmpty, NonEmpty]
    milliseconds_suffix: str
    thousand_suffix: NonEmpty
    million_suffix: NonEmpty
    row_labels: dict[NonEmpty, NonEmpty]
    titles: dict[NonEmpty, ChartTitleText]


class ChartsSettings(_Frozen):
    """Everything `dbbench charts` draws with: sizes, marks, fonts, colors and text."""

    figure: ChartFigureSettings
    header: ChartHeaderSettings
    footer: ChartFooterSettings
    axes: ChartAxesSettings
    marks: ChartMarkSettings
    fonts: ChartFontSettings
    themes: ChartThemes
    series: dict[NonEmpty, ChartSeriesStyle]
    series_labels: dict[NonEmpty, NonEmpty]
    wording: ChartWording


class PostgresInstallSettings(_Frozen):
    """The PostgreSQL source release: its version, source tarball, SHA-256 and configure flags."""

    version: NonEmpty
    url: NonEmpty
    sha256: HexDigest
    configure_flags: tuple[NonEmpty, ...]


class SqliteInstallSettings(_Frozen):
    """The SQLite amalgamation: its version, its zip file and its SHA3-256."""

    version: NonEmpty
    url: NonEmpty
    sha3_256: HexDigest


class Pysqlite3InstallSettings(_Frozen):
    """The pysqlite3 source release, and the setup.py lines removed before it is built."""

    version: NonEmpty
    url: NonEmpty
    sha256: HexDigest
    remove_setup_lines: tuple[NonEmpty, ...]


class InstallSettings(_Frozen):
    """Where dbbench install keeps its sources and builds, and the release it builds of each."""

    cache_dir: Path
    download_timeout_seconds: float = Field(gt=0)
    wheel_python: NonEmpty
    postgres: PostgresInstallSettings
    sqlite: SqliteInstallSettings
    pysqlite3: Pysqlite3InstallSettings
    build_env: dict[NonEmpty, NonEmpty]

    @field_validator("cache_dir", mode="before")
    @classmethod
    def _expand(cls, value: object) -> object:
        """Expands a leading ~ in the cache folder."""
        return _expand_home(value)

    def source_dir(self) -> Path:
        """Returns the folder the downloaded archives and the build trees live in."""
        return self.cache_dir / _SOURCE_SUBDIR

    def wheel_dir(self) -> Path:
        """Returns the folder the pysqlite3 wheel is built into."""
        return self.cache_dir / _WHEEL_SUBDIR

    def postgres_prefix(self) -> Path:
        """Returns the folder PostgreSQL is installed into, named by its version."""
        return self.cache_dir / f"postgresql-{self.postgres.version}"

    def postgres_bin_dir(self) -> Path:
        """Returns the bin folder of the installed PostgreSQL."""
        return self.postgres_prefix() / "bin"

    def pysqlite3_dir(self) -> Path:
        """Returns the folder the unpacked pysqlite3 wheel lives in, named by the SQLite version."""
        return self.cache_dir / f"pysqlite3-sqlite-{self.sqlite.version}"


class BenchConfig(_Frozen):
    """The whole config file, validated."""

    paths: PathSettings
    install: InstallSettings
    postgres: PostgresSettings
    sqlite: SqliteSettings
    dataset: DatasetSettings
    queries: QueriesSettings
    run: RunSettings
    settle: SettleSettings
    engines_by_scenario: dict[NonEmpty, tuple[NonEmpty, ...]]
    labels: dict[NonEmpty, NonEmpty]
    quick: QuickSettings = Field(default_factory=QuickSettings)
    charts: ChartsSettings

    def apply_quick(self) -> BenchConfig:
        """Returns a new config with the quick overrides applied. This object is not changed."""
        dataset = DatasetSettings.model_validate(
            {**self.dataset.model_dump(), **self.quick.dataset.model_dump(exclude_none=True)}
        )
        run = RunSettings.model_validate(
            {**self.run.model_dump(), **self.quick.run.model_dump(exclude_none=True)}
        )
        return self.model_copy(update={"dataset": dataset, "run": run})


class ConfigMerger:
    """Merges an overlay over a base: mappings merge key by key, and any other value is replaced."""

    def merge(self, base: object, overlay: object) -> object:
        """Returns the base with the overlay applied. Neither argument is changed."""
        if not isinstance(base, dict) or not isinstance(overlay, dict):
            return overlay
        merged: dict[object, object] = dict(base)
        for key, value in overlay.items():
            merged[key] = self.merge(merged[key], value) if key in merged else value
        return merged


class ConfigLoader:
    """Reads and validates the YAML config file, with the local overlay next to it when present."""

    def __init__(self, path: Path = DEFAULT_CONFIG) -> None:
        """Remembers the file to read. The overlay is the local.yaml in the same folder."""
        self._path = path
        self._overlay_path = path.with_name(LOCAL_OVERLAY_NAME)

    def load(self) -> BenchConfig:
        """Parses the files, applies the overlay and validates the result.

        Unknown keys and missing values are errors.
        """
        raw: object = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        if self._overlay_path.is_file():
            overlay: object = yaml.safe_load(self._overlay_path.read_text(encoding="utf-8"))
            raw = ConfigMerger().merge(raw, overlay if overlay is not None else {})
        return BenchConfig.model_validate(raw)
