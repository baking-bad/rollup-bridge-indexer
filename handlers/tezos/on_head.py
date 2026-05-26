from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosHeadBlockData

from rollup_bridge_indexer.handlers.service_container import get_container


async def on_head(
    ctx: HandlerContext,
    head: TezosHeadBlockData,
) -> None:
    await get_container(ctx).rollup_message_index.handle_realtime(head.level)
