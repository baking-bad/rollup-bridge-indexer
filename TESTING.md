# Stripped test indexer — tezosx-shadownet Tezos→EVM deposits

A block-bounded, throwaway-sqlite indexer to reproduce/verify the bug where **Tezos→EVM
deposits are not indexed on the new tezosx rollup**, without touching prod. It runs the
real handlers/config over a tight block window, exits on its own (DipDup oneshot), and
dumps the deposit tables so a fix can be verified by re-running.

## TL;DR

```bash
make test-indexer   # syncs the window into sqlite, then exits
make inspect-test   # prints deposit-table report + verdict
```

- **Buggy baseline (current code):** `l2_deposit = 0`, `bridge_operation` stuck at `CREATED` (l1 ✓, inbox ✓, l2 ✗).
- **After the fix should be:** `l2_deposit = 1` and a matched/advanced `bridge_operation`.

## The bug (to fix later)

XTZ deposits land on L2 as a synthetic tx with `from = 0x0000…feed` (FEED_DEPOSIT_ADDR) in
the new tezosx (REVM) kernel — **not** the old `0x0000…0000`. But:
- `rollup_bridge_indexer/configs/tezosx-shadownet.yaml` → index `etherlink_xtz_deposit_transactions`
  filters `from_: etherlink_rollup_kernel` (= `0x0000…0000`), so DipDup never even delivers
  the tx (`on_xtz_deposit` is never called).
- `rollup_bridge_indexer/handlers/etherlink/on_xtz_deposit.py` `_validate_xtz_transaction` also
  hardcodes `transaction.from_ == '0x0000…0000'`.

Precompile address map: see the `reference_etherlink-kernel-precompiles` memory note.
**When fixing, apply the address change to BOTH `tezosx-shadownet.yaml` and the test copy
`tezosx-shadownet-test.yaml`** (the test config is standalone, not layered).

## Files

| File | Role | Tracked |
|---|---|---|
| `rollup_bridge_indexer/configs/tezosx-shadownet-test.yaml` | Standalone config: sqlite, no hasura/sentry/`tezos_head`, `last_level` on every index (→ oneshot exit) | yes |
| `tezosx-shadownet-test.env.default` | Template: block window + rollup backfill window + sqlite path | yes |
| `tezosx-shadownet-test.env` | Your local copy with real levels | no (gitignored) |
| `verify_test_indexer.py` | stdlib-sqlite inspector / verdict | yes |
| `Makefile` | `test-indexer`, `inspect-test`, `check-test-config` targets | yes |
| `rollup_bridge_indexer/handlers/rollup_message.py` | env-gated `ROLLUP_SYNC_FIRST/LAST_LEVEL` (prod-safe, unset in prod) | yes |

## How it works / gotchas (already handled)

- **Config source:** `make test-indexer` sources the prod env (`TEST_ENV`, default
  `/home/ubuntu/deployments/stacks/etherlink-bridge-indexer/.env.tezosx-shadownet`) for
  URLs/addresses, then `tezosx-shadownet-test.env` for the window. Secrets are never printed.
- **Oneshot exit** requires every index to have `last_level` and NO `tezos.head` index — the
  test config satisfies both, so `dipdup run` syncs the window and exits (no Ctrl+C needed).
- **Package path:** a single `-c` from a subdir breaks DipDup package autodetection, so the
  Makefile exports `DIPDUP_PACKAGE_PATH=$(CURDIR)/rollup_bridge_indexer` (same trick as the Dockerfile).
- **Rollup backfill bound:** `on_restart` backfills the rollup inbox from origination —
  ~18M messages on tezosx-shadownet (hours). It is NOT bounded by the dipdup index levels.
  `ROLLUP_SYNC_FIRST_LEVEL`/`ROLLUP_SYNC_LAST_LEVEL` (read in `rollup_message.py`) bracket it
  so the test finishes in seconds. Prod leaves these unset → unchanged behavior.
- **sqlite** works (the only raw SQL is one plain INSERT). DB is wiped each `make test-indexer`.

## Current window — Candidate A (9985 XTZ)

Deposit: L1 op `opUAXNFmxdQo4y1ySFmkmgLAzjo8w2jwMyn5JArEKnrgVfZ84eC`, L1 level **3428081**,
recipient `0x8aad6553cf769aa7b89174be824ed0e53768ed70`, lands on L2 block **388226**
(L2 tx `from=0x…feed`). `tezosx-shadownet-test.env` is set to:

```
L1_FIRST_LEVEL=3428078
L1_LAST_LEVEL=3428084
L2_FIRST_LEVEL=388221
L2_LAST_LEVEL=388231
ROLLUP_SYNC_FIRST_LEVEL=3428070   # brackets the deposit's inbox transfer (L1 3428081)
ROLLUP_SYNC_LAST_LEVEL=3428090
SQLITE_PATH=/tmp/bridge_tezosx_test.sqlite
```

Backups (different deposits) and the discovery method are in the `project_tezosx-test-indexer-stand`
memory note. To target another deposit: find its L1 `default`-entrypoint op level on TzKT and
its L2 block by timestamp, then set the four `*_LEVEL` windows + the two `ROLLUP_SYNC_*` levels
around the L1 deposit level.

## Verified baseline (2026-05-25, buggy code)

```
l1_deposit            1     (lvl 3428081, 9985000000 mutez, recipient 8aad…ed70)
rollup_inbox_message  3     (bounded backfill: 2 window msgs + sentinel)
l2_deposit            0     <-- bug reproduced
bridge_operation      1     status=CREATED  l1=True inbox=True l2=False
```
