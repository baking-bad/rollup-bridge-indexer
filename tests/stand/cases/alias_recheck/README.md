# Case: alias-recheck

Exercises the re-resolution cooldown branch of `resolve_l2_account` against the live indexer.

Three XTZ deposits land on the same EVM receiver `0x7e6f…7677`, which `originOf` classifies as
native (`kind=1`, its own origin → `kind=evm`). `ALIAS_RECHECK_SECONDS=0` (in `window.env`) forces
every repeat sighting past its cooldown, so the 2nd and 3rd deposits take the re-resolution path
(`originOf` re-query + in-place UPDATE) instead of the query-once cache return.

This cannot reproduce the *recovery* of a pre-init alias — `originOf` is read at the node head, so a
currently-native address stays native (that recovery is covered by the unit tests, which fake
`originOf`). What it checks is that the re-resolution UPDATE runs in the real indexer without
duplicating the account row or dropping a deposit.

Live data, receiver `0x7e6f…7677`. Exact blocks/amounts in `window.env`; asserted vectors in
`verify.py`.

```bash
make test-indexer CASE=alias_recheck && make inspect-test CASE=alias_recheck
```
