import asyncio
import logging

from dipdup.context import HookContext

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher

logger = logging.getLogger(__name__)

async def bridge_matcher(
    ctx: HookContext,
) -> None:
    while True:
        await asyncio.sleep(.2)

        if BridgeMatcher.matcher_lock.locked():
            continue
        with BridgeMatcher.matcher_lock:
            await BridgeMatcher.check_pending_tezos_deposits()

            await BridgeMatcher.check_pending_inbox()

            await BridgeMatcher.check_pending_etherlink_deposits()
            await BridgeMatcher.check_pending_etherlink_xtz_deposits()

            await BridgeMatcher.check_pending_etherlink_withdrawals()

            await BridgeMatcher.check_pending_outbox()

            await BridgeMatcher.check_pending_tezos_withdrawals()
