"""One interface over SQLite and PostgreSQL, with the same schema, queries and result shapes.

Each engine object holds one connection. Every write is its own transaction (autocommit), except the
batch load, which puts one batch of rows in one transaction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from dbbench.config import BenchConfig, QueriesSettings
from dbbench.dataset import Order
from dbbench.errors import DbBenchError
from dbbench.sqlite_driver import SqliteDriver, SqliteDriverSpec

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from dbbench.sqlite_driver import SqliteConnection

POSTGRES_ENGINE: Final = "postgres"
POSTGRES_UNIX_ENGINE: Final = "postgres_unix"
_MS_PER_S: Final = 1000
PointRow = tuple[int, int, int, str, str, str]
UserRow = tuple[int, int, str, str]
StatusRow = tuple[str, int, int]
TopUserRow = tuple[int, int]
LoadRow = tuple[int, Order]

_WAL_SUFFIX: Final = "-wal"


class EngineError(DbBenchError):
    """Raised when an engine is used before it is connected, or cannot be built."""


class _Frozen(BaseModel):
    """Base for the engine specs: picklable, unknown keys are errors, values cannot change."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SqliteEngineSpec(_Frozen):
    """Everything a worker process needs to open one SQLite variant."""

    kind: Literal["sqlite"] = "sqlite"
    name: str
    driver: SqliteDriverSpec
    path: Path
    synchronous: Literal["NORMAL", "FULL"]
    busy_timeout_ms: int = Field(ge=0)
    cache_size_kib: int = Field(gt=0)
    mmap_size_bytes: int = Field(ge=0)
    cached_statements: int = Field(ge=0)
    lock_error_messages: tuple[str, ...]
    queries: QueriesSettings


class PostgresEngineSpec(_Frozen):
    """Everything a worker process needs to open the PostgreSQL bench cluster."""

    kind: Literal["postgres"] = "postgres"
    name: str
    dsn: str
    prepare_threshold: int = Field(ge=0)
    queries: QueriesSettings


EngineSpec = Annotated[SqliteEngineSpec | PostgresEngineSpec, Field(discriminator="kind")]


class Engine(ABC):
    """The operations every scenario needs, on one connection."""

    def __init__(self, name: str, queries: QueriesSettings) -> None:
        """Remembers the engine name and the query limits."""
        self._name = name
        self._queries = queries

    @property
    def name(self) -> str:
        """Returns the engine name used in results."""
        return self._name

    @abstractmethod
    def connect(self) -> None:
        """Opens the connection."""

    @abstractmethod
    def close(self) -> None:
        """Closes the connection. Calling it twice is harmless."""

    @abstractmethod
    def ping(self) -> None:
        """Runs SELECT 1 on the connection."""

    @abstractmethod
    def create_schema(self) -> None:
        """Drops and recreates both tables, and the writes index."""

    @abstractmethod
    def load_batch(self, rows: Sequence[LoadRow]) -> None:
        """Inserts one batch of (id, order) rows in one transaction."""

    @abstractmethod
    def build_indexes(self) -> None:
        """Builds the user index on orders, after the load."""

    @abstractmethod
    def analyze(self) -> None:
        """Updates the planner statistics for orders."""

    @abstractmethod
    def reset_writes(self) -> None:
        """Empties orders_writes, so every write run starts from the same table."""

    @abstractmethod
    def point_read(self, order_id: int) -> PointRow | None:
        """Fetches one order by id."""

    @abstractmethod
    def user_read(self, user_id: int) -> list[UserRow]:
        """Fetches the newest orders of one user."""

    @abstractmethod
    def aggregate_by_status(self, cutoff: datetime) -> list[StatusRow]:
        """Counts and sums the orders created at or after the cutoff, per status."""

    @abstractmethod
    def aggregate_top_users(self) -> list[TopUserRow]:
        """Returns the users with the largest total amount."""

    @abstractmethod
    def insert_one(self, order: Order) -> None:
        """Inserts one row into orders_writes, in its own transaction."""

    @abstractmethod
    def size_bytes(self) -> int:
        """Returns the size of the stored database in bytes."""

    @abstractmethod
    def is_lock_error(self, error: BaseException) -> bool:
        """Returns True when the error is a lock failure that the concurrent scenarios count."""


class SqliteEngine(Engine):
    """SQLite in WAL mode: one file, one connection, the variant's pragmas."""

    _CREATE_ORDERS: Final = (
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
        "amount_cents INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, "
        "note TEXT NOT NULL)"
    )
    _CREATE_WRITES: Final = (
        "CREATE TABLE orders_writes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
        "amount_cents INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, "
        "note TEXT NOT NULL)"
    )
    _CREATE_WRITES_INDEX: Final = "CREATE INDEX orders_writes_user_id ON orders_writes (user_id)"
    _CREATE_ORDERS_INDEX: Final = "CREATE INDEX orders_user_id ON orders (user_id)"
    _INSERT_ORDER: Final = (
        "INSERT INTO orders (id, user_id, amount_cents, status, created_at, note) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )
    _INSERT_WRITE: Final = (
        "INSERT INTO orders_writes (user_id, amount_cents, status, created_at, note) "
        "VALUES (?, ?, ?, ?, ?)"
    )
    _POINT_READ: Final = (
        "SELECT id, user_id, amount_cents, status, created_at, note FROM orders WHERE id = ?"
    )
    _USER_READ: Final = (
        "SELECT id, amount_cents, status, created_at FROM orders WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT ?"
    )
    _AGGREGATE_STATUS: Final = (
        "SELECT status, COUNT(*), SUM(amount_cents) FROM orders WHERE created_at >= ? "
        "GROUP BY status ORDER BY status"
    )
    _AGGREGATE_TOP: Final = (
        "SELECT user_id, SUM(amount_cents) AS total FROM orders GROUP BY user_id "
        "ORDER BY total DESC, user_id LIMIT ?"
    )

    def __init__(self, spec: SqliteEngineSpec) -> None:
        """Remembers the spec and its driver. Nothing is opened until connect."""
        super().__init__(spec.name, spec.queries)
        self._spec = spec
        self._driver = SqliteDriver(spec.driver)
        self._conn: SqliteConnection | None = None

    def _db(self) -> SqliteConnection:
        """Returns the open connection or raises when connect was not called."""
        if self._conn is None:
            message = f"engine {self._name} is not connected"
            raise EngineError(message)
        return self._conn

    def connect(self) -> None:
        """Opens the file and sets the pragmas and driver settings of this variant."""
        timeout_s = self._spec.busy_timeout_ms / _MS_PER_S
        conn = self._driver.connect(
            self._spec.path,
            timeout_seconds=timeout_s,
            cached_statements=self._spec.cached_statements,
        )
        conn.execute(f"PRAGMA busy_timeout = {self._spec.busy_timeout_ms}")
        conn.execute(f"PRAGMA synchronous = {self._spec.synchronous}")
        conn.execute(f"PRAGMA cache_size = -{self._spec.cache_size_kib}")
        conn.execute(f"PRAGMA mmap_size = {self._spec.mmap_size_bytes}")
        self._conn = conn

    def close(self) -> None:
        """Closes the connection if it is open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def ping(self) -> None:
        """Runs SELECT 1 on the connection."""
        self._db().execute("SELECT 1").fetchone()

    def create_schema(self) -> None:
        """Sets WAL mode, which the file keeps, then drops and recreates the tables."""
        conn = self._db()
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("DROP TABLE IF EXISTS orders")
        conn.execute("DROP TABLE IF EXISTS orders_writes")
        conn.execute(self._CREATE_ORDERS)
        conn.execute(self._CREATE_WRITES)
        conn.execute(self._CREATE_WRITES_INDEX)

    def load_batch(self, rows: Sequence[LoadRow]) -> None:
        """Inserts one batch in one explicit transaction."""
        conn = self._db()
        params = [
            (
                order_id,
                order.user_id,
                order.amount_cents,
                order.status,
                order.created_at.isoformat(),
                order.note,
            )
            for order_id, order in rows
        ]
        conn.execute("BEGIN")
        conn.executemany(self._INSERT_ORDER, params)
        conn.execute("COMMIT")

    def build_indexes(self) -> None:
        """Builds the user index on orders."""
        self._db().execute(self._CREATE_ORDERS_INDEX)

    def analyze(self) -> None:
        """Runs ANALYZE on the whole database."""
        self._db().execute("ANALYZE")

    def reset_writes(self) -> None:
        """Deletes every row of orders_writes."""
        self._db().execute("DELETE FROM orders_writes")

    def point_read(self, order_id: int) -> PointRow | None:
        """Fetches one order by id, with created_at as ISO 8601 text."""
        row: PointRow | None = self._db().execute(self._POINT_READ, (order_id,)).fetchone()
        return row

    def user_read(self, user_id: int) -> list[UserRow]:
        """Fetches the newest orders of one user."""
        rows: list[UserRow] = (
            self._db().execute(self._USER_READ, (user_id, self._queries.user_read_limit)).fetchall()
        )
        return rows

    def aggregate_by_status(self, cutoff: datetime) -> list[StatusRow]:
        """Counts and sums the orders created at or after the cutoff, per status."""
        rows: list[tuple[str, int, int]] = (
            self._db().execute(self._AGGREGATE_STATUS, (cutoff.isoformat(),)).fetchall()
        )
        return [(str(status), int(count), int(total)) for status, count, total in rows]

    def aggregate_top_users(self) -> list[TopUserRow]:
        """Returns the users with the largest total amount, ties broken by user id."""
        rows: list[tuple[int, int]] = (
            self._db().execute(self._AGGREGATE_TOP, (self._queries.top_users_limit,)).fetchall()
        )
        return [(int(user_id), int(total)) for user_id, total in rows]

    def insert_one(self, order: Order) -> None:
        """Inserts one row into orders_writes. Autocommit makes it its own transaction."""
        self._db().execute(
            self._INSERT_WRITE,
            (
                order.user_id,
                order.amount_cents,
                order.status,
                order.created_at.isoformat(),
                order.note,
            ),
        )

    def size_bytes(self) -> int:
        """Returns the size of the database file plus its WAL file."""
        path = self._spec.path
        wal = path.with_name(path.name + _WAL_SUFFIX)
        total = path.stat().st_size if path.exists() else 0
        if wal.exists():
            total += wal.stat().st_size
        return total

    def is_lock_error(self, error: BaseException) -> bool:
        """Returns True for an operational error of the driver whose text names a lock."""
        if not isinstance(error, self._driver.operational_error):
            return False
        text = str(error)
        return any(message in text for message in self._spec.lock_error_messages)


class PostgresEngine(Engine):
    """PostgreSQL over TCP with psycopg, autocommit on, one connection."""

    _CREATE_ORDERS: Final = (
        "CREATE TABLE orders (id BIGINT PRIMARY KEY, user_id INTEGER NOT NULL, "
        "amount_cents INTEGER NOT NULL, status TEXT NOT NULL, "
        "created_at TIMESTAMPTZ NOT NULL, note TEXT NOT NULL)"
    )
    _CREATE_WRITES: Final = (
        "CREATE TABLE orders_writes (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
        "user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL, "
        "created_at TIMESTAMPTZ NOT NULL, note TEXT NOT NULL)"
    )
    _CREATE_WRITES_INDEX: Final = "CREATE INDEX orders_writes_user_id ON orders_writes (user_id)"
    _CREATE_ORDERS_INDEX: Final = "CREATE INDEX orders_user_id ON orders (user_id)"
    _INSERT_ORDER: Final = (
        "INSERT INTO orders (id, user_id, amount_cents, status, created_at, note) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    _INSERT_WRITE: Final = (
        "INSERT INTO orders_writes (user_id, amount_cents, status, created_at, note) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    _POINT_READ: Final = (
        "SELECT id, user_id, amount_cents, status, created_at, note FROM orders WHERE id = %s"
    )
    _USER_READ: Final = (
        "SELECT id, amount_cents, status, created_at FROM orders WHERE user_id = %s "
        "ORDER BY created_at DESC LIMIT %s"
    )
    _AGGREGATE_STATUS: Final = (
        "SELECT status, COUNT(*), SUM(amount_cents) FROM orders WHERE created_at >= %s "
        "GROUP BY status ORDER BY status"
    )
    _AGGREGATE_TOP: Final = (
        "SELECT user_id, SUM(amount_cents) AS total FROM orders GROUP BY user_id "
        "ORDER BY total DESC, user_id LIMIT %s"
    )
    _DATABASE_SIZE: Final = "SELECT pg_database_size(current_database())"

    def __init__(self, spec: PostgresEngineSpec) -> None:
        """Remembers the spec. Nothing is opened until connect."""
        super().__init__(spec.name, spec.queries)
        self._spec = spec
        self._conn: psycopg.Connection[tuple[Any, ...]] | None = None

    def _db(self) -> psycopg.Connection[tuple[Any, ...]]:
        """Returns the open connection or raises when connect was not called."""
        if self._conn is None:
            message = f"engine {self._name} is not connected"
            raise EngineError(message)
        return self._conn

    def connect(self) -> None:
        """Opens an autocommit connection to the bench database, preparing every statement."""
        conn = psycopg.connect(self._spec.dsn, autocommit=True)
        conn.prepare_threshold = self._spec.prepare_threshold
        self._conn = conn

    def close(self) -> None:
        """Closes the connection if it is open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def ping(self) -> None:
        """Runs SELECT 1 on the connection."""
        self._db().execute("SELECT 1").fetchone()

    def create_schema(self) -> None:
        """Drops and recreates both tables and the writes index."""
        conn = self._db()
        conn.execute("DROP TABLE IF EXISTS orders")
        conn.execute("DROP TABLE IF EXISTS orders_writes")
        conn.execute(self._CREATE_ORDERS)
        conn.execute(self._CREATE_WRITES)
        conn.execute(self._CREATE_WRITES_INDEX)

    def load_batch(self, rows: Sequence[LoadRow]) -> None:
        """Inserts one batch in one transaction."""
        conn = self._db()
        params = [
            (
                order_id,
                order.user_id,
                order.amount_cents,
                order.status,
                order.created_at,
                order.note,
            )
            for order_id, order in rows
        ]
        with conn.transaction(), conn.cursor() as cursor:
            cursor.executemany(self._INSERT_ORDER, params)

    def build_indexes(self) -> None:
        """Builds the user index on orders."""
        self._db().execute(self._CREATE_ORDERS_INDEX)

    def analyze(self) -> None:
        """Runs VACUUM ANALYZE on orders, which needs autocommit."""
        self._db().execute("VACUUM ANALYZE orders")

    def reset_writes(self) -> None:
        """Empties orders_writes and restarts its identity."""
        self._db().execute("TRUNCATE orders_writes RESTART IDENTITY")

    def point_read(self, order_id: int) -> PointRow | None:
        """Fetches one order by id, with created_at as ISO 8601 text."""
        row = self._db().execute(self._POINT_READ, (order_id,)).fetchone()
        if row is None:
            return None
        return (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            str(row[3]),
            row[4].isoformat(),
            str(row[5]),
        )

    def user_read(self, user_id: int) -> list[UserRow]:
        """Fetches the newest orders of one user."""
        rows = (
            self._db().execute(self._USER_READ, (user_id, self._queries.user_read_limit)).fetchall()
        )
        return [(int(row[0]), int(row[1]), str(row[2]), row[3].isoformat()) for row in rows]

    def aggregate_by_status(self, cutoff: datetime) -> list[StatusRow]:
        """Counts and sums the orders created at or after the cutoff, per status."""
        rows = self._db().execute(self._AGGREGATE_STATUS, (cutoff,)).fetchall()
        return [(str(row[0]), int(row[1]), int(row[2])) for row in rows]

    def aggregate_top_users(self) -> list[TopUserRow]:
        """Returns the users with the largest total amount, ties broken by user id."""
        rows = self._db().execute(self._AGGREGATE_TOP, (self._queries.top_users_limit,)).fetchall()
        return [(int(row[0]), int(row[1])) for row in rows]

    def insert_one(self, order: Order) -> None:
        """Inserts one row into orders_writes. Autocommit makes it its own transaction."""
        self._db().execute(
            self._INSERT_WRITE,
            (order.user_id, order.amount_cents, order.status, order.created_at, order.note),
        )

    def size_bytes(self) -> int:
        """Returns the size of the bench database as the server reports it."""
        row = self._db().execute(self._DATABASE_SIZE).fetchone()
        if row is None:
            message = "pg_database_size returned no row"
            raise EngineError(message)
        return int(row[0])

    def is_lock_error(self, error: BaseException) -> bool:
        """PostgreSQL errors are never counted as lock errors: any failure stops the run."""
        del error
        return False


class EngineCatalog:
    """Builds the spec of every engine name the config knows."""

    def __init__(self, config: BenchConfig) -> None:
        """Remembers the config the specs are built from."""
        self._config = config

    def spec(self, name: str) -> EngineSpec:
        """Returns the spec for an engine name, or raises EngineError for an unknown name."""
        postgres = self._config.postgres
        if name == POSTGRES_ENGINE:
            return self._postgres_spec(name, postgres.dsn())
        if name == POSTGRES_UNIX_ENGINE:
            socket_dir = self._config.paths.work_dir / postgres.cluster_dir
            return self._postgres_spec(name, postgres.socket_dsn(socket_dir))
        variant = self._config.sqlite.variants.get(name)
        if variant is None:
            message = f"unknown engine name: {name}"
            raise EngineError(message)
        sqlite = self._config.sqlite
        return SqliteEngineSpec(
            name=name,
            driver=SqliteDriverSpec.for_config(self._config),
            path=self._config.paths.work_dir / sqlite.file_name,
            synchronous=variant.synchronous,
            busy_timeout_ms=variant.busy_timeout_ms,
            cache_size_kib=sqlite.cache_size_kib,
            mmap_size_bytes=sqlite.mmap_size_bytes,
            cached_statements=sqlite.cached_statements,
            lock_error_messages=sqlite.lock_error_messages,
            queries=self._config.queries,
        )

    def _postgres_spec(self, name: str, dsn: str) -> PostgresEngineSpec:
        """Returns the spec of one PostgreSQL engine, over the given connection string."""
        return PostgresEngineSpec(
            name=name,
            dsn=dsn,
            prepare_threshold=self._config.postgres.prepare_threshold,
            queries=self._config.queries,
        )


class EngineFactory:
    """Turns an engine spec into an engine object."""

    def build(self, spec: EngineSpec) -> Engine:
        """Returns an unconnected engine for the spec."""
        if isinstance(spec, SqliteEngineSpec):
            return SqliteEngine(spec)
        return PostgresEngine(spec)
