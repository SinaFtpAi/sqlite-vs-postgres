"""Refuses work folders that would measure the wrong thing or touch folders that must stay intact.

The benchmark writes databases and deletes data directories, so a bad work folder would cost real
data. A work folder on a RAM filesystem would also hide the fsync cost the benchmark measures. The
guard runs before every command that touches the work folder.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Final

from dbbench.errors import DbBenchError

if TYPE_CHECKING:
    from dbbench.config import PathSettings

PROC_MOUNTS: Final = Path("/proc/mounts")
RAM_FILESYSTEMS: Final = frozenset({"tmpfs", "ramfs"})
_MOUNT_FIELDS: Final = 3
_OCTAL_ESCAPE: Final = re.compile(r"\\([0-7]{3})")
_OCTAL_BASE: Final = 8


class GuardError(DbBenchError):
    """Raised when the work folder is not allowed."""


def _unescape_mount_field(field: str) -> str:
    """Decodes the octal escapes /proc/mounts uses for spaces and tabs in mount points."""
    return _OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), _OCTAL_BASE)), field)


class MountTable:
    """The mount points of a /proc/mounts style file, read when they are asked for."""

    def __init__(self, mounts_file: Path) -> None:
        """Remembers the file to read. It may not exist, for example on macOS."""
        self._mounts_file = mounts_file

    def holding(self, folder: Path) -> tuple[Path, str] | None:
        """Returns the mount point and filesystem type that hold the folder, or None.

        None means the mount table is not there, or it has no entry for the folder. The longest
        mount point that is a parent of the folder wins.
        """
        try:
            text = self._mounts_file.read_text(encoding="utf-8")
        except OSError:
            return None
        best: tuple[Path, str] | None = None
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < _MOUNT_FIELDS:
                continue
            point = Path(_unescape_mount_field(fields[1]))
            if not folder.is_relative_to(point):
                continue
            if best is None or len(point.parts) >= len(best[0].parts):
                best = (point, fields[2])
        return best


class WorkDirGuard:
    """Checks that the work folder is on a disk and does not overlap a protected folder."""

    def __init__(self, paths: PathSettings, mounts_file: Path = PROC_MOUNTS) -> None:
        """Remembers the path settings and the mount table to check the filesystem against."""
        self._paths = paths
        self._mounts = MountTable(mounts_file)

    def check(self) -> Path:
        """Returns the resolved work folder, or raises GuardError when it is not allowed."""
        work = self._paths.work_dir.resolve()
        for protected in self._paths.protected_paths:
            self._refuse_overlap(work, protected.resolve())
        self._refuse_ram_filesystem(work)
        return work

    @staticmethod
    def _refuse_overlap(work: Path, protected: Path) -> None:
        """Refuses a work folder equal to, inside or containing a protected folder."""
        if work == protected or work.is_relative_to(protected) or protected.is_relative_to(work):
            message = f"work_dir {work} overlaps protected path {protected}"
            raise GuardError(message)

    def _refuse_ram_filesystem(self, work: Path) -> None:
        """Refuses a work folder on tmpfs or ramfs. Without a mount table the check is skipped."""
        holding = self._mounts.holding(work)
        if holding is None:
            return
        point, filesystem = holding
        if filesystem in RAM_FILESYSTEMS:
            message = (
                f"work_dir {work} is on {filesystem} at {point}, a RAM filesystem: "
                "the fsync cost would not be measured"
            )
            raise GuardError(message)
