from datetime import UTC
from datetime import datetime

from dipdup.context import HandlerContext
from dipdup.models.evm import EvmEvent

from rollup_bridge_indexer.handlers.alias import resolve_l2_account
from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.rollup_message import WithdrawalEventParametersHash
from rollup_bridge_indexer.models import L2Token
from rollup_bridge_indexer.models import L2WithdrawOperation
from rollup_bridge_indexer.types.kernel.evm_events.withdrawal import WithdrawalPayload


async def on_withdraw(
    ctx: HandlerContext,
    event: EvmEvent[WithdrawalPayload],
) -> None:
    ctx.logger.info('Etherlink FA Withdraw Event found: %s', event.data.transaction_hash)
    token_contract = event.payload.ticket_owner.removeprefix('0x')
    etherlink_token = await L2Token.get_or_none(id=token_contract).prefetch_related('ticket')
    if not etherlink_token:
        if event.payload.sender == event.payload.ticket_owner:
            ctx.logger.warning('Uncommon Withdraw Routing Info: `ticket_owner == sender`. Mark Operation as `Deposit Revert`.')
        else:
            ctx.logger.warning('Incorrect Withdraw Routing Info: Specified `proxy` contract address not whitelisted: %s.', token_contract)
    if etherlink_token and event.payload.proxy != etherlink_token.ticket.ticketer_address:
        ctx.logger.warning('Uncommon Withdraw Routing Info: `proxy != ticketer_address`.')

    l2_account = await resolve_l2_account(ctx, event.payload.sender[-40:])

    withdrawal = await L2WithdrawOperation.create(
        timestamp=datetime.fromtimestamp(event.data.timestamp, tz=UTC),
        level=event.data.level,
        address=event.data.address[-40:],
        log_index=event.data.log_index,
        transaction_hash=event.data.transaction_hash[-64:],
        transaction_index=event.data.transaction_index,
        l2_account=l2_account,
        l1_account=event.payload.receiver,
        l2_token=etherlink_token,
        ticket_id=event.payload.ticket_hash,
        l2_ticket_owner=event.payload.ticket_owner[-40:],
        l1_ticket_owner=event.payload.proxy,
        amount=event.payload.amount,
        parameters_hash=await WithdrawalEventParametersHash(event).from_event(),
        kernel_withdrawal_id=event.payload.withdrawal_id,
    )

    ctx.logger.info('Etherlink FA Token Withdraw Event registered: %s', withdrawal.id)

    BridgeMatcherLocks.set_pending_l2_withdrawals()
    BridgeMatcherLocks.set_pending_outbox()
    BridgeMatcherLocks.set_pending_claimed_fast_withdrawals()
