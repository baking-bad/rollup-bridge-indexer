import asyncio

from dipdup.context import HookContext
from dipdup.index import Index
from dipdup.models import ModelUpdate
from dipdup.models import ModelUpdateAction

from rollup_bridge_indexer import models
from rollup_bridge_indexer.handlers.rollup_message import outbox_message_parameters_hash
from rollup_bridge_indexer.handlers.service_container import get_container

_rollback_lock = asyncio.Lock()


async def on_index_rollback(
    ctx: HookContext,
    index: Index,  # type: ignore[type-arg]
    from_level: int,
    to_level: int,
) -> None:
    await ctx.execute_sql('on_index_rollback')
    async with _rollback_lock:
        if from_level - to_level <= (ctx.config.advanced.rollback_depth or 0):
            await _revert_datasource_journal(ctx, index, to_level)
        await ctx.rollback(
            index=index.name,
            from_level=from_level,
            to_level=to_level,
        )


async def _revert_datasource_journal(ctx: HookContext, index: Index, to_level: int) -> None:  # type: ignore[type-arg]
    datasources = {datasource.name for datasource in index.datasources}
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
