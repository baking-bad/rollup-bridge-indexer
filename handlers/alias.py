from dipdup.context import HandlerContext
from eth_abi import decode
from eth_abi import encode
from eth_typing import HexStr
from web3 import Web3

from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models.enum import L2AccountKind

# The `RuntimeGateway` precompile exposes a view that resolves an EVM address to its origin:
#   originOf(string address, uint8 runtime) -> (uint8 kind, uint8 home_runtime, string native_address)
# Verified live on Tezos X previewnet (2026-06-17); see tests/stand/cases/alias_xtz_deposit.
RUNTIME_GATEWAY = Web3.to_checksum_address('0xff00000000000000000000000000000000000007')  # web3 eth.call requires checksum
ORIGIN_OF_SELECTOR = bytes.fromhex('e3d35459')  # keccak('originOf(string,uint8)')[:4]
RUNTIME_ETHEREUM = 1

# `kind` return values observed across known aliases, real EOAs and dead addresses:
#   0 -> unknown / no origin record   (0, 0, '')
#   1 -> native account of the runtime (1, 1, '0x<self>')
#   2 -> alias of a native Tezos account in another runtime (2, 0, '<tz1/tz2/tz3/KT1>')
ORIGIN_KIND_ALIAS = 2


async def resolve_l2_account(ctx: HandlerContext, address: str) -> L2Account:
    """Return the `L2Account` for an EVM `address` (40-hex, no `0x`), resolving its alias on first sight.

    A row keyed by this runtime address is returned as-is, no node call (query-once). Otherwise the
    `originOf` precompile is asked whether the address aliases a Tezos-native account: if so `origin`
    is that native tz/KT1 address and `kind=evm_alias`, else `origin` is the address itself (its own
    canonical identity) and `kind=evm`.
    """
    account = await L2Account.get_or_none(runtime_address=address)
    if account is not None:
        # TODO: an alias used before its native account is initialized resolves to origin=self here
        # and is never re-checked. To recover it, re-resolve rows where kind != evm_alias on a
        # cooldown (the mixin's `updated_at` is the clock) — keying on runtime_address makes it an UPDATE.
        return account

    datasource = ctx.get_evm_node_datasource('etherlink_node')
    calldata = ORIGIN_OF_SELECTOR + encode(['string', 'uint8'], [address, RUNTIME_ETHEREUM])
    raw = await datasource.web3.eth.call({'to': RUNTIME_GATEWAY, 'data': HexStr('0x' + calldata.hex())})
    kind_int, _home_runtime, native_address = decode(['uint8', 'uint8', 'string'], bytes(raw))

    if kind_int == ORIGIN_KIND_ALIAS:
        origin, kind = native_address, L2AccountKind.evm_alias
    else:
        origin, kind = address, L2AccountKind.evm

    return await L2Account.create(runtime_address=address, origin=origin, kind=kind)
