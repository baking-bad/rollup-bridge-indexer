import logging
import threading
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.candidate_pool import CandidatePool
from rollup_bridge_indexer.handlers.michelson_deposit import compute_evm_deposit_tx_hash
from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationKind
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import BridgeOperationType
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from rollup_bridge_indexer.models import EtherlinkWithdrawOperation
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupOutboxMessage
from rollup_bridge_indexer.models import RollupOutboxMessageBuilder
from rollup_bridge_indexer.models import RuntimeKind
from rollup_bridge_indexer.models import TezosDepositOperation
from rollup_bridge_indexer.models import TezosWithdrawOperation

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger('rollup_bridge_indexer.handlers.bridge_matcher')

LAYERS_TIMESTAMP_GAP_MAX = timedelta(seconds=300)
# The kernel rejects a blueprint stamped more than max_blueprint_lookahead_in_seconds (default 300)
# after the last L1 timestamp it saw, so an L2 block is at most that far ahead of the L1 level applying it.
# proof: https://gitlab.com/tezos/tezos/-/blob/413a880d1ea1e9c74a1c4e583fccef97e485ae64/etherlink/kernel_latest/kernel/src/blueprint_storage.rs#L641
BLUEPRINT_LOOKAHEAD_MAX = timedelta(seconds=300)
EVM_ADDRESS_HEX_LENGTH = 40


class BridgeMatcher:
    matcher_lock = threading.Lock()
    smart_rollup_address: str

    @classmethod
    async def check_pending_tezos_deposits(cls):
        if not BridgeMatcherLocks.pending_tezos_deposits:
            return
        BridgeMatcherLocks.pending_tezos_deposits = False

        qs = TezosDepositOperation.filter(bridge_deposits=None)
        created = False
        async for l1_deposit in qs:
            l1_deposit: TezosDepositOperation
            bridge_deposit = await BridgeDepositOperation.create(l1_transaction=l1_deposit)
            runtime_kind = RuntimeKind.michelson if l1_deposit.l2_account_id.startswith('tz') else RuntimeKind.evm
            await BridgeOperation.create(
                id=bridge_deposit.id,
                type=BridgeOperationType.deposit,
                l1_account=l1_deposit.l1_account,
                l2_account_id=l1_deposit.l2_account_id,
                runtime_kind=runtime_kind,
                created_at=l1_deposit.timestamp,
                updated_at=l1_deposit.timestamp,
                status=BridgeOperationStatus.created,
            )
            created = True

        if created:
            BridgeMatcherLocks.set_pending_inbox()

    @classmethod
    async def check_pending_inbox(cls):
        if not BridgeMatcherLocks.pending_inbox:
            return
        BridgeMatcherLocks.pending_inbox = False

        qs = (
            BridgeDepositOperation.filter(
                inbox_message=None,
            )
            .order_by(
                'l1_transaction__level',
                'l1_transaction__counter',
                'l1_transaction__nonce',
            )
            .prefetch_related('l1_transaction')
        )
        pending = await qs
        if not pending:
            return

        candidates: CandidatePool[RollupInboxMessage, tuple[str | None, int]] = CandidatePool(
            'pending_inbox',
            RollupInboxMessage.filter(
                parameters_hash__in=sorted({deposit.l1_transaction.parameters_hash for deposit in pending} - {None}),
            ).order_by('level', 'index'),
            key=lambda message: (message.parameters_hash, message.level),
            warn_on_tie=False,
        )

        attached = False
        for bridge_deposit in pending:
            bridge_deposit: BridgeDepositOperation
            inbox_message = await candidates.take(
                (bridge_deposit.l1_transaction.parameters_hash, bridge_deposit.l1_transaction.level),
            )

            if inbox_message:
                bridge_deposit.inbox_message = inbox_message
                await bridge_deposit.save()
                bridge_deposit.l1_transaction.parameters_hash = None
                await bridge_deposit.l1_transaction.save()
                inbox_message.parameters_hash = None
                await inbox_message.save()
                attached = True

        if attached:
            BridgeMatcherLocks.set_pending_michelson_deposits()
            BridgeMatcherLocks.set_pending_etherlink_deposits()
            BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()

    @classmethod
    async def check_pending_michelson_deposits(cls):
        if not BridgeMatcherLocks.pending_michelson_deposits:
            return
        BridgeMatcherLocks.pending_michelson_deposits = False

        qs = EtherlinkDepositOperation.filter(
            bridge_deposits=None,
            inbox_message_level__isnull=True,
            runtime_kind=RuntimeKind.michelson,
        ).order_by('level', 'transaction_index')

        pending = await qs
        if not pending:
            return

        candidates: CandidatePool[RollupInboxMessage, str] = CandidatePool(
            'pending_michelson_deposits',
            RollupInboxMessage.filter(
                expected_l2_op_hash__in=[l2_deposit.transaction_hash for l2_deposit in pending],
            ).order_by('level', 'index'),
            key=lambda message: message.expected_l2_op_hash,
        )

        backfilled = 0
        unmatched = 0
        for l2_deposit in pending:
            l2_deposit: EtherlinkDepositOperation
            inbox_message = await candidates.take(l2_deposit.transaction_hash)
            if inbox_message is None:
                unmatched += 1
                continue
            l2_deposit.inbox_message_level = inbox_message.level
            l2_deposit.inbox_message_index = inbox_message.index
            await l2_deposit.save()
            backfilled += 1

        if backfilled:
            BridgeMatcherLocks.set_pending_etherlink_deposits()
        if unmatched:
            logger.warning('%d L2 Michelson deposit(s) without a matching inbox op-hash', unmatched)

    @classmethod
    async def check_pending_etherlink_deposits(cls):
        if not BridgeMatcherLocks.pending_etherlink_deposits:
            return
        BridgeMatcherLocks.pending_etherlink_deposits = False

        qs = (
            EtherlinkDepositOperation.filter(
                bridge_deposits=None,
                inbox_message_level__isnull=False,
                inbox_message_index__isnull=False,
            )
            .prefetch_related('l2_token')
            .order_by('level', 'transaction_index', 'log_index')
        )

        candidates: CandidatePool[BridgeDepositOperation, tuple[int, int]] = CandidatePool(
            'pending_etherlink_deposits',
            BridgeDepositOperation.filter(
                l2_transaction=None,
                inbox_message_id__isnull=False,
            )
            .order_by('-created_at')
            .prefetch_related('inbox_message'),
            key=lambda deposit: (deposit.inbox_message.level, deposit.inbox_message.index),
        )

        async for l2_deposit in qs:
            l2_deposit: EtherlinkDepositOperation
            bridge_deposit = await candidates.take((l2_deposit.inbox_message_level, l2_deposit.inbox_message_index))
            if bridge_deposit is None:
                continue

            bridge_deposit.l2_transaction = l2_deposit
            await bridge_deposit.save()

            bridge_operation = await BridgeOperation.get(id=bridge_deposit.pk)
            bridge_operation.is_completed = True
            bridge_operation.is_successful = l2_deposit.l2_token is not None
            bridge_operation.runtime_kind = l2_deposit.runtime_kind
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
    def _expected_xtz_tx_hash(cls, deposit):
        l1_transaction = deposit.l1_transaction
        receiver = l1_transaction.l2_account_id
        if receiver is None or len(receiver) != EVM_ADDRESS_HEX_LENGTH:
            return None
        inbox_message = deposit.inbox_message
        return compute_evm_deposit_tx_hash(
            int(l1_transaction.amount),
            receiver,
            inbox_message.level,
            inbox_message.index,
            cls.smart_rollup_address,
        )

    @staticmethod
    async def _finish_xtz_deposit(bridge_deposit, l2_deposit):
        bridge_deposit.l2_transaction = l2_deposit
        await bridge_deposit.save()
        bridge_deposit.l1_transaction.parameters_hash = None
        await bridge_deposit.l1_transaction.save()
        bridge_deposit.inbox_message.parameters_hash = None
        await bridge_deposit.inbox_message.save()

        bridge_operation = await BridgeOperation.get(id=bridge_deposit.id)
        bridge_operation.is_completed = True
        bridge_operation.is_successful = l2_deposit.l2_token is not None
        bridge_operation.runtime_kind = l2_deposit.runtime_kind
        bridge_operation.updated_at = l2_deposit.timestamp
        bridge_operation.status = BridgeOperationStatus.finished
        await bridge_operation.save()

    @classmethod
    async def check_pending_etherlink_xtz_deposits(cls):
        if not BridgeMatcherLocks.pending_etherlink_xtz_deposits:
            return
        BridgeMatcherLocks.pending_etherlink_xtz_deposits = False

        pending = await (
            EtherlinkDepositOperation.filter(
                bridge_deposits=None,
                l2_token_id='xtz_evm',
                runtime_kind=RuntimeKind.evm,
            )
            .order_by('level', 'transaction_index')
            .prefetch_related('l2_token', 'l2_token__ticket', 'l2_token__ticket__token')
        )
        if not pending:
            return

        def open_deposits():
            return (
                BridgeDepositOperation.filter(
                    l2_transaction=None,
                    inbox_message_id__isnull=False,
                )
                .order_by('l1_transaction__timestamp')
                .prefetch_related('inbox_message', 'l1_transaction')
            )

        by_tx_hash: CandidatePool[BridgeDepositOperation, str] = CandidatePool(
            'pending_etherlink_xtz_deposits',
            open_deposits(),
            key=cls._expected_xtz_tx_hash,
        )
        unnamed: list[EtherlinkDepositOperation] = []
        for l2_deposit in pending:
            bridge_deposit = await by_tx_hash.take(l2_deposit.transaction_hash)
            if bridge_deposit is None:
                unnamed.append(l2_deposit)
                continue
            await cls._finish_xtz_deposit(bridge_deposit, l2_deposit)

        if not unnamed:
            return

        by_value: CandidatePool[BridgeDepositOperation, tuple[str, str, str]] = CandidatePool(
            'pending_etherlink_xtz_deposits_by_value',
            open_deposits(),
            key=lambda deposit: (
                deposit.l1_transaction.ticket_id,
                deposit.l1_transaction.l2_account_id,
                deposit.l1_transaction.amount,
            ),
        )

        for l2_deposit in unnamed:
            scale = 10 ** (l2_deposit.l2_token.decimals - l2_deposit.l2_token.ticket.token.decimals)
            l1_amount = str(int(l2_deposit.amount) // scale)
            window_start = l2_deposit.timestamp - LAYERS_TIMESTAMP_GAP_MAX
            bridge_deposit = await by_value.take(
                (l2_deposit.l2_token.ticket_id, l2_deposit.l2_account_id, l1_amount),
                where=lambda d, start=window_start, end=l2_deposit.timestamp: start <= d.l1_transaction.timestamp <= end,
            )

            if bridge_deposit is None:
                continue

            logger.warning('EVM XTZ credit %s paired by value: its transaction hash names no open deposit', l2_deposit.transaction_hash)
            await cls._finish_xtz_deposit(bridge_deposit, l2_deposit)

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
                l2_account_id=l2_withdrawal.l2_account_id,
                runtime_kind=l2_withdrawal.runtime_kind,
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

        qs = (
            BridgeWithdrawOperation.filter(
                outbox_message=None,
            )
            .order_by(
                'l2_transaction__level',
                'l2_transaction__transaction_index',
                'l2_transaction__log_index',
            )
            .prefetch_related('l2_transaction')
        )
        pending = await qs
        if not pending:
            return

        candidates: CandidatePool[RollupOutboxMessage, str | None] = CandidatePool(
            'pending_outbox',
            RollupOutboxMessage.filter(
                parameters_hash__in=sorted({withdrawal.l2_transaction.parameters_hash for withdrawal in pending} - {None}),
                bridge_withdrawals=None,
            ).order_by('level', 'index'),
            key=lambda message: message.parameters_hash,
            warn_on_tie=False,
        )

        for bridge_withdrawal in pending:
            bridge_withdrawal: BridgeWithdrawOperation
            not_before = bridge_withdrawal.l2_transaction.timestamp - BLUEPRINT_LOOKAHEAD_MAX
            outbox_message = await candidates.take(
                bridge_withdrawal.l2_transaction.parameters_hash,
                where=lambda message, not_before=not_before: message.created_at >= not_before,
            )

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

        candidates: CandidatePool[BridgeWithdrawOperation, UUID] = CandidatePool(
            'pending_tezos_withdrawals',
            BridgeWithdrawOperation.filter(l1_transaction=None).order_by('-created_at'),
            key=lambda withdrawal: withdrawal.outbox_message_id,
        )

        async for l1_withdrawal in qs:
            l1_withdrawal: TezosWithdrawOperation
            bridge_withdrawal = await candidates.take(l1_withdrawal.outbox_message_id)

            if bridge_withdrawal is None:
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

        qs = (
            TezosWithdrawOperation.filter(
                bridge_withdrawals=None,
                outbox_message__builder=RollupOutboxMessageBuilder.service_provider,
                outbox_message__parameters_hash__isnull=False,
            )
            .prefetch_related('outbox_message')
            .order_by('level')
        )

        payouts: list[tuple[TezosWithdrawOperation, int]] = []
        async for l1_payout in qs:
            l1_payout: TezosWithdrawOperation
            try:
                payouts.append((l1_payout, int(l1_payout.outbox_message.parameters_hash or '')))
            except ValueError:
                continue

        if not payouts:
            return

        l2_withdrawals: dict[int, EtherlinkWithdrawOperation] = {
            l2_withdrawal.kernel_withdrawal_id: l2_withdrawal
            async for l2_withdrawal in EtherlinkWithdrawOperation.filter(
                kernel_withdrawal_id__in=sorted({withdrawal_id for _, withdrawal_id in payouts})
            )
        }
        if not l2_withdrawals:
            return

        settled_l2_ids: set[int] = set(
            await BridgeWithdrawOperation.filter(
                l1_transaction_id__isnull=False,
                l2_transaction_id__in=[l2_withdrawal.pk for l2_withdrawal in l2_withdrawals.values()],
            ).values_list('l2_transaction_id', flat=True)
        )

        for l1_payout, withdrawal_id in payouts:
            l2_withdrawal = l2_withdrawals.get(withdrawal_id)
            if l2_withdrawal is None:
                continue

            if l2_withdrawal.pk in settled_l2_ids:
                l1_payout.outbox_message.parameters_hash = None
                await l1_payout.outbox_message.save()
                continue

            l1_payout_parameters = l1_payout.outbox_message.message
            if (
                l1_payout_parameters['withdrawal']['ticketer'] == l2_withdrawal.l1_ticket_owner
                and l1_payout_parameters['withdrawal']['payload'] == l2_withdrawal.fast_payload.hex()
                and l1_payout_parameters['withdrawal']['l2_caller'] == l2_withdrawal.l2_account_id
                and int(datetime.fromisoformat(l1_payout_parameters['withdrawal']['timestamp']).timestamp())
                == int(l2_withdrawal.timestamp.timestamp())
                and int(l1_payout_parameters['withdrawal']['full_amount']) * int(1e12) == int(l2_withdrawal.amount)
                and l1_payout_parameters['withdrawal']['base_withdrawer'] == l2_withdrawal.l1_account
            ):

                customers_bridge_withdrawal = await BridgeWithdrawOperation.get(
                    l1_transaction=None,
                    l2_transaction=l2_withdrawal,
                ).prefetch_related('outbox_message')

                service_provider_outbox_message = customers_bridge_withdrawal.outbox_message

                l1_payout.outbox_message.parameters_hash = None
                await l1_payout.outbox_message.save()

                customers_bridge_withdrawal.outbox_message = l1_payout.outbox_message
                customers_bridge_withdrawal.l1_transaction = l1_payout
                await customers_bridge_withdrawal.save()
                settled_l2_ids.add(l2_withdrawal.pk)

                customers_bridge_operation = await BridgeOperation.get(id=customers_bridge_withdrawal.pk)
                customers_bridge_operation.is_completed = True
                customers_bridge_operation.is_successful = True
                customers_bridge_operation.updated_at = l1_payout.timestamp
                customers_bridge_operation.kind = BridgeOperationKind.fast_withdrawal_claimed
                customers_bridge_operation.status = BridgeOperationStatus.finished
                await customers_bridge_operation.save()

                service_provider_bridge_withdrawal = await BridgeWithdrawOperation.create(
                    created_at=l2_withdrawal.timestamp,
                    l2_transaction=l2_withdrawal,
                    outbox_message=service_provider_outbox_message,
                )

                await BridgeOperation.create(
                    id=service_provider_bridge_withdrawal.id,
                    type=BridgeOperationType.withdrawal,
                    kind=BridgeOperationKind.fast_withdrawal_service_provider,
                    l1_account=l1_payout_parameters['service_provider'],
                    l2_account_id=l2_withdrawal.l2_account_id,
                    runtime_kind=l2_withdrawal.runtime_kind,
                    created_at=l2_withdrawal.timestamp,
                    updated_at=l1_payout.timestamp,
                    status=BridgeOperationStatus.created,
                )
