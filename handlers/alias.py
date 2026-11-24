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

RUNTIME_GATEWAY = Web3.to_checksum_address('0xff00000000000000000000000000000000000007')
ORIGIN_OF_SELECTOR = bytes.fromhex('e3d35459')
RUNTIME_ETHEREUM = 1

ORIGIN_KIND_ALIAS = 2
_ORIGIN_KIND_BY_INT = {0: OriginKind.unknown, 1: OriginKind.native, ORIGIN_KIND_ALIAS: OriginKind.alias}
_RUNTIME_BY_ID = {0: RuntimeKind.michelson, 1: RuntimeKind.evm}

ALIAS_RECHECK_INTERVAL = timedelta(seconds=int(os.environ.get('ALIAS_RECHECK_SECONDS', 86400)))


def _due_for_recheck(account: L2Account) -> bool:
    updated = account.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return datetime.now(UTC) - updated >= ALIAS_RECHECK_INTERVAL


def _use_cached(account: L2Account) -> bool:
    return account.kind != OriginKind.unknown or not _due_for_recheck(account)


class RuntimeGatewayUnsupportedError(Exception): ...


async def _query_origin_of(ctx: HandlerContext, address: str) -> tuple[int, int, str]:
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
    value = ctx.config.custom.get('has_michelson_runtime')
    if not isinstance(value, bool):
        raise ConfigurationError(
            'custom.has_michelson_runtime must be set to a bool: true for a rollup with a Michelson '
            'runtime (originOf authoritative), false for plain Etherlink (EVM-only). There is no safe '
            f'default — got {value!r}.'
        )
    return value


async def resolve_l2_account(ctx: HandlerContext, address: str) -> L2Account:
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
        origin, home_runtime = address, _RUNTIME_BY_ID.get(home_runtime_int)
    else:
        origin, home_runtime = address, None

    if account is None:
        return await L2Account.create(runtime_address=address, origin=origin, kind=kind, home_runtime=home_runtime)
    account.origin, account.kind = origin, kind
    account.home_runtime = home_runtime  # type: ignore[assignment]  # nullable field, tortoise types it non-optional
    await account.save()
    return account
