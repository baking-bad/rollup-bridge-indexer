# `tests/perf/` — what a matcher pass costs

Two instruments, for two different questions.

```bash
make bench-matcher                                          # per-step cost, measured pools, 3 passes
make bench-matcher SCALE=0.05 PASSES=1                      # quick shape check
make bench-matcher LABEL=x OUT=tests/perf/snapshots/x.json  # keep the raw numbers
make perf-db-down                                           # drop the throwaway Postgres

python3 tests/perf/baseline.py 300 --url <graphql>          # throughput of a deployed instance
```

`bench_matcher.py` answers *what does one pass cost, step by step* — locally, on a seeded
backlog, against Postgres. `baseline.py` answers *is the deployed instance getting more done*
— over GraphQL, against whatever backlog that instance really has. Neither replaces the other:
the bench cannot see a deployment, and the deployed instance cannot be asked per step.

Output under `snapshots/` is gitignored: it is what one machine measured at one moment.

## What the bench measures

One matcher pass over a backlog the size of a real one. `seed.py` builds that backlog
directly — a block-window run yields pools of a few rows, and the matcher's cost is driven
by pool size, not by arrival rate.

Each step is timed and its SQL statements are counted separately. **Queries is the verdict**:
it is exact, machine-independent, and it is the thing being watched — a step whose query count
tracks its pool size is doing one round-trip per unmatched row. Milliseconds corroborate, and
they are the only number that reflects the unindexed foreign keys those queries scan.

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

A query count that tracks the pool means the step asks the database once per walked row. The
count should be a small constant instead, with `pools` unchanged — same work, same backlog,
one round trip for the whole counter side.

## The measurement this suite was built for

Pools as seeded (the defaults in `seed.py`, measured on etherlink-bridge-mainnet-staging on
2026-08-05, 28 hours into a reindex): 8886 tezos withdrawals, 2656 xtz deposits, 950 claimed
fast payouts, 453 etherlink deposits, against counter sides of 26 and 16 rows.

Per-row lookups against pools batched into one query per counter side, 3 passes, `fixed_point`
true on both runs:

| step | queries | | ms | |
|---|--:|--:|--:|--:|
| | **per row** | **batched** | **per row** | **batched** |
| `tezos_withdrawals` | 8888 | 2 | 14174 | 184 |
| `etherlink_xtz_deposits` | 2660 | 7 | 2632 | 81 |
| `claimed_fast_withdrawals` | 952 | 3 | 686 | 55 |
| `etherlink_deposits` | 455 | 4 | 397 | 32 |
| `tezos_deposits`, `inbox`, `michelson_deposits`, `outbox`, `etherlink_withdrawals` | 1 each | 1 each | | |
| **whole pass** | **12960** | **21** | **17939** | **413** |

The headline is not the ratio but the change of class: the cost was linear in the backlog
(12960 queries against a backlog of 12945 rows) and is now constant. `inbox`, `outbox` and
`michelson_deposits` read 1 query here because this seed leaves their pools empty — they are
the whole deposit and withdrawal backlog when starting from an empty database, and their
counter side is read lazily, on the first candidate actually asked for.

## Known and not done

`bridge_withdrawal.outbox_message_id` and `bridge_deposit.inbox_message_id` carry no index, so
any query filtering on them is a sequential scan — that is most of the milliseconds in the
"per row" column above. Adding the indexes changes the schema
(`advanced.reindex.schema_modified: exception` ⇒ a production reindex), and no hot query
filters on those columns any more, so it is worth doing only alongside a planned reindex.
