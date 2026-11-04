"""Engine specs: the Unix socket engine and the driver settings come from the config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbbench.engines import EngineCatalog, PostgresEngineSpec, SqliteEngineSpec

if TYPE_CHECKING:
    from dbbench.config import BenchConfig


def test_unix_socket_engine_uses_the_cluster_folder_as_host(bench_config: BenchConfig) -> None:
    """postgres_unix connects through the socket folder, on the same port as the TCP engine."""
    spec = EngineCatalog(bench_config).spec("postgres_unix")
    assert isinstance(spec, PostgresEngineSpec)
    socket_dir = bench_config.paths.work_dir / bench_config.postgres.cluster_dir
    assert f"host={socket_dir}" in spec.dsn
    assert f"port={bench_config.postgres.port}" in spec.dsn
    assert spec.prepare_threshold == 0


def test_tcp_engine_uses_the_loopback_address(bench_config: BenchConfig) -> None:
    """postgres goes over TCP to the configured host and port."""
    spec = EngineCatalog(bench_config).spec("postgres")
    assert isinstance(spec, PostgresEngineSpec)
    assert "host=127.0.0.1" in spec.dsn


def test_sqlite_spec_carries_the_driver_settings(bench_config: BenchConfig) -> None:
    """The mmap size and the statement cache of the config reach the SQLite spec."""
    spec = EngineCatalog(bench_config).spec("sqlite_wal_full")
    assert isinstance(spec, SqliteEngineSpec)
    assert spec.synchronous == "FULL"
    assert spec.mmap_size_bytes == 0
    assert spec.cached_statements == 128
