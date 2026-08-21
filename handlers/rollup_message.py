from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from uuid import NAMESPACE_OID
from uuid import uuid5

import aiohttp
import orjson
from dipdup.models import IndexStatus
from dipdup.models import Meta
from pydantic import BaseModel
from pydantic import ValidationError
from pytezos import MichelsonRuntimeError
from pytezos import MichelsonType
from pytezos import michelson_to_micheline
from tortoise.exceptions import DoesNotExist

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.michelson_deposit import expected_op_hash_from_inbox
from rollup_bridge_indexer.handlers.ticket import FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE
from rollup_bridge_indexer.handlers.ticket import WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE
from rollup_bridge_indexer.handlers.ticket import TicketService
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import RollupCementedCommitment
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType
from rollup_bridge_indexer.models import RollupOutboxMessage
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models import json_dumps_fallback
from rollup_bridge_indexer.types.fast_withdrawal.tezos_parameters.default import (
    DefaultParameter as ExecuteOutboxMessageFastWithdrawalDefaultParameter,
)
from rollup_bridge_indexer.types.kernel.evm_events.withdrawal import WithdrawalPayload as FAWithdrawalPayload
from rollup_bridge_indexer.types.kernel_native.evm_events.fast_withdrawal import FastWithdrawalPayload
from rollup_bridge_indexer.types.kernel_native.evm_events.withdrawal import WithdrawalPayload as NativeWithdrawalPayload
from rollup_bridge_indexer.types.rollup.tezos_parameters.default import DefaultParameter
from rollup_bridge_indexer.types.rollup.tezos_parameters.default import TicketContent as RollupParametersTicketContent
from rollup_bridge_indexer.types.ticketer.tezos_parameters.withdraw import (
    WithdrawParameter as ExecuteOutboxMessageTicketerWithdrawParameter,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from logging import Logger

    from dipdup.datasources.http import HttpDatasource
    from dipdup.datasources.tezos_tzkt import TezosTzktDatasource
    from dipdup.models.evm import EvmEvent
    from dipdup.models.tezos import TezosOperationData
    from dipdup.models.tezos import TezosTransaction

    from rollup_bridge_indexer.handlers.service_container import BridgeConstantStorage
    from rollup_bridge_indexer.handlers.service_container import ProtocolConstantStorage
    from rollup_bridge_indexer.types.rollup.tezos_storage import RollupStorage


_WITHDRAW_MICHELSON_TYPE = MichelsonType.match(michelson_to_micheline(WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE))
_FAST_WITHDRAW_MICHELSON_TYPE = MichelsonType.match(michelson_to_micheline(FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE))


class InboxMessageService:
    @classmethod
    async def _read_inbox_level(cls, inbox_level: int) -> AsyncGenerator[RollupInboxMessage, None]:
        async for inbox_message in RollupInboxMessage.filter(level=inbox_level, l1_deposits__isnull=True).order_by('id'):
            yield inbox_message

    @classmethod
    async def match_transaction_with_inbox(cls, data: TezosOperationData) -> RollupInboxMessage:
        async for inbox_message in cls._read_inbox_level(data.level):
            # only `transfer` type inbox messages here
            if data.parameter_json == inbox_message.message:
                return inbox_message

        raise TypeError('Transaction not matched')

    @staticmethod
    async def find_by_index(inbox_level: int, index: int):
        return await RollupInboxMessage.get(level=inbox_level, index=index)


class OutboxMessageService:
    def __init__(self, tzkt: TezosTzktDatasource, rollup_node: HttpDatasource, protocol: ProtocolConstantStorage):
        self._tzkt = tzkt
        self._rollup_node = rollup_node
        self._protocol = protocol

    @classmethod
    def estimate_outbox_message_cemented_level(cls, outbox_level: int, origination_level: int, protocol: ProtocolConstantStorage) -> int:
        commitment_period = protocol.smart_rollup_commitment_period
        challenge_window = protocol.smart_rollup_challenge_window

        return (
            outbox_level
            + (origination_level - outbox_level) % commitment_period
            + challenge_window
            + (commitment_period - challenge_window % commitment_period)
            % commitment_period  # well, at this line, I'm just fucking around already.
            + 5
        )

    @classmethod
    async def find_by_index(cls, outbox_level: int, index: int):
        return await RollupOutboxMessage.get(level=outbox_level, index=index)

    @staticmethod
    def extract_l1_amount(message: dict) -> int:
        try:
            parameters_micheline = message['transactions'][0]['parameters']
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f'Outbox message has no transaction parameters: {message}') from e

        for michelson_type, parameter_cls in (
            (_WITHDRAW_MICHELSON_TYPE, ExecuteOutboxMessageTicketerWithdrawParameter),
            (_FAST_WITHDRAW_MICHELSON_TYPE, ExecuteOutboxMessageFastWithdrawalDefaultParameter),
        ):
            try:
                parameters_data = michelson_type.from_micheline_value(parameters_micheline).to_python_object()
                parameters = parameter_cls.model_validate(parameters_data)
            except (ValueError, MichelsonRuntimeError, ValidationError):
                continue
            return parameters.ticket.amount

        raise ValueError(f"Can't extract ticket.amount from outbox message: {message}")

    async def update_proof(self):
        head_data = await self._tzkt.get_head_block()
        async for bridge_withdraw_operation in BridgeWithdrawOperation.filter(
            l1_transaction=None,
            outbox_message_id__isnull=False,
        ).prefetch_related('outbox_message'):
            bridge_withdraw_operation: BridgeWithdrawOperation
            outbox_message = bridge_withdraw_operation.outbox_message

            if head_data.level > sum(
                [
                    outbox_message.level,
                    self._protocol.smart_rollup_challenge_window,
                    self._protocol.smart_rollup_max_active_outbox_levels,
                ]
            ):
                continue

            if await RollupCementedCommitment.filter(inbox_level__gte=outbox_message.level).count() == 0:
                continue

            try:
                proof_data = await self._rollup_node.request(
                    'GET',
                    f'global/block/head/helpers/proofs/outbox/{outbox_message.level}/messages?index={outbox_message.index}',
                )
            except (
                TimeoutError,
                aiohttp.ClientConnectionError,
                aiohttp.ClientConnectorError,
                aiohttp.ClientResponseError,
                aiohttp.ClientPayloadError,
            ):
                continue

            outbox_message.proof = proof_data['proof']
            commitment = await RollupCementedCommitment.get(hash=proof_data['commitment'])
            outbox_message.commitment = commitment
            outbox_message.updated_at = commitment.created_at
            await outbox_message.save()

            bridge_withdraw_operation.updated_at = commitment.created_at
            await bridge_withdraw_operation.save()

            bridge_operation = await BridgeOperation.get(id=bridge_withdraw_operation.id)
            bridge_operation.updated_at = commitment.created_at
            bridge_operation.status = BridgeOperationStatus.sealed
            await bridge_operation.save()

    @classmethod
    async def _read_inbox_level(cls, inbox_level: int) -> AsyncGenerator[RollupInboxMessage, None]:
        async for inbox_message in RollupInboxMessage.filter(level=inbox_level, l1_deposits__isnull=True).order_by('id'):
            yield inbox_message


class RollupMessageIndex:
    """Indexes the rollup inbox from TzKT and the outbox from the rollup node.

    One pass is `_process`. The inbox is walked with a cursor; every `external` message means
    its L1 level has outbox messages to fetch, and that level goes into `_outbox_level_queue`.

    The queue is the one piece of that work the cursor cannot describe — the level of a full
    outbox's continuation has no inbox message behind it at all — so it is stored alongside the
    rows, in `dipdup_meta`. Two orderings make it durable: it is written **before** the inbox
    rows that move the cursor past the externals that filled it, and a level leaves it **after**
    the outbox rows it produced are committed. Between those two points a crash costs a re-fetch
    and never a message.
    """

    first_ticket_level: int | None = None
    request_limit = 10000
    _lock = threading.Lock()

    # Not part of the package schema, so storing here costs no schema hash change — and
    # therefore no reindex.
    pending_outbox_levels_key = 'rollup_message_pending_outbox_levels'

    def __init__(
        self,
        tzkt: TezosTzktDatasource,
        rollup_node: HttpDatasource,
        bridge: BridgeConstantStorage,
        ticket_service: TicketService,
        protocol: ProtocolConstantStorage,
        logger: Logger,
    ):
        self._tzkt = tzkt
        self._rollup_node = rollup_node
        self._bridge = bridge
        self._ticket_service = ticket_service
        self._protocol = protocol
        self._logger = logger

        self._status: IndexStatus = IndexStatus.new

        self._inbox_id_cursor: int = 0
        self._inbox_level_cursor: int = 0
        self._outbox_level_cursor: int = 0
        self._outbox_index_cursor: int = 0
        self._origination_level: int | None = None

        # Test-only inbox-backfill window (prod leaves these unset -> full backfill from origination).
        # The full inbox is ~18M messages; bounding it makes the stripped test indexer finish in seconds.
        _sync_first = os.environ.get('ROLLUP_SYNC_FIRST_LEVEL')
        _sync_last = os.environ.get('ROLLUP_SYNC_LAST_LEVEL')
        self._sync_first_level: int | None = int(_sync_first) if _sync_first else None
        self._sync_last_level: int | None = int(_sync_last) if _sync_last else None

        self._outbox_level_queue: set = set()
        # Last value written to `dipdup_meta`; keeps the durability write off the hot path
        # when the queue has not moved. `None` means "not read from the database yet".
        self._saved_outbox_level_queue: set[int] | None = None
        self._create_inbox_batch: list[RollupInboxMessage] = []
        self._create_outbox_batch: list[RollupOutboxMessage] = []

    async def _get_origination_level(self) -> int:
        if self._origination_level is None:
            rollup_data = await self._tzkt.request(
                method='GET',
                url=f'v1/smart_rollups/{self._bridge.smart_rollup_address}',
            )
            self._origination_level = rollup_data['firstActivity']
        return self._origination_level

    async def synchronize(self):
        with self._lock:
            while True:
                if self._status == IndexStatus.realtime:
                    break

                if self._status == IndexStatus.new:
                    await self._prepare_new_index()

                if self._status == IndexStatus.syncing:
                    await self._process()

    async def handle_realtime(self, head_level: int):
        with self._lock:
            if self._status == IndexStatus.realtime:
                previous_outbox_level_cursor = self._outbox_level_cursor
                await self._process()
                if self._outbox_level_cursor > previous_outbox_level_cursor:
                    BridgeMatcherLocks.set_pending_outbox()
                    BridgeMatcherLocks.set_pending_tezos_withdrawals()
                    BridgeMatcherLocks.set_pending_claimed_fast_withdrawals()

    async def _process(self):
        await self._drain_outbox_levels()

        inbox = await self._fetch_inbox_page()
        if not inbox:
            if self._status == IndexStatus.syncing:
                self._status = IndexStatus.realtime
                return
        else:
            await self._walk_inbox_page(inbox)
            await self._commit_inbox_page()

        await self._drain_outbox_levels()
        await self._write_cursor_sentinel()

    async def _fetch_inbox_page(self):
        """The messages after the cursor. `id.gt=` — the cursor is the last id already consumed."""
        inbox = await self._tzkt.request(
            method='GET',
            url=f'v1/smart_rollups/inbox?id.gt={self._inbox_id_cursor}&type.in=transfer,external&micheline=0&sort=id&limit={self.request_limit}',
        )
        if len(inbox):
            self._logger.info('Found %d not indexed Inbox Messages.', len(inbox))
        return inbox

    async def _walk_inbox_page(self, inbox):
        """Sort the page into the two in-memory batches. Nothing here touches the database."""
        for inbox_message in inbox:
            # Test-only upper bound: stop once we pass the requested window.
            if self._sync_last_level is not None and inbox_message['level'] > self._sync_last_level:
                self._status = IndexStatus.realtime
                break
            match inbox_message['type']:
                case RollupInboxMessageType.transfer.value:
                    if 'target' not in inbox_message or 'address' not in inbox_message.get('target', {}):
                        raise ValueError(f"Transfer inbox message {inbox_message['id']} is missing target field")
                    # TzKT has no `target=` filter, so the rollup is selected here.
                    if inbox_message['target']['address'] != self._bridge.smart_rollup_address:
                        continue
                    await self._handle_transfer_inbox_message(inbox_message)
                case RollupInboxMessageType.external.value:
                    await self._handle_external_inbox_message(inbox_message)
                case _:
                    continue
            self._inbox_id_cursor = inbox_message['id']

    async def _commit_inbox_page(self):
        """Store the pending outbox levels, then the rows — in that order.

        The rows move the resume cursor past the external messages that filled the queue, so a
        queue stored after them would describe work that nothing can reach again.
        """
        await self._save_pending_outbox_levels()

        if not len(self._create_inbox_batch):
            return
        await RollupInboxMessage.bulk_create(self._create_inbox_batch)
        self._logger.info('Successfully saved %d new Inbox Messages.', len(self._create_inbox_batch))
        self._inbox_level_cursor = self._create_inbox_batch[-1].level
        BridgeMatcherLocks.set_pending_inbox()
        # A late-arriving inbox message may complete an already-recorded L2 Michelson deposit.
        BridgeMatcherLocks.set_pending_michelson_deposits()

        del self._create_inbox_batch[:]

    async def _write_cursor_sentinel(self):
        """Mark the cursor with a level-0 row when no real message carries its id."""
        self._logger.info('Update Inbox Message cursor index to %s', self._inbox_id_cursor)
        if await RollupInboxMessage.exists(id=self._inbox_id_cursor):
            return
        await RollupInboxMessage.filter(
            level=0,
            type=RollupInboxMessageType.external.value,
            id__lt=self._inbox_id_cursor,
        ).delete()
        await RollupInboxMessage.create(
            id=self._inbox_id_cursor,
            level=0,
            index=0,
            message={},
            parameters_hash=None,
            type=RollupInboxMessageType.external,
        )

    async def _drain_outbox_levels(self):
        """Fetch every pending outbox level the rollup node has already applied, then store the result.

        A level leaves the pending set only once its messages are committed: the fetch can
        fail (a wedged rollup node answers 500 for every level above the one it processed),
        and until the rows are in the database the pending level is the only record that the
        work is still owed.
        """
        if len(self._outbox_level_queue):
            processed_level = await self._rollup_processed_level()
            while len(self._outbox_level_queue) > 0 and min(self._outbox_level_queue) <= processed_level:
                # Lowest first: the loop condition speaks about the lowest level, so the drain
                # has to take that one. Popping an arbitrary element can reach above the ceiling.
                outbox_level = min(self._outbox_level_queue)
                self._outbox_level_queue.discard(outbox_level)
                await self._handle_outbox_level(outbox_level)

        if len(self._create_outbox_batch):
            await RollupOutboxMessage.bulk_create(self._create_outbox_batch, ignore_conflicts=True)
            self._logger.info('Successfully saved %d new Outbox Messages.', len(self._create_outbox_batch))
            self._outbox_index_cursor = self._create_outbox_batch[-1].index
            self._outbox_level_cursor = self._create_outbox_batch[-1].level
            BridgeMatcherLocks.set_pending_outbox()

            del self._create_outbox_batch[:]

        # The drained levels are rows now; what is left is what is still deferred or owed.
        await self._save_pending_outbox_levels()

    async def _rollup_processed_level(self) -> int:
        """The last L1 level the rollup node has applied — the ceiling the drain may ask for.

        Not the L1 head: a node that stops applying blocks keeps answering RPC, and every level
        above the one it applied has no hash it can resolve. That is the fault this ceiling is
        for, and the L1 head does not see it.
        """
        return int(await self._rollup_node.request(method='GET', url='global/block/head/level'))

    async def _load_pending_outbox_levels(self):
        """Take over the outbox levels the previous run queued but never stored."""
        meta = await Meta.get_or_none(key=self.pending_outbox_levels_key)
        pending = {int(level) for level in (meta.value or [])} if meta else set()
        self._saved_outbox_level_queue = set(pending)
        if pending:
            # A backfill page can leave thousands of levels pending — log the span, not the list.
            self._logger.info('Restored %d pending Outbox level(s) from the previous run: %d..%d', len(pending), min(pending), max(pending))
        self._outbox_level_queue |= pending

    async def _drop_pending_outbox_levels(self):
        """Forget levels owed to a database that no longer exists.

        `dipdup_meta` is immune to the reindex wipe, so without this a fresh backfill would
        inherit the previous history's queue and drag its outbox cursor ahead of itself.
        """
        deleted = await Meta.filter(key=self.pending_outbox_levels_key).delete()
        if deleted:
            self._logger.info('Dropped the pending Outbox levels of a wiped database.')
        self._saved_outbox_level_queue = set()

    async def _save_pending_outbox_levels(self):
        """Persist the queue itself — the only piece of the drain the inbox cursor cannot describe.

        The full-outbox continuation level (`_handle_outbox_level`, `outbox_level + 1`) has no
        inbox message behind it at all, so it can only be recovered from here.
        """
        if self._outbox_level_queue == self._saved_outbox_level_queue:
            return
        await Meta.update_or_create(
            key=self.pending_outbox_levels_key,
            defaults={'value': sorted(self._outbox_level_queue)},
        )
        self._saved_outbox_level_queue = set(self._outbox_level_queue)

    async def _handle_transfer_inbox_message(self, message):
        try:
            expected_l2_op_hash = expected_op_hash_from_inbox(
                message['parameter'], message['level'], message['index'], self._bridge.smart_rollup_address
            )
        except ValueError:
            # Not Michelson-routed (legacy/FA routing shapes) — no synthetic L2 op to match.
            expected_l2_op_hash = None
        self._create_inbox_batch.append(
            RollupInboxMessage(
                id=message['id'],
                level=message['level'],
                index=message['index'],
                type=RollupInboxMessageType.transfer,
                message=message['parameter'],
                parameters_hash=await InboxMessageParametersHash(message['parameter']).from_inbox_message_parameters(),
                expected_l2_op_hash=expected_l2_op_hash,
            )
        )

    async def _handle_external_inbox_message(self, message):
        self._outbox_level_queue.add(message['level'])

    async def _handle_outbox_level(self, outbox_level):
        outbox = await self._rollup_node.request(method='GET', url=f'global/block/{outbox_level}/outbox/{outbox_level}/messages')
        if len(outbox) == 0:
            return
        self._logger.info('_handle_outbox_level %d with %d messages.', outbox_level, len(outbox))

        if len(outbox) == self._protocol.smart_rollup_max_outbox_messages_per_level:
            if outbox_level < self._outbox_level_cursor:
                return
            if outbox_level == self._outbox_level_cursor:
                if self._outbox_index_cursor < len(outbox) - 1:
                    outbox = outbox[self._outbox_index_cursor :]
                else:
                    return

        created_at = datetime.fromisoformat(await self._tzkt.request('GET', f'v1/blocks/{outbox_level}/timestamp'))
        cemented_level = OutboxMessageService.estimate_outbox_message_cemented_level(
            outbox_level,
            await self._get_origination_level(),
            self._protocol,
        )
        cemented_at = datetime.fromisoformat(await self._tzkt.request('GET', f'v1/blocks/{cemented_level}/timestamp'))

        for outbox_message in outbox:
            try:
                parameters_hash = await OutboxMessageParametersHash(outbox_message).from_outbox_message(self._ticket_service)
            except (ValueError, MichelsonRuntimeError):
                try:
                    parameters_hash = await OutboxMessageParametersHash(outbox_message).from_fast_outbox_message(self._ticket_service)
                except (ValueError, MichelsonRuntimeError) as e:
                    self._logger.warning('Skip hashing outbox message. %s', str(e))
                    continue

            self._create_outbox_batch.append(
                RollupOutboxMessage(
                    level=outbox_message['outbox_level'],
                    index=outbox_message['message_index'],
                    message=outbox_message['message'],
                    parameters_hash=parameters_hash,
                    created_at=created_at,
                    cemented_at=cemented_at,
                    cemented_level=cemented_level,
                )
            )

        if len(outbox) == self._protocol.smart_rollup_max_outbox_messages_per_level:
            self._logger.info('Full outbox found at level %d, going to check next level for the rest Outbox Messages...', outbox_level)
            # No inbox message stands behind this level — the stored queue is its only record.
            self._outbox_level_queue.add(outbox_level + 1)

    async def _prepare_new_index(self):
        try:
            last_saved_inbox_message = await RollupInboxMessage.all().order_by('-id').first()
            # The cursor is the last id consumed — `id.gt=` resumes strictly after it, so the
            # saved row is not re-read and the one behind it is not skipped.
            self._inbox_id_cursor = last_saved_inbox_message.id
            self._logger.info('Last previous saved Inbox Message found. Going to continue with next Inbox Message.')
            await self._load_pending_outbox_levels()
        except AttributeError:
            # No inbox rows: this database was wiped or is brand new. `dipdup_meta` survives a
            # reindex, so a stored queue here belongs to a history that no longer exists.
            await self._drop_pending_outbox_levels()
            if self._sync_first_level is not None:
                self._logger.info(
                    'No previous saved Inbox Message found. TEST bound: start indexing since level %d.', self._sync_first_level
                )
                first_level = self._sync_first_level
            elif self.first_ticket_level is not None:
                self._logger.info('No previous saved Inbox Message found. Going to start indexing since first Whitelisted Token activity.')
                first_level = self.first_ticket_level
            else:
                self._logger.info('No previous saved Inbox Message found. Going to start indexing since Smart Rollup origination moment.')
                rollup_data = await self._tzkt.request(method='GET', url=f'v1/smart_rollups/{self._bridge.smart_rollup_address}')
                first_level = rollup_data['firstActivity']
            # Note: TzKT API doesn't support target= filter, transfer messages will be filtered in _process()
            inbox = await self._tzkt.request(
                method='GET',
                url=f'v1/smart_rollups/inbox?type.in=transfer,external&level.ge={first_level}&sort.asc=id&limit=1',
            )
            # ...which is the first message to index, not one already indexed: step back so the
            # cursor keeps meaning the same thing.
            self._inbox_id_cursor = inbox[0]['id'] - 1

        self._logger.info('Inbox Message cursor index is %d.', self._inbox_id_cursor)
        self._status = IndexStatus.syncing


def _inbox_parameters_hash(dto: Any) -> str:
    return uuid5(NAMESPACE_OID, json_dumps_fallback(dto, option=orjson.OPT_SORT_KEYS)).hex


class InboxMessageParametersHash:
    def __init__(self, parameters: Any):
        self._parameters = parameters

    async def from_inbox_message_parameters(self) -> str:
        return _inbox_parameters_hash(self._parameters)


class TransactionParametersHash:
    def __init__(self, transaction: TezosTransaction[DefaultParameter, RollupStorage]):
        self._transaction = transaction

    async def from_transaction(self) -> str:
        return _inbox_parameters_hash(self._transaction.data.parameter_json)


class WithdrawalParametersHashableDTO(BaseModel):
    receiver: str
    ticket_hash: str
    amount: int
    ticketer_address: str
    proxy: str


class FastWithdrawalParametersHashableDTO(BaseModel):
    withdrawal_id: int


def _comparable_parameters_hash(dto: WithdrawalParametersHashableDTO | FastWithdrawalParametersHashableDTO) -> str:
    return uuid5(NAMESPACE_OID, json_dumps_fallback(dto.model_dump(), option=orjson.OPT_SORT_KEYS)).hex


class OutboxMessageParametersHash:
    def __init__(self, message: dict[str, Any]):
        self._message = message

    async def from_outbox_message(self, ticket_service: TicketService) -> str:
        try:
            transaction = self._message['message']['transactions'][0]
            parameters_micheline = transaction['parameters']

            parameters_data = _WITHDRAW_MICHELSON_TYPE.from_micheline_value(parameters_micheline).to_python_object()
            parameters: ExecuteOutboxMessageTicketerWithdrawParameter = ExecuteOutboxMessageTicketerWithdrawParameter.model_validate(
                parameters_data
            )

            bytes_field = None
            if parameters.ticket.content.metadata:
                bytes_field = parameters.ticket.content.metadata.hex()

            ticket = await ticket_service.fetch_ticket(
                parameters.ticket.ticketer,
                RollupParametersTicketContent.model_validate(
                    obj={
                        'nat': str(parameters.ticket.content.ticket_id),
                        'bytes': bytes_field,
                    }
                ),
            )

            comparable_data = WithdrawalParametersHashableDTO(
                receiver=str(parameters.receiver),
                ticket_hash=ticket.hash,
                amount=parameters.ticket.amount,
                ticketer_address=str(parameters.ticket.ticketer),
                proxy=transaction['destination'],
            )
        except (AttributeError, KeyError, DoesNotExist):
            raise ValueError(f"Can't get OutboxParametersHash from message: {self._message}") from None

        return _comparable_parameters_hash(comparable_data)

    async def from_fast_outbox_message(self, ticket_service: TicketService) -> str:
        try:
            transaction = self._message['message']['transactions'][0]
            parameters_micheline = transaction['parameters']

            parameters_data = _FAST_WITHDRAW_MICHELSON_TYPE.from_micheline_value(parameters_micheline).to_python_object()
            parameters: ExecuteOutboxMessageFastWithdrawalDefaultParameter = (
                ExecuteOutboxMessageFastWithdrawalDefaultParameter.model_validate(parameters_data)
            )
            assert parameters

            comparable_data = FastWithdrawalParametersHashableDTO(
                withdrawal_id=int(parameters_data['withdrawal_id'])
                # receiver=str(parameters.receiver),
                # ticket_hash=ticket.hash,
                # amount=parameters.ticket.amount,
                # ticketer_address=str(parameters.ticket.ticketer),
                # proxy=transaction['destination'],
            )
        except (AttributeError, KeyError, DoesNotExist) as e:
            raise ValueError(f"Can't get FastOutboxParametersHash from message: {self._message}, {e}") from None

        return _comparable_parameters_hash(comparable_data)


class WithdrawalEventParametersHash:
    def __init__(
        self,
        event: EvmEvent[FAWithdrawalPayload] | EvmEvent[NativeWithdrawalPayload | FastWithdrawalPayload],
    ):
        self._event = event

    async def from_event(self) -> str:
        payload = self._event.payload
        if isinstance(payload, FAWithdrawalPayload):
            return await self._from_fa_event(payload)
        if isinstance(payload, NativeWithdrawalPayload):
            return await self._from_native_event(payload)
        if isinstance(payload, FastWithdrawalPayload):
            return await self._from_fast_native_event(payload)
        raise TypeError('Unexpected Withdrawal Event type')

    async def _from_native_event(self, payload: NativeWithdrawalPayload) -> str:
        try:
            ticket = await TezosTicket.get(token_id='xtz')

            comparable_data = WithdrawalParametersHashableDTO(
                receiver=str(payload.receiver),
                ticket_hash=ticket.hash,
                amount=int(str(payload.amount)[:-12]),
                ticketer_address=ticket.ticketer_address,
                proxy=ticket.ticketer_address,
            )
        except (DoesNotExist, AssertionError, AttributeError):
            raise ValueError(f"Can't get OutboxParametersHash from NativeWithdrawal Event: {payload}") from None

        return _comparable_parameters_hash(comparable_data)

    async def _from_fast_native_event(self, payload: FastWithdrawalPayload) -> str:
        try:
            # ticket = await TezosTicket.get(token_id='xtz')

            comparable_data = FastWithdrawalParametersHashableDTO(
                withdrawal_id=int(payload.withdrawal_id),
                # receiver=str(payload.receiver),
                # ticket_hash=ticket.hash,
                # amount=int(str(payload.amount)[:-12]),
                # ticketer_address=ticket.ticketer_address,
                # proxy=ticket.ticketer_address,
            )
        except (DoesNotExist, AssertionError, AttributeError):
            raise ValueError(f"Can't get OutboxParametersHash from NativeFastWithdrawal Event: {payload}") from None

        return _comparable_parameters_hash(comparable_data)

    async def _from_fa_event(self, payload: FAWithdrawalPayload) -> str:
        try:
            ticket = await TezosTicket.get(hash=payload.ticket_hash)

            comparable_data = WithdrawalParametersHashableDTO(
                receiver=str(payload.receiver),
                ticket_hash=ticket.hash,
                amount=payload.amount,
                ticketer_address=ticket.ticketer_address,
                proxy=str(payload.proxy),
            )
        except (DoesNotExist, AssertionError, AttributeError):
            raise ValueError(f"Can't get OutboxParametersHash from FAWithdrawal Event: {payload}") from None

        return _comparable_parameters_hash(comparable_data)
