"""Facts about the machine, the software and the code version, written next to each result set.

Paths are written with the home folder shown as ~, so a results folder shared publicly does not
reveal the name of the machine's user.
"""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

import psycopg
from pydantic import BaseModel, Field

from dbbench.sqlite_driver import SqliteDriver

if TYPE_CHECKING:
    from dbbench.config import BenchConfig

_PROC_CPUINFO: Final = Path("/proc/cpuinfo")
_PROC_MEMINFO: Final = Path("/proc/meminfo")
_PROC_MOUNTS: Final = Path("/proc/mounts")
_KB_PER_GB: Final = 1024 * 1024
_RAM_DECIMALS: Final = 1
_MOUNT_FIELDS: Final = 3
_UNKNOWN: Final = "unknown"
_HOME_MARK: Final = "~"


class MachineRecord(BaseModel):
    """The contents of machine.json."""

    started_at: str
    mode: str
    cpu_model: str
    logical_cpus: int
    ram_gb: float
    kernel: str
    work_dir: str
    work_dir_filesystem: str
    python: str
    sqlite: str
    psycopg: str
    postgres: str
    postgres_configure: str = "unknown"
    sqlite_driver: str = "unknown"
    sqlite_compile_options: list[str] = Field(default_factory=list)
    git_commit: str | None
    config: dict[str, object]

    def engine_labels(self) -> dict[str, str]:
        """Returns the reader-facing name of each engine, from the config the run used.

        An engine without a label is shown under its id, so an old folder still reads.
        """
        labels = self.config.get("labels")
        if not isinstance(labels, dict):
            return {}
        return {str(name): str(label) for name, label in labels.items()}

    def engine_order(self, scenario: str) -> list[str]:
        """Returns the engines of one scenario in the order the config lists them.

        Empty when the folder has no such list.
        """
        order = self.config.get("engines_by_scenario")
        if not isinstance(order, dict):
            return []
        names = order.get(scenario)
        if not isinstance(names, list):
            return []
        return [str(name) for name in names]


class HomeRedactor:
    """Replaces the home folder at the start of a path with ~, in text and nested values."""

    def __init__(self, home: Path) -> None:
        """Remembers the home folder, as it is written and with links resolved."""
        self._prefixes = tuple(sorted({str(home), str(home.resolve())}, key=len, reverse=True))

    def text(self, value: str) -> str:
        """Returns the text with the home folder replaced by ~, when it starts with it."""
        for prefix in self._prefixes:
            if value == prefix:
                return _HOME_MARK
            if value.startswith(prefix + os.sep):
                return _HOME_MARK + value[len(prefix) :]
        return value

    def inline(self, value: str) -> str:
        """Replaces the home folder with ~ wherever it occurs in a longer text, like a command."""
        for prefix in self._prefixes:
            value = value.replace(prefix + os.sep, _HOME_MARK + os.sep)
        return value

    def mapping(self, data: dict[str, object]) -> dict[str, object]:
        """Returns a copy of a nested dict with every string redacted."""
        return {key: self._value(item) for key, item in data.items()}

    def _value(self, item: object) -> object:
        """Redacts one value of any nesting level. Numbers and booleans pass through."""
        if isinstance(item, str):
            return self.text(item)
        if isinstance(item, dict):
            return {str(key): self._value(part) for key, part in item.items()}
        if isinstance(item, list):
            return [self._value(part) for part in item]
        return item


class GitRevision:
    """Reads the short commit of the repository the tool runs from."""

    def __init__(self, repo: Path) -> None:
        """Remembers the repository folder."""
        self._repo = repo

    def short_commit(self) -> str | None:
        """Returns the short commit, or None when git or the repository is not there."""
        try:
            # git is found on PATH on purpose, and the arguments are fixed, not user input.
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
                cwd=self._repo,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None


class MachineInfo:
    """Collects the machine facts for one run."""

    def __init__(
        self,
        config: BenchConfig,
        mode: str,
        postgres_version: str,
        postgres_configure: str,
        repo: Path,
    ) -> None:
        """Remembers the config, the run mode, the server facts and the repository folder."""
        self._config = config
        self._mode = mode
        self._postgres_version = postgres_version
        self._postgres_configure = postgres_configure
        self._repo = repo

    def collect(self) -> MachineRecord:
        """Reads the machine and returns the record for machine.json."""
        work_dir = self._config.paths.work_dir.resolve()
        home = HomeRedactor(Path.home())
        driver = SqliteDriver.from_config(self._config)
        return MachineRecord(
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            mode=self._mode,
            cpu_model=self._cpu_model(),
            logical_cpus=_logical_cpus(),
            ram_gb=round(self._ram_kb() / _KB_PER_GB, _RAM_DECIMALS),
            kernel=platform.release(),
            work_dir=home.text(str(work_dir)),
            work_dir_filesystem=_filesystem_of(work_dir),
            python=platform.python_version(),
            sqlite=driver.version,
            sqlite_driver=driver.kind,
            sqlite_compile_options=driver.compile_options(),
            psycopg=psycopg.__version__,
            postgres=self._postgres_version,
            postgres_configure=home.inline(self._postgres_configure),
            git_commit=GitRevision(self._repo).short_commit(),
            config=home.mapping(self._config.model_dump(mode="json")),
        )

    def _cpu_model(self) -> str:
        """Returns the first model name from /proc/cpuinfo."""
        for line in _read_lines(_PROC_CPUINFO):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
        return _UNKNOWN

    def _ram_kb(self) -> int:
        """Returns the total memory in kB from /proc/meminfo."""
        for line in _read_lines(_PROC_MEMINFO):
            if line.startswith("MemTotal:"):
                return int(line.split()[1])
        return 0


def _logical_cpus() -> int:
    """Returns the number of logical CPUs the process can see, or 0 when unknown."""
    return os.cpu_count() or 0


def _filesystem_of(path: Path) -> str:
    """Returns the filesystem type of the mount that holds the path, from /proc/mounts."""
    best_point = ""
    best_type = _UNKNOWN
    for line in _read_lines(_PROC_MOUNTS):
        fields = line.split()
        if len(fields) < _MOUNT_FIELDS:
            continue
        point = fields[1]
        if path.is_relative_to(point) and len(point) >= len(best_point):
            best_point = point
            best_type = fields[2]
    return best_type


def _read_lines(path: Path) -> list[str]:
    """Returns the lines of a text file, or an empty list when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
