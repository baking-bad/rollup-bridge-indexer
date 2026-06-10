# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Etherlink Bridge Indexer — a DipDup-based Python indexer that tracks bridge operations between Tezos (L1) and Etherlink (L2, an EVM-compatible smart rollup on Tezos). It reconciles events from both chains into unified `BridgeOperation` records.

## Commands

```bash
make test          # Run tests: PYTHONPATH=. uv run pytest tests/
make lint          # Run all linters: black, ruff, mypy
make black         # Format code with Black
make ruff          # Lint with Ruff (auto-fix)
make mypy          # Type check with mypy
make run           # Run indexer: uv run dipdup -c . run
make up            # Docker compose up (postgres + hasura)
make down          # Docker compose down with volumes
make wipe          # Wipe DipDup schema
make init          # Initialize DipDup
```

Run a single test: `PYTHONPATH=. uv run pytest tests/path/to/test_file.py::test_name`

Package manager is `uv`. Python 3.12 required.

## Code Style

- **Line length:** 140
- **Quotes:** Single quotes (`skip-string-normalization = true` in Black)
- **Imports:** Force single-line (ruff isort)
- **Ruff extends:** B, C4, FA, G, I, PTH, Q, RET, RUF, TCH, UP
- **Target:** Python 3.12

## Architecture

### DipDup Framework

The indexer uses [DipDup](https://dipdup.io/) v8.5.1+. Configuration lives in `./dipdup.yaml` (base) with network-specific overlays in `./configs/` (mainnet, ghostnet, quebecnet, rainbownet, shadownet, tezosx-shadownet). DipDup manages the Tortoise ORM models, Hasura GraphQL metadata, and event subscription lifecycle.

### Package layout (canonical DipDup, flattened)

The DipDup package **is the repo root** — `dipdup.yaml`, `handlers/`, `models/`, `hooks/`, `types/`, `configs/`, etc. live directly at the root (not in a subdirectory). The Python **package import name is `rollup_bridge_indexer`** (the `[project].name` distribution is the hyphenated `rollup-bridge-indexer`). Importability comes from the committed self-symlink **`rollup_bridge_indexer -> .`** at the root, so `import rollup_bridge_indexer.*` resolves; it is also editable-installed via `[build-system]`.

`package` in `dipdup.yaml` (`rollup_bridge_indexer`) MUST differ from `[project].name` (`rollup-bridge-indexer`): DipDup's `get_package_path()` returns `cwd` when `package == pyproject-name == cwd-name`, which previously made `dipdup init` scaffold an empty skeleton at the wrong place. With distinct names and the package at the root, `dipdup -c . <cmd>` (and `make init/run/wipe`) resolve the package to the repo root correctly. mypy must keep `exclude = "rollup_bridge_indexer"` (skip the self-symlink, else infinite recursion); black/ruff exclude it too.

**Docker packaging (`Dockerfile`):** DipDup derives the package *name* from the **basename of `DIPDUP_PACKAGE_PATH`**, and `package.initialize()` runs on **every** command, writing marker files (`py.typed`, `**/.keep`) at the package root. So in the image two things are load-bearing: (1) `DIPDUP_PACKAGE_PATH=/opt/app/rollup_bridge_indexer` points at the committed self-symlink (basename = real package name) — pointing at `/opt/app` would name the package `app` and break every `rollup_bridge_indexer.*` import; (2) `/opt/app` is `chown`ed to the runtime user so `initialize()` can write (the venv `COPY` only chowns *contents*, leaving the dir root-owned → `PermissionError`). Config files stay at `/opt/app` (WORKDIR), so the deployment compose's absolute `-c /opt/app/dipdup.yaml -c /opt/app/configs/<net>.yaml` still works. `tests/e2e/smoke_test.sh` (`make docker-test`, also a CI gate) guards all this by booting the indexer in the built image.

### Test layout (`tests/`)

Tests are split by purpose and kept out of the prod image (`.dockerignore` excludes `tests/**`):
- `tests/unit/` — pytest suite (decoder, rollup, types); `pyproject.toml` pins `testpaths=["tests/unit"]`. `make test`.
- `tests/e2e/` — production-readiness gate. `make prod-check [BOOT=1]` runs `run_all.sh` = the full sequence (black/ruff/mypy check + unit + docker smoke). The docker smoke (`smoke_test.sh` + `smoke.env`) is also `make docker-test`; its hermetic form is the CI publish gate. It builds the image, runs `package verify`, validates every deployed overlay, and audits that no test code/caches ship in the image.
- `tests/stand/` — block-bounded, secret-free **per-case** test-indexer stand: each case is `tests/stand/cases/<name>/` (`config.yaml` + `window.env` + `verify.py` + `README.md`); shared committed `tezosx.env` (public endpoints/addresses) + `verify_lib.py`. Each `config.yaml` is standalone (resolves the package via the editable install, so it lives outside `configs/`) and is a copy, not an overlay — mirror prod-config fixes into it. `make test-indexer CASE=<name>` / `inspect-test CASE=<name>` / `check-test-config CASE=<name>` (load envs via `dipdup -e`). See `tests/stand/README.md`.

### Dual-Chain Event Processing

- **Tezos (L1) handlers** in `./handlers/tezos/`: deposit calls (`on_rollup_call`), withdrawal executions (`on_rollup_execute`), commitment cementing (`on_cement_commitment`), fast withdrawal claims, head tracking. Also `on_michelson_deposit` — Tezos-shaped but records the **L2** leg of a Tezos X Michelson (tz1-receiver) XTZ deposit
- **Etherlink (L2) handlers** in `./handlers/etherlink/`: deposit events (`on_deposit`, `on_xtz_deposit`), withdrawal events (`on_withdraw`, `on_xtz_withdraw`), ERC-20 transfers (`on_transfer`)

### Bridge Matcher (core reconciliation)

`./handlers/bridge_matcher.py` is the central matching engine. It correlates L1 and L2 operations into unified `BridgeOperation` records through 8 ordered matching steps. Uses a **lock-based batching system** (`bridge_matcher_locks.py`): handlers set boolean flags, and the batch handler (`batch.py`) checks and clears them after each handler batch.

A ninth, deliberately **separated** step lives in `./handlers/michelson_matcher.py`: Tezos X L2 Michelson (tz1-receiver) XTZ deposits are matched by reconstructing the L2 synthetic-op hash from L1 inbox data (`./handlers/michelson_deposit.py`, kernel-verified derivation) because TzKT drops the kernel's implicit-source deposit event. It is an interim mechanism — when TzKT serves those events, delete the module + its lock and let these deposits flow through the regular inbox-coords step (the future path is kept alive in `./handlers/tezos_x/on_michelson_deposit.py` + the `michelson_l2_deposit` stand case).

### Parameter Hash Matching

L1 operations and L2 events are correlated via deterministic parameter hashes: `uuid5(NAMESPACE_OID, orjson.dumps(params, OPT_SORT_KEYS))`. Inbox messages match deposits; outbox messages match withdrawals.

### Rollup Message Index

`./handlers/rollup_message.py` is a custom indexer for rollup inbox/outbox messages (not natively supported by DipDup). Has its own sync lifecycle (new → syncing → realtime). Fetches inbox messages from TzKT API and outbox messages from the rollup node RPC.

### Service Container

`./handlers/service_container.py` provides dependency injection. Instantiated during `on_restart`/`on_reindex` hooks, attached to `ctx.container`. Holds TicketService, RollupMessageIndex, OutboxMessageService, protocol constants, and datasource references.

### Lifecycle Hooks (`./hooks/`)

- `on_reindex`: Runs seed SQL, registers ServiceContainer, registers native + FA tickets
- `on_restart`: Registers ServiceContainer, syncs RollupMessageIndex, sets all matcher locks
- `on_synchronized`: Sets all matcher locks for full re-matching
- `on_index_rollback`: Executes rollback SQL + DipDup built-in rollback

### Models

All models in `./models/__init__.py`, enums in `./models/enum.py`. Key tables: `tezos_token`, `tezos_ticket`, `etherlink_token`, `l1_deposit`, `l2_deposit`, `l1_withdrawal`, `l2_withdrawal`, `bridge_operation`, `bridge_deposit`, `bridge_withdrawal`, `rollup_inbox_message`, `rollup_outbox_message`.

### Output Proof Decoder

`./types/output_proof/` implements custom binary schema-based unpacking for rollup output proofs (Micheline expressions, inode trees, tree encodings).

### Ticket Hashing

Uses `Web3.keccak()` of ABI-encoded ticketer address + Micheline-forged ticket content to produce uint256 hashes matching on-chain `ticket_hash` values. See `./handlers/ticket.py`.

## Infrastructure

- **Database:** PostgreSQL 17 (via docker-compose)
- **GraphQL:** Hasura (port 49180 default, `camel_case: false`)
- **Datasources:** TzKT, Tezos node, Etherlink EVM node (rate-limited 900 req/min), Etherlink Subsquid archive, rollup node RPC
- **CI:** GitHub Actions builds and pushes Docker image to GHCR on branch push/tag
