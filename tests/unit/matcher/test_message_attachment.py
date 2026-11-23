"""Attaching rollup messages to bridge rows: `check_pending_inbox` / `check_pending_outbox`.

Both steps hand one message to one bridge row. Two operations with identical parameters
share a hash, so the pass has to keep them apart — that is what these tests state.
"""

from datetime import UTC
from datetime import datetime
from datetime import timedelta

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


async def test_attaching_an_outbox_message_consumes_both_hashes(db):
    xtz = await seed_xtz()
    l2 = await l2_withdrawal(xtz, parameters_hash='b' * 32)
    outbox = await outbox_message(parameters_hash='b' * 32)

    await run_withdrawal_matching()

    await l2.refresh_from_db()
    await outbox.refresh_from_db()
    assert l2.parameters_hash is None
    assert outbox.parameters_hash is None


async def test_an_outbox_message_older_than_the_withdrawal_is_not_its_own(db):
    """Mainnet, withdrawal 8c0cdf8c: an outbox message the kernel wrote before the L2 index
    started has no L2 leg, so its hash is never consumed. Taken oldest-first, it went to the
    next identical withdrawal a month later — which then carried someone else's L1 execution
    or expired — and every identical withdrawal after it took its predecessor's message."""
    xtz = await seed_xtz()
    first_ts = datetime(2024, 11, 11, 23, 51, 23, tzinfo=UTC)
    first = await l2_withdrawal(xtz, level=150, parameters_hash='b' * 32, timestamp=first_ts)
    second = await l2_withdrawal(xtz, level=160, parameters_hash='b' * 32, timestamp=first_ts + timedelta(hours=1))
    stale = await outbox_message(level=6782016, parameters_hash='b' * 32, created_at=datetime(2024, 10, 6, 12, 0, tzinfo=UTC))
    own_first = await outbox_message(level=7092875, parameters_hash='b' * 32, created_at=first_ts + timedelta(seconds=12))
    own_second = await outbox_message(level=7093200, parameters_hash='b' * 32, created_at=first_ts + timedelta(hours=1, seconds=12))

    await run_withdrawal_matching()

    assert (await BridgeWithdrawOperation.get(l2_transaction_id=first.id)).outbox_message_id == own_first.id
    assert (await BridgeWithdrawOperation.get(l2_transaction_id=second.id)).outbox_message_id == own_second.id
    await stale.refresh_from_db()
    assert stale.parameters_hash == 'b' * 32, 'the stale message must stay unclaimed'


@pytest.mark.parametrize(('outbox_offset', 'attached'), [(-300, True), (-301, False)])
async def test_an_outbox_level_may_precede_its_withdrawal_by_the_blueprint_lookahead(db, outbox_offset, attached):
    """The kernel accepts a blueprint stamped up to `max_blueprint_lookahead_in_seconds` (300 s)
    past the L1 time it has seen, so an outbox level's timestamp can trail the L2 block by that
    much and no more."""
    xtz = await seed_xtz()
    l2 = await l2_withdrawal(xtz, parameters_hash='b' * 32)
    outbox = await outbox_message(parameters_hash='b' * 32, created_at=l2.timestamp + timedelta(seconds=outbox_offset))

    await run_withdrawal_matching()

    expected = outbox.id if attached else None
    assert (await BridgeWithdrawOperation.get(l2_transaction_id=l2.id)).outbox_message_id == expected
