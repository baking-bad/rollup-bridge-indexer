from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosTransaction

from bridge_indexer.handlers import setup_handler_logger
from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import TezosDepositOperation
from bridge_indexer.types.rollup.tezos_parameters.default import DefaultParameter
from bridge_indexer.types.rollup.tezos_storage import RollupStorage


async def on_rollup_call(
    ctx: HandlerContext,
    default: TezosTransaction[DefaultParameter, RollupStorage],
) -> None:
    setup_handler_logger(ctx)
    ctx.logger.info(f'Tezos Deposit Transaction found: {default.data.hash}')
    parameter = default.parameter.root.LL

    routing_info = bytes.fromhex(parameter.bytes)
    l2_receiver = routing_info[:20]

    ticket = await ctx.container.ticket_service.fetch_ticket(parameter.ticket.address, parameter.ticket.content)

    inbox_message = await ctx.container.inbox_message_service.match_transaction_with_inbox(default.data)

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
        inbox_message=inbox_message,
    )

    ctx.logger.info(f'Tezos Deposit Transaction registered: {deposit.id}')

    BridgeMatcher.set_pending_tezos_deposits()
