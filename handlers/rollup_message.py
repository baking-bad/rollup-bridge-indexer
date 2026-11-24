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
from dipdup.http import safe_exceptions
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
from rollup_bridge_indexer.models import RollupPendingOutboxLevel
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
    from collections.abc import Iterator
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


TRANSIENT_HTTP_ERRORS = safe_exceptions


class InboxMessageService:
    @classmethod
    async def _read_inbox_level(cls, inbox_level: int) -> AsyncGenerator[RollupInboxMessage, None]:
        async for inbox_message in RollupInboxMessage.filter(level=inbox_level, l1_deposits__isnull=True).order_by('id'):
            yield inbox_message

    @classmethod
    async def match_transaction_with_inbox(cls, data: TezosOperationData) -> RollupInboxMessage:
        async for inbox_message in cls._read_inbox_level(data.level):
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
            + (commitment_period - challenge_window % commitment_period) % commitment_period
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


class PendingOutboxLevels:

    key = 'rollup_message_pending_outbox_levels'

    def __init__(self, logger: Logger) -> None:
        self._levels: set[int] = set()
        self._stored: set[int] = set()
        self._staged: set[int] = set()
        self._drained: set[int] = set()
        self._logger = logger

    def __len__(self) -> int:
        return len(self._levels)

    def __iter__(self) -> Iterator[int]:
        return iter(sorted(self._levels))

    def add(self, level: int) -> None:
        self._levels.add(level)
        self._staged.add(level)
        self._drained.discard(level)

    def take_lowest(self, ceiling: int) -> int | None:
        if not self._levels:
            return None
        lowest = min(self._levels)
        if lowest > ceiling:
            return None
        self._levels.discard(lowest)
        self._staged.discard(lowest)
        return lowest

    async def refresh(self) -> None:
        self._stored = {row['level'] for row in await RollupPendingOutboxLevel.all().values('level')}
        if returned := self._stored & self._drained:
            self._logger.info('An L1 head rollback returned outbox level(s) %s to the queue.', sorted(returned))
            self._drained -= returned
        self._levels = self._stored | self._staged

    async def load(self, floor: int) -> None:
        await self.refresh()
        await self._adopt_meta_queue()
        if impossible := {level for level in self._levels if level < floor}:
            self._logger.warning(
                'Dropped %d owed Outbox level(s) below the rollup origination level %d: %d..%d.',
                len(impossible),
                floor,
                min(impossible),
                max(impossible),
            )
            await RollupPendingOutboxLevel.filter(level__lt=floor).delete()
            self._levels -= impossible
            self._stored -= impossible
            self._staged -= impossible
        if self._levels:
            self._logger.info(
                'Restored %d pending Outbox level(s) from the previous run: %d..%d',
                len(self._levels),
                min(self._levels),
                max(self._levels),
            )

    async def _adopt_meta_queue(self) -> None:
        meta = await Meta.get_or_none(key=self.key)
        if meta is None:
            return
        if inherited := {int(level) for level in (meta.value or [])}:
            self._logger.info(
                'Adopting %d owed Outbox level(s) from the `%s` key of the previous version: %d..%d.',
                len(inherited),
                self.key,
                min(inherited),
                max(inherited),
            )
            for level in inherited:
                self.add(level)
            await self.save()
        await Meta.filter(key=self.key).delete()
        await self.refresh()

    async def save(self) -> None:
        if candidates := self._levels - self._stored:
            present = {row['level'] for row in await RollupPendingOutboxLevel.filter(level__in=sorted(candidates)).values('level')}
            if learned := sorted(candidates - present):
                await RollupPendingOutboxLevel.bulk_create(
                    [RollupPendingOutboxLevel(level=level) for level in learned],
                    ignore_conflicts=True,
                )
        if drained := sorted(self._stored - self._levels):
            await RollupPendingOutboxLevel.filter(level__in=drained).delete()
            self._drained = set(drained)
        self._stored = set(self._levels)
        self._staged.clear()

    async def drop(self) -> None:
        await RollupPendingOutboxLevel.filter().delete()
        if await Meta.filter(key=self.key).delete():
            self._logger.info('Dropped the pending Outbox levels of a wiped database.')
        self._levels.clear()
        self._stored.clear()
        self._staged.clear()
        self._drained.clear()


class RollupMessageIndex:

    first_ticket_level: int | None = None
    request_limit = 10000
    _lock = threading.Lock()

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

        self._inbox_start_id: int | None = None
        self._origination_level: int | None = None

        _sync_first = os.environ.get('ROLLUP_SYNC_FIRST_LEVEL')
        _sync_last = os.environ.get('ROLLUP_SYNC_LAST_LEVEL')
        self._sync_first_level: int | None = int(_sync_first) if _sync_first else None
        self._sync_last_level: int | None = int(_sync_last) if _sync_last else None

        self._pending_outbox_levels = PendingOutboxLevels(logger)
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
                if await self._process():
                    BridgeMatcherLocks.set_pending_outbox()
                    BridgeMatcherLocks.set_pending_tezos_withdrawals()
                    BridgeMatcherLocks.set_pending_claimed_fast_withdrawals()

    async def _process(self) -> bool:
        committed = await self._drain_outbox_levels()

        cursor = await self._inbox_cursor()
        inbox = await self._fetch_inbox_page(cursor)
        if not inbox:
            if self._status == IndexStatus.syncing:
                self._status = IndexStatus.realtime
                return committed
        else:
            cursor = await self._walk_inbox_page(inbox, cursor)
            await self._commit_inbox_page()

        committed |= await self._drain_outbox_levels()
        await self._write_cursor_sentinel(cursor)
        return committed

    async def _inbox_cursor(self) -> int:
        last_saved = await RollupInboxMessage.all().order_by('-id').limit(1).values('id')
        if last_saved:
            return int(last_saved[0]['id'])
        if self._inbox_start_id is None:
            self._inbox_start_id = await self._inbox_window_start_id()
        return self._inbox_start_id

    async def _fetch_inbox_page(self, cursor: int):
        inbox = await self._tzkt.request(
            method='GET',
            url=f'v1/smart_rollups/inbox?id.gt={cursor}&type.in=transfer,external&micheline=0&sort=id&limit={self.request_limit}',
        )
        if len(inbox):
            self._logger.info('Found %d not indexed Inbox Messages.', len(inbox))
        return inbox

    async def _walk_inbox_page(self, inbox, cursor: int) -> int:
        for inbox_message in inbox:
            if self._sync_last_level is not None and inbox_message['level'] > self._sync_last_level:
                self._status = IndexStatus.realtime
                break
            match inbox_message['type']:
                case RollupInboxMessageType.transfer.value:
                    if 'target' not in inbox_message or 'address' not in inbox_message.get('target', {}):
                        raise ValueError(f"Transfer inbox message {inbox_message['id']} is missing target field")
                    if inbox_message['target']['address'] != self._bridge.smart_rollup_address:
                        continue
                    await self._handle_transfer_inbox_message(inbox_message)
                case RollupInboxMessageType.external.value:
                    await self._handle_external_inbox_message(inbox_message)
                case _:
                    continue
            cursor = inbox_message['id']
        return cursor

    async def _commit_inbox_page(self):
        await self._pending_outbox_levels.save()

        if not len(self._create_inbox_batch):
            return
        await RollupInboxMessage.bulk_create(self._create_inbox_batch)
        self._logger.info('Successfully saved %d new Inbox Messages.', len(self._create_inbox_batch))
        BridgeMatcherLocks.set_pending_inbox()
        BridgeMatcherLocks.set_pending_michelson_deposits()

        del self._create_inbox_batch[:]

    async def _write_cursor_sentinel(self, cursor: int):
        self._logger.info('Update Inbox Message cursor index to %s', cursor)
        if await RollupInboxMessage.exists(id=cursor):
            return
        await RollupInboxMessage.filter(
            level=0,
            type=RollupInboxMessageType.external.value,
            id__lt=cursor,
        ).delete()
        await RollupInboxMessage.create(
            id=cursor,
            level=0,
            index=0,
            message={},
            parameters_hash=None,
            type=RollupInboxMessageType.external,
        )

    async def _drain_outbox_levels(self) -> bool:
        await self._pending_outbox_levels.refresh()
        if len(self._pending_outbox_levels):
            outbox_level = None
            try:
                processed_level = await self._rollup_processed_level()
                while (outbox_level := self._pending_outbox_levels.take_lowest(processed_level)) is not None:
                    await self._handle_outbox_level(outbox_level)
            except TRANSIENT_HTTP_ERRORS as e:
                if outbox_level is not None:
                    self._pending_outbox_levels.add(outbox_level)
                self._logger.error('Outbox drain stopped at level %s, retrying on the next head: %r', outbox_level, e)

        committed = bool(self._create_outbox_batch)
        if committed:
            await RollupOutboxMessage.bulk_create(self._create_outbox_batch, ignore_conflicts=True)
            self._logger.info('Successfully saved %d new Outbox Messages.', len(self._create_outbox_batch))
            del self._create_outbox_batch[:]

        await self._pending_outbox_levels.save()
        return committed

    async def _rollup_processed_level(self) -> int:
        return int(await self._rollup_node.request(method='GET', url='global/block/head/level'))

    async def _handle_transfer_inbox_message(self, message):
        try:
            expected_l2_op_hash = expected_op_hash_from_inbox(
                message['parameter'], message['level'], message['index'], self._bridge.smart_rollup_address
            )
        except ValueError:
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
        if message['level'] < await self._get_origination_level():
            return
        self._pending_outbox_levels.add(message['level'])

    async def _handle_outbox_level(self, outbox_level):
        outbox = await self._rollup_node.request(method='GET', url=f'global/block/{outbox_level}/outbox/{outbox_level}/messages')
        if len(outbox) == 0:
            return
        self._logger.info('_handle_outbox_level %d with %d messages.', outbox_level, len(outbox))

        if len(outbox) == self._protocol.smart_rollup_max_outbox_messages_per_level:
            self._logger.info('Full outbox found at level %d, going to check next level for the rest Outbox Messages...', outbox_level)
            self._pending_outbox_levels.add(outbox_level + 1)

        created_at = datetime.fromisoformat(await self._tzkt.request('GET', f'v1/blocks/{outbox_level}/timestamp'))
        cemented_level = OutboxMessageService.estimate_outbox_message_cemented_level(
            outbox_level,
            await self._get_origination_level(),
            self._protocol,
        )
        cemented_at = datetime.fromisoformat(await self._tzkt.request('GET', f'v1/blocks/{cemented_level}/timestamp'))

        for outbox_message in outbox:
            try:
                parameters_hash = await outbox_message_parameters_hash(outbox_message, self._ticket_service)
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

    async def _prepare_new_index(self):
        origination_level = await self._get_origination_level()
        if await RollupInboxMessage.all().exists():
            self._logger.info('Last previous saved Inbox Message found. Going to continue with next Inbox Message.')
            await self._pending_outbox_levels.load(floor=origination_level)
        else:
            self._logger.info('No previous saved Inbox Message found.')
            await self._pending_outbox_levels.drop()

        self._logger.info('Inbox Message cursor index is %d.', await self._inbox_cursor())
        self._status = IndexStatus.syncing

    async def _inbox_window_start_id(self) -> int:
        origination_level = await self._get_origination_level()
        if self._sync_first_level is not None:
            self._logger.info('TEST bound: the inbox window starts at level %d.', self._sync_first_level)
            first_level = self._sync_first_level
        elif self.first_ticket_level is not None:
            self._logger.info('The inbox window starts at the first Whitelisted Token activity.')
            first_level = max(self.first_ticket_level, origination_level)
        else:
            self._logger.info('The inbox window starts at the Smart Rollup origination moment.')
            first_level = origination_level
        inbox = await self._tzkt.request(
            method='GET',
            url=f'v1/smart_rollups/inbox?type.in=transfer,external&level.ge={first_level}&sort.asc=id&limit=1',
        )
        return inbox[0]['id'] - 1


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

            comparable_data = FastWithdrawalParametersHashableDTO(withdrawal_id=int(parameters_data['withdrawal_id']))
        except (AttributeError, KeyError, DoesNotExist) as e:
            raise ValueError(f"Can't get FastOutboxParametersHash from message: {self._message}, {e}") from None

        return _comparable_parameters_hash(comparable_data)


async def outbox_message_parameters_hash(outbox_message: dict[str, Any], ticket_service: TicketService) -> str:
    try:
        return await OutboxMessageParametersHash(outbox_message).from_outbox_message(ticket_service)
    except (ValueError, MichelsonRuntimeError):
        return await OutboxMessageParametersHash(outbox_message).from_fast_outbox_message(ticket_service)


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

            comparable_data = FastWithdrawalParametersHashableDTO(
                withdrawal_id=int(payload.withdrawal_id),
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
