from dipdup.context import HookContext
from dipdup.exceptions import IndexAlreadyExistsError

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher


async def on_synchronized(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_synchronized')
    try:
        await ctx.add_index('tezos_head', 'tezos_head_index_template', values={})
    except IndexAlreadyExistsError:
        pass

    await BridgeMatcher.check_pending_tezos_deposits()
    await BridgeMatcher.check_pending_etherlink_withdrawals()

    await BridgeMatcher.check_pending_etherlink_deposits()
    await BridgeMatcher.check_pending_tezos_withdrawals()
