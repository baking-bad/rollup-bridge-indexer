from dipdup.context import HandlerContext
from dipdup.models import Index
from dipdup.models import IndexStatus
from dipdup.models.tezos_tzkt import TzktTransaction

from bridge_indexer.handlers import setup_handler_logger
from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import TezosDepositOperation
from bridge_indexer.types.rollup.tezos_parameters.default import DefaultParameter
from bridge_indexer.types.rollup.tezos_storage import RollupStorage


async def on_rollup_call(
    ctx: HandlerContext,
    default: TzktTransaction[DefaultParameter, RollupStorage],
) -> None:
    setup_handler_logger(ctx)
    parameter = default.parameter.__root__.LL

    routing_info = bytes.fromhex(parameter.bytes)
    l2_receiver = routing_info[:20]

    ticket = await ctx.container.ticket_service.fetch_ticket(parameter.ticket.address, parameter.ticket.data)

    inbox_message = await ctx.container.inbox_message_service.match_transaction_with_inbox(default.data)

    await TezosDepositOperation.create(
        timestamp=default.data.timestamp,
        level=default.data.level,
        operation_hash=default.data.hash,
        counter=default.data.counter,
        nonce=default.data.nonce,
        initiator=default.data.initiator_address,
        sender=default.data.sender_address,
        target=default.data.target_address,
        l1_account=default.data.initiator_address,
        l2_account=l2_receiver.hex(),
        ticket=ticket,
        amount=parameter.ticket.amount,
        inbox_message=inbox_message,
    )

    ctx.logger.info(f'Deposit Call registered: {default}')

    status = await Index.get(name='tezos_rollup_operations').only('status').values_list('status', flat=True)
    if status == IndexStatus.realtime:
        await BridgeMatcher.check_pending_tezos_deposits()
        await BridgeMatcher.check_pending_etherlink_deposits()
        await BridgeMatcher.check_pending_etherlink_xtz_deposits()
