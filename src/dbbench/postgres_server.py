"""Runs the bench PostgreSQL cluster on its own port and data folder, from the PostgreSQL binaries.

The binaries come from `dbbench install`, which builds the official source into the cache
folder, or from a folder the user names with postgres.bin_dir. They are only run, never changed.
The cluster lives under the work folder, and this module touches nothing else.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

import psycopg
from psycopg import sql

from dbbench.errors import DbBenchError

if TYPE_CHECKING:
    from dbbench.config import InstallSettings, PostgresSettings

_KB_PER_MB: Final = 1024
_ADMIN_DATABASE: Final = "postgres"
_POSTMASTER_PID_FILE: Final = "postmaster.pid"
_PG_VERSION_FILE: Final = "PG_VERSION"
_PROC: Final = Path("/proc")
_STAT_PPID_FIELD: Final = 1
_MAJOR_PATTERN: Final = re.compile(r"\(PostgreSQL\) (\d+)")
_SERVER_BINARY: Final = "postgres"
_PG_CONFIG_BINARY: Final = "pg_config"
INSTALL_HINT: Final = "run uv run dbbench install first"
UNKNOWN: Final = "unknown"


class PostgresError(DbBenchError):
    """Raised when a PostgreSQL binary fails or the cluster is not in the expected state."""


class PostgresBinaries:
    """Finds the folder of the PostgreSQL binaries: the user's bin_dir or the installed one."""

    def __init__(self, settings: PostgresSettings, install: InstallSettings) -> None:
        """Remembers the settings, whose bin_dir decides the source, and the install settings."""
        self._settings = settings
        self._install = install

    def resolve(self) -> Path:
        """Returns the binary folder. A bin_dir set by the user is returned as given, untouched."""
        if self._settings.bin_dir is not None:
            return self._settings.bin_dir
        folder = self._install.postgres_bin_dir()
        if not (folder / _SERVER_BINARY).is_file():
            message = f"no PostgreSQL binaries at {folder}: {INSTALL_HINT}"
            raise PostgresError(message)
        return folder


class PostgresServer:
    """Starts, stops and measures the bench cluster."""

    def __init__(
        self,
        settings: PostgresSettings,
        install: InstallSettings,
        work_dir: Path,
    ) -> None:
        """Finds the binaries, and remembers the folders of the cluster under the work folder."""
        self._settings = settings
        self._bin_dir = PostgresBinaries(settings, install).resolve()
        self._cluster_dir = work_dir / settings.cluster_dir
        self._data_dir = self._cluster_dir / settings.data_dir
        self._log_path = self._cluster_dir / settings.log_file

    @property
    def data_dir(self) -> Path:
        """Returns the cluster data folder."""
        return self._data_dir

    def _binary(self, name: str) -> str:
        """Returns the absolute path of one PostgreSQL binary."""
        return str(self._bin_dir / name)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Runs a binary with the given arguments and raises PostgresError when it fails."""
        # The arguments come from the config file and the work folder, never from user input.
        result = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
        if result.returncode != 0:
            message = f"{args[0]} failed: {result.stderr.strip()}"
            raise PostgresError(message)
        return result

    def is_initialized(self) -> bool:
        """Returns True when the data folder holds a cluster."""
        return (self._data_dir / _PG_VERSION_FILE).is_file()

    def is_running(self) -> bool:
        """Returns True when the bench port accepts connections."""
        args = [
            self._binary("pg_isready"),
            "-h",
            self._settings.host,
            "-p",
            str(self._settings.port),
        ]
        result = subprocess.run(args, capture_output=True, check=False)  # noqa: S603
        return result.returncode == 0

    def initdb(self) -> None:
        """Creates the cluster in the data folder with trust authentication."""
        self._cluster_dir.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                self._binary("initdb"),
                "-D",
                str(self._data_dir),
                "-U",
                self._settings.user,
                "--auth=trust",
            ]
        )

    def start(self) -> float:
        """Starts the cluster and waits until it accepts connections. Returns the seconds taken."""
        self._cluster_dir.mkdir(parents=True, exist_ok=True)
        options = [f"-p {self._settings.port}", f"-k {self._cluster_dir}"]
        options.extend(f"-c {key}={value}" for key, value in self._settings.settings.items())
        began = time.perf_counter()
        self._run(
            [
                self._binary("pg_ctl"),
                "-D",
                str(self._data_dir),
                "-l",
                str(self._log_path),
                "-o",
                " ".join(options),
                "-w",
                "start",
            ]
        )
        return time.perf_counter() - began

    def stop(self) -> bool:
        """Stops the bench cluster in fast mode. Returns False when it was not running."""
        if not self.is_initialized() or not self.is_running():
            return False
        self._run([self._binary("pg_ctl"), "-D", str(self._data_dir), "stop", "-m", "fast"])
        return True

    def setup(self) -> None:
        """Creates the cluster if needed, starts it if needed and creates the bench database.

        A cluster from another major version is refused, because the server would not start on it.
        """
        if self.is_initialized():
            self._refuse_other_major()
        else:
            self.initdb()
        if not self.is_running():
            self.start()
        self.ensure_database()

    def _refuse_other_major(self) -> None:
        """Raises when the cluster was made by another PostgreSQL major than the binaries."""
        cluster = (self._data_dir / _PG_VERSION_FILE).read_text(encoding="utf-8").strip()
        server = self._major()
        if cluster != server:
            message = (
                f"the bench cluster in {self._data_dir} was made by PostgreSQL {cluster}, but the "
                f"binaries are PostgreSQL {server}: move that folder aside, then run again"
            )
            raise PostgresError(message)

    def _major(self) -> str:
        """Returns the major version of the server binaries, read from its version line."""
        line = self.version()
        match = _MAJOR_PATTERN.search(line)
        if match is None:
            message = f"cannot read the major version from: {line}"
            raise PostgresError(message)
        return match.group(1)

    def ensure_database(self) -> None:
        """Creates the bench database if it does not exist yet."""
        database = self._settings.database
        dsn = self._settings.dsn(_ADMIN_DATABASE)
        with psycopg.connect(dsn, autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
            ).fetchone()
            if exists is None:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    def version(self) -> str:
        """Returns the server version line, for example 'postgres (PostgreSQL) 18.6'."""
        return self._run([self._binary(_SERVER_BINARY), "--version"]).stdout.strip()

    def configure(self) -> str:
        """Returns the output of pg_config --configure, or unknown without a pg_config."""
        if not (self._bin_dir / _PG_CONFIG_BINARY).is_file():
            return UNKNOWN
        return self._run([self._binary(_PG_CONFIG_BINARY), "--configure"]).stdout.strip()

    def memory(self) -> tuple[float, float]:
        """Returns (RSS MB, PSS MB) summed over the postmaster and its direct children."""
        postmaster = self._postmaster_pid()
        pids = [postmaster, *self._children(postmaster)]
        rss_kb = sum(self._status_kb(pid, "VmRSS:") for pid in pids)
        pss_kb = sum(self._status_kb(pid, "Pss:", rollup=True) for pid in pids)
        return rss_kb / _KB_PER_MB, pss_kb / _KB_PER_MB

    def data_dir_bytes(self) -> int:
        """Returns the total size of every file in the cluster data folder."""
        return sum(path.stat().st_size for path in self._data_dir.rglob("*") if path.is_file())

    def _postmaster_pid(self) -> int:
        """Reads the postmaster pid from the first line of postmaster.pid."""
        pid_file = self._data_dir / _POSTMASTER_PID_FILE
        try:
            first_line = pid_file.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError) as exc:
            message = f"cannot read the postmaster pid from {pid_file}"
            raise PostgresError(message) from exc
        return int(first_line)

    def _children(self, parent: int) -> list[int]:
        """Returns the pids whose parent is the given pid, read from /proc."""
        children: list[int] = []
        for entry in _PROC.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text(encoding="utf-8")
            except OSError:
                continue
            # The command name sits in parentheses and may contain spaces, so split after it.
            fields = stat.rsplit(")", 1)[1].split()
            if int(fields[_STAT_PPID_FIELD]) == parent:
                children.append(int(entry.name))
        return children

    def _status_kb(self, pid: int, key: str, *, rollup: bool = False) -> int:
        """Returns one kB value from /proc/<pid>/status, or smaps_rollup when rollup is set."""
        name = "smaps_rollup" if rollup else "status"
        try:
            lines = (_PROC / str(pid) / name).read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        for line in lines:
            if line.startswith(key):
                return int(line.split()[1])
        return 0
