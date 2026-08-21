"""Attaching rollup messages to bridge rows: `check_pending_inbox` / `check_pending_outbox`.

Both steps hand one message to one bridge row. Two operations with identical parameters
share a hash, so the pass has to keep them apart — that is what these tests state.
"""

import pytest

from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from tests.unit.matcher.factories import inbox_message
from tests.unit.matcher.factories import l1_deposit
from tests.unit.matcher.factories import l2_withdrawal
from tests.unit.matcher.factories import outbox_message
from tests.unit.matcher.factories import run_deposit_matching
from tests.unit.matcher.factories import run_withdrawal_matching
from tests.unit.matcher.factories import seed_xtz

pytestmark = pytest.mark.anyio


async def test_two_deposits_sharing_a_hash_take_different_inbox_messages(db):
    xtz = await seed_xtz()
    first = await l1_deposit(xtz.ticket, level=100, parameters_hash='a' * 32)
    second = await l1_deposit(xtz.ticket, level=100, amount='2000000', parameters_hash='a' * 32)
    await inbox_message(id=1, level=100, index=5, parameters_hash='a' * 32)
    await inbox_message(id=2, level=100, index=6, parameters_hash='a' * 32)

    await run_deposit_matching()

    attached = {
        (await BridgeDepositOperation.get(l1_transaction_id=first.id)).inbox_message_id,
        (await BridgeDepositOperation.get(l1_transaction_id=second.id)).inbox_message_id,
    }
    assert attached == {1, 2}


async def test_two_withdrawals_sharing_a_hash_take_different_outbox_messages(db):
    xtz = await seed_xtz()
    first = await l2_withdrawal(xtz, level=150, log_index=0, parameters_hash='b' * 32)
    second = await l2_withdrawal(xtz, level=150, log_index=1, parameters_hash='b' * 32)
    early = await outbox_message(level=200, index=0, parameters_hash='b' * 32)
    late = await outbox_message(level=201, index=0, parameters_hash='b' * 32)

    await run_withdrawal_matching()

    attached = {
        (await BridgeWithdrawOperation.get(l2_transaction_id=first.id)).outbox_message_id,
        (await BridgeWithdrawOperation.get(l2_transaction_id=second.id)).outbox_message_id,
    }
    assert attached == {early.id, late.id}


async def test_an_attached_message_is_not_offered_again(db):
    xtz = await seed_xtz()
    lonely = await l1_deposit(xtz.ticket, level=100, parameters_hash='a' * 32)
    await inbox_message(id=1, level=100, index=5, parameters_hash='a' * 32)

    await run_deposit_matching()
    # A second deposit on the same hash arrives after the message is spoken for.
    late = await l1_deposit(xtz.ticket, level=100, amount='2000000', parameters_hash='a' * 32)
    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=lonely.id)).inbox_message_id == 1
    assert (await BridgeDepositOperation.get(l1_transaction_id=late.id)).inbox_message_id is None
