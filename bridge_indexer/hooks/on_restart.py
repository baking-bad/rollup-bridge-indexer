from dipdup.context import HookContext

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.service_container import ServiceContainer


async def on_restart(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_restart')

    await ServiceContainer(ctx).register()

    BridgeMatcher.set_pending_tezos_deposits()
    BridgeMatcher.set_pending_etherlink_withdrawals()
    BridgeMatcher.set_pending_etherlink_deposits()
    BridgeMatcher.set_pending_etherlink_xtz_deposits()
    BridgeMatcher.set_pending_tezos_withdrawals()
    await BridgeMatcher.check_pending_transactions()
