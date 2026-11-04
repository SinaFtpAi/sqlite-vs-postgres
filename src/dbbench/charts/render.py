"""Writes one chart in one theme as an SVG file, and optionally as a PNG preview.

The Agg backend draws without a display, so the charts build on a server or in CI. The SVG text
is drawn with outlines (svg.fonttype path) and has a fixed hash salt and no date, so the same data
gives the same bytes on every machine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import matplotlib as mpl

from dbbench.charts.format import ValueFormatter
from dbbench.charts.forms import FORMS
from dbbench.charts.theme import ChartTheme

if TYPE_CHECKING:
    from pathlib import Path

    from dbbench.charts.data import ChartDraft, ChartMachine, ChartSet
    from dbbench.charts.theme import ThemeMode
    from dbbench.config import ChartsSettings

AGG_BACKEND: Final = "Agg"
SVG_FORMAT: Final = "svg"
PNG_FORMAT: Final = "png"
SVG_TITLE_KEY: Final = "Title"
SVG_CREATOR_KEY: Final = "Creator"
SVG_DATE_KEY: Final = "Date"
SVG_CREATOR: Final = "dbbench"
SVG_FONT_TYPE: Final = "svg.fonttype"
SVG_OUTLINES: Final = "path"
SVG_HASH_SALT: Final = "svg.hashsalt"
FONT_FAMILY: Final = "font.family"


class SvgRenderer:
    """Draws charts with matplotlib, using the sizes, colors and text of the charts config."""

    def __init__(self, settings: ChartsSettings) -> None:
        """Selects the Agg backend and remembers the settings."""
        mpl.use(AGG_BACKEND)
        self._settings = settings
        self._formatter = ValueFormatter(settings.wording)

    def footer(self, machine: ChartMachine, repeats: int) -> str:
        """Returns the footer line of a chart set: the software, the CPU, the disk and the runs."""
        return self._settings.footer.template.format(
            sqlite=machine.sqlite,
            postgres=machine.postgres,
            cpu=machine.cpu,
            filesystem=machine.filesystem,
            runs=self._formatter.runs(repeats),
            url=self._settings.footer.project_url,
        )

    def save(
        self,
        draft: ChartDraft,
        chart_set: ChartSet,
        mode: ThemeMode,
        *,
        svg: Path,
        png: Path | None,
    ) -> None:
        """Draws one chart in one theme and writes the SVG, and the PNG when a path is given."""
        theme = ChartTheme(mode, self._settings)
        form = FORMS[draft.model.form](
            draft, theme, self.footer(chart_set.machine, chart_set.repeats)
        )
        with mpl.rc_context(
            {
                SVG_FONT_TYPE: SVG_OUTLINES,
                SVG_HASH_SALT: self._settings.figure.svg_hashsalt,
                FONT_FAMILY: self._settings.fonts.family,
            }
        ):
            figure = form.render()
            figure.savefig(
                svg,
                format=SVG_FORMAT,
                metadata={
                    SVG_TITLE_KEY: draft.model.title,
                    SVG_CREATOR_KEY: SVG_CREATOR,
                    SVG_DATE_KEY: None,
                },
            )
            if png is not None:
                figure.savefig(png, format=PNG_FORMAT, dpi=self._settings.figure.dpi)
