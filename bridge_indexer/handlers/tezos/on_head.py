from dipdup.context import HandlerContext
from dipdup.models.tezos_tzkt import TzktHeadBlockData

from bridge_indexer.handlers.rollup_message import OutboxMessageService
from bridge_indexer.models import RollupCommitment


async def update_commitment(ctx):
    datasource = ctx.get_tzkt_datasource('tzkt')
    rollup = ctx.config.get_tezos_contract('tezos_smart_rollup')
    commitment_data = await datasource.request(
        'GET',
        f'https://api.nairobinet.tzkt.io/v1/smart_rollups/commitments?rollup={rollup.address}&sort.desc=id&limit=2',
    )
    await RollupCommitment.create(
        id=commitment_data[0]['id'],
        inbox_level=commitment_data[0]['inboxLevel'],
        first_level=commitment_data[0]['firstLevel'],
        last_level=commitment_data[0]['firstLevel'] + (commitment_data[1]['lastLevel'] - commitment_data[1]['firstLevel']),
        state=commitment_data[0]['state'],
        hash=commitment_data[0]['hash'],
    )

    await OutboxMessageService.update_proof(ctx)


async def on_head(
    ctx: HandlerContext,
    head: TzktHeadBlockData,
) -> None:
    commitment = await RollupCommitment.all().order_by('-id').first()
    if not commitment:
        await update_commitment(ctx)
        return
    if commitment.last_level < head.level:
        await update_commitment(ctx)