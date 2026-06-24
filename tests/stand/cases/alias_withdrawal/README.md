# Case: alias-withdrawal

Withdrawals FROM an EVM alias resolve the sender to its native tz account.

`on_xtz_withdraw` (XTZ, `0xff…01`) and `on_withdraw` (FA, `0xff…02`) each resolve the EVM
`sender` through the `originOf` precompile, so `l2_withdrawal` is attributed to the Tezos user.

Also exercises the fast-withdrawal step: it compares the kernel's raw `l2_caller` to
`l2_account.runtime_address` (raw EVM), not the resolved `origin` (a tz1 once the alias resolves).

Live data, alias `0x21ab…bc21` → `tz1ekkz…Vydc`. Exact blocks/amounts in `window.env`; asserted
vectors in `verify.py`.

```bash
make test-indexer CASE=alias_withdrawal && make inspect-test CASE=alias_withdrawal
```
