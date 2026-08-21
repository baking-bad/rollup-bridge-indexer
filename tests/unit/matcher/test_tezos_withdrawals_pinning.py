"""What `check_pending_tezos_withdrawals` does today, pinned row by row.

The step walks every unmatched L1 execution and asks the database for one open bridge
withdrawal per row. The behaviour that survives a rewrite of that loop is not only "the L1
leg lands somewhere": it is *which* candidate wins when several are eligible, which rows are
excluded from the walk entirely, and what the step leaves alone. Those are the assertions
here — each one green against the current implementation, so a rewrite has something to
fall over.

The tie-break is implicit: `BridgeWithdrawOperation.Meta.ordering = ('-created_at',)` applies
to the step's inner queryset because it has no `order_by`, so `.first()` returns the
*newest-created* open candidate.
"""

from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import RollupOutboxMessageBuilder
from tests.unit.matcher import factories as f

pytestmark = [pytest.mark.anyio]

HOUR_BEFORE = f.TS - timedelta(hours=1)


async def test_l1_execution_closes_the_bridge_withdrawal_holding_its_outbox(db):
    """The whole point of the step: an `sr_execute` of a kernel outbox message settles the
    bridge withdrawal that holds it."""
    xtz = await f.seed_xtz()
    l2 = await f.l2_withdrawal(xtz, parameters_hash='b' * 32)
    outbox = await f.outbox_message(parameters_hash='b' * 32)
    l1 = await f.l1_withdrawal(outbox)

    await f.run_withdrawal_matching()

    bridge = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id)
    assert bridge.l1_transaction_id == l1.id

    operation = await BridgeOperation.get(id=bridge.id)
    assert operation.is_completed
    assert operation.is_successful
    assert operation.status == BridgeOperationStatus.finished


async def test_settlement_stamps_updated_at_at_save_time_not_from_the_l1_leg(db):
    """The step assigns `updated_at = l1_withdrawal.timestamp`, but the assignment does not
    reach the database: `updated_at` is `auto_now`, so Tortoise overwrites it with wall clock
    on `save()`. Pinned because a rewrite that settles rows through a bulk `.update()` would
    *stop* triggering `auto_now` (it fires per instance, not per query) and would silently
    start persisting the L1 timestamp instead."""
    xtz = await f.seed_xtz()
    await f.l2_withdrawal(xtz, parameters_hash='b' * 32)
    outbox = await f.outbox_message(parameters_hash='b' * 32)
    l1 = await f.l1_withdrawal(outbox)

    before = datetime.now(UTC)
    await f.run_withdrawal_matching()

    operation = await BridgeOperation.get(id=(await BridgeWithdrawOperation.first()).id)
    assert operation.updated_at != l1.timestamp
    assert operation.updated_at >= before


async def test_withdrawal_without_its_l1_leg_stays_open(db):
    """No execution yet: the bridge row holds its outbox message and stays unfinished."""
    xtz = await f.seed_xtz()
    l2 = await f.l2_withdrawal(xtz, parameters_hash='b' * 32)
    outbox = await f.outbox_message(parameters_hash='b' * 32)

    await f.run_withdrawal_matching()

    bridge = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id)
    assert bridge.outbox_message_id == outbox.id
    assert bridge.l1_transaction_id is None

    operation = await BridgeOperation.get(id=bridge.id)
    assert not operation.is_completed
    assert operation.status == BridgeOperationStatus.created


async def test_service_provider_payout_is_left_to_the_claimed_fast_step(db):
    """`outbox_message__builder=kernel` is the only thing keeping this step off a fast-
    withdrawal payout. The state below would match but for that filter — an open bridge
    withdrawal holding the payout's own outbox message — and must stay open.

    Only this step's lock is armed, so `check_pending_claimed_fast_withdrawals` (the step the
    payout belongs to) cannot be the one that leaves the row alone.
    """
    xtz = await f.seed_xtz()
    l2 = await f.l2_withdrawal(xtz, parameters_hash='c' * 32, fast_payload=b'\x01\x02', kernel_withdrawal_id=7)
    payout_outbox = await f.outbox_message(level=205, builder=RollupOutboxMessageBuilder.service_provider, parameters_hash='7')
    bridge = await f.bridge_withdrawal(l2, outbox=payout_outbox)
    payout = await f.l1_withdrawal(payout_outbox)

    BridgeMatcherLocks.set_pending_tezos_withdrawals()
    await f.run_matcher_pass()

    await bridge.refresh_from_db()
    assert bridge.l1_transaction_id is None
    assert await BridgeWithdrawOperation.filter(l1_transaction_id=payout.id).count() == 0
    operation = await BridgeOperation.get(id=bridge.id)
    assert not operation.is_completed
    assert operation.status == BridgeOperationStatus.created


async def test_newest_created_bridge_withdrawal_wins_a_shared_outbox_message(db):
    """Two open bridge withdrawals on one outbox message: the `-created_at` default ordering
    hands the L1 leg to the one created LAST. A rewrite that batches candidates into a dict
    keyed by outbox message keeps whichever row it iterates last — under this ordering the
    *older* one — and the swap is silent."""
    xtz = await f.seed_xtz()
    outbox = await f.outbox_message(parameters_hash='b' * 32)
    # Distinct parameters_hash values so no candidate can pick up an outbox message of its own.
    older_l2 = await f.l2_withdrawal(xtz, parameters_hash='y' * 32, transaction_index=0, timestamp=HOUR_BEFORE)
    newer_l2 = await f.l2_withdrawal(xtz, parameters_hash='z' * 32, transaction_index=1)
    older = await f.bridge_withdrawal(older_l2, outbox=outbox, created_at=HOUR_BEFORE)
    newer = await f.bridge_withdrawal(newer_l2, outbox=outbox)
    l1 = await f.l1_withdrawal(outbox)

    await f.run_withdrawal_matching()

    await older.refresh_from_db()
    await newer.refresh_from_db()
    assert newer.l1_transaction_id == l1.id
    assert older.l1_transaction_id is None
    assert (await BridgeOperation.get(id=newer.id)).status == BridgeOperationStatus.finished
    assert (await BridgeOperation.get(id=older.id)).status == BridgeOperationStatus.created


async def test_bridge_withdrawal_without_an_outbox_message_is_never_matched(db):
    """An L1 execution always has an outbox message (the FK is non-null), so a bridge
    withdrawal that has none can never be its counterpart — no matter how many executions are
    pending. A rewrite that indexes candidates by `outbox_message_id` must keep the null key
    out of that index, or every pending execution becomes a match for it."""
    xtz = await f.seed_xtz()
    # A hash no outbox message carries, so the outbox step cannot attach one behind our back.
    l2 = await f.l2_withdrawal(xtz, parameters_hash='q' * 32)
    bridge = await f.bridge_withdrawal(l2, outbox=None)
    for level, index in ((200, 0), (201, 1), (202, 2)):
        await f.l1_withdrawal(await f.outbox_message(level=level, index=index, parameters_hash=f'{level}'))

    await f.run_withdrawal_matching()

    await bridge.refresh_from_db()
    assert bridge.outbox_message_id is None
    assert bridge.l1_transaction_id is None
    operation = await BridgeOperation.get(id=bridge.id)
    assert not operation.is_completed
    assert operation.status == BridgeOperationStatus.created


async def test_settled_bridge_withdrawal_is_not_rematched_or_restamped(db):
    """An L1 execution that already has its bridge withdrawal is out of the walk entirely
    (`bridge_withdrawals=None`), so neither its own settled row nor the open sibling sharing
    that outbox message is touched. The settled row is deliberately left unstamped by the
    factory: if anything re-processed the execution it would flip to `finished` here."""
    xtz = await f.seed_xtz()
    outbox = await f.outbox_message(parameters_hash='b' * 32)
    l1 = await f.l1_withdrawal(outbox)
    settled_l2 = await f.l2_withdrawal(xtz, parameters_hash='y' * 32, transaction_index=0, timestamp=HOUR_BEFORE)
    open_l2 = await f.l2_withdrawal(xtz, parameters_hash='z' * 32, transaction_index=1)
    settled = await f.bridge_withdrawal(settled_l2, outbox=outbox, l1=l1, created_at=HOUR_BEFORE)
    still_open = await f.bridge_withdrawal(open_l2, outbox=outbox)

    await f.run_withdrawal_matching()

    await settled.refresh_from_db()
    await still_open.refresh_from_db()
    assert settled.l1_transaction_id == l1.id
    assert still_open.l1_transaction_id is None
    assert (await BridgeOperation.get(id=settled.id)).status == BridgeOperationStatus.created
    assert (await BridgeOperation.get(id=still_open.id)).status == BridgeOperationStatus.created


async def test_second_pass_over_a_settled_withdrawal_changes_nothing(db):
    """Every lock is raised again by `on_synchronized`, so the step re-runs over an already
    settled backlog constantly. `updated_at` is the witness: it is `auto_now`, so any re-save
    of the operation — even one writing identical values — would move it."""
    xtz = await f.seed_xtz()
    l2 = await f.l2_withdrawal(xtz, parameters_hash='b' * 32)
    outbox = await f.outbox_message(parameters_hash='b' * 32)
    l1 = await f.l1_withdrawal(outbox)

    await f.run_withdrawal_matching()
    bridge = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id)
    operation = await BridgeOperation.get(id=bridge.id)

    await f.run_withdrawal_matching()

    await bridge.refresh_from_db()
    after = await BridgeOperation.get(id=bridge.id)
    assert bridge.l1_transaction_id == l1.id
    assert after.updated_at == operation.updated_at
    assert after.status == BridgeOperationStatus.finished
    assert await BridgeWithdrawOperation.all().count() == 1
