"""The charts step: reads one results folder and writes its charts, charts.json and embed.md.

The SVG files go to <results>/charts/, one dark and one light file per chart. The PNG previews go
to a folder outside the repository, and only when one is asked for.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from dbbench.charts.data import ChartDataBuilder
from dbbench.charts.render import SvgRenderer
from dbbench.charts.theme import THEME_MODES
from dbbench.config import TOOL_ROOT
from dbbench.errors import DbBenchError
from dbbench.results import ResultReader

if TYPE_CHECKING:
    from pathlib import Path

    from dbbench.charts.data import ChartDraft
    from dbbench.charts.theme import ThemeMode
    from dbbench.config import ChartsSettings

CHARTS_DIR: Final = "charts"
CHARTS_JSON: Final = "charts.json"
EMBED_FILE: Final = "embed.md"
EMBED_ROOT: Final = "results"
EMBED_BLOCK: Final = (
    "<picture>\n"
    '  <source media="(prefers-color-scheme: dark)" srcset="{base}/{chart_id}-dark.svg">\n'
    '  <img alt="{alt}" src="{base}/{chart_id}-light.svg" width="100%">\n'
    "</picture>"
)
EMBED_SEPARATOR: Final = "\n\n"
PREVIEW_INSIDE_REPO: Final = "the PNG preview folder must be outside the repository: {path}"


@dataclass(frozen=True, slots=True)
class ChartRunResult:
    """What one charts run wrote, and the charts it skipped for lack of data."""

    charts_dir: Path
    written: tuple[str, ...]
    skipped: tuple[str, ...]


class ChartRunner:
    """Turns one results folder into its charts, the charts.json file and embed.md."""

    def __init__(
        self,
        settings: ChartsSettings,
        labels: dict[str, str],
        results_dir: Path,
        preview_dir: Path | None = None,
    ) -> None:
        """Remembers what to draw, from where, and where the optional PNG previews go."""
        self._settings = settings
        self._labels = labels
        self._results_dir = results_dir
        self._preview_dir = preview_dir

    def run(self) -> ChartRunResult:
        """Writes every chart that has data, in both themes, and returns what was written."""
        self._check_preview_location()
        reader = ResultReader(self._results_dir)
        builder = ChartDataBuilder(reader.summary(), reader.machine(), self._labels, self._settings)
        drafts, skipped = builder.drafts()
        chart_set = builder.chart_set(drafts)
        charts_dir = self._results_dir / CHARTS_DIR
        charts_dir.mkdir(parents=True, exist_ok=True)
        renderer = SvgRenderer(self._settings)
        for draft in drafts:
            for mode in THEME_MODES:
                chart_id = draft.model.id
                renderer.save(
                    draft,
                    chart_set,
                    mode,
                    svg=charts_dir / f"{chart_id}-{mode}.svg",
                    png=self._preview_path(chart_id, mode),
                )
        builder.write_json(chart_set, charts_dir / CHARTS_JSON)
        (charts_dir / EMBED_FILE).write_text(self._embed(drafts), encoding="utf-8")
        return ChartRunResult(
            charts_dir=charts_dir,
            written=tuple(draft.model.id for draft in drafts),
            skipped=tuple(skipped),
        )

    def _preview_path(self, chart_id: str, mode: ThemeMode) -> Path | None:
        """Returns the PNG path of one chart in one theme, or None when no preview is asked for."""
        if self._preview_dir is None:
            return None
        self._preview_dir.mkdir(parents=True, exist_ok=True)
        return self._preview_dir / f"{chart_id}-{mode}.png"

    def _check_preview_location(self) -> None:
        """Refuses a preview folder inside the repository, so no PNG lands in the project."""
        if self._preview_dir is None:
            return
        if self._preview_dir.resolve().is_relative_to(TOOL_ROOT.resolve()):
            message = PREVIEW_INSIDE_REPO.format(path=self._preview_dir)
            raise DbBenchError(message)

    def _embed(self, drafts: list[ChartDraft]) -> str:
        """Returns the Markdown with one picture block per chart, dark or light by theme."""
        run_name = self._results_dir.resolve().name
        base = f"{EMBED_ROOT}/{run_name}/{CHARTS_DIR}"
        blocks = []
        for draft in drafts:
            model = draft.model
            blocks.append(
                EMBED_BLOCK.format(
                    base=base,
                    chart_id=model.id,
                    alt=html.escape(f"{model.title}: {model.subtitle}", quote=True),
                )
            )
        return EMBED_SEPARATOR.join(blocks) + "\n"
