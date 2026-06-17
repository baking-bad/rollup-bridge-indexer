from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosTransaction

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.michelson_deposit import l2_account_from_routing_info
from rollup_bridge_indexer.handlers.rollup_message import TransactionParametersHash
from rollup_bridge_indexer.handlers.service_container import get_container
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import TezosDepositOperation
from rollup_bridge_indexer.models.enum import L2AccountKind
from rollup_bridge_indexer.types.rollup.tezos_parameters.default import DefaultParameter
from rollup_bridge_indexer.types.rollup.tezos_storage import RollupStorage


async def on_rollup_call(
    ctx: HandlerContext,
    default: TezosTransaction[DefaultParameter, RollupStorage],
) -> None:
    if not hasattr(default.parameter.root, 'LL'):
        return
    ctx.logger.info('Tezos Deposit Transaction found: %s', default.data.hash)
    parameter = default.parameter.root.LL

    routing_info = bytes.fromhex(parameter.routing_info)
    l2_account_address, receiver_kind = l2_account_from_routing_info(routing_info)
    l2_account = await L2Account.get_or_create_for(
        l2_account_address,
        L2AccountKind.tz if receiver_kind == 'tezos' else L2AccountKind.evm,
    )

    try:
        ticket = await get_container(ctx).ticket_service.fetch_ticket(parameter.ticket.address, parameter.ticket.content)
    except ValueError:
        return

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
        l2_account=l2_account,
        ticket=ticket,
        amount=parameter.ticket.amount,
        parameters_hash=await TransactionParametersHash(default).from_transaction(),
    )

    ctx.logger.info('Tezos Deposit Transaction registered: %s', deposit.id)

    BridgeMatcherLocks.set_pending_tezos_deposits()
