"""The settle step: a fake meminfo drives the wait, and the record says what happened."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbbench.config import SettleSettings
from dbbench.engines import EngineCatalog, SqliteEngineSpec
from dbbench.settle import Settler

if TYPE_CHECKING:
    from pathlib import Path

    from dbbench.config import BenchConfig


def _sqlite_spec(config: BenchConfig) -> SqliteEngineSpec:
    """Returns the SQLite spec of the normal variant, under the config's work folder."""
    spec = EngineCatalog(config).spec("sqlite_wal_normal")
    assert isinstance(spec, SqliteEngineSpec)
    config.paths.work_dir.mkdir(parents=True, exist_ok=True)
    return spec


def test_settle_returns_a_record_for_a_quiet_disk(
    small_config: BenchConfig, tmp_path: Path
) -> None:
    """A fake meminfo under the limit and no rest: the record shows a quiet disk."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1 kB\nDirty:        120 kB\n", encoding="utf-8")
    settings = small_config.settle.model_copy(update={"min_seconds": 0.0})
    spec = _sqlite_spec(small_config)
    spec.path.touch()
    record = Settler(settings, meminfo).settle(spec)
    assert record.dirty_kib == 120
    assert record.timed_out is False
    assert record.waited_seconds >= 0.0


def test_settle_times_out_when_dirty_stays_high(small_config: BenchConfig, tmp_path: Path) -> None:
    """Dirty memory above the limit ends the wait at the timeout, and the record says so."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("Dirty:        900000 kB\n", encoding="utf-8")
    settings = SettleSettings(
        dirty_max_kib=10,
        poll_seconds=0.01,
        timeout_seconds=0.0,
        min_seconds=0.0,
    )
    spec = _sqlite_spec(small_config)
    record = Settler(settings, meminfo).settle(spec)
    assert record.dirty_kib == 900000
    assert record.timed_out is True


def test_settle_skips_the_wait_without_meminfo(small_config: BenchConfig, tmp_path: Path) -> None:
    """Where /proc/meminfo does not exist the Dirty value is null and nothing times out."""
    settings = small_config.settle.model_copy(update={"min_seconds": 0.0})
    spec = _sqlite_spec(small_config)
    record = Settler(settings, tmp_path / "missing").settle(spec)
    assert record.dirty_kib is None
    assert record.timed_out is False
