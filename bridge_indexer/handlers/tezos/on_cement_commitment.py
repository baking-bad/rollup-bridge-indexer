from dipdup.context import HandlerContext
from dipdup.models.tezos_tzkt import TzktSmartRollupCement

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.rollup_message import OutboxMessageService
from bridge_indexer.models import RollupCommitment
from bridge_indexer.models import RollupOutboxMessage


async def on_cement_commitment(
    ctx: HandlerContext,
    cement: TzktSmartRollupCement,
) -> None:
    new_record, _ = await RollupCommitment.update_or_create(
        id=cement.commitment.id,
        defaults={
            'inbox_level': cement.commitment.inbox_level,
            'first_level': cement.commitment.first_level,
            'first_time': cement.commitment.first_time,
            # 'last_level': commitment_data[0]['lastLevel'],
            # 'last_time': commitment_data[0]['lastTime'],
            'state': cement.commitment.state,
            'hash': cement.commitment.hash,
            'status': 'cemented',
        },
    )

    if not ctx.datasource._signalr_client:
        ctx.logger.info('Skip syncing message with level %d', cement.data.level)
        return

    await BridgeMatcher.check_pending_transactions()

    pending_count = await RollupOutboxMessage.filter(
        l1_withdrawals__isnull=True,
        l2_withdrawals__isnull=False,
        level__gt=cement.commitment.first_level - 80640,  # todo: avoid magic numbers
    ).count()
    if not pending_count:
        return

    await OutboxMessageService.update_proof(ctx)
