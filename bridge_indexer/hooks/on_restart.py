from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.service_container import ServiceContainer
from dipdup.context import HookContext


async def on_restart(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_restart')

    ServiceContainer(ctx)
    await BridgeMatcher.check_pending_transactions()
