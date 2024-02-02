from dipdup.context import HookContext

from bridge_indexer.handlers.tezos.on_head import update_commitment


async def on_synchronized(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_synchronized')
