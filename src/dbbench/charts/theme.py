"""One chart theme (dark or light): its palette, series colors and pixel to point conversion.

Matplotlib sizes markers and lines in points, while the design is in pixels at the figure's dpi.
The conversion uses the dpi of the config, so a 10 px marker is the same size in SVG and PNG.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from dbbench.config import ChartPalette, ChartsSettings

ThemeMode = Literal["dark", "light"]
THEME_MODES: Final[tuple[ThemeMode, ...]] = ("dark", "light")
POINTS_PER_INCH: Final = 72


class ChartTheme:
    """The palette and series styles of one theme, read from the charts block of the config."""

    def __init__(self, mode: ThemeMode, settings: ChartsSettings) -> None:
        """Picks the palette of the mode. Each theme is drawn into its own files."""
        self._mode = mode
        self._settings = settings
        self._palette = settings.themes.dark if mode == "dark" else settings.themes.light

    @property
    def mode(self) -> ThemeMode:
        """Returns the mode, dark or light."""
        return self._mode

    @property
    def settings(self) -> ChartsSettings:
        """Returns the charts settings the theme was made from."""
        return self._settings

    @property
    def palette(self) -> ChartPalette:
        """Returns the colors of this theme."""
        return self._palette

    def points(self, pixels: float) -> float:
        """Converts pixels at the figure's dpi to points."""
        return pixels * POINTS_PER_INCH / self._settings.figure.dpi

    def pixels(self, points: float) -> float:
        """Converts points to pixels at the figure's dpi."""
        return points * self._settings.figure.dpi / POINTS_PER_INCH
