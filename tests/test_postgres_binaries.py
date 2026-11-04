"""The PostgreSQL binaries: the installed build is found, a user's bin_dir is kept as given."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dbbench.postgres_server import PostgresBinaries, PostgresError

if TYPE_CHECKING:
    from pathlib import Path

    from dbbench.config import BenchConfig


def test_installed_binaries_are_found_in_the_install_folder(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """With bin_dir null, the bin folder of the install's PostgreSQL version is used."""
    install = bench_config.install.model_copy(update={"cache_dir": tmp_path})
    bin_dir = install.postgres_bin_dir()
    bin_dir.mkdir(parents=True)
    (bin_dir / "postgres").write_text("", encoding="utf-8")
    assert PostgresBinaries(bench_config.postgres, install).resolve() == bin_dir


def test_a_user_bin_dir_is_used_as_given(bench_config: BenchConfig, tmp_path: Path) -> None:
    """A bin_dir that is set is returned unchanged, and nothing in it is touched."""
    own = tmp_path / "own-pg" / "bin"
    own.mkdir(parents=True)
    tool = own / "postgres"
    tool.write_text("", encoding="utf-8")
    settings = bench_config.postgres.model_copy(update={"bin_dir": own})
    assert PostgresBinaries(settings, bench_config.install).resolve() == own


def test_missing_install_is_a_clear_error(bench_config: BenchConfig, tmp_path: Path) -> None:
    """Without an install, the error tells the reader to run dbbench install first."""
    install = bench_config.install.model_copy(update={"cache_dir": tmp_path / "empty"})
    with pytest.raises(PostgresError, match="run uv run dbbench install first"):
        PostgresBinaries(bench_config.postgres, install).resolve()
