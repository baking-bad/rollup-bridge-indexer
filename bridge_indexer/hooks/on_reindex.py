from bridge_indexer.handlers.service_container import ServiceContainer
from dipdup.context import HookContext


async def on_reindex(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_reindex')

    ServiceContainer(ctx)
    await ctx.container.ticket_service.register_native_ticket()
    await ctx.container.ticket_service.register_fa_tickets()
