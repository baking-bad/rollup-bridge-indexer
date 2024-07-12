from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosSmartRollupCement

from bridge_indexer.models import BridgeDepositOperation
from bridge_indexer.models import BridgeOperation
from bridge_indexer.models import BridgeOperationStatus
from bridge_indexer.models import BridgeOperationType
from bridge_indexer.models import BridgeWithdrawOperation
from bridge_indexer.models import RollupCementedCommitment
from bridge_indexer.models import RollupOutboxMessage


async def on_cement_commitment(
    ctx: HandlerContext,
    cement: TezosSmartRollupCement,
) -> None:
    new_record, _ = await RollupCementedCommitment.update_or_create(
        id=cement.commitment.id,
        defaults={
            'inbox_level': cement.commitment.inbox_level,
            'state': cement.commitment.state,
            'hash': cement.commitment.hash,
            'created_at': cement.data.timestamp,
            'updated_at': cement.data.timestamp,
        },
    )

    ctx.logger.info(f'Cemented Commitment registered: {cement.commitment.hash}')

    protocol = ctx.container.protocol

    sealed = (
        await BridgeOperation.filter(
            type=BridgeOperationType.withdrawal,
            status__in=[BridgeOperationStatus.created, BridgeOperationStatus.sealed],
        )
        .order_by('created_at')
        .limit(100)
        .only('id')
        .values_list('id', flat=True)
    )
    if len(sealed):
        expired = (
            await BridgeWithdrawOperation.filter(
                id__in=sealed,
                outbox_message__level__lte=cement.commitment.inbox_level - protocol.smart_rollup_max_active_outbox_levels,
            )
            .only('id')
            .values_list('id', flat=True)
        )
        if len(expired):
            await BridgeOperation.filter(id__in=expired).update(status=BridgeOperationStatus.outbox_expired)

    created = (
        await BridgeOperation.filter(
            type=BridgeOperationType.deposit,
            status=BridgeOperationStatus.created,
        )
        .order_by('created_at')
        .limit(100)
        .only('id')
        .values_list('id', flat=True)
    )
    if len(created):
        failed = (
            await BridgeDepositOperation.filter(
                id__in=created,
                l1_transaction__level__lte=cement.commitment.inbox_level - protocol.smart_rollup_commitment_period + 1,
            )
            .only('id')
            .values_list('id', flat=True)
        )
        if len(failed):
            await BridgeOperation.filter(id__in=failed).update(status=BridgeOperationStatus.inbox_matching_timeout)

    pending_count = await RollupOutboxMessage.filter(
        bridge_withdrawals__l1_transaction=None,
        level__gt=cement.commitment.inbox_level - protocol.smart_rollup_max_active_outbox_levels,
    ).count()
    if not pending_count:
        return

    ctx.logger.info(f'Updating Proof for {pending_count} Outbox Messages...')
    await ctx.container.outbox_message_service.update_proof()
