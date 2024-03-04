from dipdup.context import HandlerContext
from dipdup.models.tezos_tzkt import TzktSmartRollupCement

from bridge_indexer.handlers import setup_handler_logger
from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import RollupCementedCommitment
from bridge_indexer.models import RollupOutboxMessage


async def on_cement_commitment(
    ctx: HandlerContext,
    cement: TzktSmartRollupCement,
) -> None:
    setup_handler_logger(ctx)
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

    if not ctx.datasource._signalr_client:
        ctx.logger.debug('Skip syncing message with level %d', cement.data.level)
        return

    await BridgeMatcher.check_pending_transactions()

    pending_count = await RollupOutboxMessage.filter(
        l1_withdrawals__isnull=True,
        l2_withdrawals__isnull=False,
        level__gt=cement.commitment.inbox_level - 80640,  # todo: avoid magic numbers
    ).count()
    if not pending_count:
        return

    await ctx.container.outbox_message_service.update_proof()
