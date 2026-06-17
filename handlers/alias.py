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
    """Return the `L2Account` for an EVM `address` (40-hex, no `0x`), resolving aliases on first sight.

    If a row keyed by this address already exists it is returned as-is (no node call). Otherwise the
    `originOf` precompile is asked whether the address aliases a Tezos-native account: if so the row
    stores `origin` = that native tz/KT1 address with `kind=evm_alias`, else `origin` = the address
    itself with `kind=evm`. Either way the alias relation is recorded once and reused.
    """
    account = await L2Account.get_or_none(address=address)
    if account is not None:
        return account

    datasource = ctx.get_evm_node_datasource('etherlink_node')
    calldata = ORIGIN_OF_SELECTOR + encode(['string', 'uint8'], [address, RUNTIME_ETHEREUM])
    raw = await datasource.web3.eth.call({'to': RUNTIME_GATEWAY, 'data': HexStr('0x' + calldata.hex())})
    kind_int, _home_runtime, native_address = decode(['uint8', 'uint8', 'string'], bytes(raw))

    if kind_int == ORIGIN_KIND_ALIAS:
        origin, kind = native_address, L2AccountKind.evm_alias
    else:
        origin, kind = address, L2AccountKind.evm

    # Key on `origin` (the canonical native identity), never on `address`: one native account is
    # reachable by several addresses (its tz-side Michelson receiver AND its EVM alias), so a second
    # sighting must reuse the existing row, not INSERT a duplicate-`origin` PK (which crash-looped the
    # indexer). When an alias is found for an already-recorded native account, update its stored
    # address+kind so the row converges on the alias form regardless of which leg was indexed first.
    account = await L2Account.get_or_none(origin=origin)
    if account is None:
        return await L2Account.create(origin=origin, address=address, kind=kind)
    account.address = address
    account.kind = kind
    await account.save()
    return account
