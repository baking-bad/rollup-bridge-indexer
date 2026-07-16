"""Value-path matching when (ticket, receiver, amount) collide across deposits.

The EVM-XTZ value step (`check_pending_etherlink_xtz_deposits`) has no inbox coords to
key on — it zips an L2 row to a bridge deposit by ticket + receiver + scaled amount, inside
a backward time window, picking the earliest L1 by timestamp. When a single account repeats
the *same* amount (the mainnet whale: ~23k near-identical XTZ deposits), that key is no longer
unique and only the window + ordering separate the legs.

Production incident (mainnet, 2026-06-17..21): 18 completed deposits paired a fresh L1 to a
stale 2025 L2 of a *different* account and amount, L2 ~470 days before L1. The current value
filters (receiver-eq + scaled-amount-eq + `l1.timestamp ∈ [l2-140s, l2]`) forbid every one of
those, so these tests pin that the live code stays immune and the leg-integrity invariant holds.

`assert_pairs_are_possible` is the invariant the prod residue violated; it is the unit-test
twin of the monitoring check (account-eq, scaled-amount-eq, L1<=L2) — assert it after every pass.
"""

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

# xtz_evm (18 decimals) over the native xtz ticket (6) — the value step's wei->mutez scale.
SCALE = 10**12
ACCT = 'ab' * 20  # one EVM receiver; the whole point is repeated deposits to the SAME account
ACCT_OTHER = 'cd' * 20


async def mk_l1(xtz, *, level, ts, amount='1000000', receiver=ACCT):
    """An L1 XTZ deposit plus the inbox message that attaches to its bridge deposit.

    Distinct level => distinct parameters_hash and a private inbox row, so the inbox-attach
    step is a clean 1:1 and the test isolates the *value* step.
    """
    ph = format(level, '032d')
    l1 = await l1_deposit(xtz.ticket, level=level, amount=amount, l2_account=receiver, parameters_hash=ph, timestamp=ts)
    await inbox_message(id=level, level=level, index=0, parameters_hash=ph)
    return l1


async def mk_l2(xtz, *, level, ts, amount='1000000', receiver=ACCT):
    """A coords-less EVM XTZ L2 row — the only class the value step handles."""
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
    """The leg-integrity invariant the prod residue broke: every completed pair must agree on
    receiver and scaled amount, and L1 must not be after L2. A violation is a definite mis-stitch."""
    for bridge in await completed_pairs():
        l1, l2 = bridge.l1_transaction, bridge.l2_transaction
        assert l1.l2_account_id == l2.l2_account_id, f'account mismatch in completed pair {bridge.id}'
        assert int(l1.amount) == int(l2.amount) // SCALE, f'amount mismatch in completed pair {bridge.id}'
        assert l1.timestamp <= l2.timestamp, f'L2 precedes L1 in completed pair {bridge.id}'


async def assert_perfect_matching(n):
    """n L1 and n L2, all identical in value: a valid 1:1 zip with nothing stranded."""
    pairs = await completed_pairs()
    assert len(pairs) == n, f'expected {n} completed, got {len(pairs)}'
    l1_ids = {p.l1_transaction_id for p in pairs}
    l2_ids = {p.l2_transaction_id for p in pairs}
    assert len(l1_ids) == n and len(l2_ids) == n, 'a leg was reused across two pairs'
    assert await EtherlinkDepositOperation.filter(bridge_deposits=None).count() == 0, 'an L2 was stranded'


# --- A. collision / isolation / orphan / the prod scenario ---------------------------------


async def test_two_identical_close_pair_one_to_one(db):
    # A1: two identical deposits 10s apart, all legs present in one pass. Legs are
    # interchangeable, but the zip must still be 1:1 (no leg reused, none stranded).
    xtz = await seed_xtz()
    await mk_l1(xtz, level=100, ts=TS)
    await mk_l1(xtz, level=101, ts=TS + timedelta(seconds=10))
    await mk_l2(xtz, level=200, ts=TS)
    await mk_l2(xtz, level=201, ts=TS + timedelta(seconds=10))

    await run_deposit_matching()

    await assert_perfect_matching(2)
    await assert_pairs_are_possible()


async def test_identical_deposits_far_apart_do_not_cross(db):
    # A2: two identical deposits >140s apart. Each L2's window excludes the other's L1, so
    # they must pair within their own time neighbourhood — never cross.
    xtz = await seed_xtz()
    early = await mk_l1(xtz, level=100, ts=TS)
    late = await mk_l1(xtz, level=101, ts=TS + timedelta(seconds=300))
    l2_early = await mk_l2(xtz, level=200, ts=TS)
    l2_late = await mk_l2(xtz, level=201, ts=TS + timedelta(seconds=300))

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=early.id)).l2_transaction_id == l2_early.id
    assert (await BridgeDepositOperation.get(l1_transaction_id=late.id)).l2_transaction_id == l2_late.id
    await assert_pairs_are_possible()


async def test_burst_of_identical_deposits_all_match(db):
    # A3: a 5-deposit burst, each 10s apart (90s span, all mutually in-window), all present.
    # The earliest-first greedy must produce a full 1:1 matching, stranding none.
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
    # A4: an L2 whose own L1 never arrived must not latch onto a later identical L1 that sits
    # outside its window; that later L1 keeps its own L2.
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
    # A5 — the mainnet incident, in miniature. A year-old unmatched L2 and a fresh L1 with the
    # SAME receiver+amount must NOT pair: the window forbids the ~year inversion. The stale leg
    # stays unmatched (a backlog item), the fresh deposit completes on its own L2.
    xtz = await seed_xtz()
    stale_l2 = await mk_l2(xtz, level=200, ts=TS - timedelta(days=300))
    fresh_l1 = await mk_l1(xtz, level=101, ts=TS)
    fresh_l2 = await mk_l2(xtz, level=201, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=fresh_l1.id)).l2_transaction_id == fresh_l2.id
    assert await BridgeDepositOperation.filter(l2_transaction_id=stale_l2.id).count() == 0
    await assert_pairs_are_possible()


# --- B. the time window's four edges -------------------------------------------------------


async def test_l1_on_lower_window_edge_matches(db):
    # B1: L1 exactly 140s before L2 — the inclusive lower bound (`__gte`).
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS - timedelta(seconds=140))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id == l2.id
    await assert_pairs_are_possible()


async def test_l1_equal_timestamp_matches(db):
    # B2: L1 and L2 in the same instant — the inclusive upper bound (`__lte`).
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS)
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id == l2.id
    await assert_pairs_are_possible()


async def test_l1_just_below_lower_window_edge_does_not_match(db):
    # B3: one second past the 140s window — must not match, L2 stays pending.
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS - timedelta(seconds=141))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id is None
    assert await BridgeDepositOperation.filter(l2_transaction_id=l2.id).count() == 0


async def test_l1_after_l2_never_matches(db):
    # B4 — the invariant edge. L1 one second AFTER L2: the upper bound rejects it, so the
    # matcher can never create an "L2 before L1" pair (exactly what the prod residue violated).
    xtz = await seed_xtz()
    l1 = await mk_l1(xtz, level=100, ts=TS + timedelta(seconds=1))
    l2 = await mk_l2(xtz, level=200, ts=TS)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id is None
    assert await BridgeDepositOperation.filter(l2_transaction_id=l2.id).count() == 0
    await assert_pairs_are_possible()


# --- C. tie-break determinism --------------------------------------------------------------


async def test_two_l1_same_timestamp_one_l2_links_exactly_one(db):
    # C1: two identical L1 at the SAME instant, one L2. `order_by('l1_transaction__timestamp')`
    # has no secondary key, so the *which* is unpinned — but the matcher must link exactly one
    # (no double-spend of the L2) and the invariant must hold either way.
    xtz = await seed_xtz()
    a = await mk_l1(xtz, level=100, ts=TS)
    b = await mk_l1(xtz, level=101, ts=TS)
    l2 = await mk_l2(xtz, level=200, ts=TS + timedelta(seconds=10))

    await run_deposit_matching()

    linked = await BridgeDepositOperation.filter(l2_transaction_id=l2.id)
    assert len(linked) == 1, 'the single L2 was linked to more than one L1'
    assert linked[0].l1_transaction_id in {a.id, b.id}
    await assert_pairs_are_possible()


# --- D. receiver discrimination under amount collision -------------------------------------


async def test_same_amount_different_accounts_never_cross(db):
    # D1: two accounts deposit the identical amount in the same window. The receiver filter
    # must keep them apart — this is the axis the prod residue violated (different accounts).
    xtz = await seed_xtz()
    x1 = await mk_l1(xtz, level=100, ts=TS, receiver=ACCT)
    y1 = await mk_l1(xtz, level=101, ts=TS, receiver=ACCT_OTHER)
    x2 = await mk_l2(xtz, level=200, ts=TS + timedelta(seconds=5), receiver=ACCT)
    y2 = await mk_l2(xtz, level=201, ts=TS + timedelta(seconds=5), receiver=ACCT_OTHER)

    await run_deposit_matching()

    assert (await BridgeDepositOperation.get(l1_transaction_id=x1.id)).l2_transaction_id == x2.id
    assert (await BridgeDepositOperation.get(l1_transaction_id=y1.id)).l2_transaction_id == y2.id
    await assert_pairs_are_possible()


# --- F. property fuzz: an all-identical burst under independent indexer lag ----------------


@pytest.mark.parametrize('seed', range(8))
async def test_identical_value_burst_matches_under_independent_lag(db, seed):
    # The whale at speed: N deposits, ALL identical (same receiver + amount), 15s apart so the
    # whole burst sits inside one 140s window — the value key cannot separate them, only the
    # window + ordering can. Each leg-indexer is its own FIFO (L1 / inbox / L2-EVM drain at
    # independent rates), so the per-stream order is preserved while the streams overtake each
    # other. The matcher must still produce a full 1:1 zip with the invariant intact — no
    # stranding, no inversion, no leg reused. (Distinct-amount fuzz lives in test_matcher_fuzz;
    # this is its degenerate, maximally-ambiguous twin.)
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
