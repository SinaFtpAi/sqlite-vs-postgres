"""Shared fixtures: the real config file, and a small copy of it that writes under tmp_path."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dbbench.config import BenchConfig, ConfigLoader
from dbbench.settle import Settler

if TYPE_CHECKING:
    from pathlib import Path

SMALL_ROWS = 2_000
SMALL_USERS = 200
SMALL_BATCH = 500
SMALL_POINT_OPS = 300
SMALL_USER_OPS = 100
SMALL_INSERT_OPS = 50
SMALL_AGGREGATE_RUNS = 2
SMALL_WARMUP_OPS = 10
SMALL_WARMUP_SECONDS = 0.1
SMALL_DURATION_SECONDS = 0.5
SMALL_WORKERS = 2
SQLITE_ONLY = ("sqlite_wal_normal",)


@pytest.fixture
def bench_config() -> BenchConfig:
    """Returns the real config file, unchanged."""
    return ConfigLoader().load()


@pytest.fixture
def small_config(tmp_path: Path) -> BenchConfig:
    """Returns a copy of the config under tmp_path, with small numbers and SQLite only."""
    data = ConfigLoader().load().model_dump()
    data["paths"]["work_dir"] = tmp_path / "work"
    data["sqlite"]["driver"] = "stdlib"
    data["dataset"].update(rows=SMALL_ROWS, users=SMALL_USERS, batch_size=SMALL_BATCH)
    data["run"].update(
        repeats=1,
        warmup_seconds=SMALL_WARMUP_SECONDS,
        duration_seconds=SMALL_DURATION_SECONDS,
        single_insert_ops=SMALL_INSERT_OPS,
        point_read_ops=SMALL_POINT_OPS,
        user_read_ops=SMALL_USER_OPS,
        aggregate_runs=SMALL_AGGREGATE_RUNS,
        concurrency_levels=[SMALL_WORKERS],
        mixed_workers=SMALL_WORKERS,
        count_warmup_ops=SMALL_WARMUP_OPS,
    )
    data["engines_by_scenario"] = {
        "seed": list(SQLITE_ONLY),
        "point_read": list(SQLITE_ONLY),
        "user_read": list(SQLITE_ONLY),
        "aggregate": list(SQLITE_ONLY),
        "concurrent_write": list(SQLITE_ONLY),
    }
    return BenchConfig.model_validate(data)


@pytest.fixture
def quiet_settler(tmp_path: Path, small_config: BenchConfig) -> Settler:
    """Returns a settler that reads a fake meminfo with no dirty pages and does not rest."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1 kB\nDirty: 0 kB\n", encoding="utf-8")
    settings = small_config.settle.model_copy(update={"min_seconds": 0.0})
    return Settler(settings, meminfo)
