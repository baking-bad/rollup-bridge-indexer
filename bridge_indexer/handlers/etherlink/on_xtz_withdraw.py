from datetime import UTC
from datetime import datetime

from dipdup.context import HandlerContext
from dipdup.models.evm import EvmEvent

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from bridge_indexer.handlers.rollup_message import OutboxParametersHash
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import EtherlinkWithdrawOperation
from bridge_indexer.types.kernel_native.evm_events.withdrawal import WithdrawalPayload


async def on_xtz_withdraw(
    ctx: HandlerContext,
    event: EvmEvent[WithdrawalPayload],
) -> None:
    ctx.logger.info(f'Etherlink Native Withdraw Event found: {event.data.transaction_hash}')
    etherlink_token = await EtherlinkToken.get(id='xtz').prefetch_related('ticket')

    withdrawal = await EtherlinkWithdrawOperation.create(
        timestamp=datetime.fromtimestamp(event.data.timestamp, tz=UTC),
        level=event.data.level,
        address=event.data.address[-40:],
        log_index=event.data.log_index,
        transaction_hash=event.data.transaction_hash[-64:],
        transaction_index=event.data.transaction_index,
        l2_account=event.payload.sender[-40:],
        l1_account=event.payload.receiver,
        l2_token=etherlink_token,
        ticket_id=etherlink_token.ticket.hash,
        l2_ticket_owner=event.data.address[-40:],
        l1_ticket_owner=etherlink_token.ticket.ticketer_address,
        amount=event.payload.amount,
        parameters_hash=await OutboxParametersHash(event).from_event(),
        kernel_withdrawal_id=event.payload.withdrawal_id,
    )

    ctx.logger.info(f'Etherlink Native Token Withdraw Event registered: {withdrawal.id}')

    BridgeMatcherLocks.set_pending_etherlink_withdrawals()
