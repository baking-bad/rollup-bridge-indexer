# Production-readiness gate (`tests/e2e/`)

The script-based checks that say **"this repo state is shippable"**. Run the whole sequence
before publishing/redeploying:

```bash
make prod-check          # the full sequence below (non-mutating — asserts, doesn't fix)
make prod-check BOOT=1   # also boots the indexer against throwaway Postgres
```

`make prod-check` runs `run_all.sh`, which executes each check in order and stops at the
first failure:

| # | Check | Script / command | What it proves |
|---|---|---|---|
| 1 | Formatting | `black --check` | code is formatted (`make black` fixes) |
| 2 | Lint | `ruff check` | lint clean (`make ruff` fixes) |
| 3 | Types | `mypy` | type-checks |
| 4 | Unit | `pytest tests/unit` | unit suite passes |
| 5 | Docker smoke | `smoke_test.sh` | image builds; package resolves as `rollup_bridge_indexer`; `package verify` passes (runs `initialize()` as the runtime user + imports handlers/hooks/types); every deployed overlay (`mainnet`, `shadownet`, `tezosx-shadownet`) validates with the *exact* prod command form; **no test code / tool caches shipped** in the image; with `--boot`, the indexer boots against throwaway Postgres through schema init + `on_reindex`. |

Step 5 alone is also available as `make docker-test` (the CI publish gate in
`.github/workflows/build.yml` runs the hermetic form — steps inside 5, no `--boot`).

`smoke.env` is a fixture of dummy/public values consumed by `smoke_test.sh` (allowlisted in
`.gitignore`; it is NOT a real secret env).

## What is NOT here

`tests/stand/` — the block-bounded, per-case test-indexer harness — is **not** a readiness
gate. Each case picks a hand-picked block window and produces a sqlite for human inspection
rather than a pass/fail result. It's a manual bug-repro tool; see `tests/stand/README.md`.

## Adding a check

Drop a `*.sh` here that exits non-zero on failure, add a step to `run_all.sh` (and a row
above), and — if it should gate releases in CI — wire it into `.github/workflows/build.yml`.
