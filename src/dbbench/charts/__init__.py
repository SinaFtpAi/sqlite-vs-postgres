"""Static SVG charts made from a results folder, with the same numbers in charts.json.

The package reads summary.json and machine.json, builds the chart models (data.py), draws them
with matplotlib (forms.py through render.py) and writes the files (runner.py). Every size, color,
font and piece of chart text comes from the charts block of config/dbbench.yaml.
"""

from dbbench.charts.runner import ChartRunner, ChartRunResult

__all__ = ["ChartRunResult", "ChartRunner"]
