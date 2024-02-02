from dipdup.context import HookContext

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.tezos.on_head import update_commitment


async def on_restart(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_restart')
    await BridgeMatcher.check_pending_tezos_deposits()
    await BridgeMatcher.check_pending_etherlink_withdrawals()

    await BridgeMatcher.check_pending_etherlink_deposits()
    await BridgeMatcher.check_pending_tezos_withdrawals()

    await update_commitment(ctx)
