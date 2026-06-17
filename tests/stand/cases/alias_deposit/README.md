# Case: alias-deposit

**A4 — deposits TO an EVM alias resolve the receiver to its native tz account.**

`on_xtz_deposit` (XTZ, transaction-matched) and `on_deposit` (FA, `Deposit` event on `0xff…02`)
must each resolve the EVM receiver through the `originOf` precompile, so `l2_deposit` is attributed
to the Tezos user rather than the raw alias.

Live data, alias `0x21ab…bc21` → `tz1ekkz…Vydc`. Exact blocks/amounts in `window.env`; asserted
vectors in `verify.py`. Covers XTZ before- and after-init plus the before-init FA deposit.

**Limitation surfaced:** the *after-init FA* deposit is not indexed — it emits a different
(queued/claim) event the `on_deposit` ABI doesn't decode. That's the deferred queued-deposits
scope, not an A4 bug. The after-init *XTZ* deposit still indexes because `on_xtz_deposit` matches
the transaction, not an event.

```bash
make test-indexer CASE=alias_deposit && make inspect-test CASE=alias_deposit
```
