# Case: outbox-fetch-failure — a transient rollup-node error costs outbox messages for good

`RollupMessageIndex._process` drains the pending outbox levels like this
(`handlers/rollup_message.py:304-308`):

```python
while len(self._outbox_level_queue) > 0 and (...):
    outbox_level = self._outbox_level_queue.pop()     # removed from the set FIRST
    await self._handle_outbox_level(outbox_level)     # ...then the network call
```

`_handle_outbox_level` does an unguarded `self._rollup_node.request(...)`
(`rollup_message.py:359`). Nothing on the path `on_head` → `handle_realtime` → `_process` →
`_handle_outbox_level` (nor `on_restart` → `synchronize` → `_process` → …) catches transport
errors, so one 500 from the rollup node aborts the callback — with the level already out of
the queue and `_create_outbox_batch` never flushed.

The recovery question is decided by the inbox cursor. `_process` bulk-creates the inbox
messages of the page **before** the drain, and the next start resumes at
`1 + last saved inbox id` (`_prepare_new_index`, `rollup_message.py:410`). Any outbox level
enqueued by an external message *below* that id is therefore never queued again — the index
reports itself caught up while those outbox messages are simply absent.

## The window

One TzKT inbox page (`level.ge=4285890` → ids 38264644..38278225, levels 4285890..4287084) on
the post-reset Tezos X previewnet rollup `sr1BvTT7…`:

| inbox id | level | kind | effect |
|---|---|---|---|
| 38264766 | 4285901 | transfer | saved by `bulk_create` |
| 38264889 | 4285912 | external | enqueues outbox level 4285912 — **1 message** |
| 38271382 | 4286481 | external | enqueues outbox level 4286481 — **1 message** |
| 38271561 | 4286496 | transfer | saved by `bulk_create` — becomes the resume cursor |

Both outbox-carrying levels sit *between* the two transfers, so a crash during the drain
leaves a cursor above them. Every number above lives in `window.env` (the `CASE_*` variables);
`run_arms.sh` sources that file and `verify.py` parses it, so this table is the only copy that
can go stale — check it against `window.env` if you edit either.

The `rollup_node` datasource points at `proxy.py`, a pass-through proxy for the real node that
answers a chosen outbox level with a chosen status (default 500 — the shape a wedged node has
in production: it 500s for every level above its `processed_level`). Upstream bodies are
cached, so the arms differ only by the injected failure. The rollup node is a **rolling** node
(it serves roughly the last ~250k L1 levels); once this window ages out, re-pick one with
`proxy.py prewarm` + a scan for non-empty `global/block/<L>/outbox/<L>/messages`.

## Run

```bash
tests/stand/cases/outbox_fetch_failure/run_arms.sh   # asserts; exit 0 GREEN / 1 RED / 2 VOID
make inspect-test CASE=outbox_fetch_failure          # same verdict on the state the last arm left
```

`run_arms.sh` needs no argument and prints every arm's facts as JSON (also collected in
`$OUT/arms.json`, with the logs and the per-arm env files; `OUT_DIR` moves them).
`SQLITE_PATH` / `PROXY_PORT` / `PROXY_CACHE_DIR` override `window.env` so a second sequence can
run beside this one from another worktree — the response cache is only read after the prewarm
and may be shared. `ARM_TIMEOUT` (default 600s) bounds a single arm, so a fix with an unbounded
retry loop fails instead of hanging.

Single arms by hand (the proxy must be running; `make test-indexer` wipes the sqlite, which is
exactly what the restart arms must not do):

```bash
PROXY_FAIL_LEVELS=4285912 uv run python tests/stand/cases/outbox_fetch_failure/proxy.py &
make test-indexer CASE=outbox_fetch_failure
```

## Expected

The six arms and what `run_arms.sh` asserts about each. A failed **precondition** aborts the
run as VOID (exit 2) — a sequence that cannot mean what it is supposed to mean must not be
reported as RED or GREEN. An arm whose *behaviour* is wrong aborts as RED (exit 1) instead.

The first two arms are about which levels may be asked for at all; the last four are about what
survives a fetch that fails.

- **WEDGED** (`PROXY_WEDGED_AT=4285911`, fresh db) — the node stopped applying L1 blocks and
  still answers RPC, reporting a level below both outbox levels. Neither can be served, so
  neither may be asked for: exit **0** (the pass finishes instead of dying), zero outbox rows,
  and inbox rows present — an arm that never reached the drain proves nothing ⇒ VOID.
- **WEDGE-RECOVERY** (healthy node, same db) — both levels indexed. The debt the wedged arm
  recorded is what makes this reachable; the cursor is already past those externals.
- **CONTROL** (`PROXY_FAIL_LEVELS=`, fresh db) — exit 0, `rollup_outbox_message` = exactly the
  2 rows of levels 4285912 and 4286481, `rollup_inbox_message` = the two transfers + the
  level=0 cursor sentinel. Anything else means the window rotted off the rolling node ⇒ VOID.
- **TREATMENT** (`PROXY_FAIL_LEVELS=4285912`, fresh db) — DipDup aborts with a
  `ClientResponseError: 500` raised through `_handle_outbox_level` → `_process` → `synchronize`
  → `on_restart`; DipDup re-raises it and the process exits **1** (an unhandled exception, not
  a DipDup exit code). Asserted: nonzero exit, **both** inbox transfers committed, **zero**
  outbox rows, and `rollup_message.py`/`ClientResponseError` in the traceback. If it crashed
  *before* the inbox `bulk_create`, the db is empty, the restart arms degenerate into a clean
  CONTROL run that passes with no fix at all ⇒ VOID.
- **TREATMENT-AGAIN** (fault still armed, same db, no wipe) — the production node stayed broken
  across many restarts, so a fix whose pending-level state survives one restart but is dropped
  on the second must not reach RECOVERY intact. Either exit code is legitimate here (a fix
  retries and crashes again; the unfixed code never asks for the level again), so only the db
  is asserted: the committed inbox transfers are still there, i.e. nothing wiped them.
- **RECOVERY** (healthy node, same db, no wipe) — must advance the inbox cursor past
  TREATMENT's (otherwise the restart did not run the backfill at all ⇒ VOID), and then the
  verdict is taken on what it leaves.

`verify.py` (the verdict, also reachable as `make inspect-test`) requires, on that final state:

- `rollup_outbox_message` holds exactly 2 rows and its levels are exactly {4285912, 4286481};
- each row is usable, not just present: `index == 0`, `parameters_hash` set (an unhashed row
  never matches a withdrawal), `cemented_level > level`;
- **permanence**, asserted rather than printed: a missing outbox level is only forgivable while
  the cursor can still reach it — nothing persisted, inbox row or level=0 sentinel, may sit
  above the id of the external message that enqueues it;
- the arm actually advanced the backfill: a level=0 sentinel above the window's transfers.
  ("both transfers saved" cannot say this — TREATMENT wrote those rows and they survive by
  design, so the check passes even if the restart does nothing.)

RED, the current behaviour, means the restart resumed at inbox id 38271562, never re-queued
4285912/4286481, and both outbox messages stay missing forever.

### Pre-seeded state

Production always carries the previous page's level=0 cursor sentinel; this window is a single
page, so TREATMENT crashes before writing one and the restart arms would inherit a database no
prod restart ever sees. Of the two ways to fix that — span two inbox pages, or seed the row —
the case seeds it (`CASE_SENTINEL_SEED_ID`, one id below the page's first message), because a
two-page window would be ~20k inbox messages and would move every id in the table above.

It matters because of `unique_together ('level', 'index')` on `(0, 0)`: `_process` deletes old
sentinels with `id__lt=cursor` before creating the new one, so a fix that *rewinds* the cursor
below an existing sentinel leaves it undeleted and raises `IntegrityError` on the create — in
production, and now here too.

### Harness self-tests

A gate that can only ever say RED is worth nothing, so the harness tests itself:

```bash
HARNESS_SELFTEST=green tests/stand/cases/outbox_fetch_failure/run_arms.sh   # must exit 0 GREEN
HARNESS_SELFTEST=void  tests/stand/cases/outbox_fetch_failure/run_arms.sh   # must exit 2 VOID
```

`green` rewinds the inbox cursor to the sentinel before each restart arm — by hand, what a real
fix has to achieve — and the sequence must end GREEN. `void` runs TREATMENT against a dead TzKT
so it dies before the inbox `bulk_create`, and the run must abort as VOID rather than sail on to
a RECOVERY that is really a clean CONTROL run.

## What this case does NOT cover

- **The realtime drain.** Everything here happens under `on_restart` → `synchronize`;
  `handle_realtime` is never entered and `_realtime_head_level` stays 0. The realtime path pops
  the same queue with the same unguarded fetch, but a loss there is not necessarily permanent
  (the cursor may still be below the external), and this case says nothing about it.
- **The full-outbox continuation** at `rollup_message.py:405` (`_outbox_level_queue.add(outbox_level + 1)`
  when a level is filled to `smart_rollup_max_outbox_messages_per_level`). Both levels of this
  window carry exactly 1 message, so that branch never fires — and a level queued by *it*, not
  by an inbox message, has no inbox id protecting it at all.
- **A real wedged node.** `PROXY_WEDGED_AT` encodes what the endpoint means — `head/level` is
  the last level the node applied, and the level above it has no hash to resolve, verified
  against a healthy node as an exact boundary (that level 200, the next 500 `kind: temporary`).
  That a node which *stops* applying reports its frozen level rather than an advancing one
  follows from the same semantics but was never observed on a broken node.
- **Postgres.** The stand is sqlite; prod is Postgres. The case removes `unsafe_sqlite` (which
  turns off the journal and gives undefined post-crash state) so that at least the crash
  semantics resemble a journalled database.

A variant worth knowing: with `ROLLUP_SYNC_FIRST_LEVEL=4288010` /
`ROLLUP_SYNC_LAST_LEVEL=4289200` the page contains **no** transfers, so a crash leaves no inbox
row at all and the restart replays the whole window — the loss is silent but not permanent.
Permanence is a property of the cursor, not of the queue.
