"""Number text for chart labels, axis ticks, ratio notes and the run count.

Labels are short on purpose: 199,839 operations per second reads as 200k, and 5,008 ms reads as
5,008 ms. The unit words and suffixes live in the wording block of the config; this module only
picks the rounding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from dbbench.charts.units import MILLISECONDS, SHARE

if TYPE_CHECKING:
    from dbbench.config import ChartWording

THOUSAND: Final = 1_000
MILLION: Final = 1_000_000
ONE_DECIMAL_BELOW: Final = 10
MS_THREE_DECIMALS_BELOW: Final = 1.0
MS_ONE_DECIMAL_BELOW: Final = 100.0
MS_SMALL_DECIMALS: Final = 3
MS_MID_DECIMALS: Final = 1
PERCENT_SCALE: Final = 100
RATIO_DECIMALS: Final = 1


class ValueFormatter:
    """Turns numbers into the short text the charts show, using the wording of the config."""

    def __init__(self, wording: ChartWording) -> None:
        """Remembers the unit words and suffixes."""
        self._wording = wording

    def label(self, unit: str, value: float) -> str:
        """Returns the text of one value label: a count, a ms value with its unit, or a share."""
        if unit == MILLISECONDS:
            return f"{self.milliseconds(value)}{self._wording.milliseconds_suffix}"
        if unit == SHARE:
            return self.percent(value)
        return self.count(value)

    def tick(self, unit: str, value: float) -> str:
        """Returns the text of one axis tick. The unit is in the subtitle, not on the ticks."""
        if unit == MILLISECONDS:
            return self.milliseconds_tick(value)
        if unit == SHARE:
            return self.percent(value)
        return self.count(value)

    def count(self, value: float) -> str:
        """Returns a count in thousands or millions: 1, 10, 1k, 200k, 1M."""
        if abs(value) >= MILLION:
            return self._scaled(value / MILLION, self._wording.million_suffix)
        if abs(value) >= THOUSAND:
            return self._scaled(value / THOUSAND, self._wording.thousand_suffix)
        return f"{value:,.0f}"

    def milliseconds(self, value: float) -> str:
        """Returns a ms value without its unit: 0.003, 16.7, 5,008."""
        if value < MS_THREE_DECIMALS_BELOW:
            return f"{value:.{MS_SMALL_DECIMALS}f}"
        if value < MS_ONE_DECIMAL_BELOW:
            return f"{value:.{MS_MID_DECIMALS}f}"
        return f"{round(value):,}"

    def milliseconds_tick(self, value: float) -> str:
        """Returns a ms axis tick: 0.01, 0.1, 1, 10, 1,000, with no trailing zeros below one."""
        if value < MS_THREE_DECIMALS_BELOW:
            return f"{value:.{MS_SMALL_DECIMALS}f}".rstrip("0").rstrip(".")
        return f"{value:,.0f}"

    def percent(self, value: float) -> str:
        """Returns a share as a whole percentage: 0.7097 reads as 71%."""
        return f"{round(value * PERCENT_SCALE)}%"

    def ratio(self, ratio: float) -> str:
        """Returns a ratio with one decimal, for the ratio note."""
        return f"{ratio:.{RATIO_DECIMALS}f}"

    def runs(self, count: int) -> str:
        """Returns the number of runs, in the singular for one run."""
        if count == 1:
            return self._wording.runs_one.format(count=count)
        return self._wording.runs_many.format(count=count)

    def _scaled(self, scaled: float, suffix: str) -> str:
        """Rounds a scaled count: one decimal below ten, whole numbers from ten up."""
        if abs(scaled) < ONE_DECIMAL_BELOW:
            return f"{scaled:.1f}".removesuffix(".0") + suffix
        return f"{round(scaled):,}" + suffix
