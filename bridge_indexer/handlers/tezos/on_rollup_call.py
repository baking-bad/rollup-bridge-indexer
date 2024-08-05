from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosTransaction

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from bridge_indexer.handlers.rollup_message import InboxParametersHash
from bridge_indexer.models import TezosDepositOperation
from bridge_indexer.types.rollup.tezos_parameters.default import DefaultParameter
from bridge_indexer.types.rollup.tezos_storage import RollupStorage


async def on_rollup_call(
    ctx: HandlerContext,
    default: TezosTransaction[DefaultParameter, RollupStorage],
) -> None:
    ctx.logger.info(f'Tezos Deposit Transaction found: {default.data.hash}')
    parameter = default.parameter.root.LL

    routing_info = bytes.fromhex(parameter.routing_info)
    l2_receiver = routing_info[:20]

    ticket = await ctx.container.ticket_service.fetch_ticket(parameter.ticket.address, parameter.ticket.content)

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
        parameters_hash=await InboxParametersHash(default).from_transaction(),
    )

    ctx.logger.info(f'Tezos Deposit Transaction registered: {deposit.id}')

    BridgeMatcherLocks.set_pending_tezos_deposits()
