from dipdup.context import HookContext

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks


async def on_synchronized(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_synchronized')

    BridgeMatcherLocks.set_pending_tezos_deposits()
    BridgeMatcherLocks.set_pending_inbox()
    BridgeMatcherLocks.set_pending_etherlink_deposits()
    BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()
    BridgeMatcherLocks.set_pending_etherlink_withdrawals()
    BridgeMatcherLocks.set_pending_outbox()
    BridgeMatcherLocks.set_pending_tezos_withdrawals()
    BridgeMatcherLocks.set_pending_claimed_fast_withdrawals()
