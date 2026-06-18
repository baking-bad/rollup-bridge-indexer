from datetime import UTC
from datetime import datetime

from dipdup.context import HandlerContext
from dipdup.models.evm import EvmTransactionData

from rollup_bridge_indexer.handlers.alias import resolve_l2_account
from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.models import L2DepositOperation
from rollup_bridge_indexer.models import L2Token
from rollup_bridge_indexer.models import TezosTicket


async def _validate_xtz_transaction(transaction: EvmTransactionData):
    validators = [
        transaction.value is not None and transaction.value > 0,
        # The deposit-sender `from_` (0x…feed on the new kernel, 0x0 on the legacy one) is
        # enforced per-network by the index `from_` filter, so we don't hardcode it here.
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

    ctx.logger.info('Etherlink XTZ Deposit Transaction found: %s', transaction.hash)

    try:
        await _validate_xtz_transaction(transaction)
    except ValueError as exception:
        ctx.logger.warning('Incorrect XTZ Deposit. %s. Operation ignored.', exception.args[0].format(*exception.args[1:]))
        return

    assert transaction.to is not None  # validated above: a deposit always has a destination
    etherlink_token = await L2Token.get(id='xtz_evm')
    tezos_ticket = await TezosTicket.get(token_id='xtz')
    l2_account = await resolve_l2_account(ctx, transaction.to[-40:])

    deposit = await L2DepositOperation.create(
        timestamp=datetime.fromtimestamp(transaction.timestamp, tz=UTC),
        level=transaction.level,
        address=transaction.from_[-40:],
        transaction_hash=transaction.hash[-64:],
        transaction_index=transaction.transaction_index,
        account=l2_account,
        l2_token=etherlink_token,
        ticket=tezos_ticket,
        ticket_owner=etherlink_token.id,
        amount=transaction.value,
    )

    ctx.logger.info('XTZ Deposit Transaction registered: %s', deposit.id)

    BridgeMatcherLocks.set_pending_l2_xtz_deposits()
