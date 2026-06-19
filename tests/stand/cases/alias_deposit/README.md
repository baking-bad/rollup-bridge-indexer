# Case: alias-deposit

Deposits TO an EVM alias resolve the receiver to its native tz account.

`on_xtz_deposit` (XTZ, transaction-matched) and `on_deposit` (FA, `Deposit` event on `0xff…02`)
each resolve the EVM receiver through the `originOf` precompile, so `l2_deposit` is attributed
to the Tezos user rather than the raw alias.

Live data, alias `0x21ab…bc21` → `tz1ekkz…Vydc` (already initialized on-chain when this window is
indexed). Exact blocks/amounts in `window.env`; asserted vectors in `verify.py`. Three deposits —
two XTZ and one FA — all resolved to the native origin.

```bash
make test-indexer CASE=alias_deposit && make inspect-test CASE=alias_deposit
```
