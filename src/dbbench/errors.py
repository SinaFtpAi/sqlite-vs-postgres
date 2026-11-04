"""Error base class shared by the benchmark modules."""

from __future__ import annotations


class DbBenchError(RuntimeError):
    """Base class for every error the tool reports on the command line."""
