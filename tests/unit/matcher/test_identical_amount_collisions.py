import random
from datetime import timedelta

import pytest

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from tests.unit.matcher.factories import TS
from tests.unit.matcher.factories import evm_l2_deposit
from tests.unit.matcher.factories import inbox_message
from tests.unit.matcher.factories import l1_deposit
from tests.unit.matcher.factories import run_deposit_matching
from tests.unit.matcher.factories import seed_xtz

pytestmark = pytest.mark.anyio

SCALE = 10**12
ACCT = 'ab' * 20
ACCT_OTHER = 'cd' * 20


async def mk_l1(xtz, *, level, ts, amount='1000000', receiver=ACCT):
    ph = format(level, '032d')
    l1 = await l1_deposit(xtz.ticket, level=level, amount=amount, l2_account=receiver, parameters_hash=ph, timestamp=ts)
    await inbox_message(id=level, level=level, index=0, parameters_hash=ph)
    return l1


async def mk_l2(xtz, *, level, ts, amount='1000000', receiver=ACCT):
    return await evm_l2_deposit(
        xtz,
        level=level,
        inbox_message_level=None,
        inbox_message_index=None,
        amount_wei=amount + '0' * 12,
        l2_account=receiver,
        timestamp=ts,
    )


async def completed_pairs():
    return await BridgeDepositOperation.filter(l2_transaction_id__isnull=False).prefetch_related('l1_transaction', 'l2_transaction')


async def assert_pairs_are_possible():
    for bridge in await completed_pairs():
        l1, l2 = bridge.l1_transaction, bridge.l2_transaction
        assert l1.l2_account_id == l2.l2_account_id, f'account mismatch in completed pair {bridge.id}'
        assert int(l1.amount) == int(l2.amount) // SCALE, f'amount mismatch in completed pair {bridge.id}'
        assert l1.timestamp <= l2.timestamp, f'L2 precedes L1 in completed pair {bridge.id}'


async def assert_perfect_matching(n):
    pairs = await completed_pairs()
    assert len(pairs) == n, f'expected {n} completed, got {len(pairs)}'
    l1_ids = {p.l1_transaction_id for p in pairs}
    l2_ids = {p.l2_transaction_id for p in pairs}
    assert len(l1_ids) == n and len(l2_ids) == n, 'a leg was reused across two pairs'
    assert await EtherlinkDepositOperation.filter(bridge_deposits=None).count() == 0, 'an L2 was stranded'


async def test_two_identical_close_pair_one_to_one(db):
    xtz = await seed_xtz()
    await mk_l1(xtz, level=100, ts=TS)
    await mk_l1(xtz, level=101, ts=TS + timedelta(seconds=10))
    await mk_l2(xtz, level=200, ts=TS)
    await mk_l2(xtz, level=201, ts=TS + timedelta(seconds=10))

    await run_deposit_matching()

    await assert_perfect_matching(2)
    await assert_pairs_are_possible()


async def test_identical_deposits_far_apart_do_not_cross(db):
    xtz = await seed_xtz()
    early = await mk_l1(xtz, level=100, ts=TS)
    late = await mk_l1(xtz, level=101, ts=TS + timedelta(seconds=400))
    l2_early = await mk_l2(xtz, level=200, ts=TS)
    l2_late = await mk_l2(xtz, level=201, ts=TS + timedelta(seconds=400))

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=early.id)).l2_transaction_id == l2_early.id
    assert (await BridgeDepositOperation.get(l1_transaction_id=late.id)).l2_transaction_id == l2_late.id
    await assert_pairs_are_possible()


async def test_burst_of_identical_deposits_all_match(db):
    xtz = await seed_xtz()
    n = 5
    for i in range(n):
        await mk_l1(xtz, level=100 + i, ts=TS + timedelta(seconds=10 * i))
    for i in range(n):
        await mk_l2(xtz, level=200 + i, ts=TS + timedelta(seconds=10 * i))

    await run_deposit_matching()

    await assert_perfect_matching(n)
    await assert_pairs_are_possible()


async def test_orphan_l2_does_not_grab_out_of_window_identical_l1(db):
    xtz = await seed_xtz()
    orphan_l2 = await mk_l2(xtz, level=200, ts=TS)
    later_l1 = await mk_l1(xtz, level=101, ts=TS + timedelta(seconds=300))
    later_l2 = await mk_l2(xtz, level=201, ts=TS + timedelta(seconds=300))

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=later_l1.id)).l2_transaction_id == later_l2.id
    await orphan_l2.refresh_from_db()
    assert await BridgeDepositOperation.filter(l2_transaction_id=orphan_l2.id).count() == 0
    await assert_pairs_are_possible()


async def test_stale_l2_backlog_is_not_stitched_to_fresh_identical_l1(db):
    xtz = await seed_xtz()
    stale_l2 = await mk_l2(xtz, level=200, ts=TS - timedelta(days=300))
    fresh_l1 = await mk_l1(xtz, level=101, ts=TS)
    fresh_l2 = await mk_l2(xtz, level=201, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=fresh_l1.id)).l2_transaction_id == fresh_l2.id
    assert await BridgeDepositOperation.filter(l2_transaction_id=stale_l2.id).count() == 0
    await assert_pairs_are_possible()


async def test_l1_on_lower_window_edge_matches(db):
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS - timedelta(seconds=300))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id == l2.id
    await assert_pairs_are_possible()


async def test_l1_equal_timestamp_matches(db):
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS)
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id == l2.id
    await assert_pairs_are_possible()


async def test_l1_just_below_lower_window_edge_does_not_match(db):
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS - timedelta(seconds=301))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id is None
    assert await BridgeDepositOperation.filter(l2_transaction_id=l2.id).count() == 0


async def test_l1_after_l2_never_matches(db):
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS + timedelta(seconds=1))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id is None
    assert await BridgeDepositOperation.filter(l2_transaction_id=l2.id).count() == 0
    await assert_pairs_are_possible()


async def test_two_l1_same_timestamp_one_l2_links_exactly_one(db):
    xtz = await seed_xtz()
    a = await mk_l1(xtz, level=100, ts=TS)
    b = await mk_l1(xtz, level=101, ts=TS)
    l2 = await mk_l2(xtz, level=200, ts=TS + timedelta(seconds=10))

    await run_deposit_matching()

    linked = await BridgeDepositOperation.filter(l2_transaction_id=l2.id)
    assert len(linked) == 1, 'the single L2 was linked to more than one L1'
    assert linked[0].l1_transaction_id in {a.id, b.id}
    await assert_pairs_are_possible()


async def test_same_amount_different_accounts_never_cross(db):
    xtz = await seed_xtz()
    x1 = await mk_l1(xtz, level=100, ts=TS, receiver=ACCT)
    y1 = await mk_l1(xtz, level=101, ts=TS, receiver=ACCT_OTHER)
    x2 = await mk_l2(xtz, level=200, ts=TS + timedelta(seconds=5), receiver=ACCT)
    y2 = await mk_l2(xtz, level=201, ts=TS + timedelta(seconds=5), receiver=ACCT_OTHER)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=x1.id)).l2_transaction_id == x2.id
    assert (await BridgeDepositOperation.get(l1_transaction_id=y1.id)).l2_transaction_id == y2.id
    await assert_pairs_are_possible()


@pytest.mark.parametrize('seed', range(8))
async def test_identical_value_burst_matches_under_independent_lag(db, seed):
    rng = random.Random(seed)
    xtz = await seed_xtz()
    n = 8
    streams: dict[str, list] = {'l1': [], 'inbox': [], 'l2': []}
    for i in range(n):
        level, ts, ph = 100 + i, TS + timedelta(seconds=15 * i), format(100 + i, '032d')

        async def reveal_l1(level=level, ts=ts, ph=ph):
            await l1_deposit(xtz.ticket, level=level, amount='1000000', l2_account=ACCT, parameters_hash=ph, timestamp=ts)
            BridgeMatcherLocks.set_pending_tezos_deposits()

        async def reveal_inbox(level=level, ph=ph):
            await inbox_message(id=level, level=level, index=0, parameters_hash=ph)
            BridgeMatcherLocks.set_pending_inbox()

        async def reveal_l2(level=level, ts=ts):
            await mk_l2(xtz, level=300 + level, ts=ts)
            BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()

        streams['l1'].append(reveal_l1)
        streams['inbox'].append(reveal_inbox)
        streams['l2'].append(reveal_l2)

    while any(streams.values()):
        name = rng.choice([k for k, v in streams.items() if v])
        take = rng.randint(1, len(streams[name]))
        for reveal in streams[name][:take]:
            await reveal()
        streams[name] = streams[name][take:]
        await run_deposit_matching()

    await assert_perfect_matching(n)
    await assert_pairs_are_possible()
