"""The guard refuses RAM filesystems and protected folders, and accepts disk folders."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dbbench.guard import GuardError, WorkDirGuard

if TYPE_CHECKING:
    from pathlib import Path

    from dbbench.config import BenchConfig


def _guard(
    config: BenchConfig, work_dir: Path, mounts: Path, protected: list[Path]
) -> WorkDirGuard:
    """Returns a guard whose work folder, protected folders and mount table are the given ones."""
    paths = config.paths.model_copy(
        update={"work_dir": work_dir, "protected_paths": protected},
    )
    return WorkDirGuard(paths, mounts)


def _mounts(tmp_path: Path, *lines: str) -> Path:
    """Writes a fake /proc/mounts file in tmp_path and returns its path."""
    mounts = tmp_path / "mounts"
    mounts.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return mounts


def test_tmpfs_work_folder_is_refused(bench_config: BenchConfig, tmp_path: Path) -> None:
    """A tmpfs folder would hide the fsync cost, so it is refused."""
    base = tmp_path.resolve()
    mounts = _mounts(base, f"tmpfs {base}/ram tmpfs rw 0 0")
    with pytest.raises(GuardError, match="RAM filesystem"):
        _guard(bench_config, base / "ram" / "work", mounts, []).check()


def test_ramfs_work_folder_is_refused(bench_config: BenchConfig, tmp_path: Path) -> None:
    """ramfs is a RAM filesystem too."""
    base = tmp_path.resolve()
    mounts = _mounts(base, f"ramfs {base}/ram ramfs rw 0 0")
    with pytest.raises(GuardError, match="RAM filesystem"):
        _guard(bench_config, base / "ram", mounts, []).check()


def test_the_longest_mount_point_decides(bench_config: BenchConfig, tmp_path: Path) -> None:
    """A disk mount above a tmpfs mount does not hide the tmpfs; the closest mount decides."""
    base = tmp_path.resolve()
    mounts = _mounts(base, f"/dev/sda1 {base} ext4 rw 0 0", f"tmpfs {base}/ram tmpfs rw 0 0")
    with pytest.raises(GuardError, match="RAM filesystem"):
        _guard(bench_config, base / "ram" / "work", mounts, []).check()
    assert _guard(bench_config, base / "disk", mounts, []).check() == base / "disk"


def test_ext4_work_folder_is_accepted_and_resolved(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """An ext4 folder passes and comes back resolved."""
    base = tmp_path.resolve()
    mounts = _mounts(base, "/dev/sda1 / ext4 rw 0 0")
    work = base / "disk" / "work"
    assert _guard(bench_config, work, mounts, []).check() == work


def test_escaped_mount_points_are_read(bench_config: BenchConfig, tmp_path: Path) -> None:
    """A mount point with a space is written with \\040 in /proc/mounts and is still matched."""
    base = tmp_path.resolve()
    mounts = _mounts(base, f"tmpfs {base}/my\\040ram tmpfs rw 0 0")
    with pytest.raises(GuardError, match="RAM filesystem"):
        _guard(bench_config, base / "my ram" / "work", mounts, []).check()


def test_missing_mount_table_skips_the_filesystem_check(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """Without /proc/mounts, as on macOS, only the protected folders are checked."""
    base = tmp_path.resolve()
    work = base / "work"
    assert _guard(bench_config, work, base / "no-such-file", []).check() == work


def test_protected_folder_is_refused_when_equal_inside_or_containing(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """A work folder may not be a protected folder, sit inside it, or contain it."""
    base = tmp_path.resolve()
    protected = base / "keep"
    mounts = base / "no-such-file"
    with pytest.raises(GuardError, match="overlaps protected path"):
        _guard(bench_config, protected, mounts, [protected]).check()
    with pytest.raises(GuardError, match="overlaps protected path"):
        _guard(bench_config, protected / "inside", mounts, [protected]).check()
    with pytest.raises(GuardError, match="overlaps protected path"):
        _guard(bench_config, base, mounts, [protected]).check()


def test_a_sibling_of_a_protected_folder_is_accepted(
    bench_config: BenchConfig, tmp_path: Path
) -> None:
    """A folder next to the protected one is not an overlap."""
    base = tmp_path.resolve()
    work = base / "keep-not"
    assert _guard(bench_config, work, base / "no-such-file", [base / "keep"]).check() == work
