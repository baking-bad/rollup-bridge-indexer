import logging

from dipdup.context import HookContext
from dipdup.models import Index

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.handlers.tezos.on_head import update_commitment


async def on_restart(
    ctx: HookContext,
) -> None:
    await ctx.execute_sql('on_restart')
    await Index.filter(name='tezos_head').delete()

    await BridgeMatcher.check_pending_tezos_deposits()
    await BridgeMatcher.check_pending_etherlink_withdrawals()

    await BridgeMatcher.check_pending_etherlink_deposits()
    await BridgeMatcher.check_pending_tezos_withdrawals()
