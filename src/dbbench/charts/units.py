"""The unit names of the chart models, as they appear in charts.json."""

from __future__ import annotations

from typing import Final, Literal

COUNT: Final = "ops_per_second"
MILLISECONDS: Final = "ms"
SHARE: Final = "share"

ChartUnit = Literal["ops_per_second", "ms", "share"]
ChartForm = Literal["dot", "line", "bar", "percentile"]
ChartScale = Literal["log", "linear"]
