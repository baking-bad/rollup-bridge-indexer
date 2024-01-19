from dipdup.context import HandlerContext
from dipdup.models.evm_subsquid import SubsquidEvent

from evm_indexer.models import DepositEvent
from evm_indexer.types.kernel_module.evm_events.deposit import Deposit


async def on_deposit(
    ctx: HandlerContext,
    event: SubsquidEvent[Deposit],
) -> None:
    await DepositEvent.create(
        timestamp=event.data.timestamp,
        level=event.data.level,
        address=event.data.address,
        log_index=event.data.log_index,
        transaction_hash=event.data.transaction_hash,
        transaction_index=event.data.transaction_index,
        ticket_hash=event.payload.ticket_hash,
        ticket_owner=event.payload.ticket_owner,
        receiver=event.payload.receiver,
        amount=event.payload.amount,
        inbox_level=event.payload.inbox_level,
        inbox_msg_id=event.payload.inbox_msg_id,
    )

    ctx.logger.info(f'Deposit Event registered: {event}')
