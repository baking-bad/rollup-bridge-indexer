"""Alias resolution in `resolve_l2_account` / `L2Account.get_or_create_for`.

Only the `originOf` precompile (an `eth.call`) is faked; everything else is real ORM on
in-memory sqlite.
"""

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
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import OriginKind
from rollup_bridge_indexer.models import RuntimeKind

pytestmark = pytest.mark.anyio

# Public previewnet sample: native tz account and its EVM alias (40-hex, no 0x).
NATIVE = 'tz1ekkzEN2LB1cpf7dCaonKt6x9KVd9YVydc'
ALIAS = '21ab829afe4b41dd3fb6eb5be4de2d20d9cebc21'


def _ctx(eth_call: AsyncMock, has_michelson_runtime: bool = True):
    """Fake HandlerContext: `config.custom['has_michelson_runtime']` drives whether the rollup is
    treated as having a Michelson runtime (so `originOf` is authoritative), and `eth_call` stands in
    for the `originOf` precompile. Use the named wrappers below, not this."""
    datasource = SimpleNamespace(web3=SimpleNamespace(eth=SimpleNamespace(call=eth_call)))
    config = SimpleNamespace(custom={'has_michelson_runtime': has_michelson_runtime})
    return SimpleNamespace(config=config, get_evm_node_datasource=lambda _name: datasource)


def _ctx_origin_of_returns(raw: bytes):
    """Michelson runtime expected, and the node's `originOf` returns `raw`. Used both for happy-path
    tuples and for malformed answers (precompile absent → empty `0x`, or a wrong/garbage node →
    non-empty but undecodable) that must surface as RuntimeGatewayUnsupportedError."""
    return _ctx(AsyncMock(return_value=raw))


def _ctx_with_origin_of(kind_int: int, native_address: str, home_runtime_int: int = 0):
    """`originOf` eth_call returns `(kind_int, home_runtime_int, native_address)`, so resolution is
    driven without a real EVM node. Rollup has a Michelson runtime (originOf is authoritative)."""
    return _ctx_origin_of_returns(encode(['uint8', 'uint8', 'string'], [kind_int, home_runtime_int, native_address]))


def _ctx_no_michelson_runtime():
    """Plain Etherlink rollup: no Michelson runtime, so `originOf` must never be called. The eth_call
    spy lets a test assert it stays untouched."""
    return _ctx(AsyncMock(), has_michelson_runtime=False)


def _ctx_missing_flag():
    """Config omits `has_michelson_runtime` entirely — whether to resolve aliases is unknown. There is
    no safe default, so this must be a hard config error rather than a guess."""
    ctx = _ctx(AsyncMock())
    ctx.config.custom = {}
    return ctx


def _ctx_resolving_alias(native_address: str):
    """`originOf` reports the address is an alias of `native_address`, home runtime Michelson (0)."""
    return _ctx_with_origin_of(ORIGIN_KIND_ALIAS, native_address, home_runtime_int=0)


def _ctx_resolving_native(address: str):
    """`originOf` reports the address is native — its own origin, home runtime EVM (1)."""
    return _ctx_with_origin_of(1, '0x' + address, home_runtime_int=1)


def _ctx_resolving_unknown():
    """`originOf` has no record for the address yet (kind 0 → unknown, empty native address)."""
    return _ctx_with_origin_of(0, '')


async def test_evm_alias_and_tz_leg_share_one_origin(db):
    # Michelson tz-side leg records the native account; EVM-alias leg resolves the same native.
    tz_row = await L2Account.get_or_create_for(NATIVE, RuntimeKind.michelson)
    alias_row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)

    # Each runtime address is its own row, both sharing the native origin in the Michelson runtime.
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
    # Reverse index order: the alias row is recorded first, then the tz-side leg.
    alias_row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)
    tz_row = await L2Account.get_or_create_for(NATIVE, RuntimeKind.michelson)

    assert await L2Account.all().count() == 2
    assert tz_row.origin == NATIVE == alias_row.origin
    # The tz-side leg is its own row and does not touch the already-recorded alias row.
    assert (alias_row.runtime_address, alias_row.kind) == (ALIAS, OriginKind.alias)


async def test_plain_evm_account_is_its_own_origin(db):
    # originOf reports a native account: the row is its own origin, kind native, home runtime EVM.
    evm = 'cd' * 20
    row = await resolve_l2_account(_ctx_resolving_native(evm), evm)

    assert await L2Account.all().count() == 1
    assert row.origin == evm == row.runtime_address
    assert (row.kind, row.home_runtime) == (OriginKind.native, RuntimeKind.evm)


async def test_missing_has_michelson_runtime_config_raises(db):
    # The config did not declare has_michelson_runtime, so whether this rollup has a Michelson runtime
    # is unknown. Defaulting either way is wrong (true crashes EVM-only nodes; false silently
    # misclassifies real aliases as native), so the absence is a hard config error. No row is written.
    with pytest.raises(ConfigurationError):
        await resolve_l2_account(_ctx_missing_flag(), 'ab' * 20)
    assert await L2Account.all().count() == 0


async def test_no_michelson_runtime_classifies_native_without_originof(db):
    # On a rollup without a Michelson runtime there are no aliases and no RuntimeGateway precompile:
    # every EVM address is a native EVM account, and originOf must not be queried at all.
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
    # Michelson runtime is expected (flag true) but the node's originOf answer is not a valid
    # (uint8, uint8, string) tuple — the precompile is absent (empty `0x`) or the node is wrong and
    # returns garbage. Either way, fail loud with our diagnostic error (not a cryptic eth_abi decode
    # error), and write no row, so the wrong node / wrong has_michelson_runtime config surfaces.
    with pytest.raises(RuntimeGatewayUnsupportedError):
        await resolve_l2_account(_ctx_origin_of_returns(raw), 'ab' * 20)
    assert await L2Account.all().count() == 0


async def test_unknown_recovered_after_cooldown(db):
    # First sighting before the native account is initialized: originOf has no record (unknown).
    row = await resolve_l2_account(_ctx_resolving_unknown(), ALIAS)
    assert (row.origin, row.kind, row.home_runtime) == (ALIAS, OriginKind.unknown, None)

    # Age the row past the recheck cooldown (`.update()` bypasses the auto_now bump on `.save()`).
    await L2Account.filter(runtime_address=ALIAS).update(updated_at=datetime.now(UTC) - timedelta(days=2))

    # Next sighting now resolves the alias and updates the same row in place.
    row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)
    assert await L2Account.all().count() == 1
    assert (row.runtime_address, row.origin, row.kind, row.home_runtime) == (
        ALIAS,
        NATIVE,
        OriginKind.alias,
        RuntimeKind.michelson,
    )


async def test_within_cooldown_not_rechecked(db):
    # First sighting records an unknown row.
    await resolve_l2_account(_ctx_resolving_unknown(), ALIAS)

    # A second sighting within the cooldown must NOT call originOf again, even though it would now
    # report an alias — the cached row is returned untouched.
    ctx = _ctx_resolving_alias(NATIVE)
    row = await resolve_l2_account(ctx, ALIAS)
    ctx.get_evm_node_datasource('etherlink_node').web3.eth.call.assert_not_called()
    assert (row.origin, row.kind) == (ALIAS, OriginKind.unknown)
