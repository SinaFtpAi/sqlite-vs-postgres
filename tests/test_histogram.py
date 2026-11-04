"""Percentiles, the overflow list and merging of the latency histogram."""

from __future__ import annotations

import pytest

from dbbench.histogram import LatencyHistogram

US = 1_000
MS = 1_000_000
BUCKET_NS = 1 * US
RANGE_NS = 100 * MS


def _histogram() -> LatencyHistogram:
    """Returns an empty histogram with 1 us buckets up to 100 ms."""
    return LatencyHistogram(BUCKET_NS, RANGE_NS)


def test_empty_histogram_has_no_percentiles() -> None:
    """Without samples there is nothing to report."""
    histogram = _histogram()
    assert histogram.count == 0
    assert histogram.percentile(50) is None
    assert histogram.max_ms() is None


def test_nearest_rank_percentiles_on_bucket_edges() -> None:
    """Values 1 us to 100 us: the median is 50 us, the p99 is 99 us, the max is 100 us."""
    histogram = _histogram()
    for value in range(1, 101):
        histogram.record_ns(value * US)
    assert histogram.count == 100
    assert histogram.percentile(50) == pytest.approx(0.050)
    assert histogram.percentile(99) == pytest.approx(0.099)
    assert histogram.percentile(100) == pytest.approx(0.100)
    assert histogram.max_ms() == pytest.approx(0.100)


def test_values_above_the_range_are_kept_exactly() -> None:
    """A value past 100 ms is not hidden in a bucket: it is reported as it was recorded."""
    histogram = _histogram()
    histogram.record_ns(1 * US)
    histogram.record_ns(150 * MS + 123 * US)
    assert histogram.percentile(100) == pytest.approx(150.123)
    assert histogram.max_ms() == pytest.approx(150.123)
    assert histogram.overflow == [150 * MS + 123 * US]


def test_merge_adds_counts_overflow_and_maximum() -> None:
    """Merging two histograms gives the same numbers as recording into one."""
    left = _histogram()
    right = _histogram()
    for value in (10 * US, 20 * US):
        left.record_ns(value)
    for value in (30 * US, 200 * MS):
        right.record_ns(value)
    left.merge(right)
    assert left.count == 4
    assert left.overflow == [200 * MS]
    assert left.max_ms() == pytest.approx(200.0)
    assert left.percentile(50) == pytest.approx(0.020)


def test_merge_rejects_a_different_shape() -> None:
    """Histograms with different buckets cannot be merged."""
    with pytest.raises(ValueError, match="cannot be merged"):
        _histogram().merge(LatencyHistogram(2 * US, RANGE_NS))


def test_p999_of_a_thousand_values_is_the_999th() -> None:
    """Values 1 us to 1000 us: the nearest rank of 99.9 percent is 999, so the value is 0.999 ms."""
    histogram = _histogram()
    for value in range(1, 1001):
        histogram.record_ns(value * US)
    assert histogram.percentile(99.9) == pytest.approx(0.999)
    assert histogram.percentile(90) == pytest.approx(0.900)
