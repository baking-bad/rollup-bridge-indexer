import threading
from datetime import datetime
from datetime import timedelta
from uuid import uuid4

from tortoise.exceptions import DoesNotExist

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from bridge_indexer.models import BridgeDepositOperation
from bridge_indexer.models import BridgeOperation
from bridge_indexer.models import BridgeOperationKind
from bridge_indexer.models import BridgeOperationStatus
from bridge_indexer.models import BridgeOperationType
from bridge_indexer.models import BridgeWithdrawOperation
from bridge_indexer.models import EtherlinkDepositOperation
from bridge_indexer.models import EtherlinkWithdrawOperation
from bridge_indexer.models import RollupInboxMessage
from bridge_indexer.models import RollupOutboxMessage
from bridge_indexer.models import RollupOutboxMessageBuilder
from bridge_indexer.models import TezosDepositOperation
from bridge_indexer.models import TezosWithdrawOperation

LAYERS_TIMESTAMP_GAP_MAX = timedelta(seconds=20 * 7)


class BridgeMatcher:
    matcher_lock = threading.Lock()

    @classmethod
    async def check_pending_tezos_deposits(cls):
        if not BridgeMatcherLocks.pending_tezos_deposits:
            return
        BridgeMatcherLocks.pending_tezos_deposits = False

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
        if not BridgeMatcherLocks.pending_inbox:
            return
        BridgeMatcherLocks.pending_inbox = False

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
        if not BridgeMatcherLocks.pending_etherlink_deposits:
            return
        BridgeMatcherLocks.pending_etherlink_deposits = False

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
        if not BridgeMatcherLocks.pending_etherlink_xtz_deposits:
            return
        BridgeMatcherLocks.pending_etherlink_xtz_deposits = False

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
    async def check_pending_etherlink_withdrawals(cls):
        if not BridgeMatcherLocks.pending_etherlink_withdrawals:
            return
        BridgeMatcherLocks.pending_etherlink_withdrawals = False

        qs = EtherlinkWithdrawOperation.filter(bridge_withdrawals=None)
        async for l2_withdrawal in qs:
            bridge_withdrawal = await BridgeWithdrawOperation.create(created_at=l2_withdrawal.timestamp, l2_transaction=l2_withdrawal)
            await BridgeOperation.create(
                id=bridge_withdrawal.id,
                type=BridgeOperationType.withdrawal,
                l1_account=l2_withdrawal.l1_account,
                l2_account=l2_withdrawal.l2_account,
                created_at=l2_withdrawal.timestamp,
                updated_at=l2_withdrawal.timestamp,
                status=BridgeOperationStatus.created,
                kind=BridgeOperationKind.fast_withdrawal if l2_withdrawal.fast_payload else None,
            )

    @classmethod
    async def check_pending_outbox(cls):
        if not BridgeMatcherLocks.pending_outbox:
            return
        BridgeMatcherLocks.pending_outbox = False

        qs = BridgeWithdrawOperation.filter(
            outbox_message=None,
        ).order_by(
            'l2_transaction__level', 'l2_transaction__transaction_index', 'l2_transaction__log_index',
        ).prefetch_related('l2_transaction')
        async for bridge_withdrawal in qs:
            bridge_withdrawal: BridgeWithdrawOperation
            outbox_message = await RollupOutboxMessage.filter(
                parameters_hash=bridge_withdrawal.l2_transaction.parameters_hash,
                bridge_withdrawals=None,
            ).order_by('level', 'index').first()

            if outbox_message:
                bridge_withdrawal.outbox_message = outbox_message
                await bridge_withdrawal.save()
                bridge_withdrawal.l2_transaction.parameters_hash = None
                await bridge_withdrawal.l2_transaction.save()
                outbox_message.parameters_hash = None
                await outbox_message.save()

    @classmethod
    async def check_pending_tezos_withdrawals(cls):
        if not BridgeMatcherLocks.pending_tezos_withdrawals:
            return
        BridgeMatcherLocks.pending_tezos_withdrawals = False

        qs = TezosWithdrawOperation.filter(
            bridge_withdrawals=None,
            outbox_message__builder=RollupOutboxMessageBuilder.kernel,
        ).order_by('level')
        async for l1_withdrawal in qs:
            l1_withdrawal: TezosWithdrawOperation
            bridge_withdrawal = await BridgeWithdrawOperation.filter(
                l1_transaction=None,
                outbox_message_id=l1_withdrawal.outbox_message_id,
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

    @classmethod
    async def check_pending_claimed_fast_withdrawals(cls):
        if not BridgeMatcherLocks.pending_claimed_fast_withdrawals:
            return
        BridgeMatcherLocks.pending_claimed_fast_withdrawals = False

        qs = TezosWithdrawOperation.filter(
            bridge_withdrawals=None,
            outbox_message__builder=RollupOutboxMessageBuilder.service_provider,
            outbox_message__parameters_hash__isnull=False,
        ).prefetch_related('outbox_message').order_by('level')

        async for l1_withdrawal in qs:
            l1_withdrawal: TezosWithdrawOperation

            try:
                l2_withdrawal = await EtherlinkWithdrawOperation.get(
                    kernel_withdrawal_id=l1_withdrawal.outbox_message.parameters_hash
                )
            except DoesNotExist:
                continue

            withdrawal_id_finished = await BridgeWithdrawOperation.exists(
                l1_transaction_id__isnull=False,
                l2_transaction=l2_withdrawal,
            )
            if withdrawal_id_finished:
                l1_withdrawal.outbox_message.parameters_hash=None
                await l1_withdrawal.outbox_message.save()
                continue

            message = l1_withdrawal.outbox_message.message
            if (
                message['withdrawal']['ticketer'] == l2_withdrawal.l1_ticket_owner and
                message['withdrawal']['payload'] == l2_withdrawal.fast_payload.hex() and
                message['withdrawal']['l2_caller'] == l2_withdrawal.l2_account and
                int(datetime.fromisoformat(message['withdrawal']['timestamp']).timestamp()) == int(l2_withdrawal.timestamp.timestamp()) and
                int(message['withdrawal']['full_amount'])*int(1e12) == int(l2_withdrawal.amount) and
                message['withdrawal']['base_withdrawer'] ==l2_withdrawal.l1_account
            ):

                customers_bridge_withdrawal = await BridgeWithdrawOperation.get(
                    l1_transaction=None,
                    l2_transaction=l2_withdrawal,
                ).prefetch_related('outbox_message')

                service_provider_outbox_message = customers_bridge_withdrawal.outbox_message

                l1_withdrawal.outbox_message.parameters_hash=None
                await l1_withdrawal.outbox_message.save()

                customers_bridge_withdrawal.outbox_message=l1_withdrawal.outbox_message
                customers_bridge_withdrawal.l1_transaction=l1_withdrawal
                await customers_bridge_withdrawal.save()

                customers_bridge_operation = await BridgeOperation.get(id=customers_bridge_withdrawal.pk)
                customers_bridge_operation.is_completed = True
                customers_bridge_operation.is_successful = True
                customers_bridge_operation.updated_at = l1_withdrawal.timestamp
                customers_bridge_operation.kind = BridgeOperationKind.fast_withdrawal_claimed
                customers_bridge_operation.status = BridgeOperationStatus.finished
                await customers_bridge_operation.save()

                service_provider_l2_transaction = l2_withdrawal.clone(pk=uuid4())

                service_provider_l2_transaction.l1_account=message['service_provider']
                service_provider_l2_transaction.kernel_withdrawal_id=int(l2_withdrawal.kernel_withdrawal_id)*-1
                await service_provider_l2_transaction.save()

                service_provider_bridge_withdrawal = await BridgeWithdrawOperation.create(
                    created_at=service_provider_l2_transaction.timestamp,
                    l2_transaction=service_provider_l2_transaction,
                    outbox_message=service_provider_outbox_message,
                )

                await BridgeOperation.create(
                    id=service_provider_bridge_withdrawal.id,
                    type=BridgeOperationType.withdrawal,
                    kind=BridgeOperationKind.fast_withdrawal_service_provider,
                    l1_account=service_provider_l2_transaction.l1_account,
                    l2_account=service_provider_l2_transaction.l2_account,
                    created_at=l2_withdrawal.timestamp,
                    updated_at=l1_withdrawal.timestamp,
                    status=BridgeOperationStatus.created,
                )
