from datetime import datetime
from datetime import timezone

from dipdup.context import HandlerContext
from dipdup.models import Index
from dipdup.models import IndexStatus
from dipdup.models.evm_node import EvmNodeTransactionData
from dipdup.models.evm_subsquid import SubsquidTransactionData

from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import EtherlinkDepositEvent
from bridge_indexer.models import EtherlinkToken


async def on_xtz_deposit(
    ctx: HandlerContext,
    transaction: SubsquidTransactionData | EvmNodeTransactionData,
) -> None:
    validators = [
        transaction.value > 0,
        transaction.from_ == '0x0000000000000000000000000000000000000000',
        transaction.to != transaction.from_,
        transaction.input == '0x',
        transaction.sighash == '0x',
        transaction.data is None,
    ]
    if not all(validators):
        return

    etherlink_token = await EtherlinkToken.get(id='xtz')

    await EtherlinkDepositEvent.create(
        timestamp=datetime.fromtimestamp(transaction.timestamp, tz=timezone.utc),
        level=transaction.level,
        address=transaction.from_[-40:],
        log_index=0,
        transaction_hash=transaction.hash[-64:],
        transaction_index=transaction.transaction_index,
        l2_account=transaction.to[-40:],
        l2_token=etherlink_token,
        amount=transaction.value,
        inbox_message=None,
    )

    ctx.logger.info(f'Deposit Transaction registered: {transaction}')

    sync_level = ctx.datasources['etherlink_node']._subscriptions._subscriptions[None]
    status = await Index.get(name='etherlink_kernel_events').only('status').values_list('status', flat=True)
    if status == IndexStatus.realtime or sync_level - transaction.level < 5:
        await BridgeMatcher.check_pending_etherlink_deposits()
