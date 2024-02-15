from datetime import timedelta

from bridge_indexer.models import BridgeDepositTransaction
from bridge_indexer.models import BridgeWithdrawTransaction
from bridge_indexer.models import EtherlinkDepositEvent
from bridge_indexer.models import EtherlinkWithdrawEvent
from bridge_indexer.models import TezosDepositEvent
from bridge_indexer.models import TezosWithdrawEvent


class BridgeMatcher:
    @staticmethod
    async def check_pending_tezos_deposits():
        qs = TezosDepositEvent.filter(bridge_deposits__isnull=True)
        async for l1_deposit in qs:
            await BridgeDepositTransaction.create(l1_transaction=l1_deposit)

    @staticmethod
    async def check_pending_etherlink_withdrawals():
        qs = EtherlinkWithdrawEvent.filter(bridge_withdrawals__isnull=True)
        async for l2_withdrawal in qs:
            await BridgeWithdrawTransaction.create(l2_transaction=l2_withdrawal)

    @staticmethod
    async def check_pending_etherlink_deposits():
        qs = EtherlinkDepositEvent.filter(bridge_deposits__isnull=True).order_by('level', 'transaction_index')
        async for l2_deposit in qs:
            bridge_deposit = (
                await BridgeDepositTransaction.filter(
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

        qs = EtherlinkDepositEvent.filter(
            bridge_deposits__isnull=True,
            inbox_message_id__isnull=True,
            l2_token_id='xtz',
        ).order_by('level', 'transaction_index')
        async for l2_deposit in qs:
            await l2_deposit.fetch_related('l2_token', 'l2_token__ticket')
            bridge_deposit = (
                await BridgeDepositTransaction.filter(
                    l2_transaction=None,
                    l1_transaction__inbox_message_id__gt=0,
                    l1_transaction__ticket=l2_deposit.l2_token.ticket,
                    l1_transaction__timestamp__gt=l2_deposit.timestamp - timedelta(minutes=5),
                    l1_transaction__timestamp__lt=l2_deposit.timestamp + timedelta(minutes=5),
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

    @staticmethod
    async def check_pending_tezos_withdrawals():
        qs = TezosWithdrawEvent.filter(bridge_withdrawals__isnull=True).order_by('level')
        async for l1_withdrawal in qs:
            bridge_withdrawal = await BridgeWithdrawTransaction.filter(
                l1_transaction=None,
                l2_transaction__outbox_message_id=l1_withdrawal.outbox_message_id,
            ).first()

            if not bridge_withdrawal:
                continue

            bridge_withdrawal.l1_transaction = l1_withdrawal
            await bridge_withdrawal.save()

    @staticmethod
    async def check_pending_transactions():
        await BridgeMatcher.check_pending_tezos_deposits()
        await BridgeMatcher.check_pending_etherlink_withdrawals()

        await BridgeMatcher.check_pending_etherlink_deposits()
        await BridgeMatcher.check_pending_tezos_withdrawals()
