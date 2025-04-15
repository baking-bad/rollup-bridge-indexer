from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosTransaction

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from bridge_indexer.models import RollupOutboxMessage
from bridge_indexer.models import RollupOutboxMessageBuilder
from bridge_indexer.models import TezosWithdrawOperation
from bridge_indexer.types.fast_withdrawal.tezos_parameters.payout_withdrawal import PayoutWithdrawalParameter
from bridge_indexer.types.fast_withdrawal.tezos_storage import FastWithdrawalStorage


async def on_claim_xtz_fast_withdrawal(
    ctx: HandlerContext,
    payout: TezosTransaction[PayoutWithdrawalParameter, FastWithdrawalStorage],
) -> None:

    outbox_message = await RollupOutboxMessage.create(
        builder=RollupOutboxMessageBuilder.service_provider,
        level=payout.data.level,
        index=payout.data.counter,
        message=payout.parameter.model_dump(mode='json'),
        created_at=payout.data.timestamp,
        cemented_at=payout.data.timestamp,
        cemented_level=payout.data.level,
        parameters_hash=payout.parameter.withdrawal.withdrawal_id,
        # proof=None,
        # commitment_id=None,
        # failure_count=None,
    )
    payout_transaction = await TezosWithdrawOperation.create(
        timestamp=payout.data.timestamp,
        level=payout.data.level,
        operation_hash=payout.data.hash,
        counter=payout.data.counter,
        nonce=payout.data.nonce,
        initiator=payout.data.sender_address,
        sender=payout.data.sender_address,
        target=payout.data.target_address,
        amount=payout.data.amount,
        outbox_message=outbox_message,
    )
    ctx.logger.info('Tezos PayoutFastWithdrawal Transaction registered: %s', payout_transaction.id)

    BridgeMatcherLocks.set_pending_claimed_fast_withdrawals()
