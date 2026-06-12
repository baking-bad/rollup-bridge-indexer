"""Behavior of one batch-handler matching pass over indexed deposit rows.

Each test states a world (rows the handlers indexed), runs one production
matching pass, and asserts which bridge links exist afterwards.
"""

import pytest

from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from tests.unit.matcher.factories import evm_l2_deposit
from tests.unit.matcher.factories import inbox_message
from tests.unit.matcher.factories import l1_deposit
from tests.unit.matcher.factories import michelson_l2_deposit
from tests.unit.matcher.factories import run_deposit_matching
from tests.unit.matcher.factories import seed_xtz

pytestmark = pytest.mark.anyio


async def test_deposit_with_matching_inbox_coords_is_finished(db):
    # The regular path: L1 deposit + its inbox message (same parameters hash),
    # L2 event carrying that message's coords -> one finished bridge operation.
    xtz = await seed_xtz()
    l1 = await l1_deposit(xtz.ticket, level=100, parameters_hash='a' * 32)
    await inbox_message(level=100, index=5, parameters_hash='a' * 32)
    l2 = await evm_l2_deposit(xtz, inbox_message_level=100, inbox_message_index=5)

    await run_deposit_matching()

    bridge = await BridgeDepositOperation.get(l1_transaction_id=l1.id)
    assert bridge.l2_transaction_id == l2.id
    operation = await BridgeOperation.get(id=bridge.id)
    assert operation.is_completed
    assert operation.is_successful
    assert operation.status == BridgeOperationStatus.finished


async def test_evm_xtz_deposit_without_coords_still_matches_by_value(db):
    # EVM XTZ deposits carry no inbox coords either — they must keep flowing
    # through the value-based xtz step (ticket + receiver + amount + time window)
    # once the deposit's inbox message is attached.
    xtz = await seed_xtz()
    receiver = 'ab' * 20
    l1 = await l1_deposit(xtz.ticket, level=100, amount='1000000', l2_account=receiver, parameters_hash='a' * 32)
    await inbox_message(level=100, index=5, parameters_hash='a' * 32)
    l2 = await evm_l2_deposit(xtz, inbox_message_level=None, inbox_message_index=None, l2_account=receiver)

    await run_deposit_matching()

    bridge = await BridgeDepositOperation.get(l1_transaction_id=l1.id)
    assert bridge.l2_transaction_id == l2.id
    operation = await BridgeOperation.get(id=bridge.id)
    assert operation.is_completed
    assert operation.status == BridgeOperationStatus.finished


async def test_l2_deposit_without_coords_never_links_to_unrelated_deposit(db):
    # tezosx-shadownet prod incident (2026-06-11): a Michelson L2 row carries no
    # inbox coords, and the deposit whose inbox message is missing carries no
    # coords either — coords-based matching has nothing to compare, so it must
    # NOT link them, however plausible the pair looks. (Tortoise renders the
    # None-coords filter as LEFT JOIN ... IS NULL, which matched everything.)
    xtz = await seed_xtz()
    l1 = await l1_deposit(xtz.ticket, level=100, amount='5000000', l2_account='tz1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
    # No inbox message indexed for this deposit (inbox backfill gap).
    michelson = await michelson_l2_deposit(xtz, amount_mutez=1000000, l2_account='tz1bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')

    await run_deposit_matching()

    bridge = await BridgeDepositOperation.get(l1_transaction_id=l1.id)
    assert bridge.l2_transaction_id is None
    operation = await BridgeOperation.get(id=bridge.id)
    assert not operation.is_completed
    assert operation.status == BridgeOperationStatus.created
    await michelson.refresh_from_db()
    assert michelson.inbox_message_level is None


async def test_michelson_deposit_matches_by_op_hash_and_backfills_coords(db):
    # The intended Michelson path: once the deposit's inbox message is indexed, the
    # op-hash matcher reconstructs the L2 synthetic-op hash from the inbox data and
    # links deterministically, backfilling coords so the row looks event-matched.
    # Live golden vector: inbox (3599297, 8) -> opAhDW... (1 XTZ to tz1PSJ...).
    inbox_payload = {
        'LL': {
            'bytes': '01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0',
            'ticket': {'amount': '1000000', 'address': 'KT1FcWeWiEC7Ve5JMdZpKyvaFdsJv7n4GFzi', 'content': {'nat': '0', 'bytes': None}},
        }
    }
    receiver = 'tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7'
    op_hash = 'opAhDWYxwDWFnKXG892itvC1TmMtUbeuSThVopzVDGd41mRxomE'

    xtz = await seed_xtz()
    l1 = await l1_deposit(xtz.ticket, level=3599297, amount='1000000', l2_account=receiver, parameters_hash='a' * 32)
    await inbox_message(level=3599297, index=8, message=inbox_payload, parameters_hash='a' * 32)
    michelson = await michelson_l2_deposit(xtz, op_hash=op_hash, amount_mutez=1000000, l2_account=receiver)

    await run_deposit_matching()

    bridge = await BridgeDepositOperation.get(l1_transaction_id=l1.id)
    assert bridge.l2_transaction_id == michelson.id
    await michelson.refresh_from_db()
    assert (michelson.inbox_message_level, michelson.inbox_message_index) == (3599297, 8)
    operation = await BridgeOperation.get(id=bridge.id)
    assert operation.is_completed
    assert operation.is_successful
    assert operation.status == BridgeOperationStatus.finished
