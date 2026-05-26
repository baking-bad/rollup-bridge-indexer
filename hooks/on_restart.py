from dipdup.context import HookContext

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.service_container import ServiceContainer
from rollup_bridge_indexer.handlers.service_container import get_container


async def on_restart(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_restart')

    await ServiceContainer(ctx).register()

    ctx.logger.info('Start of Rollup Message Index syncing.')
    await get_container(ctx).rollup_message_index.synchronize()
    ctx.logger.info('Rollup Message Index syncing complete. Switch to realtime indexing mode.')

    BridgeMatcherLocks.set_pending_tezos_deposits()
    BridgeMatcherLocks.set_pending_inbox()
    BridgeMatcherLocks.set_pending_etherlink_deposits()
    BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()
    BridgeMatcherLocks.set_pending_etherlink_withdrawals()
    BridgeMatcherLocks.set_pending_outbox()
    BridgeMatcherLocks.set_pending_tezos_withdrawals()
    BridgeMatcherLocks.set_pending_claimed_fast_withdrawals()
