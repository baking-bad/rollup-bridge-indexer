# Case: michelson-l2-deposit (op-hash variant)

The **op-hash** variant of [`michelson_l2_deposit`](../michelson_l2_deposit/README.md):
same on-chain pair, same expected link, but established **without reading the deposit
event and without any Tezos node call**.

On Tezos X an XTZ deposit to a Michelson `tz1` receiver lands on L2 as a *synthetic*
pseudo-Michelson `transaction` from TEZLINK_DEPOSITOR. TzKT drops the kernel's deposit
event (implicit tz1 source), so the sibling case reads the inbox coords from the
**Michelson node** per op. This variant instead **reconstructs the L2 synthetic-tx
op-hash from L1 inbox data alone** and matches by hash equality — deterministic, no
event, no node round-trip (`handlers/michelson_deposit.py`).

## Verified on-chain pair

- **L2 Michelson deposit**: `opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE`
  @ previewnet level **562967** — `tz1Ke2h7…(depositor) → tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7`,
  amount **1000000** mutez.
- **L1 deposit / inbox**: `ooGrnVtriNUXUPpsFWaKGtFkM4MfroTHmvHs9BUzC53ewVCveC9`
  @ shadownet level **3599297**, inbox message **(level=3599297, index=8)**.

The op-hash reconstructed from inbox `(3599297, 8)` equals the L2 op-hash above.

## What this proves (reconstruction-only scope)

`verify.py` replays **every** indexed `rollup_inbox_message` through
`expected_op_hash_from_inbox` (returns `None` for EVM-target / non-deposit rows) and
checks that the op-hash of the L2 deposit recorded by `tezos.on_michelson_deposit`
appears among them. The match lives in the verifier, **not** in the production
bridge matcher — the L2 row is stored with no inbox coords and no `l2_token`, so the
matcher leaves it untouched. This case demonstrates the op-hash link; wiring it into
`bridge_matcher` is a separate step.

Contrast `tezos_x.on_michelson_deposit` (sibling case): node-polling, ~715 ms/deposit,
loses a deposit on a dropped node call. This variant: ~54 µs/deposit CPU, no I/O.

## Expected (GREEN)

`rollup_inbox_message >= 1`, at least one inbox row reconstructs to a tz1-target
op-hash, `l2_deposit >= 1`, and the L2 deposit op-hash matches its L1-reconstructed
op-hash.

## Run

```bash
make test-indexer CASE=michelson_l2_deposit_ophash   # logs: L2 Michelson deposit recorded (op-hash variant) … opAhDW…
make inspect-test CASE=michelson_l2_deposit_ophash
```
