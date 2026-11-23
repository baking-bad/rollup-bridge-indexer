from datetime import timedelta

import pytest

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import RuntimeKind
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models import TezosToken
from tests.unit.matcher.factories import NATIVE_TICKETER
from tests.unit.matcher.factories import TS
from tests.unit.matcher.factories import evm_l2_deposit
from tests.unit.matcher.factories import inbox_message
from tests.unit.matcher.factories import l1_deposit
from tests.unit.matcher.factories import run_deposit_matching
from tests.unit.matcher.factories import run_matcher_pass
from tests.unit.matcher.factories import seed_xtz

pytestmark = [pytest.mark.anyio]

ACCT = 'ab' * 20
ACCT_OTHER = 'cd' * 20


async def mk_l1(xtz, *, level, ts, amount='1000000', receiver=ACCT, ticket=None, with_inbox=True):
    ph = format(level, '032d')
    l1 = await l1_deposit(ticket or xtz.ticket, level=level, amount=amount, l2_account=receiver, parameters_hash=ph, timestamp=ts)
    if with_inbox:
        await inbox_message(id=level, level=level, index=0, parameters_hash=ph)
    return l1


async def mk_l2(xtz, *, level, ts, amount_wei='1000000' + '0' * 12, receiver=ACCT):
    return await evm_l2_deposit(
        xtz,
        level=level,
        inbox_message_level=None,
        inbox_message_index=None,
        amount_wei=amount_wei,
        l2_account=receiver,
        timestamp=ts,
    )


async def linked_l2_of(l1):
    return (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id


async def test_matched_xtz_credit_finishes_the_operation_with_the_l2_runtime_kind(db):
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS)
    l2 = await mk_l2(xtz, level=200, ts=TS + timedelta(seconds=20))

    await run_deposit_matching()

    bridge = await BridgeDepositOperation.get(l1_transaction_id=l1.id)
    assert bridge.l2_transaction_id == l2.id
    operation = await BridgeOperation.get(id=bridge.id)
    assert operation.is_completed
    assert operation.is_successful
    assert operation.status == BridgeOperationStatus.finished
    assert operation.runtime_kind == RuntimeKind.evm


async def test_match_nulls_the_parameters_hash_on_both_the_l1_transaction_and_the_inbox_message(db):
    xtz = await seed_xtz()
    ph = 'a' * 32
    l1 = await l1_deposit(xtz.ticket, level=100, amount='1000000', l2_account=ACCT, parameters_hash=ph, timestamp=TS)
    inbox = await inbox_message(id=1, level=999, index=0, parameters_hash=ph)
    BridgeMatcherLocks.set_pending_tezos_deposits()
    await run_matcher_pass()
    bridge = await BridgeDepositOperation.get(l1_transaction_id=l1.id)
    assert bridge.inbox_message_id is None
    bridge.inbox_message = inbox
    await bridge.save()

    l2 = await mk_l2(xtz, level=200, ts=TS)
    BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()
    await run_matcher_pass()

    await bridge.refresh_from_db()
    assert bridge.l2_transaction_id == l2.id
    await l1.refresh_from_db()
    assert l1.parameters_hash is None, 'the L1 transaction kept its parameters_hash'
    await inbox.refresh_from_db()
    assert inbox.parameters_hash is None, 'the inbox message kept its parameters_hash'


async def test_amount_off_by_one_order_of_magnitude_does_not_pair(db):
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS, amount='1000000')
    l2 = await mk_l2(xtz, level=200, ts=TS, amount_wei='10000000' + '0' * 12)

    await run_deposit_matching()

    assert await linked_l2_of(l1) is None
    assert await BridgeDepositOperation.filter(l2_transaction_id=l2.id).count() == 0


async def test_amount_scale_follows_the_token_decimals_and_not_a_hardcoded_gap(db):
    xtz = await seed_xtz(evm_decimals=15)
    l1 = await mk_l1(xtz, level=100, ts=TS, amount='1000000')
    l2 = await mk_l2(xtz, level=200, ts=TS, amount_wei='1000000' + '0' * 9)

    await run_deposit_matching()

    assert await linked_l2_of(l1) == l2.id


async def test_l1_outside_the_300s_window_on_either_side_does_not_pair(db):
    xtz = await seed_xtz()
    late = await mk_l1(xtz, level=100, ts=TS + timedelta(seconds=1))
    stale = await mk_l1(xtz, level=101, ts=TS - timedelta(seconds=301))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert await linked_l2_of(late) is None, 'an L1 after the L2 credit was paired'
    assert await linked_l2_of(stale) is None, 'an L1 older than the 300s window was paired'
    assert await BridgeDepositOperation.filter(l2_transaction_id=l2.id).count() == 0


async def test_deposit_to_a_different_l2_account_loses_to_the_matching_one(db):
    xtz = await seed_xtz()
    other = await mk_l1(xtz, level=100, ts=TS - timedelta(seconds=100), receiver=ACCT_OTHER)
    same = await mk_l1(xtz, level=101, ts=TS - timedelta(seconds=50), receiver=ACCT)
    l2 = await mk_l2(xtz, level=200, ts=TS, receiver=ACCT)

    await run_deposit_matching()

    assert await linked_l2_of(same) == l2.id
    assert await linked_l2_of(other) is None


async def test_deposit_on_a_different_ticket_loses_to_the_matching_one(db):
    xtz = await seed_xtz()
    fa_token = await TezosToken.create(id='fa', contract_address=NATIVE_TICKETER, name='FA', symbol='FA', decimals=6, type='fa2')
    fa_ticket = await TezosTicket.create(hash='2', ticketer_address=NATIVE_TICKETER, token=fa_token, whitelisted=True)
    other_ticket = await mk_l1(xtz, level=100, ts=TS - timedelta(seconds=100), ticket=fa_ticket)
    native = await mk_l1(xtz, level=101, ts=TS - timedelta(seconds=50))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert await linked_l2_of(native) == l2.id
    assert await linked_l2_of(other_ticket) is None


async def test_deposit_without_an_inbox_message_loses_to_one_that_has_it(db):
    xtz = await seed_xtz()
    no_inbox = await mk_l1(xtz, level=100, ts=TS - timedelta(seconds=100), with_inbox=False)
    attached = await mk_l1(xtz, level=101, ts=TS - timedelta(seconds=50))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert await linked_l2_of(attached) == l2.id
    assert await linked_l2_of(no_inbox) is None


async def test_earliest_l1_by_timestamp_wins_among_admissible_candidates(db):
    xtz = await seed_xtz()
    oldest = await mk_l1(xtz, level=100, ts=TS - timedelta(seconds=100))
    middle = await mk_l1(xtz, level=101, ts=TS - timedelta(seconds=50))
    newest = await mk_l1(xtz, level=102, ts=TS - timedelta(seconds=10))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert await linked_l2_of(oldest) == l2.id, 'the earliest admissible L1 must take the credit'
    assert await linked_l2_of(middle) is None
    assert await linked_l2_of(newest) is None


async def test_two_credits_one_deposit_pairs_the_first_l2_and_stays_put(db):
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS)
    first = await mk_l2(xtz, level=200, ts=TS + timedelta(seconds=10))
    second = await mk_l2(xtz, level=201, ts=TS + timedelta(seconds=10))

    await run_deposit_matching()

    assert await linked_l2_of(l1) == first.id
    assert await BridgeDepositOperation.filter(l2_transaction_id=second.id).count() == 0

    await run_deposit_matching()

    assert await linked_l2_of(l1) == first.id, 'a repeat pass handed the deposit to the other credit'
    assert await BridgeDepositOperation.filter(l2_transaction_id=second.id).count() == 0


async def test_repeat_pass_over_a_matched_deposit_changes_nothing(db):
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS)
    l2 = await mk_l2(xtz, level=200, ts=TS + timedelta(seconds=10))
    await run_deposit_matching()
    operation = await BridgeOperation.get(id=(await BridgeDepositOperation.get(l1_transaction_id=l1.id)).id)
    before = operation.updated_at

    await run_deposit_matching()

    assert await linked_l2_of(l1) == l2.id
    assert await BridgeDepositOperation.all().count() == 1, 'a repeat pass created another bridge deposit'
    await operation.refresh_from_db()
    assert operation.updated_at == before, 'a repeat pass re-saved an already matched operation'
    assert operation.status == BridgeOperationStatus.finished
