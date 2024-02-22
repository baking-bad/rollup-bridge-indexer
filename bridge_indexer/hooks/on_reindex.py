from bridge_indexer.handlers.ticket import TicketService
from dipdup.context import HookContext


async def on_reindex(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_reindex')

    await TicketService.register_native_ticket(ctx)
    await TicketService.register_fa_tickets(ctx)
