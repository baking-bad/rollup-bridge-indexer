from dipdup.context import HandlerContext
from dipdup.models import Index
from dipdup.models import IndexStatus
from dipdup.models.evm_subsquid import SubsquidEvent
from tortoise.exceptions import DoesNotExist

from bridge_indexer.handlers import setup_handler_logger
from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import EtherlinkWithdrawOperation
from bridge_indexer.types.kernel.evm_events.withdrawal import Withdrawal


async def on_withdraw(
    ctx: HandlerContext,
    event: SubsquidEvent[Withdrawal],
) -> None:
    setup_handler_logger(ctx)
    ctx.logger.info(f'Etherlink Withdraw Event found: {event.data.transaction_hash}')
    ctx.logger.debug(f'https://blockscout.dipdup.net/tx/0x{event.data.transaction_hash}')
    token_contract = event.payload.ticket_owner.removeprefix('0x')
    etherlink_token = await EtherlinkToken.get_or_none(id=token_contract)
    if not etherlink_token:
        if event.payload.sender == event.payload.ticket_owner:
            ctx.logger.warning('Uncommon Withdraw Routing Info: `ticket_owner == sender`. Mark Operation as `Deposit Revert`.')
        else:
            ctx.logger.warning(
                'Incorrect Withdraw Routing Info: Specified `erc_proxy` contract not whitelisted: {}. Operation ignored.', token_contract
            )
            return

    try:
        outbox_message = await ctx.container.outbox_message_service.find_by_index(event.payload.outbox_level, event.payload.outbox_msg_id)
    except DoesNotExist:
        ctx.logger.error(
            'Failed to fetch Outbox Message with level %d and index %d. Operation ignored.',
            event.payload.outbox_level,
            event.payload.outbox_msg_id,
        )
        return

    withdrawal = await EtherlinkWithdrawOperation.create(
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

    ctx.logger.info(f'Etherlink Withdraw Event registered: {withdrawal.id}')

    sync_level = ctx.datasources['etherlink_node']._subscriptions._subscriptions[None]
    status = await Index.get(name='etherlink_kernel_events').only('status').values_list('status', flat=True)
    if status == IndexStatus.realtime or sync_level - event.data.level < 5:
        await BridgeMatcher.check_pending_etherlink_withdrawals()
        await BridgeMatcher.check_pending_tezos_withdrawals()
