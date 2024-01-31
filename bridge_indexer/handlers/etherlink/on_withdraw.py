from dipdup.context import HandlerContext
from dipdup.models.evm_subsquid import SubsquidEvent

from bridge_indexer.models import EtherlinkWithdrawEvent
from bridge_indexer.types.kernel_module.evm_events.withdrawal import Withdrawal


async def on_withdraw(
    ctx: HandlerContext,
    event: SubsquidEvent[Withdrawal],
) -> None:
    await EtherlinkWithdrawEvent.create(
        timestamp=event.data.timestamp,
        level=event.data.level,
        address=event.data.address,
        log_index=event.data.log_index,
        transaction_hash=event.data.transaction_hash,
        transaction_index=event.data.transaction_index,
        sender=event.payload.sender,
        ticket_hash=event.payload.ticket_hash,
        ticket_owner=event.payload.ticket_owner,
        receiver=event.payload.receiver,
        amount=event.payload.amount,
        outbox_level=event.payload.outbox_level,
        outbox_msg_id=event.payload.outbox_msg_id,
    )

    ctx.logger.info(f'Withdraw Event registered: {event}')
