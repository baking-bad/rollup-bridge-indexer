"""L2Account alias resolution — one native identity, reached from both bridge legs.

A native Tezos account is reachable two ways: its Michelson tz-side receiver
(``L2Account.get_or_create_for``) and its EVM alias (``resolve_l2_account`` via the
``originOf`` precompile). Each runtime address is its own row keyed by ``runtime_address``;
both legs converge on the same ``origin`` (the native identity), in either index order.

The precompile is the only external dependency, so it (and only it) is faked at the
``eth.call`` boundary; the resolution behaviour under test is real ORM against sqlite.
"""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from eth_abi import encode

from rollup_bridge_indexer.handlers.alias import ORIGIN_KIND_ALIAS
from rollup_bridge_indexer.handlers.alias import resolve_l2_account
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import L2AccountKind

pytestmark = pytest.mark.anyio

# Public previewnet sample: native tz account and its EVM alias (40-hex, no 0x).
NATIVE = 'tz1ekkzEN2LB1cpf7dCaonKt6x9KVd9YVydc'
ALIAS = '21ab829afe4b41dd3fb6eb5be4de2d20d9cebc21'


def _ctx_with_origin_of(kind_int: int, native_address: str):
    """Fake HandlerContext: its `originOf` eth_call returns `(kind_int, 0, native_address)`,
    so resolution is driven without a real EVM node. Use the named wrappers below, not this."""
    raw = encode(['uint8', 'uint8', 'string'], [kind_int, 0, native_address])
    eth = SimpleNamespace(call=AsyncMock(return_value=raw))
    datasource = SimpleNamespace(web3=SimpleNamespace(eth=eth))
    return SimpleNamespace(get_evm_node_datasource=lambda _name: datasource)


def _ctx_resolving_alias(native_address: str):
    """`originOf` reports the address is an alias of `native_address`."""
    return _ctx_with_origin_of(ORIGIN_KIND_ALIAS, native_address)


def _ctx_resolving_native(address: str):
    """`originOf` reports the address is native — its own origin, not an alias (kind=1)."""
    return _ctx_with_origin_of(1, '0x' + address)


async def test_evm_alias_and_tz_leg_share_one_origin(db):
    # Michelson tz-side leg records the native account; EVM-alias leg resolves the same native.
    tz_row = await L2Account.get_or_create_for(NATIVE, L2AccountKind.tz)
    alias_row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)

    # Each runtime address is its own row, both sharing the native origin.
    assert await L2Account.all().count() == 2
    assert (tz_row.runtime_address, tz_row.origin, tz_row.kind) == (NATIVE, NATIVE, L2AccountKind.tz)
    assert (alias_row.runtime_address, alias_row.origin, alias_row.kind) == (ALIAS, NATIVE, L2AccountKind.evm_alias)


async def test_tz_leg_after_evm_alias_leaves_alias_row_intact(db):
    # Reverse index order: the alias row is recorded first, then the tz-side leg.
    alias_row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)
    tz_row = await L2Account.get_or_create_for(NATIVE, L2AccountKind.tz)

    assert await L2Account.all().count() == 2
    assert tz_row.origin == NATIVE == alias_row.origin
    # The tz-side leg is its own row and does not touch the already-recorded alias row.
    assert (alias_row.runtime_address, alias_row.kind) == (ALIAS, L2AccountKind.evm_alias)


async def test_plain_evm_account_is_its_own_origin(db):
    # originOf reports a non-alias account: the row is its own origin, kind=evm.
    evm = 'cd' * 20
    row = await resolve_l2_account(_ctx_resolving_native(evm), evm)

    assert await L2Account.all().count() == 1
    assert row.origin == evm == row.runtime_address
    assert row.kind == L2AccountKind.evm


async def test_not_yet_alias_recovered_after_cooldown(db):
    # First sighting before the native account is initialized: originOf reports non-alias.
    row = await resolve_l2_account(_ctx_resolving_native(ALIAS), ALIAS)
    assert (row.origin, row.kind) == (ALIAS, L2AccountKind.evm)

    # Age the row past the recheck cooldown (`.update()` bypasses the auto_now bump on `.save()`).
    await L2Account.filter(runtime_address=ALIAS).update(updated_at=datetime.now(UTC) - timedelta(days=2))

    # Next sighting now resolves the alias and updates the same row in place.
    row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)
    assert await L2Account.all().count() == 1
    assert (row.runtime_address, row.origin, row.kind) == (ALIAS, NATIVE, L2AccountKind.evm_alias)


async def test_within_cooldown_not_rechecked(db):
    # First sighting records a non-alias row.
    await resolve_l2_account(_ctx_resolving_native(ALIAS), ALIAS)

    # A second sighting within the cooldown must NOT call originOf again, even though it would now
    # report an alias — the cached row is returned untouched.
    ctx = _ctx_resolving_alias(NATIVE)
    row = await resolve_l2_account(ctx, ALIAS)
    ctx.get_evm_node_datasource('etherlink_node').web3.eth.call.assert_not_called()
    assert (row.origin, row.kind) == (ALIAS, L2AccountKind.evm)
