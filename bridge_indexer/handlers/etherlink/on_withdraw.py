from dipdup.context import HandlerContext
from dipdup.models.evm_subsquid import SubsquidEvent

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import EtherlinkWithdrawEvent
from bridge_indexer.models import TezosTicket
from bridge_indexer.types.kernel.evm_events.withdrawal import Withdrawal


async def on_withdraw(
    ctx: HandlerContext,
    event: SubsquidEvent[Withdrawal],
) -> None:
    token_contract = event.payload.ticket_owner[-40:]
    etherlink_token = await EtherlinkToken.get_or_none(id=token_contract)
    if not etherlink_token:
        raise ValueError('L2 token not found!')

    ticket = await TezosTicket.get_or_none(ticket_hash=event.payload.ticket_hash)
    assert ticket.id == etherlink_token.ticket_id

    await EtherlinkWithdrawEvent.create(
        timestamp=event.data.timestamp,
        level=event.data.level,
        address=event.data.address[-40:],
        log_index=event.data.log_index,
        transaction_hash=event.data.transaction_hash[-64:],
        transaction_index=event.data.transaction_index,
        l2_account=event.payload.sender[-40:],
        l1_account=event.payload.receiver,
        l2_token=etherlink_token,
        amount=event.payload.amount,
        outbox_level=event.payload.outbox_level,
        outbox_msg_id=event.payload.outbox_msg_id,
    )

    ctx.logger.info(f'Withdraw Event registered: {event}')

    await BridgeMatcher.check_pending_etherlink_withdrawals()
    await BridgeMatcher.check_pending_tezos_withdrawals()
