from datetime import datetime
from datetime import timezone

from dipdup.context import HandlerContext
from dipdup.models import Index
from dipdup.models import IndexStatus
from dipdup.models.evm_subsquid import SubsquidEvent

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.rollup_message import InboxMessageService
from bridge_indexer.models import EtherlinkDepositOperation
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import TezosTicket
from bridge_indexer.types.kernel.evm_events.deposit import Deposit


async def register_etherlink_token(token_contract: str, ticket_hash: int) -> EtherlinkToken:
    etherlink_token = await EtherlinkToken.get_or_none(id=token_contract)

    if etherlink_token:
        return etherlink_token

    tezos_ticket = await TezosTicket.get_or_none(pk=ticket_hash)
    if tezos_ticket:
        if await EtherlinkToken.filter(ticket_id=ticket_hash).exclude(id=token_contract).count():
            raise ValueError('Deposit with not whitelisted erc_proxy contract', token_contract)

        etherlink_token = await EtherlinkToken.create(
            id=token_contract,
            ticket_id=ticket_hash,
        )
        return etherlink_token

    raise ValueError('Ticket with given hash not found', ticket_hash)


async def on_deposit(
    ctx: HandlerContext,
    event: SubsquidEvent[Deposit],
) -> None:
    if event.payload.ticket_owner == event.payload.receiver:
        ctx.logger.warning('Deposit routing info seems wrong')
        etherlink_token = None
    else:
        token_contract = event.payload.ticket_owner[-40:]
        try:
            etherlink_token = await register_etherlink_token(token_contract, event.payload.ticket_hash)
        except ValueError as exception:
            ctx.logger.warning(str(exception))
            return

    inbox_message = await InboxMessageService.find_by_index(event.payload.inbox_level, event.payload.inbox_msg_id, ctx)

    await EtherlinkDepositOperation.create(
        timestamp=datetime.fromtimestamp(event.data.timestamp, tz=timezone.utc),
        level=event.data.level,
        address=event.data.address[-40:],
        log_index=event.data.log_index,
        transaction_hash=event.data.transaction_hash[-64:],
        transaction_index=event.data.transaction_index,
        l2_account=event.payload.receiver[-40:],
        l2_token=etherlink_token,
        ticket_id=event.payload.ticket_hash,
        amount=event.payload.amount,
        inbox_message=inbox_message,
    )

    ctx.logger.info(f'Deposit Event registered: {event}')

    sync_level = ctx.datasources['etherlink_node']._subscriptions._subscriptions[None]
    status = await Index.get(name='etherlink_kernel_events').only('status').values_list('status', flat=True)
    if status == IndexStatus.realtime or sync_level - event.data.level < 5:
        await BridgeMatcher.check_pending_etherlink_deposits()
