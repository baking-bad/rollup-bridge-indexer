from dipdup.context import HookContext

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher


async def on_synchronized(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_synchronized')

    await BridgeMatcher.check_pending_transactions()
