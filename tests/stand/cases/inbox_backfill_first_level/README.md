# Case: inbox-backfill-first-level — fresh-DB inbox backfill must cover native deposits

Regression for the tezosx-shadownet **2026-06-11 incident**: after a DB wipe, the
rollup-inbox backfill started at the **FA tickets'** first level (3618904) because
`TicketService.register_fa_tickets` was the only writer of
`RollupMessageIndex.first_ticket_level` — `register_native_ticket` discarded the
native ticket's `firstLevel` (3102183). All 106 inbox transfer messages of the
months-long native-only era were silently skipped, leaving 75/91 bridge deposits
without an inbox message — unmatchable forever (the inbox row is both the
parameters-hash link and the Michelson op-hash source).

The fix: every whitelisted ticket (native AND FA) lowers `first_ticket_level`
(`TicketService._lower_first_ticket_level`), so the fresh-DB backfill starts at the
earliest whitelisted-ticket activity.

## What the case runs

Only the L1 deposit index, over the network's **first XTZ deposit** — the very op
that minted the native xtz ticket, so the native ticket's `firstLevel` equals the
deposit level:

- **L1 deposit / inbox**: 10 XTZ to `tz1KqTpEZ7Yob7QbPE4Hy4Wo8fHG8LhKxZSx`
  @ shadownet level **3102183**, inbox message **(3102183, 8)**.
- FA ticketers' first ticket on this network: **3618904** (live TzKT) — far above.

**`window.env` deliberately omits `ROLLUP_SYNC_FIRST_LEVEL`.** That test override
short-circuits `_prepare_new_index`, i.e. the exact production logic under test.
Only `ROLLUP_SYNC_LAST_LEVEL` bounds the run (the backfill flips realtime at the
first message past it), so the case stays oneshot-fast either way.

## Expected

- **GREEN (fixed)**: backfill starts at the native ticket level →
  `rollup_inbox_message` contains transfer `(3102183, 8)`, and the bridge deposit
  has `inbox_message_id` attached (parameters-hash link) — the deposit is matchable.
- **RED (bug)**: `first_ticket_level = 3618904` > `ROLLUP_SYNC_LAST_LEVEL` → the
  backfill stores nothing and flips realtime instantly; the inbox table holds only
  the cursor-marker row, `bridge_deposit.inbox_message_id` stays NULL.

The bridge operation stays `CREATED` in both outcomes — the window has no L2 side
on purpose; the case isolates the backfill start decision.

## Run

```bash
make test-indexer CASE=inbox_backfill_first_level
make inspect-test CASE=inbox_backfill_first_level
```
