"""Latency histogram with fixed buckets, cheap to record into and to merge across workers.

Values below the range land in a fixed-width bucket. Values at or above the range are kept exactly
in a list, so a slow outlier is never hidden in a bucket.
"""

from __future__ import annotations

import math
import operator
from array import array

from dbbench.config import NS_PER_MS

PERCENT_SCALE = 100
_RANK_DECIMALS = 6


class LatencyHistogram:
    """Counts latencies in fixed buckets and keeps the values above the range exactly."""

    def __init__(self, bucket_ns: int, range_ns: int) -> None:
        """Creates an empty histogram with the given bucket width and range, in nanoseconds."""
        self._bucket_ns = bucket_ns
        self._range_ns = range_ns
        self._counts = array("q", [0]) * (range_ns // bucket_ns)
        self._overflow: list[int] = []
        self._count = 0
        self._max_ns = 0

    @property
    def bucket_ns(self) -> int:
        """Returns the bucket width in nanoseconds."""
        return self._bucket_ns

    @property
    def range_ns(self) -> int:
        """Returns the upper limit of the bucketed range in nanoseconds."""
        return self._range_ns

    @property
    def counts(self) -> array[int]:
        """Returns the bucket counts, index i holding the values in [i, i + 1) bucket widths."""
        return self._counts

    @property
    def overflow(self) -> list[int]:
        """Returns a copy of the raw values at or above the range, in nanoseconds."""
        return list(self._overflow)

    @property
    def count(self) -> int:
        """Returns the number of recorded values."""
        return self._count

    @property
    def max_ns(self) -> int:
        """Returns the largest recorded value in nanoseconds, or 0 when empty."""
        return self._max_ns

    def record_ns(self, value_ns: int) -> None:
        """Records one latency in nanoseconds."""
        self._count += 1
        self._max_ns = max(self._max_ns, value_ns)
        if value_ns >= self._range_ns:
            self._overflow.append(value_ns)
            return
        self._counts[value_ns // self._bucket_ns] += 1

    def merge(self, other: LatencyHistogram) -> None:
        """Adds the counts of another histogram with the same shape into this one."""
        if other.bucket_ns != self._bucket_ns or other.range_ns != self._range_ns:
            message = "histograms with different bucket widths or ranges cannot be merged"
            raise ValueError(message)
        self._counts = array("q", map(operator.add, self._counts, other.counts))
        self._overflow.extend(other.overflow)
        self._count += other.count
        self._max_ns = max(self._max_ns, other.max_ns)

    def percentile(self, percent: float) -> float | None:
        """Returns the nearest-rank percentile in milliseconds, or None when nothing was recorded.

        Values inside the range are reported at the lower edge of their bucket, so they are exact
        to one bucket width. Values in the overflow list are reported exactly.
        """
        if self._count == 0:
            return None
        # Rounding removes binary noise first: 99.9 percent of 1000 values is rank 999, not 1000.
        rank = max(1, math.ceil(round(percent * self._count / PERCENT_SCALE, _RANK_DECIMALS)))
        seen = 0
        for index, bucket in enumerate(self._counts):
            if bucket == 0:
                continue
            seen += bucket
            if seen >= rank:
                return index * self._bucket_ns / NS_PER_MS
        ordered = sorted(self._overflow)
        return ordered[rank - seen - 1] / NS_PER_MS

    def max_ms(self) -> float | None:
        """Returns the largest recorded value in milliseconds, or None when nothing was recorded."""
        if self._count == 0:
            return None
        return self._max_ns / NS_PER_MS
