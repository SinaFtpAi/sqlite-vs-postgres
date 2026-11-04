"""The config file loads, the quick override returns a new config, and unknown keys are errors."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dbbench.config import ConfigLoader, ConfigMerger

EXPECTED_ROWS = 1_000_000
QUICK_ROWS = 50_000
DEFAULT_REPEATS = 5


def test_default_file_loads_with_the_method_numbers() -> None:
    """The main numbers of the method are in the file as written."""
    config = ConfigLoader().load()
    assert config.dataset.rows == EXPECTED_ROWS
    assert config.dataset.users == 100_000
    assert config.run.repeats == DEFAULT_REPEATS
    assert config.run.order_seed == 7
    assert config.run.concurrency_levels == (1, 4, 16)
    assert config.postgres.port == 55433
    assert config.postgres.settings["max_connections"] == 50
    assert config.postgres.settings["jit"] == "off"
    assert config.postgres.settings["fsync"] == "on"


def test_paths_default_to_the_home_cache_without_protected_folders() -> None:
    """The work folder is under the home cache, and no folder is protected by default."""
    config = ConfigLoader().load()
    assert config.paths.work_dir == Path.home() / ".cache" / "dbbench"
    assert config.paths.protected_paths == []


def test_driver_and_settle_settings_are_explicit() -> None:
    """The driver settings and the settle step come from the file, not from code."""
    config = ConfigLoader().load()
    assert config.postgres.bin_dir is None
    assert config.sqlite.driver == "pysqlite3"
    assert config.postgres.prepare_threshold == 0
    assert config.sqlite.mmap_size_bytes == 0
    assert config.sqlite.cached_statements == 128
    assert config.settle.dirty_max_kib == 16384
    assert config.settle.min_seconds == pytest.approx(1.0)


def test_every_engine_in_a_scenario_has_a_label() -> None:
    """Each engine that any scenario measures has the reader-facing name summary.md shows."""
    config = ConfigLoader().load()
    engines = {name for names in config.engines_by_scenario.values() for name in names}
    assert engines <= set(config.labels)
    assert config.engines_by_scenario["concurrent_write"] == (
        "sqlite_wal_normal",
        "sqlite_wal_full",
        "sqlite_wal_nowait",
        "postgres",
    )


def test_quick_override_returns_a_new_config_and_keeps_the_original() -> None:
    """apply_quick changes only the quick values, on a copy."""
    original = ConfigLoader().load()
    quick = original.apply_quick()
    assert quick.dataset.rows == QUICK_ROWS
    assert quick.dataset.users == 5_000
    assert quick.run.repeats == 1
    assert quick.run.duration_seconds == pytest.approx(2.0)
    assert quick.run.point_read_ops == 5_000
    assert quick.run.warmup_seconds == original.run.warmup_seconds
    assert quick.settle == original.settle
    assert original.dataset.rows == EXPECTED_ROWS
    assert original.run.repeats == DEFAULT_REPEATS


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    """A typo in the file is an error, not a silently ignored setting."""
    text = (Path(__file__).resolve().parents[1] / "config" / "dbbench.yaml").read_text("utf-8")
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        text.replace(f"repeats: {DEFAULT_REPEATS}", f"repeats: {DEFAULT_REPEATS}\n  repeatz: 4", 1),
        "utf-8",
    )
    with pytest.raises(ValidationError):
        ConfigLoader(broken).load()


def test_the_local_overlay_is_merged_over_the_file(tmp_path: Path) -> None:
    """A local.yaml next to the config changes only the keys it names; the rest is the file's."""
    base = (Path(__file__).resolve().parents[1] / "config" / "dbbench.yaml").read_text("utf-8")
    (tmp_path / "dbbench.yaml").write_text(base, "utf-8")
    (tmp_path / "local.yaml").write_text(
        'install:\n  build_env:\n    M4: "/opt/m4"\n',
        "utf-8",
    )
    config = ConfigLoader(tmp_path / "dbbench.yaml").load()
    assert config.install.build_env == {"M4": "/opt/m4"}
    assert config.dataset.rows == EXPECTED_ROWS


def test_the_merge_keeps_sibling_keys_and_replaces_values() -> None:
    """Nested mappings merge key by key, and a plain value is replaced."""
    merged = ConfigMerger().merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 3}})
    assert merged == {"a": {"b": 3, "c": 2}}
