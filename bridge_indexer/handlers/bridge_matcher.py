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
        qs = EtherlinkDepositEvent.filter(bridge_deposits__isnull=True).order_by('level', 'transaction_index').prefetch_related('l2_token', 'l2_token__ticket')
        async for l2_deposit in qs:
            bridge_deposit = await BridgeDepositTransaction.filter(
                l2_transaction=None,
                l1_transaction__level=l2_deposit.inbox_level,
                l1_transaction__l2_account=l2_deposit.l2_account,
                l1_transaction__ticket_id=l2_deposit.l2_token.ticket.id,
                l1_transaction__amount=l2_deposit.amount,
            ).order_by('l1_transaction__counter').prefetch_related().first()

            if not bridge_deposit:
                continue
            bridge_deposit.l2_transaction = l2_deposit
            await bridge_deposit.save()

    @staticmethod
    async def check_pending_tezos_withdrawals():
        qs = TezosWithdrawEvent.filter(bridge_withdrawals__isnull=True).order_by('outbox_level', 'outbox_msg_id')
        async for l1_withdrawal in qs:
            bridge_withdrawal = await BridgeWithdrawTransaction.filter(
                l1_transaction=None,
                l2_transaction__outbox_level=l1_withdrawal.outbox_level,
                l2_transaction__outbox_msg_id=l1_withdrawal.outbox_msg_id,
            ).first()

            if not bridge_withdrawal:
                continue

            bridge_withdrawal.l1_transaction = l1_withdrawal
            await bridge_withdrawal.save()
