# tests/ — split by PURPOSE, not just by test type

Three suites with different contracts. Put a test where its *purpose* fits, not just its
shape. The whole `tests/` tree is excluded from the prod image (`.dockerignore: tests/**`).

## `tests/unit/` — isolated unit tests
- **Purpose:** fast, deterministic pytest over pure logic — decoders, commitment/rollup math,
  type (de)serialization, and the bridge-matcher harness (`matcher/`: real models + real
  matcher steps over in-memory sqlite; `conftest.py` registers DipDup's TransactionManager,
  `factories.py` builds handler-shaped rows and runs one batch pass).
- **Belongs:** anything testable with in-memory fixtures (in-memory sqlite included); group
  by area (`decoder/`, `rollup/`, `types/`, `matcher/`).
- **Does NOT belong:** anything that boots the indexer, hits TzKT/RPC, or needs external
  infrastructure (real postgres, network).
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
  bridge behaviours against real testnet data, without touching prod.
- **Layout:** a **per-case** harness under `cases/<name>/` — each case is a directory holding
  `config.yaml` (standalone DipDup config) + `window.env` (block window + `SQLITE_PATH`) +
  `verify.py` (case verdict) + `README.md`. Shared, committed, **secret-free** pieces:
  `tezosx.env` (public endpoints + addresses) and `verify_lib.py` (sqlite plumbing + `Verdict`).
  See `tests/stand/README.md`.
- **Does NOT belong:** anything expected to pass/fail automatically or run in CI.
- **Constraints:** **secret-free** — everything (sqlite, public testnet endpoints, addresses,
  block windows) is committed, so any setup reproduces a case with no extra files. Each
  `cases/*/config.yaml` is a **standalone copy, not an overlay** on `configs/`: a fix to a prod
  config must be mirrored into every affected case config by hand. `<name>` must be a valid
  Python identifier (underscores, no dashes) — `inspect-test` runs the verifier as a module.
- **Run:** `make test-indexer CASE=<name>` → `make inspect-test CASE=<name>`;
  `make check-test-config CASE=<name>` validates. These load `tezosx.env` + the case
  `window.env` via `dipdup -e` (DipDup parses the env files itself — do not shell-source them).
