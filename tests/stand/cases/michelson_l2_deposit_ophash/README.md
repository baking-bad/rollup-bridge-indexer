# Case: michelson-l2-deposit (op-hash variant) — THE PRODUCTION PATH

Regression for the **production** L2 Michelson XTZ deposit pipeline: same on-chain
pair as the sibling [`michelson_l2_deposit`](../michelson_l2_deposit/README.md), but
matched **without reading the deposit event and without any Tezos node call** — the
way the deployed indexer does it.

On Tezos X an XTZ deposit to a Michelson `tz1` receiver lands on L2 as a *synthetic*
pseudo-Michelson `transaction` from TEZLINK_DEPOSITOR. TzKT drops the kernel's deposit
event (implicit tz1 source), so production **reconstructs the L2 synthetic-tx op-hash
from L1 inbox data alone** and matches by hash equality — deterministic, no event, no
node round-trip. Pipeline under test:

- `tezos_x.on_michelson_deposit_ophash` — records the full consumer-visible L2 row
  (xtz token + ticket, amount scaled mutez→wei to match the token's 18 decimals);
- `rollup_message` precomputes each inbox message's `expected_l2_op_hash`;
  `BridgeMatcher.check_pending_michelson_deposits` backfills inbox coords onto the L2 row
  from the message whose hash matches, then `check_pending_etherlink_deposits` links the
  legs and finishes the `bridge_operation`;
- `tezos.on_rollup_call` — stores the real `tz1…` receiver as `l1_deposit.l2_account`
  (v1-RLP routing decode, not the legacy 20-byte slice).

Contrast `tezos_x.on_michelson_deposit` (sibling case): node-polling, ~715 ms/deposit,
loses a deposit on a dropped node call. This variant: ~54 µs/deposit CPU, no I/O.

## Verified on-chain pair

- **L2 Michelson deposit**: `opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE`
  @ previewnet level **562967** — `tz1Ke2h7…(depositor) → tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7`,
  amount **1000000** mutez.
- **L1 deposit / inbox**: `ooGrnVtriNUXUPpsFWaKGtFkM4MfroTHmvHs9BUzC53ewVCveC9`
  @ shadownet level **3599297**, inbox message **(level=3599297, index=8)**.

The op-hash reconstructed from inbox `(3599297, 8)` equals the L2 op-hash above.

## Expected (GREEN)

Produced by the **real matcher**, asserted from the result DB:

- `l1_deposit = 1` with `l2_account = tz1PSJR6…` (routing decode);
- `l2_deposit = 1`: the synthetic op-hash, `token_id = xtz`,
  `amount = 1000000000000000000` (wei), inbox coords `(3599297, 8)` backfilled;
- `bridge_operation` **FINISHED**, both legs linked, `l2_account = tz1PSJR6…`;
- secondary: the inbox row still reconstructs to the expected op-hash (separates a
  matcher regression from a derivation regression).

## Run

```bash
make test-indexer CASE=michelson_l2_deposit_ophash   # logs: Matched 1 L2 Michelson deposit(s) by op-hash
make inspect-test CASE=michelson_l2_deposit_ophash
```
