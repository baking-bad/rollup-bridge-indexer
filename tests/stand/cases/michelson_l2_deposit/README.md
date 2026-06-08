# Case: michelson-l2-deposit

TDD harness for **Block 1 — L2 Michelson deposits** (tz1 receiver on Tezos X). Currently a
**RED baseline**: the L1 side is indexed but the L2 Michelson side is not (the feature isn't
built yet). It also serves as the risk-check that DipDup can subscribe by `source = tz1`.

## Verified on-chain pair

- **L2 Michelson deposit (with event)**: `opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE`
  @ previewnet level **562967** — `tz1Ke2h7…(depositor) → tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7`,
  amount **1000000** mutez. Receipt event payload: inbox `(level=3599297, msg_id=8)`.
- **L1 deposit (matches it)**: `ooGrnVtriNUXUPpsFWaKGtFkM4MfroTHmvHs9BUzC53ewVCveC9`
  @ shadownet level **3599297** (amount 1000000; `inbox_level == this L1 block`).

Match is deterministic via `(inbox_message_level, inbox_message_index)`.

## Detection (Block 1 design)

Discriminator on TzKT: `source = TEZLINK_DEPOSITOR && amount > 0` (and `hasInternals == false`).
The deposit **event is not in TzKT** (emitted from a tz-address → skipped by-design), so the
inbox coords are read **from the Michelson node** per matched op.

Context-only (NOT tested, just be aware):
- `op8gZUtdgJrhAQuxDmscVJE21b72j2VYUofpDatxmPDC9B5echS` — a pre-kernel-update deposit with NO
  event; the handler should WARNING-log and skip gracefully.
- `oo7D52zcqWiq45udErwjZ2iRjtYVDKFysLrBiC8GM2CzNdVGm5D` — an EVM→Michelson NAC/CRAC via a
  special contract (not a direct call from the depositor) → must NOT be indexed; excluded by
  the discriminator.

## Expected

- **RED (now)**: `l1_deposit ≥ 1` present, but no L2 Michelson deposit row → `bridge_operation`
  has the L1 side only. (The handler is a stub that just logs the caught op — proving the
  `source=tz1` subscription works.)
- **GREEN (after the feature)**: the L2 Michelson deposit is indexed with inbox coords and
  matched to the L1 deposit.

## Run

```bash
make test-indexer CASE=michelson_l2_deposit   # logs should show on_michelson_deposit caught opAhDW…
make inspect-test CASE=michelson_l2_deposit   # RED until the feature lands
```
