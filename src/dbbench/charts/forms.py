"""The chart forms: one class per form, each drawing its figure with matplotlib.

ChartBase holds the anatomy every chart shares: the figure, the title and subtitle, the legend row,
the footer and the rectangle of the plot. The forms add their own plot. Sizes are in inches on the
figure, and marks are in pixels that the theme converts to points. Text labels are placed in figure
pixels, so the stacking and the de-overlapping work on the real positions of the data.
"""

from __future__ import annotations

import textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass
from math import ceil, floor, log10
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, LogLocator, MaxNLocator, NullLocator

from dbbench.charts.format import ValueFormatter

if TYPE_CHECKING:
    from collections.abc import Callable

    from matplotlib.axes import Axes
    from matplotlib.backends.backend_agg import RendererAgg
    from matplotlib.text import Text

    from dbbench.charts.data import ChartDraft, ChartModel, ChartSeries
    from dbbench.charts.theme import ChartTheme
    from dbbench.charts.units import ChartScale

MIN_LEGEND_SERIES: Final = 2
MIN_CONNECTED_POINTS: Final = 2
CATEGORY_PAD: Final = 0.3
BAR_PAD: Final = 0.5
SHARE_TOP: Final = 1.0
DOT_ROW_PAD: Final = 0.5
LOG_BASE: Final = 10.0
X_AXIS: Final = "x"
Y_AXIS: Final = "y"
NORMAL: Final = "normal"
CENTER: Final = "center"
LEFT: Final = "left"
BOTTOM: Final = "bottom"
TOP: Final = "top"
BASELINE: Final = "baseline"


class _Measure(Protocol):
    """The text measuring method of a matplotlib renderer, which matplotlib does not annotate."""

    def __call__(
        self, text: str, prop: FontProperties, *, ismath: bool
    ) -> tuple[float, float, float]:
        """Returns the width, height and descent of the text in pixels."""


@dataclass(frozen=True, slots=True)
class TextStyle:
    """How one piece of text is set: its size in points, color, weight and alignment."""

    size: float
    color: str
    weight: str = NORMAL
    ha: str = LEFT
    va: str = BASELINE


class TextMeter:
    """Measures one line of text in pixels at the figure's dpi."""

    def __init__(self, renderer: RendererAgg, family: str) -> None:
        """Remembers the renderer of the figure and the font family."""
        self._renderer = renderer
        self._family = family

    def width(self, text: str, style: TextStyle) -> float:
        """Returns the width of one line of text in pixels."""
        prop = FontProperties(family=self._family, size=style.size, weight=style.weight)
        measure = cast("_Measure", self._renderer.get_text_width_height_descent)
        width, _height, _descent = measure(text, prop, ismath=False)
        return float(width)


class ChartBase(ABC):
    """The anatomy every chart shares. Each form draws its own plot in _draw_plot."""

    def __init__(self, draft: ChartDraft, theme: ChartTheme, footer: str) -> None:
        """Creates the figure and its canvas, sized for this chart and this theme."""
        self._model: ChartModel = draft.model
        self._notes: dict[str, str] = draft.row_notes
        self._theme = theme
        self._settings = theme.settings
        self._palette = theme.palette
        self._footer = footer
        self._formatter = ValueFormatter(self._settings.wording)
        self._family = self._settings.fonts.family
        self._width_in = self._settings.figure.width_in
        self._dpi = self._settings.figure.dpi
        self._height_in = self.figure_height_in()
        self._fig = Figure(
            figsize=(self._width_in, self._height_in),
            dpi=self._dpi,
            facecolor=self._palette.surface,
        )
        self._canvas = FigureCanvasAgg(self._fig)
        get_renderer = cast("Callable[[], RendererAgg]", self._canvas.get_renderer)
        self._meter = TextMeter(get_renderer(), self._family)
        self._plot_top_in = 0.0

    @abstractmethod
    def figure_height_in(self) -> float:
        """Returns the height of the figure in inches."""

    @abstractmethod
    def _plot_right_in(self) -> float:
        """Returns the room at the right of the plot, in inches from the right edge."""

    @abstractmethod
    def _draw_plot(self) -> None:
        """Draws the plot itself, inside the rectangle that the header leaves."""

    def render(self) -> Figure:
        """Draws the whole chart and returns its figure."""
        legend_rows = self._draw_header()
        self._plot_top_in = self._plot_top(legend_rows)
        self._draw_plot()
        self._draw_footer()
        return self._fig

    @property
    def _width_px(self) -> float:
        """Returns the width of the figure in pixels."""
        return self._width_in * self._dpi

    @property
    def _height_px(self) -> float:
        """Returns the height of the figure in pixels."""
        return self._height_in * self._dpi

    def _draw_header(self) -> int:
        """Draws the title, the subtitle and, with two or more series, the legend.

        Returns the number of legend rows, or 0 when there is no legend.
        """
        header = self._settings.header
        fonts = self._settings.fonts
        margin = self._settings.figure.margin_x
        self._text(
            margin,
            self._from_top(header.title_top_in),
            self._model.title,
            TextStyle(fonts.title_pt, self._palette.text, fonts.title_weight, va=TOP),
        )
        self._text(
            margin,
            self._from_top(header.subtitle_top_in),
            self._model.subtitle,
            TextStyle(fonts.subtitle_pt, self._palette.text_secondary, va=TOP),
        )
        if len(self._model.series) < MIN_LEGEND_SERIES:
            return 0
        return self._draw_legend()

    def _plot_top(self, legend_rows: int) -> float:
        """Returns where the plot starts, from the top edge, with room for every legend row."""
        header = self._settings.header
        if legend_rows == 0:
            return header.plot_top_in
        return header.plot_top_legend_in + (legend_rows - 1) * header.legend_row_in

    def _draw_legend(self) -> int:
        """Draws one dot per series and its label. A row that is full moves to the next line.

        Returns the number of rows used.
        """
        marks = self._settings.marks
        header = self._settings.header
        margin_px = self._settings.figure.margin_x * self._width_px
        limit_px = self._width_px - margin_px
        style = TextStyle(self._settings.fonts.legend_pt, self._palette.text_secondary, va=CENTER)
        x_px = margin_px
        y_px = self._height_px - header.legend_top_in * self._dpi
        rows = 1
        for series in self._model.series:
            label_width = self._meter.width(series.label, style)
            item_width = marks.legend_dot_px + marks.legend_label_gap_px + label_width
            if x_px > margin_px and x_px + item_width > limit_px:
                x_px = margin_px
                y_px -= header.legend_row_in * self._dpi
                rows += 1
            self._add_dot(x_px + marks.legend_dot_px / 2, y_px, series)
            self._text_px(
                x_px + marks.legend_dot_px + marks.legend_label_gap_px,
                y_px,
                series.label,
                style,
            )
            x_px += item_width + marks.legend_item_gap_px
        return rows

    def _add_dot(self, x_px: float, y_px: float, series: ChartSeries) -> None:
        """Adds one legend dot at a point in figure pixels, in the series' marker style."""
        face, edge = self._marker_colors(series)
        marks = self._settings.marks
        marker = Line2D(
            [x_px / self._width_px],
            [y_px / self._height_px],
            transform=self._fig.transFigure,
            marker="o",
            linestyle="none",
            markersize=self._theme.points(marks.legend_dot_px),
            markerfacecolor=face,
            markeredgecolor=edge,
            markeredgewidth=self._theme.points(marks.marker_ring_px),
        )
        self._fig.add_artist(marker)

    def _draw_footer(self) -> None:
        """Draws the footer line at the bottom left."""
        self._text(
            self._settings.figure.margin_x,
            self._settings.footer.bottom_in / self._height_in,
            self._footer,
            TextStyle(self._settings.fonts.footer_pt, self._palette.muted, va=BOTTOM),
        )

    def _from_top(self, inches: float) -> float:
        """Converts a distance from the top edge in inches to a figure fraction from the bottom."""
        return 1.0 - inches / self._height_in

    def _text(self, x: float, y: float, text: str, style: TextStyle) -> Text:
        """Places text at a figure fraction, with its style."""
        return self._fig.text(
            x,
            y,
            text,
            fontsize=style.size,
            color=style.color,
            fontweight=style.weight,
            fontfamily=self._family,
            ha=style.ha,
            va=style.va,
            multialignment=style.ha,
        )

    def _text_px(self, x_px: float, y_px: float, text: str, style: TextStyle) -> Text:
        """Places text at a point in figure pixels, measured from the bottom left corner."""
        return self._text(x_px / self._width_px, y_px / self._height_px, text, style)

    def _display(self, ax: Axes, x: float, y: float) -> tuple[float, float]:
        """Converts a data point of an axes to figure pixels."""
        x_px, y_px = ax.transData.transform((x, y))
        return float(x_px), float(y_px)

    def _add_axes(self, left_in: float, right_in: float, bottom_in: float, top_in: float) -> Axes:
        """Adds the plot axes at the given distances, in inches.

        Left and right are measured from the figure's edges, bottom from the bottom edge, and top
        from the top edge.
        """
        width, height = self._width_in, self._height_in
        rect = (
            left_in / width,
            bottom_in / height,
            (width - left_in - right_in) / width,
            (height - top_in - bottom_in) / height,
        )
        return self._fig.add_axes(rect, facecolor=self._palette.surface)

    def _style_axes(self, ax: Axes, value_axis: Literal["x", "y"]) -> None:
        """Keeps only the baseline and the grid of the value axis, in the palette's colors."""
        marks = self._settings.marks
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(to_rgba(self._palette.axis, marks.axis_alpha))
        ax.spines["bottom"].set_linewidth(self._theme.points(marks.grid_px))
        ax.tick_params(
            length=0,
            labelsize=self._settings.fonts.tick_pt,
            labelcolor=self._palette.muted,
        )
        ax.grid(visible=False)
        ax.grid(
            visible=True,
            axis=value_axis,
            color=to_rgba(self._palette.grid, marks.grid_alpha),
            linewidth=self._theme.points(marks.grid_px),
        )
        ax.set_axisbelow(True)

    def _value_scale(
        self,
        ax: Axes,
        axis: Literal["x", "y"],
        scale: ChartScale,
        limits: tuple[float, float],
        ticks: int,
    ) -> None:
        """Sets the scale, the limits and the tick labels of one value axis."""
        target = ax.xaxis if axis == X_AXIS else ax.yaxis
        if axis == X_AXIS:
            if scale == "log":
                ax.set_xscale("log")
            ax.set_xlim(*limits)
        else:
            if scale == "log":
                ax.set_yscale("log")
            ax.set_ylim(*limits)
        locator = LogLocator(base=LOG_BASE) if scale == "log" else MaxNLocator(nbins=ticks)
        target.set_major_locator(locator)
        target.set_minor_locator(NullLocator())
        unit = self._model.unit
        target.set_major_formatter(
            FuncFormatter(lambda value, _position: self._formatter.tick(unit, value))
        )

    def _marker_colors(self, series: ChartSeries) -> tuple[str, str]:
        """Returns the face and the edge of a marker. A hollow marker has the surface inside."""
        color = self._series_color(series)
        surface = self._palette.surface
        if series.hollow:
            return surface, color
        return color, surface

    def _series_color(self, series: ChartSeries) -> str:
        """Returns the series color of the theme in use."""
        return series.color_dark if self._theme.mode == "dark" else series.color_light

    def _plot_markers(
        self,
        ax: Axes,
        xs: list[float],
        ys: list[float],
        series: ChartSeries,
        zorder: float,
    ) -> None:
        """Draws the markers of one series, in the design's pixel sizes."""
        face, edge = self._marker_colors(series)
        marks = self._settings.marks
        ax.plot(
            xs,
            ys,
            marker="o",
            linestyle="none",
            markersize=self._theme.points(marks.marker_px),
            markerfacecolor=face,
            markeredgecolor=edge,
            markeredgewidth=self._theme.points(marks.marker_ring_px),
            zorder=zorder,
        )

    def _stack_value_labels(
        self, entries: list[tuple[float, float, str]], style: TextStyle
    ) -> None:
        """Writes value labels above their dots. A label that would overlap a neighbour moves up.

        Each entry is (x, y, text) in figure pixels: x at the centre of the dot, y at its top
        edge. Lanes are assigned from left to right, so two close dots both keep a label.
        """
        marks = self._settings.marks
        lane_height = self._theme.pixels(style.size) * marks.line_spacing + marks.value_lane_gap_px
        lane_edges: list[float] = []
        for x_px, y_px, text in sorted(entries):
            width = self._meter.width(text, style)
            left = x_px - width / 2
            lane = next(
                (
                    index
                    for index, edge in enumerate(lane_edges)
                    if edge + marks.value_lane_gap_px <= left
                ),
                None,
            )
            if lane is None:
                lane_edges.append(x_px + width / 2)
                lane = len(lane_edges) - 1
            else:
                lane_edges[lane] = x_px + width / 2
            self._text_px(x_px, y_px + lane * lane_height, text, style)

    def _direct_labels(self, entries: list[tuple[float, float, str, str]], floor_px: float) -> None:
        """Writes the name and value of each line at its right end.

        Each entry is (x, y, name, value) in figure pixels, with (x, y) at the last point. The name
        sits above the point and the value below it. Labels that would overlap are pushed apart: the
        lower one moves down, and when the lowest would reach floor_px, the stack moves up from the
        bottom instead. floor_px is the lowest edge the labels may reach, the top of the footer.
        """
        marks = self._settings.marks
        fonts = self._settings.fonts
        name_style = TextStyle(fonts.direct_label_pt, self._palette.text_secondary, va=BOTTOM)
        value_style = TextStyle(fonts.value_label_pt, self._palette.text_secondary, va=TOP)
        ordered = sorted(entries, key=lambda entry: -entry[1])
        names = [
            textwrap.fill(name, width=marks.direct_wrap_chars, break_long_words=False)
            for _x, _y, name, _value in ordered
        ]
        name_line_px = self._theme.pixels(fonts.direct_label_pt) * marks.line_spacing
        value_line_px = self._theme.pixels(fonts.value_label_pt) * marks.line_spacing
        needed = [0.0] + [
            (names[index].count("\n") + 1) * name_line_px + value_line_px + marks.direct_gap_px
            for index in range(1, len(ordered))
        ]
        positions = [y for _x, y, _name, _value in ordered]
        for index in range(1, len(ordered)):
            if positions[index - 1] - positions[index] < needed[index]:
                positions[index] = positions[index - 1] - needed[index]
        floor = floor_px + value_line_px
        if positions and positions[-1] < floor:
            positions[-1] = floor
            for index in range(len(ordered) - 2, -1, -1):
                if positions[index] - positions[index + 1] < needed[index + 1]:
                    positions[index] = positions[index + 1] + needed[index + 1]
        for (x_px, _y, _name, value), name, y_px in zip(ordered, names, positions, strict=True):
            x_text = x_px + marks.direct_offset_px
            self._text_px(x_text, y_px, name, name_style)
            self._text_px(x_text, y_px, value, value_style)


class DotPlotChart(ChartBase):
    """Rows of dots on one value axis: one row per category, one dot per engine."""

    def figure_height_in(self) -> float:
        """Returns the base height plus a fixed height for each row."""
        figure = self._settings.figure
        return figure.dot_base_height_in + figure.dot_row_height_in * len(self._model.categories)

    def _plot_right_in(self) -> float:
        """Returns the room for the ratio note, or the small margin when there is none."""
        axes = self._settings.axes
        return axes.dot_ratio_right_in if self._notes else axes.dot_plain_right_in

    def _draw_plot(self) -> None:
        """Draws the connectors, the dots, the value labels, the row labels and the ratio notes."""
        axes = self._settings.axes
        marks = self._settings.marks
        labels = self._model.categories
        rows = len(labels)
        row_y = {label: float(rows - 1 - index) for index, label in enumerate(labels)}
        ax = self._add_axes(
            axes.dot_left_in, self._plot_right_in(), axes.dot_bottom_in, self._plot_top_in
        )
        points = [point for series in self._model.series for point in series.points]
        lows = [point.min for point in points]
        highs = [point.max for point in points]
        if self._model.scale == "log":
            limits = _decade_limits(lows + highs)
        else:
            limits = (0.0, max(highs) * axes.dot_headroom)
        self._style_axes(ax, X_AXIS)
        self._value_scale(ax, X_AXIS, self._model.scale, limits, axes.count_ticks)
        ax.set_ylim(-DOT_ROW_PAD, rows - DOT_ROW_PAD)
        ax.set_yticks([])
        grid = to_rgba(self._palette.grid, marks.grid_alpha)
        for label in labels:
            row = [point.value for point in points if point.category == label]
            if len(row) >= MIN_CONNECTED_POINTS:
                ax.plot(
                    [min(row), max(row)],
                    [row_y[label], row_y[label]],
                    color=grid,
                    linewidth=self._theme.points(marks.connector_px),
                    solid_capstyle="butt",
                    zorder=1,
                )
        for series in self._model.series:
            for point in series.points:
                self._plot_markers(ax, [point.value], [row_y[point.category]], series, zorder=3)
        self._draw_dot_values(ax, row_y)
        self._draw_row_labels(ax, row_y)
        self._draw_ratio_notes(ax, row_y)

    def _draw_dot_values(self, ax: Axes, row_y: dict[str, float]) -> None:
        """Writes the value above each dot, stacking the labels of one row that would overlap."""
        marks = self._settings.marks
        style = TextStyle(
            self._settings.fonts.value_label_pt,
            self._palette.text_secondary,
            ha=CENTER,
            va=BOTTOM,
        )
        gap_px = marks.marker_px / 2 + marks.value_gap_px
        for label, y in row_y.items():
            entries: list[tuple[float, float, str]] = []
            for series in self._model.series:
                for point in series.points:
                    if point.category != label:
                        continue
                    x_px, y_px = self._display(ax, point.value, y)
                    text = self._formatter.label(self._model.unit, point.value)
                    entries.append((x_px, y_px + gap_px, text))
            self._stack_value_labels(entries, style)

    def _draw_row_labels(self, ax: Axes, row_y: dict[str, float]) -> None:
        """Writes each row's label, wrapped, in the margin left of the plot."""
        marks = self._settings.marks
        style = TextStyle(
            self._settings.fonts.row_label_pt, self._palette.text_secondary, va=CENTER
        )
        left = ax.get_xlim()[0]
        for label, y in row_y.items():
            _x, y_px = self._display(ax, left, y)
            wrapped = textwrap.fill(label, width=marks.label_wrap_chars, break_long_words=False)
            self._text(self._settings.figure.margin_x, y_px / self._height_px, wrapped, style)

    def _draw_ratio_notes(self, ax: Axes, row_y: dict[str, float]) -> None:
        """Writes the ratio note at the right end of each row that has one."""
        marks = self._settings.marks
        fonts = self._settings.fonts
        style = TextStyle(fonts.ratio_pt, self._palette.text, fonts.ratio_weight, va=CENTER)
        right = ax.get_xlim()[1]
        right_px, _ = self._display(ax, right, 0.0)
        for label, note in self._notes.items():
            _x, y_px = self._display(ax, right, row_y[label])
            self._text_px(right_px + marks.ratio_gap_px, y_px, note, style)


class LineChart(ChartBase):
    """Lines over categories, with the min and max of the repeats as bars and direct labels."""

    show_spread: bool = True
    show_workers_label: bool = True

    def figure_height_in(self) -> float:
        """Returns the plot height of the figure."""
        return self._settings.figure.plot_height_in

    def _plot_right_in(self) -> float:
        """Returns the room at the right end, where the direct labels go."""
        return self._settings.axes.line_right_in

    def _draw_plot(self) -> None:
        """Draws the lines, the markers, the spread bars, the axes and the direct labels."""
        axes = self._settings.axes
        marks = self._settings.marks
        fonts = self._settings.fonts
        categories = self._model.categories
        ax = self._add_axes(
            axes.line_left_in, self._plot_right_in(), axes.plot_bottom_in, self._plot_top_in
        )
        points = [point for series in self._model.series for point in series.points]
        lows = [point.min for point in points]
        highs = [point.max for point in points]
        self._style_axes(ax, Y_AXIS)
        if self._model.scale == "log":
            limits = _decade_limits(lows + highs)
        else:
            limits = (0.0, max(highs) * axes.line_headroom)
        self._value_scale(ax, Y_AXIS, self._model.scale, limits, axes.count_ticks)
        ax.set_xlim(-CATEGORY_PAD, len(categories) - 1 + CATEGORY_PAD)
        ax.set_xticks(list(range(len(categories))))
        ax.set_xticklabels(categories)
        if self.show_workers_label:
            ax.set_xlabel(
                self._settings.wording.workers_label,
                fontsize=fonts.axis_label_pt,
                color=self._palette.muted,
            )
        ends: list[tuple[float, float, str, str]] = []
        for series in self._model.series:
            if not series.points:
                continue
            color = self._series_color(series)
            xs = [float(categories.index(point.category)) for point in series.points]
            ys = [point.value for point in series.points]
            ax.plot(xs, ys, color=color, linewidth=self._theme.points(marks.line_px), zorder=2)
            if self.show_spread:
                self._draw_spread(ax, xs, series, color)
            self._plot_markers(ax, xs, ys, series, zorder=4)
            x_px, y_px = self._display(ax, xs[-1], ys[-1])
            value = self._formatter.label(self._model.unit, ys[-1])
            ends.append((x_px, y_px, series.label, value))
        self._direct_labels(ends, self._footer_top_px())

    def _footer_top_px(self) -> float:
        """Returns the height of the top of the footer line, in figure pixels.

        Direct labels may hang below the plot, to the right of the tick labels, but not into the
        footer.
        """
        footer = self._settings.footer
        line = (
            self._theme.pixels(self._settings.fonts.footer_pt) * self._settings.marks.line_spacing
        )
        return footer.bottom_in * self._dpi + line

    def _draw_spread(self, ax: Axes, xs: list[float], series: ChartSeries, color: str) -> None:
        """Draws the min to max bar of each point, with caps at both ends."""
        marks = self._settings.marks
        lower = [point.value - point.min for point in series.points]
        upper = [point.max - point.value for point in series.points]
        ax.errorbar(
            xs,
            [point.value for point in series.points],
            yerr=[lower, upper],
            fmt="none",
            ecolor=color,
            elinewidth=self._theme.points(marks.spread_px),
            capsize=self._theme.points(marks.spread_cap_px) / 2,
            capthick=self._theme.points(marks.spread_px),
            zorder=3,
        )


class PercentileChart(LineChart):
    """Latency percentiles: one line per engine from p50 to max, on a log scale in ms."""

    show_spread = False
    show_workers_label = False


class BarChart(ChartBase):
    """Vertical bars, one group per worker level, with the value above each bar."""

    def figure_height_in(self) -> float:
        """Returns the plot height of the figure."""
        return self._settings.figure.plot_height_in

    def _plot_right_in(self) -> float:
        """Returns the margin at the right end, where there are no labels."""
        return self._settings.axes.bar_right_in

    def _draw_plot(self) -> None:
        """Draws the bars, their value labels, the axes and the workers label."""
        axes = self._settings.axes
        marks = self._settings.marks
        fonts = self._settings.fonts
        categories = self._model.categories
        ax = self._add_axes(
            axes.line_left_in, self._plot_right_in(), axes.plot_bottom_in, self._plot_top_in
        )
        self._style_axes(ax, Y_AXIS)
        self._value_scale(ax, Y_AXIS, "linear", (0.0, SHARE_TOP), axes.bar_ticks)
        ax.set_xlim(-BAR_PAD, len(categories) - 1 + BAR_PAD)
        ax.set_xticks(list(range(len(categories))))
        ax.set_xticklabels(categories)
        ax.set_xlabel(
            self._settings.wording.workers_label,
            fontsize=fonts.axis_label_pt,
            color=self._palette.muted,
        )
        style = TextStyle(fonts.bar_value_pt, self._palette.text, ha=CENTER, va=BOTTOM)
        series_count = len(self._model.series)
        width = marks.bar_width / series_count
        for index, series in enumerate(self._model.series):
            shift = (index - (series_count - 1) / 2) * width
            xs = [categories.index(point.category) + shift for point in series.points]
            ys = [point.value for point in series.points]
            ax.bar(xs, ys, width=width, color=self._series_color(series), linewidth=0, zorder=2)
            entries: list[tuple[float, float, str]] = []
            for x, y in zip(xs, ys, strict=True):
                x_px, y_px = self._display(ax, x, y)
                text = self._formatter.label(self._model.unit, y)
                entries.append((x_px, y_px + marks.value_gap_px, text))
            for x_px, y_px, text in entries:
                self._text_px(x_px, y_px, text, style)


def _decade_limits(values: list[float]) -> tuple[float, float]:
    """Returns the powers of ten just below the smallest and just above the largest value."""
    positive = [value for value in values if value > 0]
    lower = floor(log10(min(positive)))
    upper = ceil(log10(max(positive)))
    if upper <= lower:
        upper = lower + 1
    return LOG_BASE**lower, LOG_BASE**upper


FORMS: Final[dict[str, type[ChartBase]]] = {
    "dot": DotPlotChart,
    "line": LineChart,
    "bar": BarChart,
    "percentile": PercentileChart,
}
