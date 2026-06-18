import os
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from dipdup.context import HandlerContext
from eth_abi import decode
from eth_abi import encode
from eth_typing import HexStr
from web3 import Web3

from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models.enum import L2AccountKind

# The `RuntimeGateway` precompile exposes a view that resolves an EVM address to its origin:
#   originOf(string address, uint8 runtime) -> (uint8 kind, uint8 home_runtime, string native_address)
# Verified live on Tezos X previewnet (2026-06-17); see tests/stand/cases/alias_deposit.
RUNTIME_GATEWAY = Web3.to_checksum_address('0xff00000000000000000000000000000000000007')  # web3 eth.call requires checksum
ORIGIN_OF_SELECTOR = bytes.fromhex('e3d35459')  # keccak('originOf(string,uint8)')[:4]
RUNTIME_ETHEREUM = 1

# `kind` return values observed across known aliases, real EOAs and dead addresses:
#   0 -> unknown / no origin record   (0, 0, '')
#   1 -> native account of the runtime (1, 1, '0x<self>')
#   2 -> alias of a native Tezos account in another runtime (2, 0, '<tz1/tz2/tz3/KT1>')
ORIGIN_KIND_ALIAS = 2

# How often to re-`originOf` a not-yet-alias row — recovers an alias first seen before its native
# account existed, without re-querying on every sighting. Override via ALIAS_RECHECK_SECONDS.
ALIAS_RECHECK_INTERVAL = timedelta(seconds=int(os.environ.get('ALIAS_RECHECK_SECONDS', 86400)))


def _due_for_recheck(account: L2Account) -> bool:
    updated = account.updated_at
    if updated.tzinfo is None:  # naive timestamps (use_tz off) are UTC by convention
        updated = updated.replace(tzinfo=UTC)
    return datetime.now(UTC) - updated >= ALIAS_RECHECK_INTERVAL


def _use_cached(account: L2Account) -> bool:
    """Return the cached row without re-querying: a known alias is terminal, and a not-yet-alias is
    trusted until its recheck cooldown elapses."""
    return account.kind == L2AccountKind.evm_alias or not _due_for_recheck(account)


async def _query_origin_of(ctx: HandlerContext, address: str) -> tuple[int, str]:
    """Ask the RuntimeGateway `originOf` view about `address`; return (kind, native_address)."""
    datasource = ctx.get_evm_node_datasource('etherlink_node')
    calldata = ORIGIN_OF_SELECTOR + encode(['string', 'uint8'], [address, RUNTIME_ETHEREUM])
    raw = await datasource.web3.eth.call({'to': RUNTIME_GATEWAY, 'data': HexStr('0x' + calldata.hex())})
    kind_int, _home_runtime, native_address = decode(['uint8', 'uint8', 'string'], bytes(raw))
    return kind_int, native_address


async def resolve_l2_account(ctx: HandlerContext, address: str) -> L2Account:
    """Return the `L2Account` for an EVM `address` (40-hex, no `0x`), resolving its alias via `originOf`.

    A known alias (`kind=evm_alias`) is terminal and returned from cache. A not-yet-alias row is also
    cached, but re-resolved once its recheck cooldown elapses (an alias used before its native account
    was initialized only becomes resolvable later). `originOf` classifies the address: `origin` is the
    native tz/KT1 address for an alias (`kind=evm_alias`), else the address itself (`kind=evm`).
    """
    account = await L2Account.get_or_none(runtime_address=address)
    if account is not None and _use_cached(account):
        return account

    kind_int, native_address = await _query_origin_of(ctx, address)
    if kind_int == ORIGIN_KIND_ALIAS:
        origin, kind = native_address, L2AccountKind.evm_alias
    else:
        origin, kind = address, L2AccountKind.evm

    if account is None:
        return await L2Account.create(runtime_address=address, origin=origin, kind=kind)
    # Re-resolution: update in place (the PK runtime_address is stable). save() bumps `updated_at`,
    # resetting the cooldown whether or not the classification changed.
    account.origin, account.kind = origin, kind
    await account.save()
    return account
