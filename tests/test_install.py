"""dbbench install: hashes are checked, tools must exist, and build edits are exact."""

from __future__ import annotations

import hashlib
import os
import stat
from typing import TYPE_CHECKING

import pytest

from dbbench.install import (
    MISSING_TOOLS_MESSAGE,
    SHA256,
    BuildEnvironment,
    InstallError,
    PostgresBuilder,
    Pysqlite3Builder,
    SourceFetcher,
)

if TYPE_CHECKING:
    from pathlib import Path

    from dbbench.config import BenchConfig


def _say(_line: str) -> None:
    """Drops the progress lines, which the tests do not read."""


def test_a_wrong_hash_stops_with_the_file_name(bench_config: BenchConfig, tmp_path: Path) -> None:
    """An archive that does not match its hash is refused, and the message names the file."""
    install = bench_config.install.model_copy(update={"cache_dir": tmp_path})
    source = install.source_dir()
    source.mkdir(parents=True)
    (source / "fake.tar.gz").write_bytes(b"not the real release")
    fetcher = SourceFetcher(install, _say)
    with pytest.raises(InstallError, match=r"hash mismatch for .*fake\.tar\.gz"):
        fetcher.fetch("https://example.invalid/fake.tar.gz", SHA256, "0" * 64)


def test_a_matching_hash_is_accepted(bench_config: BenchConfig, tmp_path: Path) -> None:
    """The archive of the expected hash is returned as it is."""
    install = bench_config.install.model_copy(update={"cache_dir": tmp_path})
    source = install.source_dir()
    source.mkdir(parents=True)
    archive = source / "fake.tar.gz"
    archive.write_bytes(b"release")
    expected = hashlib.sha256(b"release").hexdigest()
    found = SourceFetcher(install, _say).fetch(
        "https://example.invalid/fake.tar.gz", SHA256, expected
    )
    assert found == archive


def test_missing_build_tools_stop_before_anything_is_removed(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """With bison, flex and m4 off the PATH, the build stops with the message the user reads."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    install = bench_config.install.model_copy(update={"cache_dir": tmp_path})
    env = {"PATH": str(empty)}
    assert BuildEnvironment.missing_tools(env) == ["bison", "flex", "m4"]
    builder = PostgresBuilder(install, env, _say)
    with pytest.raises(InstallError) as caught:
        builder.build(tmp_path / "postgresql-18.6.tar.bz2")
    assert str(caught.value) == MISSING_TOOLS_MESSAGE


def test_build_env_path_goes_first_and_tildes_expand(
    bench_config: BenchConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATH entries are put in front of the current PATH, and ~ is expanded in every value."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    install = bench_config.install.model_copy(
        update={"build_env": {"PATH": "~/tools/bin", "M4": "~/tools/bin/m4"}}
    )
    env = BuildEnvironment(install).variables()
    assert env["PATH"] == f"{tmp_path}/tools/bin{os.pathsep}/usr/bin"
    assert env["M4"] == f"{tmp_path}/tools/bin/m4"


def test_the_setup_line_is_removed_and_the_rest_kept(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """Only the configured line goes, whatever its indentation, and every other line stays."""
    setup = tmp_path / "setup.py"
    setup.write_text(
        "macros = [\n    ('A', 1),\n        ('SQLITE_TEMP_STORE', 3),\n    ('B', 2),\n]\n",
        encoding="utf-8",
    )
    Pysqlite3Builder(bench_config.install, {}, _say)._remove_setup_lines(setup)
    assert setup.read_text(encoding="utf-8") == "macros = [\n    ('A', 1),\n    ('B', 2),\n]\n"


def test_a_missing_setup_line_stops_the_build(bench_config: BenchConfig, tmp_path: Path) -> None:
    """When the configured line is not in setup.py, the source changed and the build stops."""
    setup = tmp_path / "setup.py"
    setup.write_text("macros = []\n", encoding="utf-8")
    builder = Pysqlite3Builder(bench_config.install, {}, _say)
    with pytest.raises(InstallError, match="SQLITE_TEMP_STORE"):
        builder._remove_setup_lines(setup)


def test_a_built_postgres_is_recognised_by_its_version_line(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """The installed server counts as built only when it reports the configured version."""
    install = bench_config.install.model_copy(update={"cache_dir": tmp_path})
    bin_dir = install.postgres_bin_dir()
    bin_dir.mkdir(parents=True)
    server = bin_dir / "postgres"
    server.write_text(
        f"#!/bin/sh\necho 'postgres (PostgreSQL) {install.postgres.version}'\n", encoding="utf-8"
    )
    server.chmod(server.stat().st_mode | stat.S_IXUSR)
    assert PostgresBuilder(install, {}, _say).is_built() is True
    older = install.postgres.model_copy(update={"version": "18.5"})
    other = install.model_copy(update={"postgres": older})
    assert PostgresBuilder(other, {}, _say).is_built() is False
