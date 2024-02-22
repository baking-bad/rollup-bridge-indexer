from dipdup.context import HandlerContext
from dipdup.models import Index
from dipdup.models import IndexStatus
from dipdup.models.evm_subsquid import SubsquidEvent
from tortoise.exceptions import DoesNotExist

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.rollup_message import OutboxMessageService
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import EtherlinkWithdrawOperation
from bridge_indexer.types.kernel.evm_events.withdrawal import Withdrawal


async def on_withdraw(
    ctx: HandlerContext,
    event: SubsquidEvent[Withdrawal],
) -> None:
    token_contract = event.payload.ticket_owner[-40:]
    etherlink_token = await EtherlinkToken.get_or_none(id=token_contract)
    if not etherlink_token:
        if event.payload.sender == event.payload.ticket_owner:
            ctx.logger.info('Deposit revert found', event)
        else:
            ctx.logger.warning('Withdraw with not whitelisted erc_proxy contract', event)
            return


    try:
        outbox_message = await OutboxMessageService.find_by_index(event.payload.outbox_level, event.payload.outbox_msg_id, ctx)
    except DoesNotExist:
        ctx.logger.error(
            'Failed to fetch Outbox Message with level %d and index %d.',
            event.payload.outbox_level,
            event.payload.outbox_msg_id,
        )
        return

    await EtherlinkWithdrawOperation.create(
        timestamp=event.data.timestamp,
        level=event.data.level,
        address=event.data.address[-40:],
        log_index=event.data.log_index,
        transaction_hash=event.data.transaction_hash[-64:],
        transaction_index=event.data.transaction_index,
        l2_account=event.payload.sender[-40:],
        l1_account=event.payload.receiver,
        l2_token=etherlink_token,
        ticket_id=event.payload.ticket_hash,
        amount=event.payload.amount,
        outbox_message=outbox_message,
    )

    ctx.logger.info(f'Withdraw Event registered: {event}')

    sync_level = ctx.datasources['etherlink_node']._subscriptions._subscriptions[None]
    status = await Index.get(name='etherlink_kernel_events').only('status').values_list('status', flat=True)
    if status == IndexStatus.realtime or sync_level - event.data.level < 5:
        await BridgeMatcher.check_pending_etherlink_withdrawals()
        await BridgeMatcher.check_pending_tezos_withdrawals()
