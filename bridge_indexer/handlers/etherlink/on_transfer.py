from dipdup.context import HandlerContext
from dipdup.models.evm import EvmEvent
from tortoise.exceptions import DoesNotExist

from bridge_indexer.models import EtherlinkTokenHolder
from bridge_indexer.types.l2_token.evm_events.transfer import TransferPayload


async def on_balance_update(
    token: str,
    holder: str,
    balance_update: int,
    level: int,
) -> None:
    pk = EtherlinkTokenHolder.get_pk(token, holder)
    try:
        token_holder = await EtherlinkTokenHolder.get(id=pk)
    except DoesNotExist:
        token_holder = EtherlinkTokenHolder(
            id=pk,
            token=token,
            holder=holder,
            balance=0,
            turnover=0,
            tx_count=0,
            last_seen=None,
        )
    token_holder.balance += balance_update
    token_holder.turnover += abs(balance_update)
    token_holder.tx_count += 1
    token_holder.last_seen = level
    await token_holder.save()


async def on_transfer(
    ctx: HandlerContext,
    event: EvmEvent[TransferPayload],
) -> None:
    ctx.logger.info(f'Etherlink Token Transfer Event found: {event.data.transaction_hash}')

    amount = event.payload.value
    if not amount:
        return

    await on_balance_update(
        token=event.data.address,
        holder=event.payload.from_,
        balance_update=-amount,
        level=event.data.level,
    )
    await on_balance_update(
        token=event.data.address,
        holder=event.payload.to,
        balance_update=amount,
        level=event.data.level,
    )
    ctx.logger.info(f'Token Holders Balance updated by {amount}.')
