# tests/stand — block-bounded test-indexer stand

A manual, **block-bounded, throwaway-sqlite** indexer for reproducing/verifying specific
bridge behaviours against real testnet data, without touching prod. Each case runs the real
handlers over a tight block window, exits on its own (DipDup oneshot), and dumps tables so a
fix/feature can be verified by re-running. **Not** a CI gate.

## Layout

```
tests/stand/
  tezosx.env              # shared, committed, non-secret env (public endpoints + addresses)
  verify_lib.py           # shared sqlite plumbing + Verdict helper for case verifiers
  cases/<name>/
    config.yaml           # standalone DipDup config: sqlite, no hasura/sentry/tezos_head,
                          #   last_level on every index (=> oneshot exit)
    window.env            # committed block-window values for this case (+ SQLITE_PATH)
    verify.py             # case-specific pass/fail verdict (imports verify_lib)
    README.md             # what this case reproduces (the on-chain ops, expected result)
```

Everything is committed and **secret-free** (sqlite, public testnet endpoints, no API
keys/passwords), so any setup can reproduce a case with no extra files.

## Run

```bash
make test-indexer  CASE=<name>   # wipe sqlite, sync the window, exit
make inspect-test  CASE=<name>   # run the case verdict (PASS/FAIL per check)
make check-test-config CASE=<name>   # validate the config exports
```

These load `tezosx.env` + `cases/<name>/window.env` via `dipdup -e` (DipDup parses the env
files itself — do not shell-source them).

## Add a case

1. `mkdir tests/stand/cases/<name>` and add `config.yaml` (standalone; copy an existing case
   and adjust datasources/contracts/indexes). Keep it oneshot: no `tezos.head` index and a
   `last_level` on every index. **`<name>` must be a valid Python identifier (underscores,
   no dashes)** — `inspect-test` runs the verifier as a module (`python -m
   tests.stand.cases.<name>.verify`).
2. Add `window.env` with the block windows + `SQLITE_PATH=/tmp/bridge_<name>.sqlite`. To find
   a window: locate the op on TzKT, bracket its level on each chain (keep it small, <10
   blocks), and bracket the rollup inbox level with `ROLLUP_SYNC_FIRST/LAST_LEVEL` (the
   `on_restart` rollup backfill is NOT bounded by index levels — see `handlers/rollup_message.py`).
3. Add `verify.py` that dumps the relevant tables and declares expectations with
   `verify_lib.Verdict` (import it as `from tests.stand import verify_lib as lib`).
4. Add `README.md` describing the reproduced behaviour and the expected verdict.
