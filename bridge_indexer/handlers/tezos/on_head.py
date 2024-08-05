from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosHeadBlockData

from bridge_indexer.handlers.rollup_message import RollupMessageIndex


async def on_head(
    ctx: HandlerContext,
    head: TezosHeadBlockData,
) -> None:
    rollup_message_index: RollupMessageIndex = ctx.container.rollup_message_index
    await rollup_message_index.handle_realtime(head.level)
