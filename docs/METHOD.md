# How the benchmark works

This document is the method of the benchmark: what is measured, how, and why each choice keeps the
comparison fair. The numbers in `config/dbbench.yaml` are the only place the values live; this text
describes them. The README has the short version and the commands.

## Goal

Real, repeatable numbers that show where SQLite wins, where PostgreSQL wins, and why. Both databases
run on the same machine, on the same disk, from the same Python code, with the same schema, the same
data and the same queries.

## Fairness rules

- **One disk.** Both stores live under `paths.work_dir` (default `~/.cache/dbbench`). The guard
  refuses any work folder on a RAM filesystem (`tmpfs` or `ramfs`), because the fsync cost would not
  be measured there. It also refuses a work folder that equals, sits inside or contains a folder
  listed in `paths.protected_paths`.
- **Same memory budget.** SQLite gets a 64 MiB page cache (`sqlite.cache_size_kib: 65536`), the same
  size as PostgreSQL's `shared_buffers`.
- **Same durability.** PostgreSQL runs with `fsync` and `synchronous_commit` on. The `sqlite_wal_full`
  variant sets `synchronous=FULL`, the same durability. The `sqlite_wal_normal` variant is the usual
  production choice, and it is the main SQLite line.
- **One connection per worker, one transaction per write.** Each worker opens one persistent
  connection. Every write is its own transaction (autocommit on both drivers). The seed is the one
  exception: it puts one batch of rows in one explicit transaction.
- **Processes, not threads.** Concurrency uses processes (the `spawn` start method), so the Python
  GIL is not part of what is measured.
- **Same answers.** The two aggregate queries must return identical results on both engines. Every
  run checks this (`results_match`), and the run stops if they differ.
- **Settled disk before every run.** Each measured run starts after the disk has gone quiet (see
  "Settle", below).
- **Shuffled order and repeats.** The engine order changes from repeat to repeat, and every scenario
  is repeated five times. The tables show the median.

## The PostgreSQL server

The settings in `postgres.settings` are the production settings of the author's site: a 256 MB, 1
core server that runs PostgreSQL 18. Two changes are made for the benchmark:

- `max_connections` is 50, so the 16 workers and the runner fit.
- The binaries are PostgreSQL 18.6, built from the official source by `dbbench install` (see
  [Versions](#versions)).

`shared_buffers = 64MB` is far below the usual starting point of 25 percent of RAM. That is on
purpose: SQLite gets the same 64 MiB page cache, so both engines have the same memory to work with.

`postgres.bin_dir` is `null` by default. Then the binaries are the ones `dbbench install` built, in
`<install.cache_dir>/postgresql-18.6/bin`. Set it to a folder with your own PostgreSQL binaries to use
your own server. The tool never changes that folder.

A bench cluster made by another PostgreSQL major version is refused, because the server would not
start on its files. Move its data folder aside, then run again.

The cluster runs on port 55433, as user `bench`, database `bench`, with trust authentication. Its
data folder is `<work_dir>/pg/data`, its Unix socket folder is `<work_dir>/pg`, and its log is
`<work_dir>/pg/log`. `stop` stops this cluster only.

## Engines

| Id | Label in the results | What it is |
|---|---|---|
| `sqlite_wal_normal` | SQLite (WAL, synchronous NORMAL) | SQLite file, WAL mode, `synchronous=NORMAL`, busy timeout 5000 ms |
| `sqlite_wal_full` | SQLite (WAL, synchronous FULL, same durability as PostgreSQL) | The same file, `synchronous=FULL`, busy timeout 5000 ms |
| `sqlite_wal_nowait` | SQLite (WAL, NORMAL, no busy timeout) | The same file, `synchronous=NORMAL`, busy timeout 0: a writer that meets a lock fails at once |
| `postgres` | PostgreSQL (TCP) | PostgreSQL over TCP to `127.0.0.1:55433`, psycopg 3 |
| `postgres_unix` | PostgreSQL (Unix socket) | The same server over its Unix socket, same port number |

All SQLite variants share one file, `<work_dir>/bench.sqlite3`. The variant changes only the pragmas
of each connection. `journal_mode=WAL` is set once when the schema is created, and the file keeps it.

SQLite runs inside the benchmark process. PostgreSQL is reached over TCP. The Unix socket rows show
the same PostgreSQL server without the TCP stack, so the difference between the two rows is the cost
of the network path.

The engines each scenario measures are in `engines_by_scenario`:

| Scenario | Engines |
|---|---|
| `footprint` | postgres, sqlite_wal_normal |
| `seed` | sqlite_wal_normal, postgres |
| `single_insert` | sqlite_wal_normal, sqlite_wal_full, postgres, postgres_unix |
| `point_read` | sqlite_wal_normal, postgres, postgres_unix |
| `user_read` | sqlite_wal_normal, postgres, postgres_unix |
| `aggregate` | sqlite_wal_normal, postgres |
| `concurrent_read` | sqlite_wal_normal, postgres |
| `concurrent_write` | sqlite_wal_normal, sqlite_wal_full, sqlite_wal_nowait, postgres |
| `mixed` | sqlite_wal_normal, postgres |

## Driver settings

The drivers are set explicitly, so the defaults of a library version do not change the numbers.

- **SQLite driver.** `sqlite.driver: pysqlite3` runs the SQLite 3.54.1 that `dbbench install` built, through
  pysqlite3. `stdlib` uses Python's own `sqlite3`, which the tests use. The driver is the one class that
  chooses the module, and the lock errors are the driver's own `OperationalError`.
- **SQLite memory mapping off.** `sqlite.mmap_size_bytes: 0` is written with `PRAGMA mmap_size` on each
  connection, so reads go through the page cache, as PostgreSQL's reads do.
- **SQLite statement cache of 128.** `sqlite.cached_statements: 128` is passed to `sqlite3.connect`,
  the Python default written out.
- **PostgreSQL prepares every statement.** `postgres.prepare_threshold: 0` is set on each psycopg
  connection, so every statement is prepared from its first use, as the SQLite statement cache does.

## Settle

Before every measured run, for every scenario, engine, worker level and repeat (including each seed
repeat), the runner settles the disk. It does not settle the footprint start phase.

1. **Checkpoint.** SQLite: a new connection runs `PRAGMA wal_checkpoint(TRUNCATE)` and closes.
   PostgreSQL: `CHECKPOINT` on an autocommit connection (the bench user is a superuser).
2. **Sync.** `os.sync()` writes out the operating system's dirty pages.
3. **Wait for quiet.** The runner polls the `Dirty:` line of `/proc/meminfo` every 0.1 seconds, until
   it is at most 16384 kB or 30 seconds have passed. Where `/proc/meminfo` does not exist (macOS),
   this step is skipped.
4. **Rest.** The runner sleeps for one second.

The step is recorded in `raw.jsonl` as `settle: {dirty_kib, waited_seconds, timed_out}`.
`dirty_kib` is the last value read (null where meminfo does not exist). `waited_seconds` is the time
from the end of the sync to the start of the run, including the polling and the rest.
`timed_out` is true when the wait ended at the timeout with dirty memory still above the limit.

The writes that a run makes to `orders_writes` are reset before the settle, so the settle also flushes
the reset.

## Engine order

For each scenario, repeat and worker level, the scenario's engine list is shuffled with
`random.Random(f"{run.order_seed}:{scenario}:{repeat}:{workers}")`, where `run.order_seed` is 7. The
same seed and key always give the same order. Each raw record carries `order_position`, its place in
that order, counted from 0. The footprint scenario is not shuffled: its server start must come first,
so its records carry no order position.

## Diagnostics

Each measured record carries diagnostics, read by the runner process outside the timed window:

- `pg_checkpoints` (PostgreSQL engines): the number of checkpoints during the run. It is read from
  `pg_stat_checkpointer` (`num_timed + num_requested`) when that view exists. PostgreSQL 18 has that
  view. Servers without it are read from `pg_stat_bgwriter` (`checkpoints_timed + checkpoints_req`).
- `pg_wal_bytes` (PostgreSQL engines): the WAL written during the run, from `pg_current_wal_lsn()`
  before and after, with `pg_wal_lsn_diff`.
- `sqlite_wal_bytes` (SQLite engines): the size of the `-wal` file right after the run.

The server flushes its cumulative statistics periodically. A checkpoint at the very end of a run can
therefore show up in the next run's count.

## Scenarios

Every scenario is repeated `run.repeats` times (5 in the full run, 1 in the quick run). Count-based
scenarios run `count_warmup_ops` untimed operations first. Timed scenarios run `warmup_seconds`
untimed, then record for `duration_seconds`.

| Id | Workers | Workload | Metrics |
|---|---|---|---|
| `footprint` | 1 | Stop PostgreSQL if running, time `pg_ctl start -w`, wait 2 s, read the memory. Time a SQLite connect and `SELECT 1`. After the seed, read the sizes. After the runs, read the memory again. | start seconds, idle RSS and PSS MB (postmaster and its children, from `/proc`), SQLite connect ms, file sizes (SQLite file plus WAL, `pg_database_size`, data folder), memory after the runs |
| `seed` | 1 | Load `rows` rows in batches of `batch_size`, one explicit transaction per batch, then the index and ANALYZE | load seconds, rows per second, index seconds, analyze seconds |
| `single_insert` | 1 | `single_insert_ops` inserts, one commit each | ops/s, latency, errors |
| `point_read` | 1 | `point_read_ops` reads of one order by id | ops/s, latency, errors |
| `user_read` | 1 | `user_read_ops` reads of the newest orders of one user | ops/s, latency, errors |
| `aggregate` | 1, in the runner | Both aggregate queries, `aggregate_runs` times each, with the answers compared | median and min ms of each query, `results_match` |
| `concurrent_read` | 1, 4, 16 | Point reads for `duration_seconds` | ops/s, latency, errors |
| `concurrent_write` | 1, 4, 16 | Inserts for `duration_seconds` | ops/s, latency, errors, error rate |
| `mixed` | 16 | Each operation is a point read with probability 0.9, otherwise an insert | ops/s, latency, read p99, write p99, errors |

Latency is the time of one operation, from `time.perf_counter_ns()` before it to the same clock after
it. The worker records it into a histogram with 1 microsecond buckets up to 100 ms. Values above that
range are kept exactly in a list, so no outlier is hidden.

The reported latency metrics are p50, p90, p95, p99, p99.9 and the maximum, all in milliseconds.
Percentiles use the nearest rank: the value at rank `ceil(percent / 100 * n)`, read from the sorted
values. The p99.9 is new in this method. A value of 99.9 percent of 1000 values is the 999th value.

The queries are:

- `point_read`: `SELECT id, user_id, amount_cents, status, created_at, note FROM orders WHERE id = ?`
- `user_read`: `SELECT id, amount_cents, status, created_at FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 20`
- aggregate by status: `SELECT status, COUNT(*), SUM(amount_cents) FROM orders WHERE created_at >= ? GROUP BY status ORDER BY status`, with the cutoff half way through the span of the data
- aggregate top users: `SELECT user_id, SUM(amount_cents) AS total FROM orders GROUP BY user_id ORDER BY total DESC, user_id LIMIT 10`
- insert: one row into `orders_writes`, in its own transaction

Before every write run `orders_writes` is emptied: `DELETE FROM orders_writes` in SQLite, and
`TRUNCATE orders_writes RESTART IDENTITY` in PostgreSQL.

## Worker results

Each worker reports its successes, its errors, its elapsed time and its histograms. The runner adds
the histograms together and reads the percentiles from the sum.

- **Ops per second** is the successes divided by the elapsed time of the longest worker. A worker
  that finished early does not inflate the rate.
- **Error rate** is the errors divided by the attempts (successes plus errors).
- **A failed operation has no latency.** It counts as an error and is not in the histogram.
- **Lock errors** are counted in the concurrent scenarios for SQLite: a `database is locked` or
  `database is busy` error is an error, is not retried, and the worker goes on. Any other error stops
  the run. PostgreSQL errors are never counted as lock errors.
- **Latencies are closed loop service times.** Each worker waits for one operation to finish before it
  sends the next. A worker that stalls sends nothing during the stall, so the stall is missing from the
  tails. The tails are therefore understated. No open loop run (a fixed arrival rate) is done.
- **Oversubscription.** A worker count above the number of CPUs makes the workers wait for CPU time,
  and that wait is part of the numbers. On the author's 12 CPU machine, the 16-worker levels
  (`concurrent_read`, `concurrent_write` and `mixed`) oversubscribe it; the 1 and 4 worker levels do not.

## Repeats and the summary

Each scenario, engine and worker level is repeated `run.repeats` times. `summary.json` holds, for each
group, `metrics` (the median of the repeats), `metrics_min` and `metrics_max` (the lowest and highest
value of each metric). A boolean takes all for the median and min, and any for the max. `summary.md`
shows the medians and says, under each table, how many runs the medians come from and where the
spread is.

## Data

The generator is `random.Random(dataset.seed)` with the seed 42. Rows get ids 1 to `rows` in order.
For each row the draws happen in this fixed order:

- `user_id` is uniform from 1 to `users`.
- `amount_cents` is uniform from 100 to 50000, inclusive at both ends.
- `status` is one of `paid` (weight 70), `pending` (20), `refunded` (5) and `cancelled` (5).
- `created_at` is the start (2025-01-01T00:00:00 UTC) plus a uniform offset of whole seconds within
  365 days.
- `note` is 100 characters drawn from the lowercase letters and the space.

Because the draws happen in this order, every engine gets the same rows. The data is generated in
Python, not by the database. Read keys come from `random.Random(seed + 1)` (for worker `n`,
`seed + 1 + n`). The rows written by the write scenarios come from `seed + 1000 + n`, with the same
field rules.

The full run has 1,000,000 orders and 100,000 users. The quick run has 50,000 orders and 5,000 users.

## Schema

SQLite:

```sql
CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT NOT NULL);
CREATE TABLE orders_writes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT NOT NULL);
CREATE INDEX orders_user_id ON orders (user_id);          -- built after the load
CREATE INDEX orders_writes_user_id ON orders_writes (user_id);
```

PostgreSQL has the same tables, with `id BIGINT PRIMARY KEY` on `orders`,
`id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY` on `orders_writes`, `user_id` and
`amount_cents` as `INTEGER`, and `created_at` as `TIMESTAMPTZ`. SQLite stores `created_at` as ISO 8601
text (`YYYY-MM-DDTHH:MM:SS+00:00`), which sorts in the same order as time.

Seeding drops and recreates both tables. After the load and the index build, SQLite runs `ANALYZE` and
PostgreSQL runs `VACUUM ANALYZE orders`.

## The seed

Each seed run builds the schema, then loads every batch of `batch_size` rows in one explicit
transaction, then builds the index and runs ANALYZE. The timed part is the load (the batches), the
index and the ANALYZE. Generating the rows is not timed.

## Footprint

The footprint scenario stops PostgreSQL if it runs, starts it again and times the start. Two seconds
after the start, it reads the memory of the postmaster and its children from `/proc`, as RSS and as
PSS. It times a SQLite connect and `SELECT 1`. After the seed it reads the file sizes. After all the
runs it reads the memory again. The start phase is not settled and not shuffled.

## Commands

Run from the repository folder with `uv run dbbench <command>`:

| Command | What it does |
|---|---|
| `setup` | Creates the bench cluster if it is missing, starts it if it is stopped, creates the `bench` database |
| `seed [--quick]` | Loads the data into both engines and prints the load times. Writes no results. |
| `run [--quick] [--scenario ID ...]` | Runs the scenarios (all by default) and writes a results folder |
| `report <folder>` | Rebuilds `summary.json` and `summary.md` from a results folder |
| `charts <folder> [--png-preview DIR]` | Draws the charts of a results folder into `<folder>/charts/`: a dark and a light SVG per chart, `charts.json` with the same numbers, and `embed.md` with the README picture blocks. `--png-preview DIR` also saves a PNG of each chart in DIR, which must be outside the repository |
| `install` | Downloads PostgreSQL 18.6 and SQLite 3.54.1 from their official sources, checks each hash and builds what is missing |
| `all [--quick]` | `install`, `setup`, then every scenario in table order, then the report and the charts |
| `stop` | Stops the bench cluster in fast mode. Nothing else is stopped. |

`--quick` uses the quick overrides in the config: 50,000 orders, one repeat, 2-second windows and
smaller operation counts. It writes to a folder ending in `-quick`.

## Output

Each run writes `results/<YYYY-MM-DD>-<full|quick>/` in the repository:

- `machine.json`: the CPU model, the logical CPU count, the RAM, the kernel, the work folder and its
  filesystem, the Python, SQLite, psycopg and PostgreSQL versions, the SQLite driver and its compile options,
  the PostgreSQL configure line, the start time, the short git commit
  of the repository (null when it is not a git checkout), and the config used. Paths are written with
  the home folder shown as `~`.
- `raw.jsonl`: one line per scenario, engine, worker count and repeat, with `metrics`, `settle`,
  `order_position` and `diagnostics`.
- `summary.json`: the median, min and max of each metric over the repeats.
- `summary.md`: one table per scenario, with the engine labels, numbers with thousands separators,
  milliseconds with 3 decimals below 1 ms and 1 decimal from 1 ms up, and this method in short.
- `charts/`: the charts drawn by `charts` and at the end of `all`: `<chart>-dark.svg` and `<chart>-light.svg` for each chart, `charts.json` and `embed.md`.

## Versions

`dbbench install` builds these releases from their official sources. Every file is checked against the
hash below before it is used, and a mismatch stops the install.

| Engine | Version | Source | Hash |
|---|---|---|---|
| PostgreSQL | 18.6 | `https://ftp.postgresql.org/pub/source/v18.6/postgresql-18.6.tar.bz2` | SHA-256 `555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f` |
| SQLite | 3.54.1 | `https://www.sqlite.org/2026/sqlite-amalgamation-3540100.zip` | SHA3-256 `92281c86e2957f9b9305890f9ac04204caa7b677cc868dc326d942689df28e30` |
| pysqlite3 | 0.6.1 | `https://files.pythonhosted.org/packages/28/43/6f1f3adc88f9f77a0f49a2959f6aac5f56900da5e60c65fa6fbcc9c0afae/pysqlite3-0.6.1.tar.gz` | SHA-256 `dd7d2b7f3a29aebdbf569c359658b091d3345b5691d9ea765a5c3febe8872bf9` |

PostgreSQL is configured with `--prefix` set to its folder in the cache, plus the flags
`--without-readline --without-zlib --without-icu --without-zstd --without-lz4`, then built with `make`
and installed with `make install`. Its source tree needs bison, flex and m4 at build time. None of the
five optional libraries is used by these tests.

pysqlite3 is built against the SQLite amalgamation. Its `sqlite3.c` and `sqlite3.h` are copied into the
root of the pysqlite3 source, so its `setup.py` compiles SQLite in statically, and `uv build` makes the
wheel. Before the build, the line `('SQLITE_TEMP_STORE', 3),` is removed from `setup.py`. That setting
would force every temporary table and sort into memory. Without it SQLite keeps its default temp store,
so the sorts are not forced into memory, as they are not in a stock SQLite.

The wheel is unpacked into `<cache>/pysqlite3-sqlite-3.54.1`. Each process that uses SQLite puts that
folder first on `sys.path`, and the driver refuses a module that reports another version.

## Code

The benchmark is Python 3.12 with `uv`. Configuration is validated with pydantic and read only from
`config/dbbench.yaml`. Every number lives in that file. The code is checked with `ruff` (all rules),
`ruff format`, `mypy --strict` and `pytest`.
