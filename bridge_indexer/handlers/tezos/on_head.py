from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosHeadBlockData

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher


async def on_head(
    ctx: HandlerContext,
    head: TezosHeadBlockData,
) -> None:
    await BridgeMatcher.check_pending_transactions()
