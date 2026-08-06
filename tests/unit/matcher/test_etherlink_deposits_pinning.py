"""`check_pending_etherlink_deposits` as it behaves today, asserted field by field.

The step walks every coords-bearing L2 deposit and, per row, queries for an open bridge
deposit on those inbox coordinates. These tests fix what that per-row query answers — which
candidate it picks, which rows it refuses, what it writes onto the bridge operation — so a
rewrite that trades the per-row query for a bulk one has to reproduce the same answers.

Most tests arm `pending_etherlink_deposits` alone: the coords step is the subject, and the
other deposit steps would otherwise be free to link the same rows by value or op-hash and
hide which step did the work.
"""

from datetime import UTC
from datetime import datetime
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
    """The link, and everything the step copies off the L2 leg onto the operation.

    `runtime_kind` is seeded michelson — the value `check_pending_tezos_deposits` derives
    from the L1 receiver shape — so the copy from the (EVM) L2 leg is observable rather
    than a coincidence.
    """
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


async def test_updated_at_is_wall_clock_not_the_l2_timestamp(db):
    """The step assigns `updated_at = l2_deposit.timestamp`, and the column throws it away.

    `updated_at` is `auto_now=True`, so Tortoise overwrites the assignment with `now()` on
    every save. The operation therefore carries the moment it was matched, not the moment
    the L2 leg was produced — pinned because a rewrite could plausibly "fix" the assignment
    and change what consumers read.
    """
    xtz = await f.seed_xtz()
    inbox = await f.inbox_message(level=100, index=5)
    bridge = await f.bridge_deposit(await f.l1_deposit(xtz.ticket), inbox=inbox)
    l2 = await f.evm_l2_deposit(xtz, inbox_message_level=100, inbox_message_index=5)

    before = datetime.now(UTC)
    BridgeMatcherLocks.set_pending_etherlink_deposits()
    await f.run_matcher_pass()

    operation = await BridgeOperation.get(id=bridge.id)
    assert operation.updated_at != l2.timestamp
    assert operation.updated_at >= before


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
    """The four arms of the step's `match`, and that `is_successful` tracks `l2_token` alone.

    Reading the arms as the kernel outcomes they stand for: a token means the deposit landed;
    a ticket without a token means it can be reverted; no ticket at all means routing failed,
    with an empty owner distinguishing a missing proxy from a non-whitelisted one.
    """
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
    """The `isnull=False` guard, from the side that would break without it.

    A coordless L2 row filtered on `inbox_message__level=None` renders as
    `LEFT JOIN ... IS NULL`, which is true for every bridge deposit that has no inbox message
    yet — so dropping the guard links an arbitrary open deposit to an unrelated L2 leg.
    """
    xtz = await f.seed_xtz()
    bridge = await f.bridge_deposit(await f.l1_deposit(xtz.ticket))  # inbox message not indexed yet
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
    """Two open candidates on the same inbox message: the step takes the newest-created.

    `.first()` carries no `order_by`, so Tortoise applies `Meta.ordering = ('-created_at',)`.
    Building the candidate set as a dict keyed by coordinates inverts this — the last row
    iterated is the OLDEST — which is why the loser is constructed and asserted too.
    """
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
    """`l2_transaction=None` in the filter: a taken candidate is passed over for an open one.

    The taken row is also the newest, so without the filter the tie-break would hand it the
    new L2 leg and silently drop the one it already holds.
    """
    xtz = await f.seed_xtz()
    inbox = await f.inbox_message(level=100, index=5)
    open_ = await f.bridge_deposit(await f.l1_deposit(xtz.ticket), inbox=inbox, created_at=f.TS - timedelta(hours=1))
    # Its L2 leg carries no coords — a value-matched XTZ deposit, which is how a bridge
    # deposit on these coordinates can already be taken while a coords-bearing row is pending.
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
    """Both sides of the join are consumed by the first pass, so a re-run is a no-op.

    `updated_at` is the tell: it is `auto_now`, so any save at all would move it.
    """
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
