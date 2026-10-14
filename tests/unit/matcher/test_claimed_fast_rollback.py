"""Contract: an L1 rollback leaves a claimed fast withdrawal as it was before the claim.

The claimed-fast split is written by two indexes, and DipDup journals and reverts each one
separately. The kernel's outbox message is stored by the rollup-message drain, which runs in
the `tezos_head` handler, so its INSERT is journalled under `tezos_head`. The split
(`check_pending_claimed_fast_withdrawals`) runs in the `batch` of the index that delivered the
payout, `tezos_claim_fast_withdrawal_operations`, and journals its writes under that index.
One of those writes moves the customer's `bridge_withdrawal.outbox_message` off the kernel
message, and the revert of that write puts the kernel message's id back.

When both writes fall in the window of one L1 reorg, each index reverts its own journal. If
`tezos_head` goes first, the kernel message is already deleted by the time the claim index
restores the customer's reference to it. Postgres refuses that UPDATE with
`bridge_withdrawal_outbox_message_id_fkey`, the hook raises and the indexer stops.

Offline: rows come from the matcher factories, the two indexes' writes are made inside
DipDup's own versioned transactions, and the rollback is DipDup's `ModelUpdate.revert` over
the journal, selected the way `ctx.rollback` selects it.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from typing import Any

import pytest
from dipdup.models import ModelUpdate
from dipdup.transactions import TransactionManager

from rollup_bridge_indexer import models
from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
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


async def _roll_back(index: str, to_level: int) -> int:
    """`ctx.rollback(index, ..., to_level)`: every journalled update of `index` above
    `to_level`, newest first, in one unversioned transaction. Returns how many were reverted."""
    manager = TransactionManager()
    async with manager.register(), manager.in_transaction():
        updates = await ModelUpdate.filter(level__gt=to_level, index=index).order_by('-id')
        for update in updates:
            await update.revert(getattr(models, update.model_name))
    return len(updates)


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
