# from dipdup.context import HandlerContext
# from dipdup.models.evm_subsquid import SubsquidEvent
# from tortoise.exceptions import DoesNotExist
#
# from bridge_indexer.models.etherlink import TokenHolder
#
#
#
# async def on_transfer(
#     ctx: HandlerContext,
#     event: SubsquidEvent[Transfer],
# ) -> None:
#     amount = event.payload.value
#     if not amount:
#         return
#
#     await on_balance_update(
#         token=event.data.address,
#         holder=event.payload.from_,
#         balance_update=-amount,
#         level=event.data.level,
#     )
#     await on_balance_update(
#         token=event.data.address,
#         holder=event.payload.to,
#         balance_update=amount,
#         level=event.data.level,
#     )
#     ctx.logger.info(f'Token Transfer registered: {event}')
#
#
# async def on_balance_update(
#     token: str,
#     holder: str,
#     balance_update: int,
#     level: int,
# ) -> None:
#     pk = TokenHolder.get_pk(token, holder)
#     try:
#         token_holder = await TokenHolder.get(id=pk)
#     except DoesNotExist:
#         token_holder = TokenHolder(
#             id=pk,
#             token=token,
#             holder=holder,
#             balance=0,
#             turnover=0,
#             tx_count=0,
#             last_seen=None,
#         )
#     token_holder.balance += balance_update
#     token_holder.turnover += abs(balance_update)
#     token_holder.tx_count += 1
#     token_holder.last_seen = level
#     await token_holder.save()
#