"""Downloads and builds PostgreSQL and SQLite from their official sources, into the cache folder.

`dbbench install` fetches three files and checks each one against its hash before it is used:

- the PostgreSQL source tarball, built with the configure flags of the config into its own prefix;
- the SQLite amalgamation, which is copied into the pysqlite3 source so SQLite is compiled in;
- the pysqlite3 source distribution, from which one setup.py line is removed, then the wheel is
  built and unpacked into its folder.

Every step is skipped when its result is already in place and reports the right version, so a second
run only checks. Build output goes to the terminal, so a long build shows its progress.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlparse

from dbbench.errors import DbBenchError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from dbbench.config import InstallSettings

PATH_VARIABLE: Final = "PATH"
SHA256: Final = "sha256"
SHA3_256: Final = "sha3_256"
BUILD_TOOLS: Final = ("bison", "flex", "m4")
MISSING_TOOLS_MESSAGE: Final = (
    "PostgreSQL 18 needs bison, flex and m4 to build. On Debian or Ubuntu: sudo apt install "
    "build-essential bison flex m4. On macOS: brew install bison flex m4."
)
POSTGRES_VERSION_LINE: Final = "postgres (PostgreSQL) {version}"
PYSQLITE3_ROOT: Final = "pysqlite3-{version}"
AMALGAMATION_FOLDER: Final = "sqlite-amalgamation"
AMALGAMATION_FILES: Final = ("sqlite3.c", "sqlite3.h")
WHEEL_PATTERN: Final = "pysqlite3-*.whl"
UV_COMMAND: Final = "uv"
POSTGRES_BINARY: Final = "postgres"
PARTIAL_SUFFIX: Final = ".part"
# Prints the SQLite version of the pysqlite3 folder given as argv[1]. It runs in an isolated
# interpreter, so no module stays loaded in the install process.
PROBE_SNIPPET: Final = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "import pysqlite3.dbapi2 as dbapi2; print(dbapi2.sqlite_version)"
)


class InstallError(DbBenchError):
    """Raised when a download, a hash check or a build step fails. The message says what to do."""


class BuildCommand:
    """Runs the commands of a build, and reads the output of the version probes."""

    def __init__(self, env: Mapping[str, str]) -> None:
        """Remembers the environment the build commands run in."""
        self._env = dict(env)

    def run(self, args: Sequence[str], cwd: Path) -> None:
        """Runs one command in the folder. Raises InstallError when it fails or cannot start."""
        # The commands are fixed in this module and the config, never user input.
        try:
            subprocess.run(list(args), cwd=cwd, env=self._env, check=True)  # noqa: S603
        except (OSError, subprocess.CalledProcessError) as error:
            message = f"{args[0]} failed in {cwd}: {error}"
            raise InstallError(message) from error

    @staticmethod
    def output(args: Sequence[str]) -> str | None:
        """Returns the stripped output of a probe command, or None when it cannot run."""
        # The probes are fixed in this module, never user input.
        try:
            result = subprocess.run(  # noqa: S603
                list(args), capture_output=True, text=True, check=False
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip()


class BuildEnvironment:
    """The environment the builds run in: the current one with install.build_env applied."""

    def __init__(self, settings: InstallSettings) -> None:
        """Remembers the settings whose build_env is applied."""
        self._settings = settings

    def variables(self) -> dict[str, str]:
        """Returns the environment: PATH entries go first, and ~ is expanded."""
        env = dict(os.environ)
        for name, value in self._settings.build_env.items():
            expanded = str(Path(value).expanduser())
            if name == PATH_VARIABLE:
                current = env.get(PATH_VARIABLE, "")
                env[PATH_VARIABLE] = os.pathsep.join(part for part in (expanded, current) if part)
            else:
                env[name] = expanded
        return env

    @staticmethod
    def missing_tools(env: Mapping[str, str]) -> list[str]:
        """Returns the build tools that cannot be found on the PATH of the environment."""
        path = env.get(PATH_VARIABLE, "")
        return [tool for tool in BUILD_TOOLS if shutil.which(tool, path=path) is None]


class SourceFetcher:
    """Keeps the source archives: downloads the missing ones and checks every hash."""

    def __init__(self, settings: InstallSettings, say: Callable[[str], None]) -> None:
        """Remembers the settings and where the progress lines go."""
        self._settings = settings
        self._say = say

    def fetch(self, url: str, algorithm: str, expected: str) -> Path:
        """Returns the local archive of the URL, downloaded first when it is not there yet.

        Raises InstallError when the archive does not have the expected hash.
        """
        target = self._settings.source_dir() / Path(urlparse(url).path).name
        if not target.is_file():
            self._download(url, target)
        self._verify(target, algorithm, expected)
        return target

    def _download(self, url: str, target: Path) -> None:
        """Downloads the URL to a partial file, then renames it, so a cut download is never used."""
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        self._say(f"downloading {url}")
        try:
            # The URLs are fixed in the config file, never user input.
            with (
                urllib.request.urlopen(  # noqa: S310
                    url, timeout=self._settings.download_timeout_seconds
                ) as response,
                partial.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle)
        except OSError as error:
            partial.unlink(missing_ok=True)
            message = f"the download of {url} failed: {error}"
            raise InstallError(message) from error
        partial.replace(target)

    @staticmethod
    def _verify(path: Path, algorithm: str, expected: str) -> None:
        """Raises InstallError when the file's digest is not the expected one."""
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, algorithm).hexdigest()
        if actual != expected:
            message = (
                f"hash mismatch for {path}: {algorithm} is {actual}, the config says {expected}. "
                "Delete the file and run again to download it once more."
            )
            raise InstallError(message)


class PostgresBuilder:
    """Builds PostgreSQL from its source tarball into its own prefix, unless it is there."""

    def __init__(
        self,
        settings: InstallSettings,
        env: Mapping[str, str],
        say: Callable[[str], None],
    ) -> None:
        """Remembers the settings, the build environment and where the progress lines go."""
        self._settings = settings
        self._env = env
        self._say = say

    def is_built(self) -> bool:
        """Returns True when the installed server reports the configured version."""
        binary = self._settings.postgres_bin_dir() / POSTGRES_BINARY
        if not binary.is_file():
            return False
        expected = POSTGRES_VERSION_LINE.format(version=self._settings.postgres.version)
        return BuildCommand.output([str(binary), "--version"]) == expected

    def build(self, archive: Path) -> None:
        """Extracts the tarball and runs configure, make and make install.

        The build tools are checked first, before anything is removed or extracted.
        """
        if BuildEnvironment.missing_tools(self._env):
            raise InstallError(MISSING_TOOLS_MESSAGE)
        version = self._settings.postgres.version
        source = self._settings.source_dir()
        tree = source / f"postgresql-{version}"
        prefix = self._settings.postgres_prefix()
        for stale in (tree, prefix):
            if stale.exists():
                shutil.rmtree(stale)
        self._say(f"extracting {archive.name}")
        with tarfile.open(archive) as tar:
            # The hash was checked before this point, and the data filter stays on.
            tar.extractall(source, filter="data")
        if not tree.is_dir():
            message = f"{archive} does not hold the folder {tree.name}"
            raise InstallError(message)
        command = BuildCommand(self._env)
        self._say(f"configuring PostgreSQL {version} into {prefix}")
        flags = self._settings.postgres.configure_flags
        command.run([str(tree / "configure"), f"--prefix={prefix}", *flags], tree)
        self._say(f"building PostgreSQL {version}")
        command.run(["make", f"-j{os.cpu_count() or 1}"], tree)
        command.run(["make", "install"], tree)


class Pysqlite3Builder:
    """Builds the pysqlite3 wheel against the SQLite amalgamation and unpacks it."""

    def __init__(
        self,
        settings: InstallSettings,
        env: Mapping[str, str],
        say: Callable[[str], None],
    ) -> None:
        """Remembers the settings, the build environment and where the progress lines go."""
        self._settings = settings
        self._env = env
        self._say = say

    def is_built(self) -> bool:
        """Returns True when the folder imports and reports the configured SQLite version."""
        folder = self._settings.pysqlite3_dir()
        if not folder.is_dir():
            return False
        probe = [sys.executable, "-I", "-c", PROBE_SNIPPET, str(folder)]
        return BuildCommand.output(probe) == self._settings.sqlite.version

    def build(self, sdist: Path, amalgamation: Path) -> None:
        """Prepares the source, builds the wheel with uv and unpacks it into its folder."""
        source = self._settings.source_dir()
        root = source / PYSQLITE3_ROOT.format(version=self._settings.pysqlite3.version)
        if root.exists():
            shutil.rmtree(root)
        self._say(f"extracting {sdist.name}")
        with tarfile.open(sdist) as tar:
            # The hash was checked before this point, and the data filter stays on.
            tar.extractall(source, filter="data")
        if not root.is_dir():
            message = f"{sdist} does not hold the folder {root.name}"
            raise InstallError(message)
        headers = self._extract_amalgamation(amalgamation)
        for name in AMALGAMATION_FILES:
            shutil.copy2(headers / name, root / name)
        self._remove_setup_lines(root / "setup.py")
        wheels = self._settings.wheel_dir()
        wheels.mkdir(parents=True, exist_ok=True)
        for old in wheels.glob(WHEEL_PATTERN):
            old.unlink()
        self._say("building the pysqlite3 wheel")
        python = self._settings.wheel_python
        BuildCommand(self._env).run(
            [UV_COMMAND, "build", "--wheel", "--python", python, "-o", str(wheels)], root
        )
        built = sorted(wheels.glob(WHEEL_PATTERN))
        if len(built) != 1:
            message = f"expected one pysqlite3 wheel in {wheels}, found {len(built)}"
            raise InstallError(message)
        target = self._settings.pysqlite3_dir()
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        with zipfile.ZipFile(built[0]) as wheel:
            # The hash of the sdist was checked, and zipfile keeps members inside the folder.
            wheel.extractall(target)  # noqa: S202
        if not self.is_built():
            version = self._settings.sqlite.version
            message = f"the pysqlite3 wheel in {target} does not report SQLite {version}"
            raise InstallError(message)

    def _extract_amalgamation(self, archive: Path) -> Path:
        """Unpacks the SQLite zip and returns the folder that holds sqlite3.c and sqlite3.h."""
        folder = self._settings.source_dir() / AMALGAMATION_FOLDER
        if folder.exists():
            shutil.rmtree(folder)
        with zipfile.ZipFile(archive) as zipped:
            # The hash of the archive was checked, and zipfile keeps members inside the folder.
            zipped.extractall(folder)  # noqa: S202
        found = next(folder.rglob(AMALGAMATION_FILES[0]), None)
        if found is None:
            message = f"{archive} does not hold {AMALGAMATION_FILES[0]}"
            raise InstallError(message)
        return found.parent

    def _remove_setup_lines(self, setup: Path) -> None:
        """Deletes each configured line from setup.py. A missing line stops the build."""
        lines = setup.read_text(encoding="utf-8").splitlines(keepends=True)
        for entry in self._settings.pysqlite3.remove_setup_lines:
            kept = [line for line in lines if line.strip() != entry.strip()]
            if len(kept) == len(lines):
                message = f"{entry} is not a line of {setup}, so the pysqlite3 source changed"
                raise InstallError(message)
            lines = kept
        setup.write_text("".join(lines), encoding="utf-8")


class Installer:
    """Runs dbbench install: fetches and checks the sources, then builds what is missing."""

    def __init__(self, settings: InstallSettings, say: Callable[[str], None]) -> None:
        """Remembers the install settings and where the progress lines go."""
        self._settings = settings
        self._say = say

    def run(self) -> None:
        """Fetches and checks every source, then builds PostgreSQL and pysqlite3 where needed.

        Raises InstallError at the first failure. The message says what failed and what to do.
        """
        self._settings.source_dir().mkdir(parents=True, exist_ok=True)
        env = BuildEnvironment(self._settings).variables()
        fetcher = SourceFetcher(self._settings, self._say)
        pg = self._settings.postgres
        sqlite = self._settings.sqlite
        py = self._settings.pysqlite3
        postgres_archive = fetcher.fetch(pg.url, SHA256, pg.sha256)
        amalgamation = fetcher.fetch(sqlite.url, SHA3_256, sqlite.sha3_256)
        sdist = fetcher.fetch(py.url, SHA256, py.sha256)

        postgres = PostgresBuilder(self._settings, env, self._say)
        if postgres.is_built():
            self._say(f"PostgreSQL {pg.version} is already built, skipping")
        else:
            postgres.build(postgres_archive)
        pysqlite3 = Pysqlite3Builder(self._settings, env, self._say)
        if pysqlite3.is_built():
            self._say(f"pysqlite3 with SQLite {sqlite.version} is already built, skipping")
        else:
            pysqlite3.build(sdist, amalgamation)
        self._say(f"install complete: PostgreSQL {pg.version}, SQLite {sqlite.version}")
