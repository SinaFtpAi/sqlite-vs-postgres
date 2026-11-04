"""Machine facts: paths are written with ~ for the home folder, and labels come from the config."""

from __future__ import annotations

from pathlib import Path

from dbbench.machine import GitRevision, HomeRedactor, MachineRecord


def test_home_folder_is_written_as_tilde() -> None:
    """The home folder becomes ~, but a folder that only starts with the same letters does not."""
    redactor = HomeRedactor(Path("/home/alice"))
    assert redactor.text("/home/alice/.cache/dbbench") == "~/.cache/dbbench"
    assert redactor.text("/home/alice") == "~"
    assert redactor.text("/home/alicex/work") == "/home/alicex/work"


def test_nested_config_values_are_redacted() -> None:
    """Every string in the nested config is redacted, and numbers and booleans are kept."""
    redactor = HomeRedactor(Path("/home/alice"))
    data: dict[str, object] = {
        "paths": {"work_dir": "/home/alice/.cache/dbbench", "list": ["/home/alice/x", 3]}
    }
    assert redactor.mapping(data) == {
        "paths": {"work_dir": "~/.cache/dbbench", "list": ["~/x", 3]},
    }


def test_git_revision_is_none_when_the_repository_is_missing(tmp_path: Path) -> None:
    """A folder that does not exist has no commit, and the record gets null, not an error."""
    assert GitRevision(tmp_path / "missing").short_commit() is None


def test_engine_labels_come_from_the_config_snapshot() -> None:
    """Labels are read from the saved config; an engine with no label keeps its id."""
    machine = MachineRecord(
        started_at="2026-11-04T10:00:00+00:00",
        mode="full",
        cpu_model="cpu",
        logical_cpus=1,
        ram_gb=1.0,
        kernel="6.6",
        work_dir="~/.cache/dbbench",
        work_dir_filesystem="ext4",
        python="3.12",
        sqlite="3.45",
        psycopg="3.3",
        postgres="postgres (PostgreSQL) 18.6",
        git_commit=None,
        config={"labels": {"postgres": "PostgreSQL (TCP)"}},
    )
    assert machine.engine_labels() == {"postgres": "PostgreSQL (TCP)"}
