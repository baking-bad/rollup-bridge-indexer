import logging
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from dipdup.exceptions import ConfigurationError
from eth_abi import encode

from rollup_bridge_indexer.handlers.alias import ORIGIN_KIND_ALIAS
from rollup_bridge_indexer.handlers.alias import RuntimeGatewayUnsupportedError
from rollup_bridge_indexer.handlers.alias import resolve_l2_account
from rollup_bridge_indexer.handlers.tezos.on_rollup_call import on_rollup_call
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import OriginKind
from rollup_bridge_indexer.models import RuntimeKind
from rollup_bridge_indexer.models import TezosDepositOperation
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models import TezosToken
from rollup_bridge_indexer.types.rollup.tezos_parameters.default import DefaultParameter

pytestmark = pytest.mark.anyio

NATIVE = 'tz1ekkzEN2LB1cpf7dCaonKt6x9KVd9YVydc'
ALIAS = '21ab829afe4b41dd3fb6eb5be4de2d20d9cebc21'
ROLLUP = 'sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu'
NATIVE_TICKETER = 'KT1FcWeWiEC7Ve5JMdZpKyvaFdsJv7n4GFzi'
DEPOSITOR = 'tz1initiatorXXXXXXXXXXXXXXXXXXXXXXXX'


def _ctx(eth_call: AsyncMock, has_michelson_runtime: bool = True):
    datasource = SimpleNamespace(web3=SimpleNamespace(eth=SimpleNamespace(call=eth_call)))
    config = SimpleNamespace(custom={'has_michelson_runtime': has_michelson_runtime})
    return SimpleNamespace(config=config, get_evm_node_datasource=lambda _name: datasource)


def _ctx_origin_of_returns(raw: bytes):
    return _ctx(AsyncMock(return_value=raw))


def _ctx_with_origin_of(kind_int: int, native_address: str, home_runtime_int: int = 0):
    return _ctx_origin_of_returns(encode(['uint8', 'uint8', 'string'], [kind_int, home_runtime_int, native_address]))


def _ctx_no_michelson_runtime():
    return _ctx(AsyncMock(), has_michelson_runtime=False)


def _ctx_missing_flag():
    ctx = _ctx(AsyncMock())
    ctx.config.custom = {}
    return ctx


def _ctx_resolving_alias(native_address: str):
    return _ctx_with_origin_of(ORIGIN_KIND_ALIAS, native_address, home_runtime_int=0)


def _ctx_resolving_native(address: str):
    return _ctx_with_origin_of(1, '0x' + address, home_runtime_int=1)


def _ctx_resolving_unknown():
    return _ctx_with_origin_of(0, '')


async def test_evm_alias_and_tz_leg_share_one_origin(db):
    tz_row = await L2Account.get_or_create_for(NATIVE, RuntimeKind.michelson)
    alias_row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)

    assert await L2Account.all().count() == 2
    assert (tz_row.runtime_address, tz_row.origin, tz_row.kind, tz_row.home_runtime) == (
        NATIVE,
        NATIVE,
        OriginKind.native,
        RuntimeKind.michelson,
    )
    assert (alias_row.runtime_address, alias_row.origin, alias_row.kind, alias_row.home_runtime) == (
        ALIAS,
        NATIVE,
        OriginKind.alias,
        RuntimeKind.michelson,
    )


async def test_tz_leg_after_evm_alias_leaves_alias_row_intact(db):
    alias_row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)
    tz_row = await L2Account.get_or_create_for(NATIVE, RuntimeKind.michelson)

    assert await L2Account.all().count() == 2
    assert tz_row.origin == NATIVE == alias_row.origin
    assert (alias_row.runtime_address, alias_row.kind) == (ALIAS, OriginKind.alias)


async def test_plain_evm_account_is_its_own_origin(db):
    evm = 'cd' * 20
    row = await resolve_l2_account(_ctx_resolving_native(evm), evm)

    assert await L2Account.all().count() == 1
    assert row.origin == evm == row.runtime_address
    assert (row.kind, row.home_runtime) == (OriginKind.native, RuntimeKind.evm)


async def test_missing_has_michelson_runtime_config_raises(db):
    with pytest.raises(ConfigurationError):
        await resolve_l2_account(_ctx_missing_flag(), 'ab' * 20)
    assert await L2Account.all().count() == 0


async def test_no_michelson_runtime_classifies_native_without_originof(db):
    ctx = _ctx_no_michelson_runtime()
    evm = 'ab' * 20
    row = await resolve_l2_account(ctx, evm)

    ctx.get_evm_node_datasource('etherlink_node').web3.eth.call.assert_not_called()
    assert await L2Account.all().count() == 1
    assert row.origin == evm == row.runtime_address
    assert (row.kind, row.home_runtime) == (OriginKind.native, RuntimeKind.evm)


@pytest.mark.parametrize(
    'raw',
    [
        pytest.param(b'', id='empty-no-precompile'),
        pytest.param(bytes(32), id='truncated'),
        pytest.param(bytes(96), id='nonempty-but-undecodable'),
    ],
)
async def test_originof_undecodable_raises_unsupported(db, raw):
    with pytest.raises(RuntimeGatewayUnsupportedError):
        await resolve_l2_account(_ctx_origin_of_returns(raw), 'ab' * 20)
    assert await L2Account.all().count() == 0


async def test_unknown_recovered_after_cooldown(db):
    row = await resolve_l2_account(_ctx_resolving_unknown(), ALIAS)
    assert (row.origin, row.kind, row.home_runtime) == (ALIAS, OriginKind.unknown, None)

    await L2Account.filter(runtime_address=ALIAS).update(updated_at=datetime.now(UTC) - timedelta(days=2))

    row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)
    assert await L2Account.all().count() == 1
    assert (row.runtime_address, row.origin, row.kind, row.home_runtime) == (
        ALIAS,
        NATIVE,
        OriginKind.alias,
        RuntimeKind.michelson,
    )


async def test_within_cooldown_not_rechecked(db):
    await resolve_l2_account(_ctx_resolving_unknown(), ALIAS)

    ctx = _ctx_resolving_alias(NATIVE)
    row = await resolve_l2_account(ctx, ALIAS)
    ctx.get_evm_node_datasource('etherlink_node').web3.eth.call.assert_not_called()
    assert (row.origin, row.kind) == (ALIAS, OriginKind.unknown)


def _l1_deposit_to(routing_info_hex: str):
    parameter_json = {
        'LL': {
            'bytes': routing_info_hex,
            'ticket': {'address': NATIVE_TICKETER, 'content': {'nat': '0', 'bytes': None}, 'amount': '1000000'},
        }
    }
    data = SimpleNamespace(
        hash='o' + 'l1' * 25,
        timestamp=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        level=100,
        counter=1,
        nonce=None,
        initiator_address=DEPOSITOR,
        sender_address=DEPOSITOR,
        target_address=ROLLUP,
        parameter_json=parameter_json,
    )
    return SimpleNamespace(parameter=DefaultParameter.model_validate(parameter_json), data=data)


async def test_l1_deposit_to_an_evm_alias_records_the_alias_not_a_native(db):
    token = await TezosToken.create(id='xtz', contract_address=NATIVE_TICKETER, name='Tezos', symbol='XTZ', decimals=6, type='native')
    ticket = await TezosTicket.create(hash='1', ticketer_address=NATIVE_TICKETER, token=token, whitelisted=True)
    ctx = _ctx_resolving_alias(NATIVE)
    ctx.logger = logging.getLogger('test.on_rollup_call')
    ctx.container = SimpleNamespace(ticket_service=SimpleNamespace(fetch_ticket=AsyncMock(return_value=ticket)))

    await on_rollup_call(ctx, _l1_deposit_to(ALIAS))

    deposit = await TezosDepositOperation.get()
    assert deposit.l2_account_id == ALIAS
    row = await L2Account.get(runtime_address=ALIAS)
    assert (row.kind, row.origin) == (OriginKind.alias, NATIVE), 'the L1 deposit leg recorded an EVM alias as a native account'
