"""The withdrawal direction, end to end: L2 event -> outbox attach -> L1 settlement.

Deposits start on L1 and are completed by L2; withdrawals run the other way, so the four
withdrawal steps have their own shapes and their own failure modes. These are the happy
paths — they exist so a rewrite of any withdrawal step has something to fall over.
"""

import pytest

from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationKind
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import BridgeOperationType
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import RollupOutboxMessageBuilder
from tests.unit.matcher import factories as f

pytestmark = [pytest.mark.anyio]


async def test_kernel_withdrawal_settles_on_l1(db):
    """L2 withdrawal -> outbox message by parameters_hash -> L1 execute closes it."""
    xtz = await f.seed_xtz()
    l2 = await f.l2_withdrawal(xtz, parameters_hash='b' * 32)
    outbox = await f.outbox_message(parameters_hash='b' * 32)
    l1 = await f.l1_withdrawal(outbox)

    await f.run_withdrawal_matching()

    bridge = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id)
    assert bridge.outbox_message_id == outbox.id
    assert bridge.l1_transaction_id == l1.id

    operation = await BridgeOperation.get(id=bridge.id)
    assert operation.type == BridgeOperationType.withdrawal
    assert operation.is_completed and operation.is_successful
    assert operation.status == BridgeOperationStatus.finished

    # The attach consumes both hashes, so a second pass cannot re-attach the same message.
    await l2.refresh_from_db()
    await outbox.refresh_from_db()
    assert l2.parameters_hash is None
    assert outbox.parameters_hash is None


async def test_withdrawal_without_its_l1_leg_stays_open(db):
    """No L1 execute yet: the bridge row exists, holds its outbox, and stays unfinished."""
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


async def test_claimed_fast_withdrawal_splits_into_two_operations(db):
    """A service provider pays the user early, then claims the kernel's outbox message.

    The user's operation is re-pointed at the payout (kind `fast_withdrawal_payed_out`) and
    a second operation is created for the provider, carrying the original kernel outbox.
    """
    xtz = await f.seed_xtz()
    l2 = await f.l2_withdrawal(xtz, parameters_hash='c' * 32, fast_payload=b'\x01\x02', kernel_withdrawal_id=7)
    kernel_outbox = await f.outbox_message(parameters_hash='c' * 32)
    payout_outbox = await f.outbox_message(
        level=205,
        builder=RollupOutboxMessageBuilder.service_provider,
        message=f.fast_payout_message(l2),
        parameters_hash='7',
    )
    payout = await f.l1_withdrawal(payout_outbox)

    await f.run_withdrawal_matching()

    customer = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id, l1_transaction_id=payout.id)
    assert customer.outbox_message_id == payout_outbox.id
    customer_operation = await BridgeOperation.get(id=customer.id)
    assert customer_operation.kind == BridgeOperationKind.fast_withdrawal_claimed
    assert customer_operation.status == BridgeOperationStatus.finished
    assert customer_operation.is_completed

    provider = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id, l1_transaction_id=None)
    assert provider.outbox_message_id == kernel_outbox.id
    provider_operation = await BridgeOperation.get(id=provider.id)
    assert provider_operation.kind == BridgeOperationKind.fast_withdrawal_service_provider
    assert provider_operation.l1_account == 'tz1providerXXXXXXXXXXXXXXXXXXXXXXXXX'


async def test_fast_payout_with_a_mismatched_field_pairs_nothing(db):
    """The six-field payload comparison is the only thing standing between a user's
    withdrawal and an unrelated provider's payout — one wrong field must pair nothing."""
    xtz = await f.seed_xtz()
    l2 = await f.l2_withdrawal(xtz, parameters_hash='c' * 32, fast_payload=b'\x01\x02', kernel_withdrawal_id=7)
    await f.outbox_message(parameters_hash='c' * 32)
    message = f.fast_payout_message(l2)
    message['withdrawal']['base_withdrawer'] = 'tz1someoneElseXXXXXXXXXXXXXXXXXXXXXX'
    payout_outbox = await f.outbox_message(
        level=205,
        builder=RollupOutboxMessageBuilder.service_provider,
        message=message,
        parameters_hash='7',
    )
    payout = await f.l1_withdrawal(payout_outbox)

    await f.run_withdrawal_matching()

    assert await BridgeWithdrawOperation.filter(l1_transaction_id=payout.id).count() == 0
    assert await BridgeWithdrawOperation.all().count() == 1
