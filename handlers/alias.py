import os
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from dipdup.context import HandlerContext
from dipdup.exceptions import ConfigurationError
from eth_abi import decode
from eth_abi import encode
from eth_abi.exceptions import DecodingError
from eth_typing import HexStr
from web3 import Web3

from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models.enum import OriginKind
from rollup_bridge_indexer.models.enum import RuntimeKind

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
_ORIGIN_KIND_BY_INT = {0: OriginKind.unknown, 1: OriginKind.native, ORIGIN_KIND_ALIAS: OriginKind.alias}
# originOf `home_runtime` ids -> our RuntimeKind. 1 = the EVM rollup runtime; 0 = the Tezos X
# Michelson (tezlink) runtime (an EVM alias of a tz account returns home_runtime 0, see above).
_RUNTIME_BY_ID = {0: RuntimeKind.michelson, 1: RuntimeKind.evm}

# How often to re-`originOf` a not-yet-alias row — recovers an alias first seen before its native
# account existed, without re-querying on every sighting. Override via ALIAS_RECHECK_SECONDS.
ALIAS_RECHECK_INTERVAL = timedelta(seconds=int(os.environ.get('ALIAS_RECHECK_SECONDS', 86400)))


def _due_for_recheck(account: L2Account) -> bool:
    updated = account.updated_at
    if updated.tzinfo is None:  # naive timestamps (use_tz off) are UTC by convention
        updated = updated.replace(tzinfo=UTC)
    return datetime.now(UTC) - updated >= ALIAS_RECHECK_INTERVAL


def _use_cached(account: L2Account) -> bool:
    """Return the cached row without re-querying: a classified row (native/alias) is terminal, and an
    `unknown` row is trusted until its recheck cooldown elapses."""
    return account.kind != OriginKind.unknown or not _due_for_recheck(account)


class RuntimeGatewayUnsupportedError(Exception):
    """`originOf` did not return a decodable `(uint8, uint8, string)` tuple: the RuntimeGateway
    precompile is absent (empty `0x`) or the node is wrong and answers with garbage. A node that has
    it answers with a ≥128-byte tuple even for unknown addresses (kind 0), so anything undecodable
    means the node is wrong/misconfigured, or `has_michelson_runtime` is mistakenly true for an
    EVM-only rollup. We fail loud here instead of silently recording the address as native — a
    transient bad answer must not permanently misclassify a real alias."""


async def _query_origin_of(ctx: HandlerContext, address: str) -> tuple[int, int, str]:
    """Ask the RuntimeGateway `originOf` view about `address`; return (kind, home_runtime, native_address)."""
    datasource = ctx.get_evm_node_datasource('etherlink_node')
    calldata = ORIGIN_OF_SELECTOR + encode(['string', 'uint8'], [address, RUNTIME_ETHEREUM])
    raw = bytes(await datasource.web3.eth.call({'to': RUNTIME_GATEWAY, 'data': HexStr('0x' + calldata.hex())}))
    try:
        kind_int, home_runtime_int, native_address = decode(['uint8', 'uint8', 'string'], raw)
    except DecodingError as error:
        raise RuntimeGatewayUnsupportedError(
            f'originOf gave an undecodable answer for {address} ({len(raw)} bytes) from RuntimeGateway '
            f'{RUNTIME_GATEWAY} on etherlink_node — check the EVM node, or set has_michelson_runtime=false '
            'if this rollup is EVM-only.'
        ) from error
    return kind_int, home_runtime_int, native_address


def _has_michelson_runtime(ctx: HandlerContext) -> bool:
    """Whether this rollup runs a Michelson runtime alongside the EVM one. Only then do EVM↔tz aliases
    exist and the RuntimeGateway `originOf` precompile answer — so only then is `originOf` queried.

    Required, with NO default: if absent we don't know the rollup's topology, and guessing is unsafe
    either way (true crashes EVM-only nodes on the missing precompile; false silently misclassifies
    real aliases as native). Every config declares it — base `dipdup.yaml` sets it false, the TezosX
    overlay true."""
    value = ctx.config.custom.get('has_michelson_runtime')
    if not isinstance(value, bool):
        raise ConfigurationError(
            'custom.has_michelson_runtime must be set to a bool: true for a rollup with a Michelson '
            'runtime (originOf authoritative), false for plain Etherlink (EVM-only). There is no safe '
            f'default — got {value!r}.'
        )
    return value


async def resolve_l2_account(ctx: HandlerContext, address: str) -> L2Account:
    """Return the `L2Account` for an EVM `address` (40-hex, no `0x`), classifying it via `originOf`.

    On a rollup without a Michelson runtime (plain Etherlink) there are no aliases and no
    RuntimeGateway precompile, so the address is recorded as a native EVM account without querying.

    Otherwise: a classified row (`kind` native/alias) is terminal and returned from cache. An
    `unknown` row is also cached, but re-resolved once its recheck cooldown elapses (an alias used
    before its native account was initialized reads as `unknown` until the record appears). `originOf`
    sets: `origin` is the native tz/KT1 address for an `alias`, else the address itself; `home_runtime`
    is the runtime the origin lives in (null while `unknown`).
    """
    if not _has_michelson_runtime(ctx):
        return await L2Account.get_or_create_for(address, RuntimeKind.evm)

    account = await L2Account.get_or_none(runtime_address=address)
    if account is not None and _use_cached(account):
        return account

    kind_int, home_runtime_int, native_address = await _query_origin_of(ctx, address)
    kind = _ORIGIN_KIND_BY_INT.get(kind_int, OriginKind.unknown)
    if kind == OriginKind.alias:
        origin, home_runtime = native_address, _RUNTIME_BY_ID.get(home_runtime_int)
    elif kind == OriginKind.native:
        # native echoes a `0x`-prefixed self; keep the bare runtime_address form as origin.
        origin, home_runtime = address, _RUNTIME_BY_ID.get(home_runtime_int)
    else:  # unknown: no record yet — origin is self, home_runtime undefined (⊥)
        origin, home_runtime = address, None

    if account is None:
        return await L2Account.create(runtime_address=address, origin=origin, kind=kind, home_runtime=home_runtime)
    # Re-resolution: update in place (the PK runtime_address is stable). save() bumps `updated_at`,
    # resetting the cooldown whether or not the classification changed.
    account.origin, account.kind = origin, kind
    account.home_runtime = home_runtime  # type: ignore[assignment]  # nullable field, tortoise types it non-optional
    await account.save()
    return account
