import logging
from typing import Any

from dipdup.config import HandlerConfig
from dipdup.context import HandlerContext
from dipdup.index import Index

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher

logger = logging.getLogger('bridge_indexer.handlers.batch')


async def batch(
    ctx: HandlerContext,
    handlers: tuple[tuple[Index[Any, Any, Any], HandlerConfig, Any]],
) -> None:
    for index, handler, data in handlers:
        await index._call_matched_handler(handler, data)

    await BridgeMatcher.check_pending_transactions()
