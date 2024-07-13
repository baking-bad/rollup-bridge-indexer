from datetime import datetime
from datetime import timezone

from dipdup.context import HandlerContext
from dipdup.models.evm import EvmTransactionData

from bridge_indexer.handlers import setup_handler_logger
from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import EtherlinkDepositOperation
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import TezosTicket


async def _validate_xtz_transaction(transaction: EvmTransactionData):
    validators = [
        transaction.value > 0,
        transaction.from_ == '0x0000000000000000000000000000000000000000',
        transaction.to != transaction.from_,
        transaction.input == '0x',
        transaction.sighash == '0x',
    ]
    if not all(validators):
        raise ValueError('Transaction validation error: {}', transaction.hash)


async def on_xtz_deposit(
    ctx: HandlerContext,
    transaction: EvmTransactionData,
) -> None:
    setup_handler_logger(ctx)
    ctx.logger.info(f'Etherlink XTZ Deposit Transaction found: 0x{transaction.hash}')

    try:
        await _validate_xtz_transaction(transaction)
    except ValueError as exception:
        ctx.logger.warning('Incorrect XTZ Deposit. ' + exception.args[0].format(*exception.args[1:]) + '. Operation ignored.')
        return

    etherlink_token = await EtherlinkToken.get(id='xtz')
    tezos_ticket = await TezosTicket.get(token_id='xtz')

    deposit = await EtherlinkDepositOperation.create(
        timestamp=datetime.fromtimestamp(transaction.timestamp, tz=timezone.utc),
        level=transaction.level,
        address=transaction.from_[-40:],
        log_index=0,
        transaction_hash=transaction.hash[-64:],
        transaction_index=transaction.transaction_index,
        l2_account=transaction.to[-40:],
        l2_token=etherlink_token,
        ticket=tezos_ticket,
        ticket_owner=etherlink_token.id,
        amount=transaction.value,
        inbox_message=None,
    )

    ctx.logger.info(f'XTZ Deposit Transaction registered: {deposit.id}')

    BridgeMatcher.set_pending_etherlink_xtz_deposits()
