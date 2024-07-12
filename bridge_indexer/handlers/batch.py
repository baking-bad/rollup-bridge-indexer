import logging

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher

logger = logging.getLogger('bridge_indexer.handlers.batch')


from collections.abc import Iterable

from dipdup.context import HandlerContext
from dipdup.index import MatchedHandler


async def batch(
    ctx: HandlerContext,
    handlers: Iterable[MatchedHandler],
) -> None:
    for handler in handlers:
        await ctx.fire_matched_handler(handler)

    with BridgeMatcher.matcher_lock:
        await BridgeMatcher.check_pending_tezos_deposits()

        await BridgeMatcher.check_pending_inbox()

        await BridgeMatcher.check_pending_etherlink_deposits()
        await BridgeMatcher.check_pending_etherlink_xtz_deposits()

        await BridgeMatcher.check_pending_etherlink_withdrawals()

        await BridgeMatcher.check_pending_outbox()

        await BridgeMatcher.check_pending_tezos_withdrawals()
