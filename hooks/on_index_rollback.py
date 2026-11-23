import asyncio

from dipdup.context import HookContext
from dipdup.index import Index
from dipdup.models import ModelUpdate
from dipdup.models import ModelUpdateAction

from rollup_bridge_indexer import models
from rollup_bridge_indexer.handlers.rollup_message import outbox_message_parameters_hash
from rollup_bridge_indexer.handlers.service_container import get_container

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
            model = getattr(models, update.model_name)
            if model is models.RollupOutboxMessage and update.action == ModelUpdateAction.INSERT:
                await _detach_withdrawals(ctx, update.model_pk)
            await update.revert(model)


async def _detach_withdrawals(ctx: HookContext, outbox_message_id: str) -> None:
    """Hand the withdrawals attached to an outbox message that is about to be deleted back to the matcher.

    What still points at the message once the journal above it is reverted was attached by an index
    on another datasource — `check_pending_outbox` runs in whichever index's batch comes next, on
    mainnet usually an L2 one — and an L1 rollback leaves that write alone. The foreign key is
    `ON DELETE CASCADE`, so deleting the message would delete the bridge withdrawal too, outside the
    journal, and orphan its `bridge_operation`. Detached instead, the withdrawal gets back the hash
    the attach consumed, and the matcher pairs it with the message the drain stores again.
    """
    withdrawals = await models.BridgeWithdrawOperation.filter(outbox_message_id=outbox_message_id).prefetch_related(
        'outbox_message', 'l2_transaction'
    )
    if not withdrawals:
        return
    parameters_hash = await outbox_message_parameters_hash(
        {'message': withdrawals[0].outbox_message.message},
        get_container(ctx).ticket_service,
    )
    for withdrawal in withdrawals:
        withdrawal.outbox_message = None
        await withdrawal.save()
        withdrawal.l2_transaction.parameters_hash = parameters_hash
        await withdrawal.l2_transaction.save()
    ctx.logger.info('An L1 rollback detached %d bridge withdrawal(s) from outbox message %s', len(withdrawals), outbox_message_id)
