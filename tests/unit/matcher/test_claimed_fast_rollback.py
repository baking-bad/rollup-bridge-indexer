"""Contract: an L1 rollback leaves a claimed fast withdrawal as it was before the claim.

The claimed-fast split is written by two indexes, and DipDup journals and reverts each one
separately. The kernel's outbox message is stored by the rollup-message drain, which runs in
the `tezos_head` handler, so its INSERT is journalled under `tezos_head`. The split
(`check_pending_claimed_fast_withdrawals`) runs in the `batch` of the index that delivered the
payout, `tezos_claim_fast_withdrawal_operations`, and journals its writes under that index.
One of those writes moves the customer's `bridge_withdrawal.outbox_message` off the kernel
message, and the revert of that write puts the kernel message's id back.

When both writes fall in the window of one L1 reorg and each index reverts only its own
journal, `tezos_head` going first deletes the kernel message before the claim index restores
the customer's reference to it, and the database refuses the UPDATE with
`bridge_withdrawal_outbox_message_id_fkey`. `hooks/on_index_rollback.py` therefore reverts the
journal of every index on the datasource in one pass.

Offline: rows come from the matcher factories, the two indexes' writes are made inside
DipDup's own versioned transactions, and each rollback is the package's `on_index_rollback`
hook calling DipDup's own `DipDupContext.rollback`.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any

import pytest
from dipdup.context import DipDupContext
from dipdup.transactions import TransactionManager

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.hooks.on_index_rollback import on_index_rollback
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import RollupOutboxMessage
from rollup_bridge_indexer.models import RollupOutboxMessageBuilder
from rollup_bridge_indexer.models import TezosWithdrawOperation
from tests.unit.matcher import factories as f

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.anyio

HEAD_INDEX = 'tezos_head'
CLAIM_INDEX = 'tezos_claim_fast_withdrawal_operations'
# An index on another datasource: its levels count L2 blocks, not L1 ones.
L2_INDEX = 'etherlink_withdrawal_events'
DATASOURCES = {HEAD_INDEX: 'tzkt', CLAIM_INDEX: 'tzkt', L2_INDEX: 'etherlink_node'}
# Any non-zero depth opens a versioned transaction.
ROLLBACK_DEPTH = 2

# The L1 block that both the drain of the kernel message and the payout land in, and that the
# reorg removes.
REORG_LEVEL = 300
PROVIDER = 'tz1providerXXXXXXXXXXXXXXXXXXXXXXXXX'
WITHDRAWAL_ID = 7


@asynccontextmanager
async def _handler_of(index: str, level: int) -> AsyncIterator[None]:
    """The versioned transaction DipDup opens around an index's handlers at `level`."""
    manager = TransactionManager(depth=ROLLBACK_DEPTH)
    async with manager.register(), manager.in_transaction(level=level, index=index):
        yield


def _datasources(index: str) -> tuple[Any, ...]:
    return (SimpleNamespace(name=DATASOURCES[index]),)


async def _roll_back(index: str, to_level: int) -> None:
    """DipDup's response to a reorg on `index`'s channel: the package's `on_index_rollback`.

    `ctx.rollback` is DipDup's own, bound to a context carrying only what it reads.
    """
    config = SimpleNamespace(
        package='rollup_bridge_indexer',
        advanced=SimpleNamespace(rollback_depth=ROLLBACK_DEPTH),
        indexes={name: SimpleNamespace(datasources=_datasources(name)) for name in DATASOURCES},
    )
    ctx: Any = SimpleNamespace(
        config=config,
        transactions=TransactionManager(),
        logger=logging.getLogger('test.on_index_rollback'),
        _rolled_back_indexes=set(),
        _skipped_rollbacks=set(),
    )
    ctx.rollback = partial(DipDupContext.rollback, ctx)

    async def execute_sql(name: str) -> None:
        pass

    ctx.execute_sql = execute_sql
    rolled_back: Any = SimpleNamespace(name=index, datasources=_datasources(index))
    async with ctx.transactions.register():
        await on_index_rollback(ctx, rolled_back, from_level=to_level + 1, to_level=to_level)


async def _claimed_fast_withdrawal_at_reorg_level() -> Any:
    """A fast withdrawal whose kernel message and payout both arrive in `REORG_LEVEL`.

    The L2 withdrawal and its bridge row predate the reorg and are not journalled. Returns
    the L2 withdrawal.
    """
    xtz = await f.seed_xtz()
    params_hash = 'c' * 32
    l2 = await f.l2_withdrawal(xtz, parameters_hash=params_hash, fast_payload=b'\x01', kernel_withdrawal_id=WITHDRAWAL_ID)
    BridgeMatcherLocks.set_pending_etherlink_withdrawals()
    await f.run_matcher_pass()

    async with _handler_of(HEAD_INDEX, REORG_LEVEL):
        await f.outbox_message(level=REORG_LEVEL - 100, parameters_hash=params_hash)

    async with _handler_of(CLAIM_INDEX, REORG_LEVEL):
        payout_outbox = await f.outbox_message(
            level=REORG_LEVEL - 95,
            builder=RollupOutboxMessageBuilder.service_provider,
            message=f.fast_payout_message(l2, service_provider=PROVIDER),
            parameters_hash=str(WITHDRAWAL_ID),
        )
        await f.l1_withdrawal(payout_outbox, level=REORG_LEVEL)
        await f.run_withdrawal_matching()

    rows = await BridgeWithdrawOperation.filter(l2_transaction_id=l2.id).count()
    assert rows == 2, 'the split did not happen, so there is nothing for the rollback to undo'
    return l2


async def _assert_back_to_before_the_reorg(l2: Any) -> None:
    """One bridge row for the withdrawal, waiting for its outbox message and its L1 leg."""
    customer = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id)
    assert customer.outbox_message_id is None  # type: ignore[attr-defined]  # tortoise generates the FK id attr
    assert customer.l1_transaction_id is None  # type: ignore[attr-defined]
    operation = await BridgeOperation.get(id=customer.id)
    assert operation.status == BridgeOperationStatus.created
    assert await BridgeOperation.all().count() == 1
    assert await RollupOutboxMessage.all().count() == 0
    assert await TezosWithdrawOperation.all().count() == 0


async def _head_then_claim() -> None:
    await _roll_back(HEAD_INDEX, REORG_LEVEL - 1)
    await _roll_back(CLAIM_INDEX, REORG_LEVEL - 1)


async def _claim_then_head() -> None:
    await _roll_back(CLAIM_INDEX, REORG_LEVEL - 1)
    await _roll_back(HEAD_INDEX, REORG_LEVEL - 1)


async def _concurrently() -> None:
    # Both reorg messages landed before the same pass of DipDup's main loop, which `gather`s
    # every index's queue, so the two rollbacks run side by side in two transactions.
    await asyncio.gather(_roll_back(HEAD_INDEX, REORG_LEVEL - 1), _roll_back(CLAIM_INDEX, REORG_LEVEL - 1))


@pytest.mark.parametrize('roll_back', [_head_then_claim, _claim_then_head, _concurrently], ids=lambda fn: fn.__name__.strip('_'))
async def test_a_reorg_restores_the_claimed_fast_withdrawal_in_any_rollback_order(db: Any, roll_back: Any) -> None:
    """The reorg reaches `tezos_head` and the claim index as two messages on two TzKT channels,
    and DipDup fixes no order between them: whichever arrives first, or both at once."""
    l2 = await _claimed_fast_withdrawal_at_reorg_level()

    await roll_back()

    await _assert_back_to_before_the_reorg(l2)


async def test_a_reorg_leaves_the_indexes_of_another_datasource_alone(db: Any) -> None:
    """An L2 index writes at L2 levels, far above any L1 level, so an L1 rollback that reached
    into its journal would revert rows the L2 chain never took back."""
    l2 = await _claimed_fast_withdrawal_at_reorg_level()
    async with _handler_of(L2_INDEX, REORG_LEVEL * 1000):
        l2_written = await f.outbox_message(level=REORG_LEVEL * 1000)

    await _head_then_claim()

    assert [message.id for message in await RollupOutboxMessage.all()] == [l2_written.id]
    await l2_written.delete()
    await _assert_back_to_before_the_reorg(l2)
