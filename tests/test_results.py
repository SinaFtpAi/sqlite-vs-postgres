"""The summarizer takes the median, min and max of each metric over the repeats."""

from __future__ import annotations

import pytest

from dbbench.results import RawRecord, Summarizer


def _record(repeat: int, ops_per_s: float, p99: float | None) -> RawRecord:
    """Builds one point read record for one repeat."""
    return RawRecord(
        scenario="point_read",
        engine="sqlite_wal_normal",
        workers=1,
        repeat=repeat,
        metrics={"ops_per_s": ops_per_s, "p99_ms": p99, "results_match": True},
    )


def test_median_min_and_max_over_three_repeats() -> None:
    """Of 100, 300 and 200 ops/s the median is 200, the min 100 and the max 300."""
    records = [_record(0, 100.0, 0.2), _record(1, 300.0, None), _record(2, 200.0, 0.4)]
    (group,) = Summarizer().summarize(records)
    assert group.repeats == 3
    assert group.metrics["ops_per_s"] == 200.0
    assert group.metrics_min["ops_per_s"] == 100.0
    assert group.metrics_max["ops_per_s"] == 300.0
    assert group.metrics["p99_ms"] == pytest.approx(0.3)
    assert group.metrics_min["p99_ms"] == pytest.approx(0.2)
    assert group.metrics_max["p99_ms"] == pytest.approx(0.4)


def test_booleans_use_all_for_the_median_and_min_and_any_for_the_max() -> None:
    """A flag that was false in one repeat is false in the median and the min, true in the max."""
    records = [
        RawRecord(
            scenario="aggregate",
            engine="postgres",
            workers=1,
            repeat=repeat,
            metrics={"results_match": value},
        )
        for repeat, value in enumerate((True, False, True))
    ]
    (group,) = Summarizer().summarize(records)
    assert group.metrics["results_match"] is False
    assert group.metrics_min["results_match"] is False
    assert group.metrics_max["results_match"] is True


def test_groups_keep_their_first_seen_order() -> None:
    """Different engines or worker counts make separate groups, in the order they were seen."""
    first = _record(0, 1.0, None)
    second = RawRecord(
        scenario="point_read", engine="postgres", workers=1, repeat=0, metrics={"ops_per_s": 5.0}
    )
    groups = Summarizer().summarize([first, second, _record(1, 3.0, None)])
    assert [group.engine for group in groups] == ["sqlite_wal_normal", "postgres"]
    assert groups[0].metrics["ops_per_s"] == 2.0
