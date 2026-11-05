"""An EVM XTZ credit is paired by the transaction hash its L1 deposit derives.

The kernel names the credit keccak256(rlp([wei, receiver, inbox_level, inbox_index]) ++ rollup),
so the delay between the two legs says nothing about whether they belong together. The vectors
below are real mainnet pairs whose credit arrived long after its L1 operation.
"""

from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest
import rlp
from Crypto.Hash import keccak
from pytezos.crypto.encoding import base58_decode

from rollup_bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from tests.unit.matcher.factories import ROLLUP
from tests.unit.matcher.factories import TS
from tests.unit.matcher.factories import evm_l2_deposit
from tests.unit.matcher.factories import inbox_message
from tests.unit.matcher.factories import l1_deposit
from tests.unit.matcher.factories import run_deposit_matching
from tests.unit.matcher.factories import seed_xtz

pytestmark = [pytest.mark.anyio]

MAINNET_ROLLUP = 'sr1Ghq66tYK9y3r8CC1Tf8i8m5nxh8nTvZEf'
ACCT = 'ab' * 20


def kernel_tx_hash(amount_mutez: int, receiver: str, level: int, index: int, rollup: str = ROLLUP) -> str:
    body = rlp.encode([amount_mutez * 10**12, bytes.fromhex(receiver), level, index])
    return keccak.new(digest_bits=256, data=body + base58_decode(rollup.encode())).hexdigest()


async def open_deposit(xtz, *, level, index=2, inbox_level=None, amount='1000000', receiver=ACCT, ts=TS):
    ph = format(level, '032d')
    l1 = await l1_deposit(xtz.ticket, level=level, amount=amount, l2_account=receiver, parameters_hash=ph, timestamp=ts)
    await inbox_message(id=level, level=inbox_level or level, index=index, parameters_hash=ph)
    return l1


async def credit(xtz, *, level, ts, transaction_hash, amount='1000000', receiver=ACCT):
    return await evm_l2_deposit(
        xtz,
        level=level,
        inbox_message_level=None,
        inbox_message_index=None,
        amount_wei=amount + '0' * 12,
        l2_account=receiver,
        timestamp=ts,
        transaction_hash=transaction_hash,
    )


async def linked_l2_of(l1):
    return (await BridgeDepositOperation.get(l1_transaction_id=l1.id)).l2_transaction_id


@pytest.mark.parametrize(
    ('amount', 'receiver', 'inbox_level', 'index', 'l1_ts', 'l2_ts', 'tx_hash'),
    [
        pytest.param(
            '1001000',
            'f9e0f0a650716e7d626c66ed13459cb92491ac05',
            15660790,
            2,
            datetime(2026, 11, 3, 23, 1, 49, tzinfo=UTC),
            datetime(2026, 11, 3, 23, 4, 35, tzinfo=UTC),
            '6bcb6a6d58b9aa934216d939436b3b481a537d540e9d4db581d1064891f93d2f',
            id='166s',
        ),
        pytest.param(
            '219000000',
            'edbe62a59eceb6e3b11d04ee14315ff5d3c29ec6',
            7056014,
            2,
            datetime(2024, 11, 7, 16, 49, 50, tzinfo=UTC),
            datetime(2024, 11, 7, 21, 14, 20, tzinfo=UTC),
            '96ffdc48cda144179913f566766f0bb0a82d88d397cb8f16a715081ed6bdd4c9',
            id='4h24m',
        ),
    ],
)
async def test_real_late_credit_finishes_a_deposit_already_marked_as_timed_out(
    db, amount, receiver, inbox_level, index, l1_ts, l2_ts, tx_hash
):
    # The historical shape: cementation has already written the deposit off when the pass that
    # can pair it finally runs, and the pass has to bring it back.
    BridgeMatcher.smart_rollup_address = MAINNET_ROLLUP
    xtz = await seed_xtz()
    l1 = await open_deposit(xtz, level=100, inbox_level=inbox_level, index=index, amount=amount, receiver=receiver, ts=l1_ts)
    await run_deposit_matching()
    deposit = await BridgeDepositOperation.get(l1_transaction_id=l1.id)
    await BridgeOperation.filter(id=deposit.id).update(status=BridgeOperationStatus.inbox_matching_timeout)

    l2 = await credit(xtz, level=200, ts=l2_ts, transaction_hash=tx_hash, amount=amount, receiver=receiver)
    await run_deposit_matching()

    assert await linked_l2_of(l1) == l2.id
    operation = await BridgeOperation.get(id=deposit.id)
    assert operation.status == BridgeOperationStatus.finished
    assert operation.is_completed is True


async def test_tx_hash_beats_the_earliest_first_tie_break(db):
    # Two open deposits fit by value and the earlier one would win the value step's race.
    xtz = await seed_xtz()
    earlier = await open_deposit(xtz, level=100, ts=TS - timedelta(seconds=20))
    later = await open_deposit(xtz, level=101, ts=TS - timedelta(seconds=10))
    l2 = await credit(xtz, level=200, ts=TS, transaction_hash=kernel_tx_hash(1000000, ACCT, 101, 2))

    await run_deposit_matching()

    assert await linked_l2_of(later) == l2.id
    assert await linked_l2_of(earlier) is None


async def test_deposit_claimed_by_tx_hash_is_not_offered_to_the_value_step(db):
    # One credit is named by its hash, the other names nobody: the second must not take the
    # deposit the first has just claimed in the same pass.
    xtz = await seed_xtz()
    named = await open_deposit(xtz, level=100, ts=TS - timedelta(seconds=20))
    other = await open_deposit(xtz, level=101, ts=TS - timedelta(seconds=10))
    by_value = await credit(xtz, level=200, ts=TS, transaction_hash='aa' * 32)
    by_hash = await credit(xtz, level=201, ts=TS, transaction_hash=kernel_tx_hash(1000000, ACCT, 100, 2))

    await run_deposit_matching()

    assert await linked_l2_of(named) == by_hash.id
    assert await linked_l2_of(other) == by_value.id


async def test_pairing_by_value_warns_because_the_derived_hash_missed(db, caplog):
    # Before the kernel era the derivation covers, and after any kernel that changes it, the value
    # step still pairs the credit. On a live network this line is the signal that the formula moved.
    xtz = await seed_xtz()
    l1 = await open_deposit(xtz, level=100, ts=TS - timedelta(seconds=10))
    l2 = await credit(xtz, level=200, ts=TS, transaction_hash='aa' * 32)

    with caplog.at_level('WARNING', logger='rollup_bridge_indexer.handlers.bridge_matcher'):
        await run_deposit_matching()

    assert await linked_l2_of(l1) == l2.id
    assert 'aa' * 32 in caplog.text
