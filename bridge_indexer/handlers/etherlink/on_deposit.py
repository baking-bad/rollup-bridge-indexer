from datetime import datetime
from datetime import timezone

from dipdup.context import HandlerContext
from dipdup.models import Index
from dipdup.models import IndexStatus
from dipdup.models.evm_subsquid import SubsquidEvent

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.rollup_message import InboxMessageService
from bridge_indexer.models import EtherlinkDepositEvent
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import TezosTicket
from bridge_indexer.types.kernel.evm_events.deposit import Deposit


async def register_etherlink_token(token_contract: str, tezos_ticket_hash: int) -> EtherlinkToken:
    etherlink_token = await EtherlinkToken.get_or_none(id=token_contract)
    tezos_ticket = await TezosTicket.get_or_none(ticket_hash=tezos_ticket_hash)
    if etherlink_token:
        if etherlink_token.tezos_ticket:
            pass
        else:
            if tezos_ticket:
                etherlink_token.tezos_ticket = tezos_ticket
                await etherlink_token.save()
    else:
        etherlink_token = await EtherlinkToken.create(
            id=token_contract,
            tezos_ticket=tezos_ticket,
            tezos_ticket_hash=tezos_ticket_hash,
        )

    return etherlink_token


async def on_deposit(
    ctx: HandlerContext,
    event: SubsquidEvent[Deposit],
) -> None:
    token_contract = event.payload.ticket_owner[-40:]
    etherlink_token = await register_etherlink_token(token_contract, event.payload.ticket_hash)

    inbox_message = await InboxMessageService.find_by_index(event.payload.inbox_level, event.payload.inbox_msg_id, ctx)

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
        inbox_message=inbox_message,
    )

    ctx.logger.info(f'Deposit Event registered: {event}')

    status = await Index.get(name='etherlink_kernel_events').only('status').values_list('status', flat=True)
    if status == IndexStatus.realtime:
        await BridgeMatcher.check_pending_etherlink_deposits()
