from dipdup.context import HookContext

from rollup_bridge_indexer.handlers.service_container import ServiceContainer
from rollup_bridge_indexer.handlers.service_container import get_container


async def on_reindex(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_reindex')

    await ServiceContainer(ctx).register()

    container = get_container(ctx)
    await container.ticket_service.register_native_ticket()
    await container.ticket_service.register_fa_tickets()
