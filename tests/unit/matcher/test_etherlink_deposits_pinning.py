from datetime import timedelta

import pytest

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import RuntimeKind
from tests.unit.matcher import factories as f

pytestmark = [pytest.mark.anyio]


async def test_coords_bearing_l2_leg_completes_the_bridge_operation(db):
    xtz = await f.seed_xtz()
    inbox = await f.inbox_message(level=100, index=5)
    bridge = await f.bridge_deposit(await f.l1_deposit(xtz.ticket), inbox=inbox, runtime_kind=RuntimeKind.michelson)
    l2 = await f.evm_l2_deposit(xtz, inbox_message_level=100, inbox_message_index=5)

    BridgeMatcherLocks.set_pending_etherlink_deposits()
    await f.run_matcher_pass()

    await bridge.refresh_from_db()
    assert bridge.l2_transaction_id == l2.id
    operation = await BridgeOperation.get(id=bridge.id)
    assert operation.is_completed
    assert operation.is_successful
    assert operation.status == BridgeOperationStatus.finished
    assert operation.runtime_kind == RuntimeKind.evm


@pytest.mark.parametrize(
    ('resolved_token', 'ticket_hash', 'ticket_owner', 'status', 'is_successful'),
    [
        (True, '1', 'xtz_evm', BridgeOperationStatus.finished, True),
        (False, '1', 'cd' * 20, BridgeOperationStatus.revertable, False),
        (False, None, '', BridgeOperationStatus.empty_proxy, False),
        (False, None, 'ab' * 20, BridgeOperationStatus.proxy_not_whitelisted, False),
    ],
    ids=['finished', 'revertable', 'empty_proxy', 'proxy_not_whitelisted'],
)
async def test_l2_token_ticket_and_owner_select_the_status(db, resolved_token, ticket_hash, ticket_owner, status, is_successful):
    xtz = await f.seed_xtz()
    inbox = await f.inbox_message(level=100, index=5)
    bridge = await f.bridge_deposit(await f.l1_deposit(xtz.ticket), inbox=inbox)
    await f.evm_l2_deposit(
        xtz if resolved_token else None,
        inbox_message_level=100,
        inbox_message_index=5,
        ticket=xtz.ticket if ticket_hash else None,
        ticket_owner=ticket_owner,
    )

    BridgeMatcherLocks.set_pending_etherlink_deposits()
    await f.run_matcher_pass()

    operation = await BridgeOperation.get(id=bridge.id)
    assert operation.is_completed
    assert operation.status == status
    assert operation.is_successful is is_successful


async def test_l2_leg_without_coords_links_no_inbox_less_bridge_deposit(db):
    xtz = await f.seed_xtz()
    bridge = await f.bridge_deposit(await f.l1_deposit(xtz.ticket))
    l2 = await f.evm_l2_deposit(xtz, inbox_message_level=None, inbox_message_index=None)

    BridgeMatcherLocks.set_pending_etherlink_deposits()
    await f.run_matcher_pass()

    await bridge.refresh_from_db()
    assert bridge.l2_transaction_id is None
    assert await BridgeDepositOperation.filter(l2_transaction=l2.id).count() == 0
    operation = await BridgeOperation.get(id=bridge.id)
    assert not operation.is_completed
    assert operation.status == BridgeOperationStatus.created


async def test_newest_of_two_bridge_deposits_on_one_inbox_message_wins(db):
    xtz = await f.seed_xtz()
    inbox = await f.inbox_message(level=100, index=5)
    older = await f.bridge_deposit(await f.l1_deposit(xtz.ticket), inbox=inbox, created_at=f.TS - timedelta(hours=1))
    newer = await f.bridge_deposit(await f.l1_deposit(xtz.ticket), inbox=inbox, created_at=f.TS + timedelta(hours=1))
    l2 = await f.evm_l2_deposit(xtz, inbox_message_level=100, inbox_message_index=5)

    BridgeMatcherLocks.set_pending_etherlink_deposits()
    await f.run_matcher_pass()

    await newer.refresh_from_db()
    await older.refresh_from_db()
    assert newer.l2_transaction_id == l2.id
    assert older.l2_transaction_id is None


async def test_bridge_deposit_that_already_has_an_l2_leg_is_skipped_not_overwritten(db):
    xtz = await f.seed_xtz()
    inbox = await f.inbox_message(level=100, index=5)
    open_ = await f.bridge_deposit(await f.l1_deposit(xtz.ticket), inbox=inbox, created_at=f.TS - timedelta(hours=1))
    already_linked = await f.evm_l2_deposit(xtz, inbox_message_level=None, inbox_message_index=None)
    taken = await f.bridge_deposit(await f.l1_deposit(xtz.ticket), inbox=inbox, l2=already_linked, created_at=f.TS + timedelta(hours=1))
    l2 = await f.evm_l2_deposit(xtz, inbox_message_level=100, inbox_message_index=5)

    BridgeMatcherLocks.set_pending_etherlink_deposits()
    await f.run_matcher_pass()

    await taken.refresh_from_db()
    await open_.refresh_from_db()
    assert taken.l2_transaction_id == already_linked.id
    assert open_.l2_transaction_id == l2.id


async def test_second_pass_over_a_matched_deposit_changes_nothing(db):
    xtz = await f.seed_xtz()
    l1 = await f.l1_deposit(xtz.ticket, level=100, parameters_hash='a' * 32)
    await f.inbox_message(level=100, index=5, parameters_hash='a' * 32)
    l2 = await f.evm_l2_deposit(xtz, inbox_message_level=100, inbox_message_index=5)

    await f.run_deposit_matching()
    bridge = await BridgeDepositOperation.get(l1_transaction_id=l1.id)
    first = await BridgeOperation.get(id=bridge.id)

    await f.run_deposit_matching()

    await bridge.refresh_from_db()
    second = await BridgeOperation.get(id=bridge.id)
    assert bridge.l2_transaction_id == l2.id
    assert second.updated_at == first.updated_at
    assert second.status == first.status
    assert (second.is_completed, second.is_successful, second.runtime_kind) == (True, True, RuntimeKind.evm)
    assert await BridgeDepositOperation.all().count() == 1
