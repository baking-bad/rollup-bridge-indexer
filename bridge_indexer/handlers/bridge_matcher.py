from datetime import timedelta

from bridge_indexer.models import BridgeDepositOperation
from bridge_indexer.models import BridgeOperation
from bridge_indexer.models import BridgeOperationStatus
from bridge_indexer.models import BridgeOperationType
from bridge_indexer.models import BridgeWithdrawOperation
from bridge_indexer.models import EtherlinkDepositOperation
from bridge_indexer.models import EtherlinkWithdrawOperation
from bridge_indexer.models import RollupInboxMessage
from bridge_indexer.models import TezosDepositOperation
from bridge_indexer.models import TezosWithdrawOperation

LAYERS_TIMESTAMP_GAP_MAX = timedelta(seconds=20 * 7)


class BridgeMatcher:
    _pending_tezos_deposits: bool = False
    _pending_etherlink_withdrawals: bool = False
    _pending_etherlink_deposits: bool = False
    _pending_etherlink_xtz_deposits: bool = False
    _pending_tezos_withdrawals: bool = False

    @classmethod
    def set_pending_tezos_deposits(cls):
        cls._pending_tezos_deposits = True

    @classmethod
    def set_pending_etherlink_withdrawals(cls):
        cls._pending_etherlink_withdrawals = True

    @classmethod
    def set_pending_etherlink_deposits(cls):
        cls._pending_etherlink_deposits = True

    @classmethod
    def set_pending_etherlink_xtz_deposits(cls):
        cls._pending_etherlink_xtz_deposits = True

    @classmethod
    def set_pending_tezos_withdrawals(cls):
        cls._pending_tezos_withdrawals = True

    @classmethod
    async def check_pending_tezos_deposits(cls):
        if not cls._pending_tezos_deposits:
            return
        else:
            cls._pending_tezos_deposits = False

        qs = TezosDepositOperation.filter(bridge_deposits=None)
        async for l1_deposit in qs:
            l1_deposit: TezosDepositOperation
            bridge_deposit = await BridgeDepositOperation.create(l1_transaction=l1_deposit)
            await BridgeOperation.create(
                id=bridge_deposit.id,
                type=BridgeOperationType.deposit,
                l1_account=l1_deposit.l1_account,
                l2_account=l1_deposit.l2_account,
                created_at=l1_deposit.timestamp,
                updated_at=l1_deposit.timestamp,
                status=BridgeOperationStatus.created,
            )

    @classmethod
    async def check_pending_inbox(cls):
        qs = BridgeDepositOperation.filter(
            inbox_message=None,
        ).order_by(
            'l1_transaction__level', 'l1_transaction__counter', 'l1_transaction__nonce',
        ).prefetch_related('l1_transaction')
        async for bridge_deposit in qs:
            bridge_deposit: BridgeDepositOperation
            inbox_message = await RollupInboxMessage.filter(
                parameters_hash=bridge_deposit.l1_transaction.parameters_hash,
                level=bridge_deposit.l1_transaction.level,
            ).order_by('index').first()

            if inbox_message:
                bridge_deposit.inbox_message = inbox_message
                await bridge_deposit.save()
                bridge_deposit.l1_transaction.parameters_hash = None
                await bridge_deposit.l1_transaction.save()
                inbox_message.parameters_hash = None
                await inbox_message.save()

    @classmethod
    async def check_pending_etherlink_deposits(cls):
        if not cls._pending_etherlink_deposits:
            return
        else:
            cls._pending_etherlink_deposits = False

        qs = EtherlinkDepositOperation.filter(
            bridge_deposits=None,
        ).prefetch_related('l2_token').order_by('level', 'transaction_index', 'log_index')

        async for l2_deposit in qs:
            l2_deposit: EtherlinkDepositOperation
            bridge_deposit = await BridgeDepositOperation.filter(
                inbox_message__level=l2_deposit.inbox_message_level,
                inbox_message__index=l2_deposit.inbox_message_index,
                l2_transaction=None,
            ).first()

            if not bridge_deposit:
                continue
            bridge_deposit.l2_transaction = l2_deposit
            await bridge_deposit.save()

            bridge_operation = await BridgeOperation.get(id=bridge_deposit.pk)
            bridge_operation.is_completed = True
            bridge_operation.is_successful = l2_deposit.l2_token is not None
            bridge_operation.updated_at = l2_deposit.timestamp
            match (l2_deposit.l2_token_id, l2_deposit.ticket_id, l2_deposit.ticket_owner):
                case str(), str(), str():
                    bridge_operation.status = BridgeOperationStatus.finished
                case None, str(), str():
                    bridge_operation.status = BridgeOperationStatus.revertable
                case None, None, '':
                    bridge_operation.status = BridgeOperationStatus.empty_proxy
                case None, None, str():
                    bridge_operation.status = BridgeOperationStatus.proxy_not_whitelisted
                case _:
                    raise ValueError

            await bridge_operation.save()

    @classmethod
    async def check_pending_etherlink_xtz_deposits(cls):
        if not cls._pending_etherlink_xtz_deposits:
            return
        else:
            cls._pending_etherlink_xtz_deposits = False

        qs = EtherlinkDepositOperation.filter(
            bridge_deposits=None,
            l2_token_id='xtz',
        ).order_by('level', 'transaction_index').prefetch_related('l2_token', 'l2_token__ticket')
        async for l2_deposit in qs:
            l2_deposit: EtherlinkDepositOperation
            bridge_deposit = (
                await BridgeDepositOperation.filter(
                    l2_transaction=None,
                    inbox_message_id__isnull=False,
                    l1_transaction__ticket=l2_deposit.l2_token.ticket,
                    l1_transaction__timestamp__lte=l2_deposit.timestamp,
                    l1_transaction__timestamp__gte=l2_deposit.timestamp - LAYERS_TIMESTAMP_GAP_MAX,
                    l1_transaction__l2_account=l2_deposit.l2_account,
                    l1_transaction__amount=l2_deposit.amount[:-12],
                )
                .order_by('l1_transaction__timestamp')
                .prefetch_related('inbox_message', 'l1_transaction')
                .first()
            )

            if not bridge_deposit:
                continue

            bridge_deposit.l2_transaction = l2_deposit
            await bridge_deposit.save()
            bridge_deposit.l1_transaction.parameters_hash = None
            await bridge_deposit.l1_transaction.save()
            bridge_deposit.inbox_message.parameters_hash = None
            await bridge_deposit.inbox_message.save()

            bridge_operation = await BridgeOperation.get(id=bridge_deposit.id)
            bridge_operation.is_completed = True
            bridge_operation.is_successful = l2_deposit.l2_token is not None
            bridge_operation.updated_at = l2_deposit.timestamp
            bridge_operation.status = BridgeOperationStatus.finished
            await bridge_operation.save()

    @classmethod
    async def check_pending_tezos_withdrawals(cls):
        if not cls._pending_tezos_withdrawals:
            return
        else:
            cls._pending_tezos_withdrawals = False

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

            bridge_operation = await BridgeOperation.get(id=bridge_withdrawal.pk)
            bridge_operation.is_completed = True
            bridge_operation.is_successful = True
            bridge_operation.updated_at = l1_withdrawal.timestamp
            bridge_operation.status = BridgeOperationStatus.finished
            await bridge_operation.save()

    @staticmethod
    async def check_pending_transactions():
        await BridgeMatcher.check_pending_tezos_deposits()
        await BridgeMatcher.check_pending_etherlink_withdrawals()

        await BridgeMatcher.check_pending_etherlink_deposits()
        await BridgeMatcher.check_pending_etherlink_xtz_deposits()
        await BridgeMatcher.check_pending_tezos_withdrawals()
