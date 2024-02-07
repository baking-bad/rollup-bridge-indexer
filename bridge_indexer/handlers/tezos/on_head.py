from dipdup.context import HandlerContext
from dipdup.models.tezos_tzkt import TzktHeadBlockData

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.rollup_message import OutboxMessageService
from bridge_indexer.models import RollupCommitment

ROLLUP_STATE_REFRESH_INTERVAL = 20  # todo: replace with expression


async def update_commitment(ctx):
    datasource = ctx.get_tzkt_datasource('tzkt')
    rollup = ctx.config.get_tezos_contract('tezos_smart_rollup')
    commitment_data = await datasource.request(
        'GET',
        f'https://api.nairobinet.tzkt.io/v1/smart_rollups/commitments?rollup={rollup.address}&sort.desc=id&status=cemented&limit=1',
    )
    new_record, _ = await RollupCommitment.update_or_create(
        id=commitment_data[0]['id'],
        defaults={
            'inbox_level': commitment_data[0]['inboxLevel'],
            'first_level': commitment_data[0]['firstLevel'],
            'first_time': commitment_data[0]['firstTime'],
            'last_level': commitment_data[0]['lastLevel'],
            'last_time': commitment_data[0]['lastTime'],
            'state': commitment_data[0]['state'],
            'hash': commitment_data[0]['hash'],
            'status': commitment_data[0]['status'],
        },
    )

    await OutboxMessageService.update_proof(ctx)


async def on_head(
    ctx: HandlerContext,
    head: TzktHeadBlockData,
) -> None:
    # todo: ensure that incomplete withdrawals exist
    commitment = await RollupCommitment.all().order_by('-id').first()
    if not commitment:
        await update_commitment(ctx)
        return
    if commitment.last_level + ROLLUP_STATE_REFRESH_INTERVAL <= head.level:
        await update_commitment(ctx)

        await BridgeMatcher.check_pending_transactions()
