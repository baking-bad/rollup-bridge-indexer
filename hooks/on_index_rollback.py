import asyncio

from dipdup.context import HookContext
from dipdup.index import Index
from dipdup.models import ModelUpdate

from rollup_bridge_indexer import models

# One reorg reaches the indexes of a datasource as one message per channel, and DipDup runs every
# index's queue concurrently, so these hooks arrive in any order or at once.
_rollback_lock = asyncio.Lock()


async def on_index_rollback(
    ctx: HookContext,
    index: Index,  # type: ignore[type-arg]
    from_level: int,
    to_level: int,
) -> None:
    await ctx.execute_sql('on_index_rollback')
    async with _rollback_lock:
        # Too deep a rollback is refused by `ctx.rollback` (`advanced.reindex.rollback`), which must
        # then find the database untouched.
        if from_level - to_level <= (ctx.config.advanced.rollback_depth or 0):
            await _revert_datasource_journal(ctx, index, to_level)
        await ctx.rollback(
            index=index.name,
            from_level=from_level,
            to_level=to_level,
        )


async def _revert_datasource_journal(ctx: HookContext, index: Index, to_level: int) -> None:  # type: ignore[type-arg]
    """Revert what every index on `index`'s datasources wrote above `to_level`, newest first.

    Rows of one index reference rows of another: the claimed-fast split, run in the claim index's
    batch, moves a `bridge_withdrawal` off an outbox message that the `tezos_head` drain inserted.
    Reverted index by index, that reference is restored after its target is gone whenever
    `tezos_head` goes first. Journal ids order the writes of all indexes, so one pass over them
    undoes the writes in exactly the reverse order they were made. `ctx.rollback` then finds
    nothing left for its index, or for a sibling whose rollback arrives later, and only rewinds
    the index level. Indexes on other datasources count levels of another chain and stay out.
    """
    datasources = {datasource.name for datasource in index.datasources}
    # A template entry carries no datasources of its own.
    siblings = [
        name
        for name, config in ctx.config.indexes.items()
        if datasources & {datasource.name for datasource in getattr(config, 'datasources', ())}
    ]
    async with ctx.transactions.in_transaction():
        updates = await ModelUpdate.filter(level__gt=to_level, index__in=siblings).order_by('-id')
        for update in updates:
            await update.revert(getattr(models, update.model_name))
