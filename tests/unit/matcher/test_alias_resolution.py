"""L2Account alias resolution — one native account, reached from both bridge legs.

A native Tezos account is reachable two ways: its Michelson tz-side receiver
(``L2Account.get_or_create_for``) and its EVM alias (``resolve_l2_account`` via the
``originOf`` precompile). Both must converge on ONE ``l2_account`` row keyed by the
native ``origin``, in either index order — production crash-looped (duplicate ``origin``
PK) when the second leg tried to insert a fresh row for an already-seen native account.

The precompile is the only external dependency, so it (and only it) is faked at the
``eth.call`` boundary; the dedupe behaviour under test is real ORM against sqlite.
"""

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
    """A HandlerContext stub whose `originOf` call returns `(kind_int, 0, native_address)`."""
    raw = encode(['uint8', 'uint8', 'string'], [kind_int, 0, native_address])
    eth = SimpleNamespace(call=AsyncMock(return_value=raw))
    datasource = SimpleNamespace(web3=SimpleNamespace(eth=eth))
    return SimpleNamespace(get_evm_node_datasource=lambda _name: datasource)


def _ctx_resolving_alias(native_address: str):
    """A HandlerContext stub whose `originOf` call reports `address` aliases `native_address`."""
    return _ctx_with_origin_of(ORIGIN_KIND_ALIAS, native_address)


async def test_evm_alias_after_tz_leg_converges_on_one_row(db):
    # Michelson tz-side leg recorded the native account first (the production order).
    tz_row = await L2Account.get_or_create_for(NATIVE, L2AccountKind.tz)
    # EVM-alias leg resolves the same native via the precompile — must not insert a 2nd row.
    alias_row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)

    assert await L2Account.all().count() == 1
    assert alias_row.origin == NATIVE
    assert alias_row.address == ALIAS
    assert alias_row.kind == L2AccountKind.evm_alias
    assert tz_row.origin == alias_row.origin  # both legs share the same FK identity


async def test_tz_leg_after_evm_alias_reuses_one_row(db):
    # EVM-alias leg recorded the native account first.
    alias_row = await resolve_l2_account(_ctx_resolving_alias(NATIVE), ALIAS)
    # Michelson tz-side leg for the SAME native must reuse it, not insert a duplicate-origin row.
    tz_row = await L2Account.get_or_create_for(NATIVE, L2AccountKind.tz)

    assert await L2Account.all().count() == 1
    assert tz_row.origin == NATIVE == alias_row.origin
    # The alias form already recorded is not downgraded back to the tz-side address/kind.
    assert tz_row.address == ALIAS
    assert tz_row.kind == L2AccountKind.evm_alias


async def test_plain_evm_account_is_its_own_origin(db):
    # originOf reports a non-alias account (kind != 2): the row is its own origin, kind=evm.
    evm = 'cd' * 20
    row = await resolve_l2_account(_ctx_with_origin_of(1, '0x' + evm), evm)

    assert await L2Account.all().count() == 1
    assert row.origin == evm == row.address
    assert row.kind == L2AccountKind.evm
