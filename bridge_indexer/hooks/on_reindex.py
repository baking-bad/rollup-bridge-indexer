from dipdup.context import HookContext

from bridge_indexer.handlers.service_container import ServiceContainer


async def on_reindex(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_reindex')

    await ServiceContainer(ctx).register()

    await ctx.container.ticket_service.register_native_ticket()
    await ctx.container.ticket_service.register_fa_tickets()
