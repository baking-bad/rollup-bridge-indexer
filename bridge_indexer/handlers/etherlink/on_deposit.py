from datetime import datetime
from datetime import timezone

from dipdup.context import HandlerContext
from dipdup.models.evm_subsquid import SubsquidEvent

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import EtherlinkDepositEvent
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import TezosTicket
from bridge_indexer.types.kernel.evm_events.deposit import Deposit


async def on_deposit(
    ctx: HandlerContext,
    event: SubsquidEvent[Deposit],
) -> None:
    ticket = await TezosTicket.get_or_none(ticket_hash=event.payload.ticket_hash)
    token_contract = event.payload.ticket_owner[-40:]
    etherlink_token = await EtherlinkToken.get_or_none(id=token_contract)
    if etherlink_token and not etherlink_token.ticket:
        etherlink_token.ticket = ticket
        await etherlink_token.save()
    if not etherlink_token:
        etherlink_token = await EtherlinkToken.create(
            id=token_contract,
            # name = ?
            ticket=ticket,
        )

    await EtherlinkDepositEvent.create(
        timestamp=datetime.fromtimestamp(event.data.timestamp, tz=timezone.utc),
        level=event.data.level,
        address=event.data.address[-40:],
        log_index=event.data.log_index,
        transaction_hash=event.data.transaction_hash[-64:],
        transaction_index=event.data.transaction_index,
        l2_account=event.payload.receiver[-40:],
        l2_token=etherlink_token,
        amount=event.payload.amount,
        inbox_level=event.payload.inbox_level,
        inbox_msg_id=event.payload.inbox_msg_id,
    )

    ctx.logger.info(f'Deposit Event registered: {event}')

    await BridgeMatcher.check_pending_etherlink_deposits()

