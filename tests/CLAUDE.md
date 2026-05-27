# tests/ — split by PURPOSE, not just by test type

Three suites with different contracts. Put a test where its *purpose* fits, not just its
shape. The whole `tests/` tree is excluded from the prod image (`.dockerignore: tests/**`).

## `tests/unit/` — isolated unit tests
- **Purpose:** fast, deterministic pytest over pure logic — decoders, commitment/rollup math,
  type (de)serialization.
- **Belongs:** anything testable with in-memory fixtures; group by area (`decoder/`,
  `rollup/`, `types/`).
- **Does NOT belong:** anything that boots the indexer, hits TzKT/RPC, or needs a DB.
- **Run:** `make test` (pinned via `testpaths=["tests/unit"]`). Runs anywhere, no secrets;
  should run in CI.

## `tests/e2e/` — production-readiness gate
- **Purpose:** script-based checks that **the current repo state is shippable** — the
  pass/fail gate before publish/redeploy.
- **Belongs:** hermetic checks that exit 0/1 — the Docker smoke test (`smoke_test.sh`) and the
  full sequence `run_all.sh` (black/ruff/mypy check + unit + docker smoke + image-hygiene
  audit). `smoke.env` is **dummy/public values only — never real secrets**.
- **Does NOT belong:** anything needing real secrets, manual input, or that is
  non-deterministic.
- **Run:** `make prod-check` (full) / `make docker-test` (docker only). CI runs the hermetic
  smoke as the publish gate.

## `tests/stand/` — manual bug-repro harness (NOT a gate)
- **Purpose:** a block-bounded, throwaway-sqlite test indexer to reproduce/verify specific
  bridge bugs against real testnet data, without touching prod.
- **Belongs:** the standalone test config, the `*.env.default` template, the sqlite inspector,
  `TESTING.md`.
- **Does NOT belong:** anything expected to pass/fail automatically or run in CI.
- **Constraints:** **requires real deploy secrets** (`TEST_ENV` →
  `/home/ubuntu/deployments/.../.env.tezosx-shadownet`), a hand-picked block window, and
  produces sqlite for human inspection. The local `*.env` is gitignored — copy it from
  `*.env.default`.
- **Run:** `make test-indexer` → `make inspect-test`; `make check-test-config` validates.
