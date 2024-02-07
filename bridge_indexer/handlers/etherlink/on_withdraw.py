from dipdup.context import HandlerContext
from dipdup.models import Index
from dipdup.models import IndexStatus
from dipdup.models.evm_subsquid import SubsquidEvent

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.rollup_message import OutboxMessageService
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

    outbox_message = await OutboxMessageService.find_by_index(event.payload.outbox_level, event.payload.outbox_msg_id, ctx)

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
        outbox_message=outbox_message,
    )

    ctx.logger.info(f'Withdraw Event registered: {event}')

    status = await Index.get(name='etherlink_kernel_events').only('status').values_list('status', flat=True)
    if status == IndexStatus.realtime:
        await BridgeMatcher.check_pending_etherlink_withdrawals()
        await BridgeMatcher.check_pending_tezos_withdrawals()
