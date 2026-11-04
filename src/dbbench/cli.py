"""Command line: setup, seed, run, report, all and stop."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from dbbench.charts import ChartRunner
from dbbench.config import TOOL_ROOT, BenchConfig, ConfigLoader
from dbbench.errors import DbBenchError
from dbbench.guard import WorkDirGuard
from dbbench.install import Installer
from dbbench.machine import MachineInfo
from dbbench.postgres_server import PostgresServer
from dbbench.report import MarkdownReport
from dbbench.results import (
    SUMMARY_MD_FILE,
    RawRecord,
    ResultReader,
    ResultWriter,
    Summarizer,
)
from dbbench.scenarios import RunContext, RunMode, ScenarioRunner
from dbbench.sqlite_driver import SqliteDriver

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
_FLOAT_DECIMALS: Final = 3
RESULT_FOLDER_COMMANDS: Final = ("report", "charts")


class _Progress:
    """Prints each record as it is measured, and appends it to raw.jsonl when a folder is given."""

    def __init__(self, writer: ResultWriter | None, out: TextIO) -> None:
        """Remembers where the records go."""
        self._writer = writer
        self._out = out

    def emit(self, record: RawRecord) -> None:
        """Writes one record to the results folder and prints a line about it."""
        if self._writer is not None:
            self._writer.append(record)
        head = f"{record.scenario}"
        if record.phase:
            head += f" {record.phase}"
        parts = [head, record.engine, f"workers={record.workers}", f"repeat={record.repeat}"]
        for key, value in record.metrics.items():
            text = f"{value:.{_FLOAT_DECIMALS}f}" if isinstance(value, float) else str(value)
            parts.append(f"{key}={text}")
        self._out.write(" ".join(parts) + "\n")


class Cli:
    """The dbbench command line. Output goes to the given stream."""

    def __init__(self, loader: ConfigLoader | None = None, out: TextIO | None = None) -> None:
        """Remembers the config loader and the output stream."""
        self._loader = loader if loader is not None else ConfigLoader()
        self._out = out if out is not None else sys.stdout

    def run(self, argv: Sequence[str]) -> int:
        """Parses the arguments, runs the command and returns the exit code."""
        args = self._parser().parse_args(list(argv))
        try:
            return self._dispatch(args)
        except DbBenchError as error:
            self._say(f"error: {error}")
            return EXIT_FAILED

    def _parser(self) -> argparse.ArgumentParser:
        """Builds the argument parser with every command and flag."""
        parser = argparse.ArgumentParser(
            prog="dbbench",
            description="SQLite vs PostgreSQL benchmark on one machine.",
        )
        parser.set_defaults(quick=False)
        commands = parser.add_subparsers(dest="command", required=True)
        commands.add_parser("setup", help="create, start and prepare the bench PostgreSQL")
        seed = commands.add_parser("seed", help="load the data set into both engines")
        seed.add_argument("--quick", action="store_true", help="use the quick data set")
        run = commands.add_parser("run", help="run scenarios and write a results folder")
        run.add_argument("--quick", action="store_true", help="use the quick settings")
        run.add_argument(
            "--scenario",
            action="append",
            dest="scenarios",
            default=[],
            choices=ScenarioRunner.scenario_ids(),
            metavar="ID",
            help="run only this scenario (repeat the flag for more); default is all",
        )
        report = commands.add_parser("report", help="rebuild summary files from a results folder")
        report.add_argument("results_dir", type=Path)
        commands.add_parser(
            "install", help="download and build PostgreSQL and SQLite from their official sources"
        )
        everything = commands.add_parser(
            "all", help="install, setup, every scenario, the report, then the charts"
        )
        everything.add_argument("--quick", action="store_true", help="use the quick settings")
        charts = commands.add_parser("charts", help="draw the SVG charts of a results folder")
        charts.add_argument("results_dir", type=Path)
        charts.add_argument(
            "--png-preview",
            type=Path,
            default=None,
            metavar="DIR",
            help="also save a PNG of every chart in DIR, outside the repository",
        )
        commands.add_parser("stop", help="stop the bench PostgreSQL cluster")
        return parser

    def _dispatch(self, args: argparse.Namespace) -> int:
        """Runs the command named in the arguments."""
        if args.command in RESULT_FOLDER_COMMANDS:
            return self._folder_command(args)
        config = self._loader.load()
        if args.command == "install":
            return self._install(config)
        if args.command == "all":
            self._install(config)
        return self._with_server(args, config)

    def _with_server(self, args: argparse.Namespace, config: BenchConfig) -> int:
        """Runs a command that needs the PostgreSQL server, with the quick settings when asked."""
        mode: RunMode = "quick" if args.quick else "full"
        if args.quick:
            config = config.apply_quick()
        work_dir = WorkDirGuard(config.paths).check()
        work_dir.mkdir(parents=True, exist_ok=True)
        server = PostgresServer(config.postgres, config.install, work_dir)
        if args.command == "setup":
            return self._setup(config, server)
        if args.command == "stop":
            return self._stop(server)
        if args.command == "seed":
            return self._execute(config, mode, server, ["seed"], write=False)
        if args.command == "run":
            return self._execute(config, mode, server, args.scenarios, write=True)
        return self._execute(config, mode, server, [], write=True, charts=True)

    def _folder_command(self, args: argparse.Namespace) -> int:
        """Runs a command that reads a results folder and needs no PostgreSQL: report or charts."""
        directory = Path(args.results_dir)
        if args.command == "report":
            return self._report(directory)
        return self._charts(directory, args.png_preview)

    def _install(self, config: BenchConfig) -> int:
        """Builds the engines from their official sources, or checks that they are built."""
        Installer(config.install, self._say).run()
        return EXIT_OK

    def _setup(self, config: BenchConfig, server: PostgresServer) -> int:
        """Creates, starts and prepares the bench cluster."""
        server.setup()
        self._say(f"bench PostgreSQL ready on port {config.postgres.port}")
        return EXIT_OK

    def _stop(self, server: PostgresServer) -> int:
        """Stops the bench cluster only, in fast mode."""
        if server.stop():
            self._say("bench PostgreSQL stopped")
        else:
            self._say("bench PostgreSQL was not running")
        return EXIT_OK

    def _execute(  # noqa: PLR0913
        self,
        config: BenchConfig,
        mode: RunMode,
        server: PostgresServer,
        selected: Sequence[str],
        *,
        write: bool,
        charts: bool = False,
    ) -> int:
        """Prepares the cluster, runs the selected scenarios and writes the results when asked.

        With charts, the charts of the new results folder are drawn after the report.
        """
        SqliteDriver.from_config(config).check()
        server.setup()
        writer: ResultWriter | None = None
        machine = None
        directory: Path | None = None
        if write:
            stamp = datetime.now().astimezone().date().isoformat()
            directory = config.paths.results_root(TOOL_ROOT) / f"{stamp}-{mode}"
            writer = ResultWriter(directory)
            writer.start_raw()
            machine = MachineInfo(
                config, mode, server.version(), server.configure(), TOOL_ROOT
            ).collect()
            writer.write_machine(machine)
            self._say(f"results folder: {directory}")
        progress = _Progress(writer, self._out)
        ctx = RunContext(config, mode, server, progress.emit)
        try:
            ScenarioRunner(ctx).run(selected)
        finally:
            if writer is not None and machine is not None:
                groups = Summarizer().summarize(ctx.records)
                writer.write_summary(groups)
                writer.write_markdown(MarkdownReport(groups, machine).render())
        if charts and directory is not None:
            self._draw_charts(config, directory, None)
        self._say("done")
        return EXIT_OK

    def _charts(self, directory: Path, preview: Path | None) -> int:
        """Draws the charts of a results folder, with optional PNG previews."""
        if not directory.is_dir():
            message = f"no results folder at {directory}"
            raise DbBenchError(message)
        self._draw_charts(self._loader.load(), directory, preview)
        return EXIT_OK

    def _draw_charts(self, config: BenchConfig, directory: Path, preview: Path | None) -> None:
        """Writes the charts, charts.json and embed.md of a results folder, and says what it did."""
        runner = ChartRunner(config.charts, config.charts.series_labels, directory, preview)
        result = runner.run()
        self._say(f"charts written to {result.charts_dir}: {', '.join(result.written)}")
        if result.skipped:
            self._say(f"charts without data, skipped: {', '.join(result.skipped)}")

    def _report(self, directory: Path) -> int:
        """Rebuilds summary.json and summary.md from raw.jsonl and machine.json."""
        if not directory.is_dir():
            message = f"no results folder at {directory}"
            raise DbBenchError(message)
        reader = ResultReader(directory)
        groups = Summarizer().summarize(reader.records())
        writer = ResultWriter(directory)
        writer.write_summary(groups)
        writer.write_markdown(MarkdownReport(groups, reader.machine()).render())
        self._say(f"wrote {directory / SUMMARY_MD_FILE}")
        return EXIT_OK

    def _say(self, text: str) -> None:
        """Writes one line of output."""
        self._out.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    """Runs the dbbench command line and returns the exit code."""
    return Cli().run(sys.argv[1:] if argv is None else argv)
