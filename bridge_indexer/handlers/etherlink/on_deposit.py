from datetime import datetime
from datetime import timezone

from dipdup.context import HandlerContext
from dipdup.models.evm_subsquid import SubsquidEvent

from bridge_indexer.models import EtherlinkDepositEvent
from bridge_indexer.types.kernel_module.evm_events.deposit import Deposit


async def on_deposit(
    ctx: HandlerContext,
    event: SubsquidEvent[Deposit],
) -> None:

    await EtherlinkDepositEvent.create(
        timestamp=datetime.fromtimestamp(event.data.timestamp, tz=timezone.utc),
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
