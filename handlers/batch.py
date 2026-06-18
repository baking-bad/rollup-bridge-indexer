import logging
from collections.abc import Iterable

from dipdup.context import HandlerContext
from dipdup.index import MatchedHandler

from rollup_bridge_indexer.handlers.bridge_matcher import BridgeMatcher

logger = logging.getLogger('rollup_bridge_indexer.handlers.batch')


async def run_matcher_steps() -> None:
    """The ordered matcher pass. ctx-free so tests run the exact production sequence.

    Each step is a no-op unless its pending flag is set. Deposit steps run
    deterministic-before-heuristic: op-hash (needs the inbox attached above) and coords
    are exact keys, the xtz value-zip is a heuristic; their candidate pools are disjoint
    regardless (see the queryset filters).
    """
    await BridgeMatcher.check_pending_tezos_deposits()
    await BridgeMatcher.check_pending_inbox()
    await BridgeMatcher.check_pending_michelson_deposits()
    await BridgeMatcher.check_pending_l2_deposits()
    await BridgeMatcher.check_pending_l2_xtz_deposits()
    await BridgeMatcher.check_pending_l2_withdrawals()
    await BridgeMatcher.check_pending_outbox()
    await BridgeMatcher.check_pending_tezos_withdrawals()
    await BridgeMatcher.check_pending_claimed_fast_withdrawals()


async def batch(
    ctx: HandlerContext,
    handlers: Iterable[MatchedHandler],
) -> None:
    for handler in handlers:
        await ctx.fire_matched_handler(handler)

    with BridgeMatcher.matcher_lock:
        await run_matcher_steps()
