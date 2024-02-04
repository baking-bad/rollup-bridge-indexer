from dipdup.context import HookContext
from dipdup.exceptions import IndexAlreadyExistsError


async def on_synchronized(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_synchronized')
    try:
        await ctx.add_index('tezos_head', 'tezos_head_index_template', values={})
    except IndexAlreadyExistsError:
        pass
