# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A DipDup (v8.6.1+, Python 3.12, `uv`) indexer that tracks bridge operations between Tezos (L1) and
Etherlink / Tezos X (L2, a smart rollup on Tezos). It ingests both chains independently and
reconciles the two legs of each transfer into unified `bridge_operation` rows. Postgres storage,
Hasura on top (`camel_case: false`; the dev compose publishes it on `HASURA_PORT`, default 49180).

## Commands

The Makefile lists every target. The ones whose behaviour is not obvious from the name:

```bash
make run                        # BASE CONFIG ONLY — no network overlay, not what prod runs
make prod-check [BOOT=1]        # the shippability sequence: lint + unit + docker smoke
make test-pg                    # the unit suite on Postgres — see Testing for why it differs
make check-config NET= ENV=     # validate a merged config without booting
make test-indexer CASE=<name>   # block-bounded stand run; inspect-test CASE= gives its verdict
make bench-matcher              # matcher cost instrument; make perf-db-down to clean up
```

Run against a real network with `uv run dipdup -c . -c configs/<network>.yaml run`, env exported.

## Packaging: the package IS the repo root

`dipdup.yaml`, `handlers/`, `models/`, `hooks/`, `types/`, `configs/`, `sql/`, `abi/` live directly
at the root. The committed self-symlink **`rollup_bridge_indexer -> .`** is what makes
`import rollup_bridge_indexer.*` resolve (plus the editable install from `[build-system]`).
Consequences you will trip over:

- `package: rollup_bridge_indexer` in `dipdup.yaml` must stay **different** from `[project].name`
  (`rollup-bridge-indexer`). When package name, pyproject name and cwd name all agree, DipDup's
  `get_package_path()` resolves the package to `cwd` — and `dipdup init` then scaffolds an empty
  skeleton in the wrong place instead of touching the real one. The mismatch is load-bearing, not
  an oversight to tidy up.
- Every tool must skip the symlink or it recurses into itself: `mypy exclude`, `ruff
  extend-exclude`, `black extend-exclude`, `pytest norecursedirs`. Keep those in place.
- Imports inside the package are absolute and fully qualified
  (`from rollup_bridge_indexer.models import ...`), never relative.
- In Docker, `DIPDUP_PACKAGE_PATH=/opt/app/rollup_bridge_indexer` points at the symlink on
  purpose: DipDup derives the package *name* from that path's basename, and `/opt/app` would name
  it `app`. `/opt/app` is also `chown`ed to the runtime user because `package.initialize()` writes
  marker files (`py.typed`, `**/.keep`) on every command. `tests/e2e/smoke_test.sh` guards both.
- CI builds with a **path** context, not the git context — the git context follows the self-symlink
  and leaks `tests/**` into the image.

## Configuration model

`dipdup.yaml` is the base (database, hasura, sentry, datasources, `advanced.reindex`, and the
`custom` block). `configs/<network>.yaml` is an overlay applied with `-c . -c configs/<net>.yaml`:
`mainnet`, `ghostnet`, `quebecnet`, `rainbownet`, `shadownet`, `tezosx-shadownet`. Everything else — endpoints,
ticketer addresses, first levels — comes from env vars, all of them `${…}` in the YAML. Three knobs
exist only in code and appear in no config: `ALIAS_RECHECK_SECONDS`, and the stand's
`ROLLUP_SYNC_FIRST_LEVEL` / `ROLLUP_SYNC_LAST_LEVEL` bounds for the rollup backfill.

**DipDup merges `-c` files with a shallow top-level `dict.update`.** An overlay that defines
`datasources` or `custom` replaces the *whole* base block. That is why `mainnet.yaml`,
`shadownet.yaml` and `tezosx-shadownet.yaml` restate `etherlink_node.http` verbatim — including
`ratelimit_rate: 900` per 60 s, which the node needs and which defaults to no throttling when the
block is lost — plus `ws_url` and `etherlink_subsquid`. Losing one of these is silent at boot and
shows up later as 503 storms or a broken EVM index.

`custom.has_michelson_runtime` is **required, no default** (`handlers/alias.py` asserts it is a
literal YAML bool). `false` = plain EVM-only Etherlink; `true` = a rollup with a Michelson runtime
where the `RuntimeGateway` precompile answers `originOf`. Guessing is unsafe in both directions.

## Architecture

### Ingestion: three handler families

Naming convention: **`tezos/` = L1, `etherlink/` = L2 EVM, `tezos_x/` = L2 Michelson.**

- `handlers/tezos/` — L1 via TzKT. `on_head` is the odd one out: it drives the rollup message
  index below, not an L1 entity.
- `handlers/etherlink/` — L2 EVM via Subsquid archive + EVM node. Deposits are not events but an
  `evm.transactions` index filtered on the synthetic senders `0x…feed` and legacy `0x0`;
  withdrawals come from the kernel precompiles `0xff…01` (native, also the source of
  `FastWithdrawal`) and `0xff…02` (FA).
- `handlers/tezos_x/` — L2 Michelson via a *second* TzKT, over the rollup's Michelson runtime.
  `on_michelson_deposit_ophash` is production; `on_michelson_deposit` is the event-based variant,
  kept alive deliberately for the `michelson_l2_deposit` stand case and for the day TzKT starts
  serving implicit-source events. It is not dead code.

### `handlers/rollup_message.py` — custom rollup inbox/outbox index

Rollup messages are not a DipDup index kind, so this is a hand-rolled indexer with its own
lifecycle (`new -> syncing -> realtime`), backfilled in `on_restart` **before** DipDup's own
indexes start and pumped afterwards from `on_head`.

- Inbox comes from the TzKT `v1/smart_rollups/inbox` endpoint, walked by an `id.gt=` cursor. When
  no real row carries the cursor id, a level-0 sentinel row records it.
- A fresh DB starts at `RollupMessageIndex.first_ticket_level` — the min first-activity level over
  *every* whitelisted ticket, native included (`TicketService._lower_first_ticket_level`). An
  FA-only minimum silently drops earlier XTZ deposits' inbox messages forever.
- Outbox comes from the rollup node RPC per level. Levels are learned from `external` inbox
  messages (and from a full outbox asking for its continuation) and held in `PendingOutboxLevels`,
  mirrored into DipDup's `dipdup_meta` table — outside the package schema, so it costs no schema
  hash change, but it also survives a reindex wipe and is therefore explicitly dropped when the
  inbox table comes up empty. **Ordering is the contract:** the pending set is saved *before* the
  rows that advance the cursor past those externals, and a level leaves the set only *after* its
  outbox rows are committed.
- The drain ceiling is the rollup node's own `global/block/head/level`, not the L1 head: a node
  that stopped applying blocks still answers RPC but cannot resolve levels above what it applied.
- The same module owns all **parameter hashing** — `uuid5(NAMESPACE_OID, orjson.dumps(dto,
  OPT_SORT_KEYS))` over normalized DTOs — for inbox messages, L1 deposit transactions, outbox
  messages (plain + fast) and L2 withdrawal events. Matching hashes is how L1 ops and L2 events
  find each other.

### `handlers/bridge_matcher.py` + `batch.py` — reconciliation

Handlers never match. They write rows and set a boolean flag on `BridgeMatcherLocks`;
`handlers/batch.py::batch` fires the matched handlers, then runs `run_matcher_steps()` under
`BridgeMatcher.matcher_lock`. Each step returns immediately unless its flag is set, and clears it
first. `on_restart` and `on_synchronized` set every flag — a full re-match pass.

`run_matcher_steps()` is the authority on which steps exist and in what order; read it rather than
a list here. What the *order* encodes, and what a reordering would break:

- **Deterministic keys before heuristics.** Every step matches on a parameter hash or on inbox /
  outbox coordinates except one: EVM XTZ deposits carry no coordinates, so they are zipped on
  `(ticket, L2 account, amount)` within a 140 s window (`LAYERS_TIMESTAMP_GAP_MAX`), with
  mutez→wei scaling taken from the two tokens' decimals.
- **That heuristic step is filtered to `runtime_kind=evm`** so it cannot preempt the op-hash step
  that claims Michelson deposits. Without the filter the two runtimes steal each other's rows.
- **A matched parameter hash is nulled on both sides** once consumed. That is what stops a hash
  being claimed twice, and what keeps the candidate pools small.

`handlers/candidate_pool.py` (`CandidatePool`) is the shared primitive for the counterpart side of
a step: one query, lazily issued on first `take`, rows keyed and claimed once each, queryset order
as the tie-break, `None` keys never enter. It exists to keep steps from issuing one query per
walked row — the N+1 that surfaces as the backlog grows. `warn_on_tie=False` only where a shared
key is legitimate (two identical ops in one block share a parameter hash).

### `handlers/michelson_deposit.py` — the op-hash bridge for tz1 deposits

TzKT does not index the kernel's implicit-source deposit event, so a tz1-receiver XTZ deposit has
no observable inbox coordinates on the L2 side. Instead the L2 synthetic op hash is recomputed
from L1:

```
op_hash = base58check('o', keccak256(rlp([amount_wei, receiver, inbox_level, inbox_msg_id]) ++ raw_rollup_address))
```

computed while storing the inbox message (`expected_l2_op_hash`) and compared against the observed
L2 op hash by the matcher. The module is pure (no I/O) and also owns `parse_routing_info` /
`l2_account_from_routing_info` (legacy 20B / 52B forms and versioned v1 RLP routing).

The derivation carries **no in-band version** — it is pinned to a kernel snapshot by golden vectors
in `tests/unit/tezos/test_michelson_deposit.py`. A kernel change fails quiet, surfacing only as the
"L2 Michelson deposit(s) without a matching inbox op-hash" warning.

### `handlers/alias.py` — L2 account identity

Calls the `RuntimeGateway` precompile `0xff…07` `originOf(string,uint8)` to classify an EVM address
as `unknown` / `native` / `alias` (an EVM stand-in for a native tz account). Results cache in
`l2_account` (PK `runtime_address`, 40-hex no `0x`; `origin` = the native address to group by;
`kind`; `home_runtime`). Classified rows are terminal; `unknown` rows are re-resolved after
`ALIAS_RECHECK_SECONDS` (24 h) so an alias first seen before its native account existed recovers.
An undecodable answer raises `RuntimeGatewayUnsupportedError` rather than silently recording a
native — but that takes L1 indexing down with it if `has_michelson_runtime` is wrong.

EVM handlers call `resolve_l2_account(ctx, addr)`; tz receivers are native by construction and use
`L2Account.get_or_create_for(addr, RuntimeKind.michelson)` with no precompile call.

`OriginKind` (identity of an address) and `RuntimeKind` (runtime that processed an *operation*) are
deliberately distinct. `runtime_kind` on `l2_deposit` / `l2_withdrawal` / `bridge_operation` is what
keeps EVM and Michelson rows from colliding: they share the L2 block counter, and the same XTZ
ticket has two L2 tokens (`xtz_evm`, 18 decimals/wei; `xtz_michelson`, 6 decimals/mutez).

### Tickets, DI, framework patches

`handlers/ticket.py`: `ticket_hash` is `uint256(Web3.keccak(abi_encode(bytes22 forged ticketer,
bytes forged ticket content)))`, matching the on-chain value. `TicketService` registers the native
ticket (creating both L2 XTZ tokens) and the whitelisted FA tickets; `sql/on_reindex/00_xtz_asset.sql`
seeds the `xtz` `tezos_token` row the native registration depends on.

`handlers/service_container.py`: dependency injection. Built in `on_reindex` / `on_restart`,
attached as `DipDupContext.container`, read via `get_container(ctx)`. Note that the protocol
constants it carries are fetched from the Tezos node at startup, not hardcoded.

`handlers/dipdup_patches.py`: applied from `on_restart`, before datasources start. Currently one
patch — `EvmNodeDatasource._handle_subscription` enqueues each head `LevelData` at most once. With
`ws_url` set, an `evm.events` index and an `evm.transactions` index open two `newHeads`
subscriptions on one connection, the node announces every head twice, and `_emitter_loop`'s
`del self._level_data[...]` raises `KeyError` and kills the process on the first realtime block.
The patch **matches the installed source byte-for-byte** and raises if upstream changed it — a
deliberate fail-loud. Upstream: dipdup-io/dipdup#1328 (unmerged, absent from 8.6.1). Removal gate:
`tests/unit/datasource/test_evm_node_duplicate_head.py` must pass with the patch gone.

Worth knowing when reading indexer state: with `ws_url` set, an `evm.events` index writes its
`dipdup_index` row only when a matching log arrives, so its `level` / `updated_at` stop tracking
the head. Read liveness from `dipdup_head[etherlink_node]` or from the `evm.transactions` index.

### Models, hooks, generated types

Models live in `models/__init__.py`, enums in `models/enum.py`.

Lifecycle hooks are in `hooks/`. The one thing to know before editing them: raw SQL lives in
`sql/<hook>/` and must stay portable, because the sqlite test stand runs the same seed.

`types/` is DipDup-generated from `abi/` and TzKT (`make init`), except `types/output_proof/`, a
hand-written binary-schema decoder for rollup output proofs (Micheline expressions, inode trees,
tree encodings) used to recover `(outbox_level, message_index)` from an execution's `output_proof`.

`hasura/*.json` are extra Hasura metadata batches (relationships DipDup cannot derive).

## Testing

Four suites with different contracts — `tests/CLAUDE.md` is the authority; read it before adding a
test.

- `tests/unit/` — the pytest suite (`make test`). **CI does not run it.** The only automated gate
  on a push is the docker smoke test; the unit suite runs by hand, via `make test` or as part of
  `make prod-check`. Decoders, rollup/commitment math, type
  round-trips, and the matcher harness (`matcher/`: real models and real matcher steps over
  in-memory sqlite). `make test-pg` runs the same suite on Postgres: sqlite and Postgres disagree
  on NULL ordering, and the matcher's tie-breaks follow query order, so a rewrite can pass on one
  and fail the other. Note that `pyproject.toml` sets `testpaths = ["tests"]` while `make test`
  targets `tests/unit` — a bare `pytest` collects perf and stand modules that `make test` does not.
- `tests/e2e/` — the shippability gate (`make prod-check`, `make docker-test`). `smoke.env` holds
  dummy/public values only.
- `tests/perf/` — matcher cost instruments, not a gate. The seeded backlog is a fixed point where
  nothing matches, so a patch that stopped matching entirely would score *well*; always run the
  unit matcher tests alongside. Postgres only, never two benchmarks at once.
- `tests/stand/` — manual, block-bounded, secret-free repro harness against real testnet data into
  a throwaway sqlite. Each case is `cases/<name>/` = `config.yaml` + `window.env` + `verify.py` +
  `README.md`, and `<name>` must be a valid Python identifier. Case configs are **standalone
  copies, not overlays** — a fix to a prod config has to be mirrored by hand into every affected
  case. Bound the rollup backfill with `ROLLUP_SYNC_FIRST/LAST_LEVEL`: it starts from origination
  and ignores the index's `last_level`.

## Shipping changes

- **PRs land as merge commits — never squashed.** The commit sequence is part of what a PR delivers
  here (a harness that goes red, then the fix that turns it green), and a squash throws it away.
  Master having single-parent commits is history, not the convention.
- **Publishing an image is not deploying it.** `.github/workflows/build.yml` runs on pushes to
  `master` and on pull requests against it, and the publish step is unconditional — a PR build
  lands in GHCR as `pr-<N>`, a master push as `master`. Tags are in the metadata but are not a
  trigger. Every publish is gated on the hermetic docker smoke test first.
  Nothing picks the new image up on its own: Swarm resolves `${TAG:-master}` to a **digest** when
  the service is deployed and stores that digest in the service spec, so every later task
  recreation — a host drain, a node reboot, a crash loop, any reschedule — starts the same binary
  it was running before. Moving a stack to a newer image is an explicit redeploy that someone
  performs. Pinning a tag to hold a version still is therefore unnecessary, and can be done later
  if a particular run needs it.
- **Any change to `models/` costs a full reindex.** `advanced.reindex.schema_modified: exception`
  makes DipDup refuse to start against a database whose schema hash no longer matches, so a new
  field is not a deploy — it is a wipe and a rebuild. Measured on mainnet from scratch: **3 d 11 h**
  end-to-end, of which the first 5 h 20 m is the rollup inbox backfill inside `on_restart`, before
  `dipdup_index` has any rows at all. Prefer `dipdup_meta` for state that does not need to be
  queryable.

## Code style

Line length 140; single quotes (Black `skip-string-normalization`); ruff isort with
`force-single-line`; ruff extends `B, C4, FA, G, I, PTH, Q, RET, RUF, TCH, UP`; target py312.

Comments here explain *why* a shape is load-bearing — merge semantics, ordering contracts, kernel
invariants. When changing such code, update the reasoning rather than deleting it.
