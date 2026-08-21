import logging
import threading
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.candidate_pool import CandidatePool
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

LAYERS_TIMESTAMP_GAP_MAX = timedelta(seconds=20 * 7)


class BridgeMatcher:
    matcher_lock = threading.Lock()

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
            # tz receiver -> Michelson L2, hex receiver -> EVM (known from the L1 routing).
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
            # A new bridge deposit may already have its inbox message in the DB (a
            # fresh-DB bootstrap backfills the whole inbox in on_restart, before the
            # deposit indexes sync) — without this re-arm nothing raises the inbox
            # lock again and attaching stalls until restart/on_synchronized. The
            # attach step runs after this one in the same batch pass.
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
        # A deposit names its inbox message by the parameters hash it carries, at its own
        # level. Attaching consumes the message — both sides drop the hash — so the pool is
        # the messages still up for grabs. On a fresh database that is the entire deposit
        # backlog on both sides, which is what this step costs when it costs anything.
        candidates: CandidatePool[RollupInboxMessage, tuple[str | None, int]] = CandidatePool(
            'pending_inbox',
            RollupInboxMessage.filter(parameters_hash__isnull=False).order_by('level', 'index'),
            key=lambda message: (message.parameters_hash, message.level),
            warn_on_tie=False,
        )

        attached = False
        async for bridge_deposit in qs:
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
            # Every L2 deposit step keys on an attached inbox message (op-hash lookup,
            # coords join, the xtz zip's isnull guard) — re-arm them so an attach
            # completes the match in this same pass even when their own producing
            # handlers fired in earlier, fruitless passes.
            BridgeMatcherLocks.set_pending_michelson_deposits()
            BridgeMatcherLocks.set_pending_etherlink_deposits()
            BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()

    @classmethod
    async def check_pending_michelson_deposits(cls):
        """Backfill inbox coords onto L2 Michelson deposits via their precomputed op-hash.

        These deposits land with no inbox coords (the kernel's coords event is not
        observable). The op-hash is precomputed from L1 data on the inbox message
        (`RollupInboxMessage.expected_l2_op_hash`); match the L2 row's op-hash to it and
        copy the coords across. The link itself is left to `check_pending_etherlink_deposits`,
        which then treats the row like any other coords-bearing deposit.
        """
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

        # The op-hash is derived from the L1 deposit, so one hash names one message and one
        # L2 row. Only the hashes this walk asks about are read — the op-hash is never
        # cleared, so an unbounded pool would grow with the whole history of the network.
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
            # The coords-based step (runs after this one) performs the link.
            BridgeMatcherLocks.set_pending_etherlink_deposits()
        if unmatched:
            # Transiently normal: the L2 row landed before its inbox message was indexed.
            # PERSISTENT rows mean a pre-event-era deposit or a kernel upgrade that changed
            # the op-hash derivation — the one tripwire for that silent failure (see
            # handlers/michelson_deposit.py "Versioning").
            logger.warning('%d L2 Michelson deposit(s) without a matching inbox op-hash', unmatched)

    @classmethod
    async def check_pending_etherlink_deposits(cls):
        if not BridgeMatcherLocks.pending_etherlink_deposits:
            return
        BridgeMatcherLocks.pending_etherlink_deposits = False

        # Runtime-agnostic by design: the unique inbox coords are the key, so both EVM
        # deposits (born with coords) and Michelson deposits (coords backfilled by the
        # step above) link here. No `runtime_kind` filter — the coords themselves are disjoint.
        qs = (
            EtherlinkDepositOperation.filter(
                bridge_deposits=None,
                # Rows without coords (EVM XTZ, L2 Michelson) have nothing to compare here
                # and belong to the xtz/michelson steps. Without this guard the None coords
                # render as LEFT JOIN ... IS NULL and link any inbox-less bridge deposit.
                inbox_message_level__isnull=False,
                inbox_message_index__isnull=False,
            )
            .prefetch_related('l2_token')
            .order_by('level', 'transaction_index', 'log_index')
        )

        # An open bridge deposit is one whose L1 leg has landed and whose L2 leg has not; the
        # inbox coords on its message are what an L2 row names it by. This side stays small
        # by construction while the walk above runs ahead by a backfill's index-speed gap.
        candidates: CandidatePool[BridgeDepositOperation, tuple[int, int]] = CandidatePool(
            'pending_etherlink_deposits',
            BridgeDepositOperation.filter(
                l2_transaction=None,
                # Only an attached message has coords to key on, and a missing one must not
                # collapse into one (None, None) key shared by every inbox-less deposit.
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
    async def check_pending_etherlink_xtz_deposits(cls):
        if not BridgeMatcherLocks.pending_etherlink_xtz_deposits:
            return
        BridgeMatcherLocks.pending_etherlink_xtz_deposits = False

        qs = (
            EtherlinkDepositOperation.filter(
                bridge_deposits=None,
                l2_token_id='xtz_evm',
                # EVM-runtime rows only. Michelson rows carry a deterministic op-hash key
                # (coords via check_pending_michelson_deposits) and this value-based zip must
                # not preempt it — the `runtime_kind` discriminator replaced the old `o…`-prefix exclude.
                runtime_kind=RuntimeKind.evm,
            )
            .order_by('level', 'transaction_index')
            .prefetch_related('l2_token', 'l2_token__ticket', 'l2_token__ticket__token')
        )

        # This step has no exact key — an XTZ deposit is recognised by its value. Three of the
        # four things that must agree are equalities and become the pool's key; only the time
        # window is left to scan. The pairing is a heuristic, so a key that fits more than one
        # open deposit is the case worth hearing about, and the pool says so.
        candidates: CandidatePool[BridgeDepositOperation, tuple[str, str, str]] = CandidatePool(
            'pending_etherlink_xtz_deposits',
            BridgeDepositOperation.filter(
                l2_transaction=None,
                inbox_message_id__isnull=False,
            )
            .order_by('l1_transaction__timestamp')
            .prefetch_related('inbox_message', 'l1_transaction'),
            key=lambda deposit: (
                deposit.l1_transaction.ticket_id,
                deposit.l1_transaction.l2_account_id,
                deposit.l1_transaction.amount,
            ),
        )

        async for l2_deposit in qs:
            l2_deposit: EtherlinkDepositOperation
            # L1 stores mutez, the L2 EVM handle stores wei; the scale is the decimal gap
            # between the two token representations of the same native ticket (no magic 12).
            scale = 10 ** (l2_deposit.l2_token.decimals - l2_deposit.l2_token.ticket.token.decimals)
            l1_amount = str(int(l2_deposit.amount) // scale)
            window_start = l2_deposit.timestamp - LAYERS_TIMESTAMP_GAP_MAX
            bridge_deposit = await candidates.take(
                (l2_deposit.l2_token.ticket_id, l2_deposit.l2_account_id, l1_amount),
                where=lambda d, start=window_start, end=l2_deposit.timestamp: start <= d.l1_transaction.timestamp <= end,
            )

            if bridge_deposit is None:
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
            bridge_operation.runtime_kind = l2_deposit.runtime_kind
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
        # A withdrawal names its outbox message by the parameters hash it carries. Attaching
        # consumes the message, so the pool is the unclaimed ones — the whole withdrawal
        # backlog on a fresh database, and a few hundred rows once the backfill has settled.
        candidates: CandidatePool[RollupOutboxMessage, str | None] = CandidatePool(
            'pending_outbox',
            RollupOutboxMessage.filter(
                parameters_hash__isnull=False,
                bridge_withdrawals=None,
            ).order_by('level', 'index'),
            key=lambda message: message.parameters_hash,
            warn_on_tie=False,
        )

        async for bridge_withdrawal in qs:
            bridge_withdrawal: BridgeWithdrawOperation
            outbox_message = await candidates.take(bridge_withdrawal.l2_transaction.parameters_hash)

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

        # An open bridge withdrawal is one whose L2 leg has landed and whose L1 execution has
        # not; the outbox message it points at is what an execution names it by. This side
        # stays small while the walk grows with every execution a backfill piles up. Reaching
        # for a candidate by `outbox_message_id` costs a sequential scan — the column carries
        # no index — so the pool is worth its one query even when the walk is short.
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

        # The payout carries its withdrawal id in `parameters_hash`, a CharField, while the
        # column it joins is an integer. Tortoise coerces a scalar lookup but not the members
        # of an `__in` list, so cast here — and a hash that is no number names no withdrawal
        # id, so it drops out instead of failing the cast for every other payout in the pass.
        payouts: list[tuple[TezosWithdrawOperation, int]] = []
        async for l1_payout in qs:
            l1_payout: TezosWithdrawOperation
            try:
                payouts.append((l1_payout, int(l1_payout.outbox_message.parameters_hash or '')))
            except ValueError:
                continue

        if not payouts:
            return

        # `kernel_withdrawal_id` is unique, so each id maps to a single L2 leg and there is no
        # candidate tie-break here.
        l2_withdrawals: dict[int, EtherlinkWithdrawOperation] = {
            l2_withdrawal.kernel_withdrawal_id: l2_withdrawal
            async for l2_withdrawal in EtherlinkWithdrawOperation.filter(
                kernel_withdrawal_id__in=sorted({withdrawal_id for _, withdrawal_id in payouts})
            )
        }
        if not l2_withdrawals:
            return

        # L2 legs already paid out on L1 — the slow way, or by an earlier payout of this very
        # pass, which is why the walk below keeps adding to the set: a leg must not be handed
        # to a second payout.
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
                # `l2_caller` is the raw EVM caller the kernel emits; `l2_account_id` is the runtime
                # address (the FK keys on it), not the resolved tz origin
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
