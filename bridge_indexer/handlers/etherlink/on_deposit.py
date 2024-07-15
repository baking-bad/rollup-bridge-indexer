import asyncio
from datetime import datetime
from datetime import timezone

from dipdup.context import HandlerContext
from dipdup.models.evm import EvmEvent
from tortoise.exceptions import DoesNotExist

from bridge_indexer.handlers import setup_handler_logger
from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import EtherlinkDepositOperation
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import TezosTicket
from bridge_indexer.types.kernel.evm_events.deposit import DepositPayload


async def _validate_ticket(ticket_hash):
    tezos_ticket = await TezosTicket.get_or_none(pk=ticket_hash)
    if tezos_ticket is None:
        raise ValueError('Ticket with given `ticket_hash` not found: {}', ticket_hash)


async def register_etherlink_token(token_contract: str, ticket_hash: int) -> EtherlinkToken:
    etherlink_token = await EtherlinkToken.get_or_none(id=token_contract)

    if etherlink_token:
        return etherlink_token

    await _validate_ticket(ticket_hash)
    if await EtherlinkToken.filter(ticket_id=ticket_hash).exclude(id=token_contract).count():
        raise ValueError('Specified `proxy` contract address not whitelisted: {}', token_contract)

    etherlink_token = await EtherlinkToken.create(
        id=token_contract,
        ticket_id=ticket_hash,
    )
    return etherlink_token


async def on_deposit(
    ctx: HandlerContext,
    event: EvmEvent[DepositPayload],
) -> None:
    setup_handler_logger(ctx)
    ctx.logger.info(f'Etherlink Deposit Event found: 0x{event.data.transaction_hash}')

    try:
        await _validate_ticket(event.payload.ticket_hash)
    except ValueError as exception:
        ctx.logger.warning(
            'Incorrect Deposit Routing Info: ' + exception.args[0].format(*exception.args[1:]) + '. Mark Operation as `Failed Deposit`.'
        )
        etherlink_token = None

    if event.payload.ticket_owner == event.payload.receiver:
        ctx.logger.warning('Incorrect Deposit Routing Info: `ticket_owner == receiver`. Mark Operation as `Revertable Deposit`.')
        etherlink_token = None
    else:
        try:
            token_contract = event.payload.ticket_owner.removeprefix('0x')
            etherlink_token = await register_etherlink_token(token_contract, event.payload.ticket_hash)
        except ValueError as exception:
            ctx.logger.warning(
                'Incorrect Deposit Routing Info: ' + exception.args[0].format(*exception.args[1:]) + '. Mark Operation as `Failed Deposit`.'
            )
            etherlink_token = None

    while True:
        try:
            inbox_message = await ctx.container.inbox_message_service.find_by_index(event.payload.inbox_level, event.payload.inbox_msg_id)
        except DoesNotExist:
            ctx.logger.warning('L2 deposit is matched before L1. Waiting for L1 deposit with inbox_message...')
            await asyncio.sleep(1)
        else:
            break

    deposit = await EtherlinkDepositOperation.create(
        timestamp=datetime.fromtimestamp(event.data.timestamp, tz=timezone.utc),
        level=event.data.level,
        address=event.data.address[-40:],
        log_index=event.data.log_index,
        transaction_hash=event.data.transaction_hash[-64:],
        transaction_index=event.data.transaction_index,
        l2_account=event.payload.receiver[-40:],
        l2_token=etherlink_token,
        ticket_id=event.payload.ticket_hash,
        ticket_owner=event.payload.ticket_owner[-40:],
        amount=event.payload.amount,
        inbox_message=inbox_message,
    )

    ctx.logger.info(f'Etherlink Deposit Event registered: {deposit.id}')

    BridgeMatcher.set_pending_etherlink_deposits()
