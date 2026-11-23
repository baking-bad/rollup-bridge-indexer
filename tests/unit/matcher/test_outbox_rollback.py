"""Contract: an L1 rollback of an outbox message keeps the bridge withdrawal attached to it.

The kernel's outbox message is stored by the rollup-message drain in the `tezos_head` handler, so
its INSERT is journalled under `tezos_head`. The withdrawal that claims it is attached by
`check_pending_outbox` in the `batch` of whichever index delivered the pass — on mainnet usually
an L2 index, whose journal an L1 rollback does not touch. Reverting the INSERT deletes the message,
and `bridge_withdrawal.outbox_message` is a plain Tortoise FK, `ON DELETE CASCADE`: the database
deletes the bridge withdrawal with it, past DipDup's journal, and leaves its `bridge_operation`
behind with no withdrawal row. Mainnet lost withdrawals that way on 2026-10-13 and 2026-10-14.

The withdrawal has to survive, and it has to match again once the drain stores the message anew.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.rollup_message import _WITHDRAW_MICHELSON_TYPE
from rollup_bridge_indexer.handlers.rollup_message import OutboxMessageParametersHash
from rollup_bridge_indexer.handlers.ticket import TicketService
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import EtherlinkToken
from rollup_bridge_indexer.models import RollupOutboxMessage
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models import TezosToken
from rollup_bridge_indexer.types.rollup.tezos_parameters.default import TicketContent
from tests.unit.matcher import factories as f
from tests.unit.matcher.test_claimed_fast_rollback import HEAD_INDEX
from tests.unit.matcher.test_claimed_fast_rollback import L2_INDEX
from tests.unit.matcher.test_claimed_fast_rollback import REORG_LEVEL
from tests.unit.matcher.test_claimed_fast_rollback import _handler_of
from tests.unit.matcher.test_claimed_fast_rollback import _roll_back

pytestmark = pytest.mark.anyio

RECEIVER = 'tz1burnburnburnburnburnburnburjAYjjX'
AMOUNT_MUTEZ = 1_000_000


async def _native_withdrawal_message() -> tuple[dict[str, Any], str]:
    """An XTZ withdrawal outbox message as the rollup node serves it, with its real ticket.

    The hash comes from the drain's own derivation, which needs the ticket row its content names.
    """
    ticket_service = TicketService(None, None, None)  # type: ignore[arg-type]  # a registered ticket needs no datasource
    ticket_hash = ticket_service.get_ticket_hash(f.NATIVE_TICKETER, TicketContent.model_validate({'nat': '0', 'bytes': None}))
    token = await TezosToken.create(id='xtz', contract_address=f.NATIVE_TICKETER, decimals=6, type='native')
    ticket = await TezosTicket.create(hash=str(ticket_hash), ticketer_address=f.NATIVE_TICKETER, token=token, whitelisted=True)
    await EtherlinkToken.create(id='xtz_evm', decimals=18, ticket=ticket)

    parameters = _WITHDRAW_MICHELSON_TYPE.from_python_object(
        {
            'receiver': RECEIVER,
            'ticket': {'ticketer': f.NATIVE_TICKETER, 'content': {'ticket_id': 0, 'metadata': None}, 'amount': AMOUNT_MUTEZ},
        }
    ).to_micheline_value()
    message = {'kind': 'untyped', 'transactions': [{'parameters': parameters, 'destination': f.NATIVE_TICKETER, 'entrypoint': 'withdraw'}]}
    parameters_hash = await OutboxMessageParametersHash({'message': message}).from_outbox_message(ticket_service)
    return message, parameters_hash


async def test_a_reorg_of_the_outbox_message_keeps_its_bridge_withdrawal(db: Any) -> None:
    message, parameters_hash = await _native_withdrawal_message()
    l2_token = await EtherlinkToken.get(id='xtz_evm').prefetch_related('ticket')
    l2 = await f.l2_withdrawal(l2_token, l1_account=RECEIVER, parameters_hash=parameters_hash)
    BridgeMatcherLocks.set_pending_etherlink_withdrawals()
    await f.run_matcher_pass()

    async with _handler_of(HEAD_INDEX, REORG_LEVEL):
        await f.outbox_message(level=REORG_LEVEL, message=message, parameters_hash=parameters_hash)
    async with _handler_of(L2_INDEX, REORG_LEVEL * 1000):
        await f.run_withdrawal_matching()
    attached = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id)
    assert attached.outbox_message_id is not None, 'nothing attached to roll back'  # type: ignore[attr-defined]

    container = SimpleNamespace(ticket_service=TicketService(None, None, None))  # type: ignore[arg-type]
    await _roll_back(HEAD_INDEX, REORG_LEVEL - 1, container=container)

    assert await RollupOutboxMessage.all().count() == 0
    withdrawal = await BridgeWithdrawOperation.get_or_none(l2_transaction_id=l2.id)
    assert withdrawal is not None, 'the rollback deleted the bridge withdrawal'
    assert withdrawal.outbox_message_id is None  # type: ignore[attr-defined]  # tortoise generates the FK id attr
    assert await BridgeOperation.filter(id=withdrawal.id).exists()

    # The reorged chain carries the message again, one level later, and the drain stores it.
    redrained = await f.outbox_message(level=REORG_LEVEL + 1, message=message, parameters_hash=parameters_hash)
    BridgeMatcherLocks.set_pending_outbox()
    await f.run_matcher_pass()

    assert (await BridgeWithdrawOperation.get(id=withdrawal.id)).outbox_message_id == redrained.id  # type: ignore[attr-defined]
