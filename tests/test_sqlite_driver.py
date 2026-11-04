"""The SQLite driver: stdlib works, and a missing or wrong pysqlite3 build says how to fix it."""

from __future__ import annotations

import sqlite3
import sys
from typing import TYPE_CHECKING

import pytest

from dbbench.sqlite_driver import INSTALL_HINT, SqliteDriver, SqliteDriverError, SqliteDriverSpec

if TYPE_CHECKING:
    from pathlib import Path

PYSQLITE3_KEYS = ("pysqlite3", "pysqlite3.dbapi2")
FAKE_VERSION = "0.0.0"


def test_stdlib_driver_connects_and_reports_its_version() -> None:
    """The stdlib driver opens a connection and reports the version of the running sqlite3."""
    driver = SqliteDriver(SqliteDriverSpec(kind="stdlib"))
    assert driver.kind == "stdlib"
    assert driver.version == sqlite3.sqlite_version
    options = driver.compile_options()
    assert all(isinstance(option, str) for option in options)
    assert issubclass(driver.operational_error, sqlite3.OperationalError)


def test_a_missing_pysqlite3_folder_says_how_to_build_it(tmp_path: Path) -> None:
    """Without the folder, the error names the install command."""
    spec = SqliteDriverSpec(
        kind="pysqlite3", module_dir=tmp_path / "missing", expected_version="3.54.1"
    )
    with pytest.raises(SqliteDriverError, match="run uv run dbbench install first"):
        SqliteDriver(spec).check()


def test_a_folder_without_pysqlite3_says_how_to_build_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder that does not import pysqlite3 gives the same instruction."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    for key in PYSQLITE3_KEYS:
        monkeypatch.delitem(sys.modules, key, raising=False)
    folder = tmp_path / "empty-build"
    folder.mkdir()
    spec = SqliteDriverSpec(kind="pysqlite3", module_dir=folder, expected_version="3.54.1")
    with pytest.raises(SqliteDriverError, match=INSTALL_HINT):
        SqliteDriver(spec).check()


def _fake_pysqlite3(folder: Path, version: str) -> None:
    """Writes a tiny pysqlite3 package that reports the given SQLite version."""
    package = folder / "pysqlite3"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "dbapi2.py").write_text(
        f"sqlite_version = {version!r}\n"
        "class OperationalError(Exception):\n    pass\n"
        "def connect(path, **kwargs):\n    raise OperationalError(path)\n",
        encoding="utf-8",
    )


def test_a_pysqlite3_with_the_wrong_version_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The version the module reports must be the configured one."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    for key in PYSQLITE3_KEYS:
        monkeypatch.delitem(sys.modules, key, raising=False)
    _fake_pysqlite3(tmp_path, FAKE_VERSION)
    spec = SqliteDriverSpec(kind="pysqlite3", module_dir=tmp_path, expected_version="3.54.1")
    with pytest.raises(SqliteDriverError, match=f"reports SQLite {FAKE_VERSION}"):
        SqliteDriver(spec).check()


def test_a_pysqlite3_folder_is_put_first_and_its_version_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folder moves to the front of sys.path, and the driver reads the version from it."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    for key in PYSQLITE3_KEYS:
        monkeypatch.delitem(sys.modules, key, raising=False)
    _fake_pysqlite3(tmp_path, "3.54.1")
    spec = SqliteDriverSpec(kind="pysqlite3", module_dir=tmp_path, expected_version="3.54.1")
    driver = SqliteDriver(spec)
    assert driver.version == "3.54.1"
    assert sys.path[0] == str(tmp_path)
