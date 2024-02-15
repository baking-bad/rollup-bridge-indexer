from datetime import timedelta

from bridge_indexer.models import BridgeDepositOperation
from bridge_indexer.models import BridgeOperation
from bridge_indexer.models import BridgeOperationType
from bridge_indexer.models import BridgeWithdrawOperation
from bridge_indexer.models import EtherlinkDepositOperation
from bridge_indexer.models import EtherlinkWithdrawOperation
from bridge_indexer.models import TezosDepositOperation
from bridge_indexer.models import TezosWithdrawOperation

LAYERS_TIMESTAMP_GAP = timedelta(minutes=5)


class BridgeMatcher:
    @staticmethod
    async def check_pending_tezos_deposits():
        qs = TezosDepositOperation.filter(bridge_deposits__isnull=True)
        async for l1_deposit in qs:
            bridge_deposit = await BridgeDepositOperation.create(l1_transaction=l1_deposit)
            await BridgeOperation.create(
                id=bridge_deposit.id,
                type=BridgeOperationType.deposit,
                l1_account=l1_deposit.l1_account,
                l2_account=l1_deposit.l2_account,
                created_at=l1_deposit.timestamp,
                updated_at=l1_deposit.timestamp,
            )

    @staticmethod
    async def check_pending_etherlink_withdrawals():
        qs = EtherlinkWithdrawOperation.filter(bridge_withdrawals__isnull=True)
        async for l2_withdrawal in qs:
            bridge_withdrawal = await BridgeWithdrawOperation.create(l2_transaction=l2_withdrawal)
            await BridgeOperation.create(
                id=bridge_withdrawal.id,
                type=BridgeOperationType.withdrawal,
                l1_account=l2_withdrawal.l1_account,
                l2_account=l2_withdrawal.l2_account,
                created_at=l2_withdrawal.timestamp,
                updated_at=l2_withdrawal.timestamp,
            )

    @staticmethod
    async def check_pending_etherlink_deposits():
        qs = EtherlinkDepositOperation.filter(bridge_deposits__isnull=True).order_by('level', 'transaction_index')
        async for l2_deposit in qs:
            bridge_deposit = (
                await BridgeDepositOperation.filter(
                    l2_transaction=None,
                    l1_transaction__inbox_message_id=l2_deposit.inbox_message_id,
                )
                .prefetch_related()
                .first()
            )

            if not bridge_deposit:
                continue
            bridge_deposit.l2_transaction = l2_deposit
            await bridge_deposit.save()

            bridge_operation = await BridgeOperation.get(id=bridge_deposit.id)
            bridge_operation.is_completed = True
            bridge_operation.is_successful = l2_deposit.l2_token is not None
            bridge_operation.updated_at = max(bridge_operation.created_at, l2_deposit.timestamp)
            await bridge_operation.save()

    @staticmethod
    async def check_pending_etherlink_xtz_deposits():
        qs = EtherlinkDepositOperation.filter(
            bridge_deposits__isnull=True,
            inbox_message_id__isnull=True,
            l2_token_id='xtz',
        ).order_by('level', 'transaction_index')
        async for l2_deposit in qs:
            await l2_deposit.fetch_related('l2_token', 'l2_token__ticket')
            bridge_deposit = (
                await BridgeDepositOperation.filter(
                    l2_transaction=None,
                    l1_transaction__inbox_message_id__gt=0,
                    l1_transaction__ticket=l2_deposit.l2_token.ticket,
                    l1_transaction__timestamp__gt=l2_deposit.timestamp - LAYERS_TIMESTAMP_GAP,
                    l1_transaction__timestamp__lt=l2_deposit.timestamp + LAYERS_TIMESTAMP_GAP,
                    l1_transaction__l2_account=l2_deposit.l2_account,
                    l1_transaction__amount=l2_deposit.amount[:-12],
                )
                .prefetch_related('l1_transaction__inbox_message')
                .first()
            )

            if not bridge_deposit:
                continue

            l2_deposit.inbox_message = bridge_deposit.l1_transaction.inbox_message
            await l2_deposit.save()

            bridge_deposit.l2_transaction = l2_deposit
            await bridge_deposit.save()

            bridge_operation = await BridgeOperation.get(id=bridge_deposit.id)
            bridge_operation.is_completed = True
            bridge_operation.is_successful = l2_deposit.l2_token is not None
            bridge_operation.updated_at = max(bridge_operation.created_at, l2_deposit.timestamp)
            await bridge_operation.save()

    @staticmethod
    async def check_pending_tezos_withdrawals():
        qs = TezosWithdrawOperation.filter(bridge_withdrawals__isnull=True).order_by('level')
        async for l1_withdrawal in qs:
            bridge_withdrawal = await BridgeWithdrawOperation.filter(
                l1_transaction=None,
                l2_transaction__outbox_message_id=l1_withdrawal.outbox_message_id,
            ).first()

            if not bridge_withdrawal:
                continue

            bridge_withdrawal.l1_transaction = l1_withdrawal
            await bridge_withdrawal.save()

            bridge_operation = await BridgeOperation.get(id=bridge_withdrawal.id)
            bridge_operation.is_completed = True
            bridge_operation.is_successful = True
            bridge_operation.updated_at = l1_withdrawal.timestamp
            await bridge_operation.save()

    @staticmethod
    async def check_pending_transactions():
        await BridgeMatcher.check_pending_tezos_deposits()
        await BridgeMatcher.check_pending_etherlink_withdrawals()

        await BridgeMatcher.check_pending_etherlink_deposits()
        await BridgeMatcher.check_pending_etherlink_xtz_deposits()
        await BridgeMatcher.check_pending_tezos_withdrawals()
