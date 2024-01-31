from bridge_indexer.models import BridgeDepositTransaction
from bridge_indexer.models import BridgeWithdrawTransaction
from bridge_indexer.models import EtherlinkDepositEvent
from bridge_indexer.models import EtherlinkWithdrawEvent
from bridge_indexer.models import TezosDepositEvent


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
            l1_deposit = await TezosDepositEvent.filter(
                bridge_deposits__isnull=False,
                level=l2_deposit.inbox_level,
                l2_account=l2_deposit.l2_account,
                ticket_id=l2_deposit.l2_token.ticket.id,
                amount=l2_deposit.amount,
            ).order_by('counter').prefetch_related().first()

            if not l1_deposit:
                continue
            bridge_deposit = await BridgeDepositTransaction.get(l1_transaction=l1_deposit)
            bridge_deposit.l2_transaction = l2_deposit
            await bridge_deposit.save()
