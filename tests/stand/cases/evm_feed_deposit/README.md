# Case: evm-feed-deposit

Reproduces (and now verifies the fix for) the bug where **Tezos→EVM XTZ deposits were not
indexed on the Tezos X (REVM) rollup**. Kept as a regression case — it should stay GREEN.

## The bug (fixed in master)

XTZ deposits land on L2 as a synthetic tx with `from = 0x0000…feed` (FEED_DEPOSIT_ADDR) in
the new tezosx kernel — not the old `0x0000…0000`. The index `etherlink_xtz_deposit_transactions`
and `handlers/etherlink/on_xtz_deposit.py` originally hardcoded `0x0000…0000`, so the tx was
never delivered / validated and no `l2_deposit` was produced.

## On-chain sample (Candidate A, 9985 XTZ)

- L1 deposit: `opUAXNFmxdQo4y1ySFmkmgLAzjo8w2jwMyn5JArEKnrgVfZ84eC` @ shadownet level **3428081**,
  recipient `0x8aad6553cf769aa7b89174be824ed0e53768ed70`.
- L2 credit: block **388226** (synthetic tx `from=0x…feed`).

Window is in `window.env`.

## Expected

- Buggy baseline: `l2_deposit = 0`, `bridge_operation` stuck at `CREATED` (l1 ✓, inbox ✓, l2 ✗).
- Fixed (current): `l2_deposit ≥ 1` and a matched/advanced `bridge_operation` → GREEN.

## Run

```bash
make test-indexer CASE=evm_feed_deposit
make inspect-test CASE=evm_feed_deposit
```
