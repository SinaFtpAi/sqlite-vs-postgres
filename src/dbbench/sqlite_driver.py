"""The one place that chooses the SQLite module the benchmark connects with.

The benchmark uses pysqlite3, built from the official SQLite amalgamation by `dbbench install`, or
Python's own sqlite3 (`sqlite.driver: stdlib`, which the tests use). The pysqlite3 build sits in the
cache folder, off the normal import path, so the driver puts that folder first on sys.path the first
time it is used. Every process does this for itself: the runner and each worker.

Nothing else in the package connects to SQLite for a benchmark connection. The annotations name the
stdlib connection type, because pysqlite3 has the same API.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict

from dbbench.errors import DbBenchError

if TYPE_CHECKING:
    from sqlite3 import Connection
    from types import ModuleType

    from dbbench.config import BenchConfig

    SqliteConnection = Connection

STDLIB_MODULE: Final = "sqlite3"
PYSQLITE3_MODULE: Final = "pysqlite3.dbapi2"
INSTALL_HINT: Final = "run uv run dbbench install first"


class SqliteDriverError(DbBenchError):
    """Raised when the chosen SQLite module cannot be loaded or reports another version."""


class SqliteDriverSpec(BaseModel):
    """Which SQLite module to use. For pysqlite3, where it is and the SQLite version it must report.

    The spec is sent to every worker process as JSON, so it holds plain values only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["pysqlite3", "stdlib"]
    module_dir: Path | None = None
    expected_version: str | None = None

    @classmethod
    def for_config(cls, config: BenchConfig) -> SqliteDriverSpec:
        """Returns the spec the config asks for: the install folder and version for pysqlite3."""
        if config.sqlite.driver == "stdlib":
            return cls(kind="stdlib")
        return cls(
            kind="pysqlite3",
            module_dir=config.install.pysqlite3_dir(),
            expected_version=config.install.sqlite.version,
        )


class SqliteDriver:
    """Loads the chosen SQLite module once per process and opens connections with it."""

    def __init__(self, spec: SqliteDriverSpec) -> None:
        """Remembers the spec. The module is loaded on first use, not here."""
        self._spec = spec
        self._module: ModuleType | None = None

    @classmethod
    def from_config(cls, config: BenchConfig) -> SqliteDriver:
        """Returns the driver the config asks for."""
        return cls(SqliteDriverSpec.for_config(config))

    @property
    def kind(self) -> str:
        """Returns the driver name as the config writes it: pysqlite3 or stdlib."""
        return self._spec.kind

    @property
    def module(self) -> ModuleType:
        """Returns the SQLite module, loading it on first use."""
        if self._module is None:
            self._module = self._load()
        return self._module

    @property
    def version(self) -> str:
        """Returns the SQLite version the module reports."""
        return str(self.module.sqlite_version)

    @property
    def operational_error(self) -> type[Exception]:
        """Returns the OperationalError class of the module, which the lock checks use."""
        error: type[Exception] = self.module.OperationalError
        return error

    def check(self) -> None:
        """Loads the module now, so a missing build stops the run before any work starts."""
        _ = self.module

    def connect(
        self,
        path: Path,
        *,
        timeout_seconds: float,
        cached_statements: int,
    ) -> SqliteConnection:
        """Opens a connection in autocommit mode, with the busy timeout and the statement cache."""
        connection: SqliteConnection = self.module.connect(
            str(path),
            isolation_level=None,
            timeout=timeout_seconds,
            cached_statements=cached_statements,
        )
        return connection

    def connect_default(self, path: str | Path) -> SqliteConnection:
        """Opens a connection with the module's own defaults, as the settle step always did."""
        connection: SqliteConnection = self.module.connect(str(path))
        return connection

    def compile_options(self) -> list[str]:
        """Returns the compile options of the module's SQLite, one per entry."""
        connection = self.connect_default(":memory:")
        try:
            rows = connection.execute("PRAGMA compile_options").fetchall()
        finally:
            connection.close()
        return [str(row[0]) for row in rows]

    def _load(self) -> ModuleType:
        """Imports the chosen module. For pysqlite3 its folder goes first and must hold a build."""
        if self._spec.kind == "stdlib":
            return importlib.import_module(STDLIB_MODULE)
        folder = self._spec.module_dir
        if folder is None or not folder.is_dir():
            message = f"pysqlite3 is not built: {INSTALL_HINT}"
            raise SqliteDriverError(message)
        self._put_first(folder)
        try:
            module = importlib.import_module(PYSQLITE3_MODULE)
        except ImportError as error:
            message = f"pysqlite3 in {folder} does not import: {INSTALL_HINT}"
            raise SqliteDriverError(message) from error
        expected = self._spec.expected_version
        found = str(module.sqlite_version)
        if expected is not None and found != expected:
            message = (
                f"pysqlite3 in {folder} reports SQLite {found}, not {expected}: {INSTALL_HINT}"
            )
            raise SqliteDriverError(message)
        return module

    @staticmethod
    def _put_first(folder: Path) -> None:
        """Moves the folder to the front of sys.path, so its pysqlite3 is the one imported."""
        text = str(folder)
        sys.path[:] = [entry for entry in sys.path if entry != text]
        sys.path.insert(0, text)
