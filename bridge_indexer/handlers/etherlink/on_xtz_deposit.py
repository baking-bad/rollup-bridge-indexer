from datetime import datetime
from datetime import timezone

from dipdup.context import HandlerContext
from dipdup.models.evm import EvmTransactionData

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
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
    if transaction.to == transaction.from_:
        return

    ctx.logger.info(f'Etherlink XTZ Deposit Transaction found: {transaction.hash}')

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
        transaction_hash=transaction.hash[-64:],
        transaction_index=transaction.transaction_index,
        l2_account=transaction.to[-40:],
        l2_token=etherlink_token,
        ticket=tezos_ticket,
        ticket_owner=etherlink_token.id,
        amount=transaction.value,
    )

    ctx.logger.info(f'XTZ Deposit Transaction registered: {deposit.id}')

    BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()
