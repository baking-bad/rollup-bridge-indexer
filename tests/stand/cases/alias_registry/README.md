# Case: alias-registry — Tezos X alias `Initialized` events -> `l2_account`

Regression for the **alias registry** (Block 2 / PR-1): standalone `l2_account` table fed by
`etherlink.on_alias_initialized`. No FK conversion, no matcher integration yet (PR-2).

On Tezos X, a Tezos-native account (tz1/tz2/tz3 or KT1) acting in EVM context gets a forwarder
**alias** contract that emits `Initialized(string nativeAddress, bytes nativePublicKey,
uint256 forwardedBalance)` exactly once — **from the alias address itself**, so it cannot be
event-subscribed (DipDup requires a fixed `contract:`). Production instead filters
`evm.transactions` by `from_` = TEZOSX_CALLER (`0x7e20580000000000000000000000000000000001`,
the kernel-only injector of `init_tezosx_alias` txs) and parses the receipt logs in the
handler, keeping only the `Initialized` topic
(`0x60a9f8ac7be7e117b08e5ff52239667fcf051d55e03ead4bfa34c73ff86642e0`). Pipeline under test:

- `etherlink.on_alias_initialized` — fetches the tx receipt via the node datasource, abi-decodes
  `(string, bytes, uint256)`, upserts `l2_account` keyed by the alias address
  (kind=`evm_alias`); `nativePublicKey` and `Forwarded` events deliberately not stored;
  txs from the caller without an `Initialized` log (e.g. claim_xtz) are skipped silently.

## Verified on-chain vectors (all 3 Initialized events in previewnet history)

| Level | Alias (event emitter) | nativeAddress | tx |
|---|---|---|---|
| 172338 | `0x9adffdb184eae59204995cadfb57aafecc715d73` | `KT19iqeZxnYDz6GbU67uwF4ECH7bY46WTV2v` (KT1, empty pubkey) | `0x61a1e6ec…6499` |
| 172570 | `0x3f2e3819832be16420810dfafc813334698bfa65` | `tz1ebDT2XwpwP6ja3ESuLUy8BgZwrmi1XpX3` | `0x0068f92f…c6e1` |
| 176345 | `0x93ee79eb06a7052f3d7f9c160b7927dd69ff4390` | `tz1LJmf4GUTrNsZVWXomSfqyWEWdNPo75Wz3` | `0x2fdc8fdc…1c34` |

Re-verified live (eth_getLogs + eth_getTransactionByHash) on 2026-06-11; all three txs have
`from = 0x7e20580000000000000000000000000000000001`. Note the first tx's `to` differs from the
emitting alias — the handler keys on `log.address`, never `tx.to`.

## Expected (GREEN)

- `l2_account` has **exactly 3 rows**, one per vector;
- each row: `kind = evm_alias`, `native_tz_address` = the decoded nativeAddress (incl. the
  KT1 one), `initialized_at_level` / `initialized_at_tx` from the carrying transaction.

## Run

```bash
make test-indexer CASE=alias_registry   # syncs ~4100 L2 blocks via the node (~30 min, node-bound)
make inspect-test CASE=alias_registry
```
