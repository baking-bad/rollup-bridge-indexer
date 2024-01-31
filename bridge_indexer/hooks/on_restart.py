from dipdup.context import HookContext

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher


async def on_restart(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_restart')
    await BridgeMatcher.check_pending_tezos_deposits()
    await BridgeMatcher.check_pending_etherlink_withdrawals()

    await BridgeMatcher.check_pending_etherlink_deposits()
    await BridgeMatcher.check_pending_tezos_withdrawals()

