from dipdup.context import HookContext

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from bridge_indexer.handlers.service_container import ServiceContainer


async def on_restart(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_restart')

    await ServiceContainer(ctx).register()

    ctx.logger.info('Start of Rollup Message Index syncing.')
    await ctx.container.rollup_message_index.synchronize()
    ctx.logger.info('Rollup Message Index syncing complete. Switch to realtime indexing mode.')
    raise RuntimeError('Rollup Message Index syncing complete')

    BridgeMatcherLocks.set_pending_tezos_deposits()
    BridgeMatcherLocks.set_pending_inbox()
    BridgeMatcherLocks.set_pending_etherlink_deposits()
    BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()
    BridgeMatcherLocks.set_pending_etherlink_withdrawals()
    BridgeMatcherLocks.set_pending_outbox()
    BridgeMatcherLocks.set_pending_tezos_withdrawals()
    BridgeMatcherLocks.set_pending_claimed_fast_withdrawals()
