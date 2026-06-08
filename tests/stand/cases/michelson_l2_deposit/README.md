# Case: michelson-l2-deposit

Regression for **Block 1 — L2 Michelson deposits** (tz1 receiver on Tezos X): the L2
Michelson deposit is indexed (`source = tz1` depositor, amount>0; inbox coords from the
node) and matched to its L1 deposit by inbox coords. Should stay GREEN.

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

## Expected (GREEN)

`l1_deposit = 1`, `l2_deposit = 1`, and `bridge_operation` FINISHED with both sides
(`l1=True l2=True`), matched by inbox coords `(3599297, 8)`.

## Run

```bash
make test-indexer CASE=michelson_l2_deposit   # logs: on_michelson_deposit registered opAhDW… inbox=(3599297,8)
make inspect-test CASE=michelson_l2_deposit
```
