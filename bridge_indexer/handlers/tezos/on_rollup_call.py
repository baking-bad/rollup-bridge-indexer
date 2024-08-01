import logging

from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosTransaction

from bridge_indexer.handlers import setup_handler_logger
from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.rollup_message import InboxParametersHash
from bridge_indexer.models import TezosDepositOperation
from bridge_indexer.types.rollup.tezos_parameters.default import DefaultParameter
from bridge_indexer.types.rollup.tezos_storage import RollupStorage

logger = logging.getLogger('bridge_indexer.handlers.tezos')


async def on_rollup_call(
    ctx: HandlerContext,
    default: TezosTransaction[DefaultParameter, RollupStorage],
) -> None:
    setup_handler_logger(ctx)
    ctx.logger.info(f'Tezos Deposit Transaction found: {default.data.hash}')
    ctx.logger.warning(f'Tezos Deposit Transaction found: {default.data.hash}')
    parameter = default.parameter.root.LL

    # while True:
    #     rollup_message_index: RollupMessageIndex = ctx.container.rollup_message_index
    #     if rollup_message_index._inbox_level_cursor < default.data.level:
    #         # ctx.logger.warning('Waiting for L1 deposit with inbox_message...')
    #         logger.warning(f'Waiting 1 sec for L1 deposit with inbox_message, call_level: {default.data.level}, inbox_level_cursor: {rollup_message_index._inbox_level_cursor}.')
    #         await asyncio.sleep(1)
    #     else:
    #         break

    routing_info = bytes.fromhex(parameter.routing_info)
    l2_receiver = routing_info[:20]

    ticket = await ctx.container.ticket_service.fetch_ticket(parameter.ticket.address, parameter.ticket.content)

    # inbox_message = await InboxMessageService.match_transaction_with_inbox(default.data)

    deposit = await TezosDepositOperation.create(
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
        # inbox_message=inbox_message,
        parameters_hash=await InboxParametersHash(default).from_transaction(),
    )

    ctx.logger.info(f'Tezos Deposit Transaction registered: {deposit.id}')

    BridgeMatcher.set_pending_tezos_deposits()
    # await BridgeMatcher.check_pending_transactions()
