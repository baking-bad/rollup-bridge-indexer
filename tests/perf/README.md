# `tests/perf/` — matcher benchmark

```bash
make bench-matcher                                  # measured production pools, 3 passes
make bench-matcher SCALE=0.05 PASSES=1              # quick shape check
make bench-matcher LABEL=P1 OUT=perf/snapshots/bench-p1.json
make perf-db-down                                   # drop the throwaway Postgres
```

## What it measures

One matcher pass over a backlog the size of a real one. `seed.py` builds that backlog
directly — a block-window run yields pools of a few rows, and the matcher's cost is driven
by pool size, not by arrival rate.

Each step is timed and its SQL statements are counted separately. **Queries is the verdict**:
it is exact, machine-independent, and it is the thing being fixed — a step whose query count
tracks its pool size is doing one round-trip per unmatched row. Milliseconds corroborate,
and they are the only number that reflects the unindexed foreign keys those queries scan.

Postgres, not sqlite: this measures round-trips to a database server plus server-side
sequential scans, and an in-process sqlite has neither.

## What it does not measure

Correctness. The seeded backlog is a **fixed point** — nothing matches, so every pass does
identical work — which is what makes passes comparable and also means a patch that stopped
matching entirely would score beautifully here. Correctness lives in `tests/unit/matcher/`;
run both.

Two guards against believing a number that isn't there:

- `pools` runs the matcher's own queryset filters and reports what the walked pools actually
  contain. A patch that drains a pool instead of walking it cheaper shows up here.
- `fixed_point` is false if any pool changed between the first and last pass. When it is
  false the medians are not comparable and the run should be discarded.

## Reading a result

```
tezos_withdrawals   8888 queries   14153 ms      <- 8886 rows in the pool, one query each
etherlink_deposits   455 queries     344 ms
```

The `+2` is the walk itself plus its prefetch. A patch has worked when the count drops to a
small constant while `pools` stays the same size.
