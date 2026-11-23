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
    xtz = await f.seed_xtz()
    outbox = await f.outbox_message(parameters_hash='b' * 32)
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
    xtz = await f.seed_xtz()
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
