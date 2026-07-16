# Case: alias-fast-withdrawal

A fast XTZ withdrawal initiated through a NAC indexes correctly on the EVM side.

The native account `tz1ekkz…` calls `call_evm`; the NAC enters the EVM runtime as that account's
alias `0x21ab…` and emits a `FastWithdrawal` on the native precompile `0xff…01`. `on_xtz_withdraw`
must record an `l2_withdrawal` with `fast_payload` set (the marker that distinguishes a fast
withdrawal from a regular one) and resolve the `l2_caller` — the alias — back to its tz origin.

Live data, EVM tx `0x345ae9…5214` at L2 block 796093, alias `0x21ab…` → `tz1ekkz…`. Exact block /
amounts in `window.env`; asserted vectors in `verify.py`.

This is the L2 side only — the L1 fast-payout and its matcher step are a separate, heavier scope
(like the alias-withdrawal case, this exercises the withdrawal handler, not full bridge matching).

```bash
make test-indexer CASE=alias_fast_withdrawal && make inspect-test CASE=alias_fast_withdrawal
```
