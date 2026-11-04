# SQLite vs PostgreSQL results (full run)

Started at 2026-11-04T20:51:45+00:00 (UTC).

Machine: 12th Gen Intel(R) Core(TM) i7-12700K, 12 logical CPUs, 19.1 GB RAM, Linux kernel 6.6.87.2-microsoft-standard-WSL2, work folder on ext4. Python 3.12.15, SQLite 3.54.1, psycopg 3.3.8, postgres (PostgreSQL) 18.6.

Aggregate results identical on both engines: yes

## Footprint

| Phase | Engine | Measure | Value |
|---|---|---|---|
| start | PostgreSQL (TCP) | start_seconds | 0.107 |
| start | PostgreSQL (TCP) | idle_rss_mb | 43.8 |
| start | PostgreSQL (TCP) | idle_pss_mb | 16.0 |
| sizes | PostgreSQL (TCP) | pg_database_bytes | 206,976,703 |
| sizes | PostgreSQL (TCP) | data_dir_bytes | 683,555,782 |
| after_runs | PostgreSQL (TCP) | rss_mb | 285.6 |
| after_runs | PostgreSQL (TCP) | pss_mb | 81.7 |
| start | SQLite (WAL, synchronous NORMAL) | sqlite_connect_ms | 0.451 |
| sizes | SQLite (WAL, synchronous NORMAL) | sqlite_file_bytes | 164,655,104 |

Median of 5 runs. Spread: see summary.json (min and max).

## Seed (load of the data set)

| Engine | Workers | Load s | Rows/s | Index s | Analyze s |
|---|---|---|---|---|---|
| SQLite (WAL, synchronous NORMAL) | 1 | 1.914 | 522,416 | 0.391 | 0.035 |
| PostgreSQL (TCP) | 1 | 10.851 | 92,160 | 0.507 | 0.184 |

Median of 5 runs. Spread: see summary.json (min and max).

## Single insert (1 worker, one commit each)

| Engine | Workers | Ops/s | p50 ms | p90 ms | p95 ms | p99 ms | p99.9 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| SQLite (WAL, synchronous NORMAL) | 1 | 34,425 | 0.013 | 0.017 | 0.021 | 0.048 | 4.6 | 5.6 |
| SQLite (WAL, synchronous FULL, same durability as PostgreSQL) | 1 | 1,140 | 0.722 | 1.5 | 1.5 | 2.2 | 5.7 | 6.9 |
| PostgreSQL (TCP) | 1 | 1,191 | 0.828 | 0.919 | 0.970 | 1.1 | 5.3 | 5.4 |
| PostgreSQL (Unix socket) | 1 | 1,184 | 0.823 | 0.921 | 0.973 | 1.1 | 5.3 | 5.6 |

Median of 5 runs. Spread: see summary.json (min and max).

## Point read by id (1 worker)

| Engine | Workers | Ops/s | p50 ms | p90 ms | p95 ms | p99 ms | p99.9 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| SQLite (WAL, synchronous NORMAL) | 1 | 168,617 | 0.004 | 0.006 | 0.006 | 0.015 | 0.074 | 0.252 |
| PostgreSQL (TCP) | 1 | 11,619 | 0.071 | 0.125 | 0.151 | 0.224 | 0.336 | 1.5 |
| PostgreSQL (Unix socket) | 1 | 11,732 | 0.068 | 0.124 | 0.144 | 0.225 | 0.319 | 1.4 |

Median of 5 runs. Spread: see summary.json (min and max).

## Newest orders of one user (1 worker)

| Engine | Workers | Ops/s | p50 ms | p90 ms | p95 ms | p99 ms | p99.9 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| SQLite (WAL, synchronous NORMAL) | 1 | 36,045 | 0.024 | 0.035 | 0.042 | 0.075 | 0.181 | 0.296 |
| PostgreSQL (TCP) | 1 | 8,216 | 0.108 | 0.165 | 0.195 | 0.277 | 0.372 | 0.619 |
| PostgreSQL (Unix socket) | 1 | 8,672 | 0.104 | 0.152 | 0.183 | 0.257 | 0.374 | 0.806 |

Median of 5 runs. Spread: see summary.json (min and max).

## Aggregates (single client, median and min of each query)

| Engine | Workers | Status median ms | Status min ms | Top users median ms | Top users min ms |
|---|---|---|---|---|---|
| SQLite (WAL, synchronous NORMAL) | 1 | 151.4 | 144.9 | 1,130.3 | 1,113.8 |
| PostgreSQL (TCP) | 1 | 130.2 | 127.6 | 1,778.7 | 1,713.3 |

Median of 5 runs. Spread: see summary.json (min and max).

## Concurrent point reads

| Engine | Workers | Ops/s | p50 ms | p90 ms | p95 ms | p99 ms | p99.9 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| SQLite (WAL, synchronous NORMAL) | 1 | 181,018 | 0.004 | 0.005 | 0.006 | 0.012 | 0.061 | 0.375 |
| SQLite (WAL, synchronous NORMAL) | 4 | 640,639 | 0.005 | 0.006 | 0.007 | 0.014 | 0.055 | 0.865 |
| SQLite (WAL, synchronous NORMAL) | 16 | 858,069 | 0.011 | 0.016 | 0.019 | 0.031 | 4.0 | 13.7 |
| PostgreSQL (TCP) | 1 | 11,295 | 0.071 | 0.133 | 0.162 | 0.242 | 0.330 | 2.1 |
| PostgreSQL (TCP) | 4 | 33,488 | 0.112 | 0.142 | 0.166 | 0.231 | 0.329 | 2.1 |
| PostgreSQL (TCP) | 16 | 167,088 | 0.078 | 0.186 | 0.237 | 0.359 | 0.593 | 19.8 |

Median of 5 runs. Spread: see summary.json (min and max).

## Concurrent inserts

| Engine | Workers | Ops/s | p50 ms | p90 ms | p95 ms | p99 ms | p99.9 ms | max ms | Errors | Error rate |
|---|---|---|---|---|---|---|---|---|---|---|
| SQLite (WAL, synchronous NORMAL) | 1 | 24,382 | 0.014 | 0.021 | 0.032 | 0.139 | 7.9 | 14.4 | 0 | 0.00% |
| SQLite (WAL, synchronous NORMAL) | 4 | 43,711 | 0.017 | 0.033 | 0.048 | 0.147 | 3.6 | 3,233.0 | 0 | 0.00% |
| SQLite (WAL, synchronous NORMAL) | 16 | 40,756 | 0.018 | 0.039 | 0.057 | 1.8 | 12.7 | 4,734.9 | 4 | 0.00% |
| SQLite (WAL, synchronous FULL, same durability as PostgreSQL) | 1 | 1,097 | 0.728 | 1.5 | 1.6 | 2.7 | 6.7 | 21.0 | 0 | 0.00% |
| SQLite (WAL, synchronous FULL, same durability as PostgreSQL) | 4 | 1,390 | 0.722 | 1.5 | 1.6 | 2.6 | 630.9 | 3,633.5 | 1 | 0.01% |
| SQLite (WAL, synchronous FULL, same durability as PostgreSQL) | 16 | 1,081 | 0.739 | 1.5 | 1.7 | 36.5 | 2,932.5 | 5,009.1 | 5 | 0.04% |
| SQLite (WAL, NORMAL, no busy timeout) | 1 | 25,073 | 0.014 | 0.021 | 0.031 | 0.141 | 7.6 | 15.1 | 0 | 0.00% |
| SQLite (WAL, NORMAL, no busy timeout) | 4 | 48,373 | 0.022 | 0.043 | 0.054 | 0.117 | 3.6 | 35.5 | 1,123,358 | 69.45% |
| SQLite (WAL, NORMAL, no busy timeout) | 16 | 28,059 | 0.036 | 0.067 | 0.083 | 0.141 | 12.3 | 65.9 | 4,877,119 | 94.56% |
| PostgreSQL (TCP) | 1 | 1,136 | 0.852 | 0.964 | 1.0 | 1.3 | 5.4 | 9.1 | 0 | 0.00% |
| PostgreSQL (TCP) | 4 | 2,732 | 1.4 | 1.6 | 1.7 | 2.9 | 6.2 | 18.9 | 0 | 0.00% |
| PostgreSQL (TCP) | 16 | 7,662 | 1.5 | 5.6 | 5.9 | 6.3 | 8.7 | 18.4 | 0 | 0.00% |

Median of 5 runs. Spread: see summary.json (min and max).

## Mixed workload (point reads and inserts in one pool)

| Engine | Workers | Ops/s | Read p99 ms | Write p99 ms | Errors |
|---|---|---|---|---|---|
| SQLite (WAL, synchronous NORMAL) | 16 | 168,543 | 0.024 | 18.3 | 0 |
| PostgreSQL (TCP) | 16 | 52,564 | 0.357 | 5.7 | 0 |

Median of 5 runs. Spread: see summary.json (min and max).

## How it was measured

- Both stores sit on the same disk under the work folder. A RAM filesystem is refused, because it would hide the fsync cost.
- PostgreSQL runs with the production settings of the author's site (a 256 MB, 1 core server running PostgreSQL 18), on PostgreSQL 18.6 built from the official source. max_connections is raised so 16 workers fit. fsync and synchronous_commit stay on.
- SQLite runs in WAL mode with a 64 MiB page cache, the same memory budget as PostgreSQL shared_buffers. SQLite 3.54.1 is built from the official amalgamation and used through pysqlite3. synchronous NORMAL is the usual production choice; FULL matches the durability of PostgreSQL.
- Each worker is a separate process with one persistent connection. Every write is its own transaction. The seed puts one batch of rows in one explicit transaction.
- Before every measured run the disk is settled: a checkpoint, an operating system sync, a wait for Dirty memory to fall, and a rest of one second. Each run's settle record is in raw.jsonl.
- The engine order is shuffled for each scenario, repeat and worker level, from a fixed seed, so no engine always goes first. The position is in raw.jsonl.
- Each scenario is repeated, every repeat starts after an untimed warmup, and the tables show the median. summary.json also has the min and max of each metric.
- Latency is taken per operation with a nanosecond clock and kept in a histogram with 1 microsecond buckets up to 100 ms. Percentiles use the nearest rank. A failed operation has no latency.
- Ops per second is the successes divided by the longest worker's measured time. The error rate is errors divided by attempts.
- Latencies are closed loop service times: a worker waits for each operation before it sends the next one. A stalled worker sends nothing, so the tails are understated. No open loop run is done.
- Worker counts above the CPU count oversubscribe the machine, and the concurrent numbers include that contention.
- SQLite runs inside the benchmark process. PostgreSQL is reached over TCP, and the Unix socket rows show the same server without the TCP stack.
- The two aggregate queries must return identical results on both engines. This is checked on every run (results_match), and the run stops if they differ.
- In the concurrent scenarios a SQLite lock error (database is locked or busy) is counted, not retried, and has no latency.
- Diagnostics in raw.jsonl: PostgreSQL checkpoints and WAL bytes, and the SQLite WAL file size after each run. They are read outside the timed window. The server flushes its statistics periodically, so a checkpoint near the end of a run may show in the next one.
