"""Result files: raw.jsonl, machine.json, summary.json and summary.md.

raw.jsonl holds one line per scenario, engine, worker count and repeat, with the settle record, the
engine's position in the run order and the diagnostics of that run. summary.json holds the median,
min and max of each metric over the repeats. summary.md is written by dbbench.report.
"""

from __future__ import annotations

import json
import statistics
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from dbbench.machine import MachineRecord
from dbbench.settle import SettleRecord

if TYPE_CHECKING:
    from pathlib import Path

MetricValue = int | float | str | bool | None

RAW_FILE: Final = "raw.jsonl"
MACHINE_FILE: Final = "machine.json"
SUMMARY_JSON_FILE: Final = "summary.json"
SUMMARY_MD_FILE: Final = "summary.md"
_JSON_INDENT: Final = 2


class RawRecord(BaseModel):
    """One measured line of raw.jsonl.

    order_position is the engine's place in the shuffled order of its scenario, repeat and worker
    level (0 based). settle and diagnostics are empty for records that are not measured runs, such
    as the footprint start phase.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario: str
    phase: str = ""
    engine: str
    workers: int
    repeat: int
    metrics: dict[str, MetricValue]
    order_position: int | None = None
    settle: SettleRecord | None = None
    diagnostics: dict[str, MetricValue] = Field(default_factory=dict)


class SummaryGroup(BaseModel):
    """The median, min and max of every repeat of one scenario, phase, engine and worker count."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario: str
    phase: str
    engine: str
    workers: int
    repeats: int
    metrics: dict[str, MetricValue]
    metrics_min: dict[str, MetricValue]
    metrics_max: dict[str, MetricValue]


class SummaryFile(BaseModel):
    """The contents of summary.json: the groups, in the order they were measured."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    groups: list[SummaryGroup]


class ResultWriter:
    """Writes one results folder."""

    def __init__(self, directory: Path) -> None:
        """Creates the folder if needed. Nothing is written until a method is called."""
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        """Returns the results folder."""
        return self._directory

    def start_raw(self) -> None:
        """Empties raw.jsonl so a new run starts from nothing."""
        (self._directory / RAW_FILE).write_text("", encoding="utf-8")

    def append(self, record: RawRecord) -> None:
        """Appends one record as a JSON line."""
        with (self._directory / RAW_FILE).open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")

    def write_machine(self, machine: MachineRecord) -> None:
        """Writes machine.json."""
        text = json.dumps(machine.model_dump(mode="json"), indent=_JSON_INDENT)
        (self._directory / MACHINE_FILE).write_text(text + "\n", encoding="utf-8")

    def write_summary(self, groups: list[SummaryGroup]) -> None:
        """Writes summary.json, with the groups in the order they were measured."""
        payload = {"groups": [group.model_dump(mode="json") for group in groups]}
        text = json.dumps(payload, indent=_JSON_INDENT)
        (self._directory / SUMMARY_JSON_FILE).write_text(text + "\n", encoding="utf-8")

    def write_markdown(self, text: str) -> None:
        """Writes summary.md."""
        (self._directory / SUMMARY_MD_FILE).write_text(text, encoding="utf-8")


class ResultReader:
    """Reads one results folder written by a run."""

    def __init__(self, directory: Path) -> None:
        """Remembers the folder to read."""
        self._directory = directory

    def records(self) -> list[RawRecord]:
        """Returns every line of raw.jsonl, in order."""
        text = (self._directory / RAW_FILE).read_text(encoding="utf-8")
        return [RawRecord.model_validate_json(line) for line in text.splitlines() if line]

    def machine(self) -> MachineRecord:
        """Returns the contents of machine.json."""
        text = (self._directory / MACHINE_FILE).read_text(encoding="utf-8")
        return MachineRecord.model_validate_json(text)

    def summary(self) -> list[SummaryGroup]:
        """Returns the groups of summary.json."""
        text = (self._directory / SUMMARY_JSON_FILE).read_text(encoding="utf-8")
        return SummaryFile.model_validate_json(text).groups


class Summarizer:
    """Groups the raw records and takes the median, min and max of each metric over the repeats."""

    def summarize(self, records: list[RawRecord]) -> list[SummaryGroup]:
        """Returns one group per scenario, phase, engine and worker count, in first-seen order."""
        grouped: dict[tuple[str, str, str, int], list[RawRecord]] = {}
        for record in records:
            key = (record.scenario, record.phase, record.engine, record.workers)
            grouped.setdefault(key, []).append(record)
        return [self._group(key, members) for key, members in grouped.items()]

    def _group(self, key: tuple[str, str, str, int], members: list[RawRecord]) -> SummaryGroup:
        """Builds the summary of one group of repeats."""
        names: dict[str, None] = {}
        for member in members:
            names.update(dict.fromkeys(member.metrics))
        medians: dict[str, MetricValue] = {}
        lows: dict[str, MetricValue] = {}
        highs: dict[str, MetricValue] = {}
        for name in names:
            median, low, high = self._spread([member.metrics.get(name) for member in members])
            medians[name] = median
            lows[name] = low
            highs[name] = high
        scenario, phase, engine, workers = key
        return SummaryGroup(
            scenario=scenario,
            phase=phase,
            engine=engine,
            workers=workers,
            repeats=len(members),
            metrics=medians,
            metrics_min=lows,
            metrics_max=highs,
        )

    def _spread(self, values: list[MetricValue]) -> tuple[MetricValue, MetricValue, MetricValue]:
        """Returns (median, min, max) of numbers, (all, all, any) of booleans, or the first text.

        Missing values are skipped. When every value is missing the result is three None values.
        """
        present = [value for value in values if value is not None]
        if not present:
            return None, None, None
        if all(isinstance(value, bool) for value in present):
            return all(present), all(present), any(present)
        if all(isinstance(value, int | float) and not isinstance(value, bool) for value in present):
            numbers = [float(value) for value in present if isinstance(value, int | float)]
            return statistics.median(numbers), min(numbers), max(numbers)
        return present[0], present[0], present[0]
