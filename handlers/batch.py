import logging
from collections.abc import Iterable

from dipdup.context import HandlerContext
from dipdup.index import MatchedHandler

from rollup_bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from rollup_bridge_indexer.handlers.michelson_matcher import check_pending_michelson_deposits
from rollup_bridge_indexer.handlers.service_container import get_container

logger = logging.getLogger('rollup_bridge_indexer.handlers.batch')


async def batch(
    ctx: HandlerContext,
    handlers: Iterable[MatchedHandler],
) -> None:
    for handler in handlers:
        await ctx.fire_matched_handler(handler)

    with BridgeMatcher.matcher_lock:
        await BridgeMatcher.check_pending_tezos_deposits()

        await BridgeMatcher.check_pending_inbox()

        # Deposit steps run deterministic-before-heuristic: op-hash (needs the inbox
        # attached above) and coords are exact keys, the xtz value-zip is a heuristic.
        # Their candidate pools are disjoint regardless (see the queryset filters).
        # Interim op-hash matching of L2 Michelson deposits — delete with handlers/michelson_matcher.py.
        await check_pending_michelson_deposits(get_container(ctx).bridge.smart_rollup_address)
        await BridgeMatcher.check_pending_etherlink_deposits()
        await BridgeMatcher.check_pending_etherlink_xtz_deposits()

        await BridgeMatcher.check_pending_etherlink_withdrawals()

        await BridgeMatcher.check_pending_outbox()

        await BridgeMatcher.check_pending_tezos_withdrawals()

        await BridgeMatcher.check_pending_claimed_fast_withdrawals()
