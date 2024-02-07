from dipdup.context import HookContext
from dipdup.models import Index

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher


async def on_restart(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_restart')

    await BridgeMatcher.check_pending_transactions()

    await Index.filter(name='tezos_head').delete()
