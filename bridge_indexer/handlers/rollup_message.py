from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from uuid import NAMESPACE_OID
from uuid import uuid5

import orjson
from dipdup.http import safe_exceptions
from dipdup.models import IndexStatus
from pydantic import BaseModel
from pytezos import MichelsonRuntimeError
from pytezos import MichelsonType
from pytezos import michelson_to_micheline
from tortoise.exceptions import DoesNotExist

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from bridge_indexer.handlers.ticket import FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE
from bridge_indexer.handlers.ticket import WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE
from bridge_indexer.handlers.ticket import TicketService
from bridge_indexer.models import BridgeOperation
from bridge_indexer.models import BridgeOperationStatus
from bridge_indexer.models import BridgeWithdrawOperation
from bridge_indexer.models import RollupCementedCommitment
from bridge_indexer.models import RollupInboxMessage
from bridge_indexer.models import RollupInboxMessageType
from bridge_indexer.models import RollupOutboxMessage
from bridge_indexer.models import TezosTicket
from bridge_indexer.types.fast_withdrawal.tezos_parameters.default import (
    DefaultParameter as ExecuteOutboxMessageFastWithdrawalDefaultParameter,
)
from bridge_indexer.types.kernel.evm_events.withdrawal import WithdrawalPayload as FAWithdrawalPayload
from bridge_indexer.types.kernel_native.evm_events.fast_withdrawal import FastWithdrawalPayload
from bridge_indexer.types.kernel_native.evm_events.withdrawal import WithdrawalPayload as NativeWithdrawalPayload
from bridge_indexer.types.rollup.tezos_parameters.default import DefaultParameter
from bridge_indexer.types.rollup.tezos_parameters.default import TicketContent as RollupParametersTicketContent
from bridge_indexer.types.ticketer.tezos_parameters.withdraw import WithdrawParameter as ExecuteOutboxMessageTicketerWithdrawParameter

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from logging import Logger

    from dipdup.datasources.http import HttpDatasource
    from dipdup.datasources.tezos_tzkt import TezosTzktDatasource
    from dipdup.models.evm import EvmEvent
    from dipdup.models.tezos import TezosOperationData
    from dipdup.models.tezos import TezosTransaction

    from bridge_indexer.handlers.service_container import BridgeConstantStorage
    from bridge_indexer.handlers.service_container import ProtocolConstantStorage
    from bridge_indexer.types.rollup.tezos_storage import RollupStorage


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
    def estimate_outbox_message_cemented_level(cls, outbox_level: int, lcc_inbox_level: int, protocol: ProtocolConstantStorage) -> int:
        commitment_period = protocol.smart_rollup_commitment_period
        challenge_window = protocol.smart_rollup_challenge_window

        return (
            outbox_level
            + (lcc_inbox_level - outbox_level) % commitment_period
            + challenge_window
            + (commitment_period - challenge_window % commitment_period)
            % commitment_period  # well, at this line, I'm just fucking around already.
            + 5
        )

    @classmethod
    async def find_by_index(cls, outbox_level: int, index: int):
        return await RollupOutboxMessage.get(level=outbox_level, index=index)

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
            except safe_exceptions:
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

        self._inbox_id_cursor: int = 0
        self._inbox_level_cursor: int = 0
        self._outbox_level_cursor: int = 0
        self._outbox_index_cursor: int = 0
        self._realtime_head_level: int = 0

        self._outbox_level_queue: set = set()
        self._create_inbox_batch: list[RollupInboxMessage] = []
        self._create_outbox_batch: list[RollupOutboxMessage] = []

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
                self._realtime_head_level = max(self._realtime_head_level, head_level)
                previous_outbox_level_cursor = self._outbox_level_cursor
                await self._process()
                if self._outbox_level_cursor > previous_outbox_level_cursor:
                    BridgeMatcherLocks.set_pending_outbox()
                    BridgeMatcherLocks.set_pending_tezos_withdrawals()
                    BridgeMatcherLocks.set_pending_claimed_fast_withdrawals()

    async def _process(self):
        inbox = await self._tzkt.request(
            method='GET',
            url=f'v1/smart_rollups/inbox?id.gt={self._inbox_id_cursor}&type.in=transfer,external&target={self._bridge.smart_rollup_address}&micheline=0&sort=id&limit={self.request_limit}',
        )

        if len(inbox) == 0:
            if self._status == IndexStatus.syncing:
                self._status = IndexStatus.realtime
                return
        else:
            self._logger.info('Found %d not indexed Inbox Messages.', len(inbox))

            for inbox_message in inbox:
                match inbox_message['type']:
                    case RollupInboxMessageType.transfer.value:
                        await self._handle_transfer_inbox_message(inbox_message)
                    case RollupInboxMessageType.external.value:
                        await self._handle_external_inbox_message(inbox_message)
                    case _:
                        continue
                self._inbox_id_cursor = inbox_message['id']

            if len(self._create_inbox_batch):
                await RollupInboxMessage.bulk_create(self._create_inbox_batch)
                self._logger.info('Successfully saved %d new Inbox Messages.', len(self._create_inbox_batch))
                self._inbox_level_cursor = self._create_inbox_batch[-1].level
                BridgeMatcherLocks.set_pending_inbox()

                del self._create_inbox_batch[:]

        while len(self._outbox_level_queue) > 0 and (
            self._status == IndexStatus.syncing or min(self._outbox_level_queue) <= self._realtime_head_level
        ):
            outbox_level = self._outbox_level_queue.pop()
            await self._handle_outbox_level(outbox_level)

        if len(self._create_outbox_batch):
            await RollupOutboxMessage.bulk_create(self._create_outbox_batch, ignore_conflicts=True)
            self._logger.info('Successfully saved %d new Outbox Messages.', len(self._create_outbox_batch))
            self._outbox_index_cursor = self._create_outbox_batch[-1].index
            self._outbox_level_cursor = self._create_outbox_batch[-1].level
            BridgeMatcherLocks.set_pending_outbox()

            del self._create_outbox_batch[:]

        self._logger.info('Update Inbox Message cursor index to %s', self._inbox_id_cursor)
        if not await RollupInboxMessage.exists(id=self._inbox_id_cursor):
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

    async def _handle_transfer_inbox_message(self, message):
        self._create_inbox_batch.append(
            RollupInboxMessage(
                id=message['id'],
                level=message['level'],
                index=message['index'],
                type=RollupInboxMessageType.transfer,
                message=message['parameter'],
                parameters_hash=await InboxParametersHash(message['parameter']).from_inbox_message_parameters(),
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

        recent_cement_operations = await self._tzkt.request(
            method='GET',
            url=f'v1/operations/sr_cement?rollup={self._bridge.smart_rollup_address}&level.lt={outbox_level}&sort.desc=level&limit=1&status=applied',
        )
        try:
            lcc_inbox_level = recent_cement_operations[0]['commitment']['inboxLevel']
        except KeyError:
            if 'errors' in recent_cement_operations[0]:
                for error_data in recent_cement_operations[0]['errors']:
                    self._logger.error(error_data['type'])
            return

        created_at = datetime.fromisoformat(await self._tzkt.request('GET', f'v1/blocks/{outbox_level}/timestamp'))
        cemented_level = OutboxMessageService.estimate_outbox_message_cemented_level(
            outbox_level,
            lcc_inbox_level,
            self._protocol,
        )
        cemented_at = datetime.fromisoformat(await self._tzkt.request('GET', f'v1/blocks/{cemented_level}/timestamp'))

        for outbox_message in outbox:
            try:
                parameters_hash = await OutboxParametersHash(outbox_message).from_outbox_message(self._ticket_service)
            except (ValueError, MichelsonRuntimeError):
                parameters_hash = await OutboxParametersHash(outbox_message).from_fast_outbox_message(self._ticket_service)
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
            self._outbox_level_queue.add(outbox_level + 1)

    async def _prepare_new_index(self):
        try:
            last_saved_inbox_message = await RollupInboxMessage.all().order_by('-id').first()
            self._inbox_id_cursor = 1 + last_saved_inbox_message.id
            self._logger.info('Last previous saved Inbox Message found. Going to continue with next Inbox Message.')
        except AttributeError:
            if self.first_ticket_level is not None:
                self._logger.info('No previous saved Inbox Message found. Going to start indexing since first Whitelisted Token activity.')
                first_level = self.first_ticket_level
            else:
                self._logger.info('No previous saved Inbox Message found. Going to start indexing since Smart Rollup origination moment.')
                rollup_data = await self._tzkt.request(method='GET', url=f'v1/smart_rollups/{self._bridge.smart_rollup_address}')
                first_level = rollup_data['firstActivity']
            inbox = await self._tzkt.request(
                method='GET',
                url=f'v1/smart_rollups/inbox?type.in=transfer,external&target={self._bridge.smart_rollup_address}&level.ge={first_level}&sort.asc=id&limit=1',
            )
            self._inbox_id_cursor = inbox[0]['id']

        self._logger.info('Inbox Message cursor index is %d.', self._inbox_id_cursor)
        self._status = IndexStatus.syncing


class InboxParametersHash:
    def __init__(self, value: TezosTransaction[DefaultParameter, RollupStorage] | RollupInboxMessage):
        self._value = value

    async def from_inbox_message_parameters(self) -> str:
        inbox_message_parameters = self._value
        return self._hash_from_dto(inbox_message_parameters)

    async def from_transaction(self) -> str:
        default = self._value
        return self._hash_from_dto(default.data.parameter_json)

    @staticmethod
    def _hash_from_dto(dto) -> str:
        parameters_hash: str = uuid5(NAMESPACE_OID, orjson.dumps(dto, option=orjson.OPT_SORT_KEYS)).hex

        return parameters_hash


class WithdrawalParametersHashableDTO(BaseModel):
    receiver: str
    ticket_hash: str
    amount: int
    ticketer_address: str
    proxy: str

class FastWithdrawalParametersHashableDTO(BaseModel):
    withdrawal_id: int



class OutboxParametersHash:
    def __init__(
        self,
        value: dict[str, Any] | EvmEvent[FAWithdrawalPayload | NativeWithdrawalPayload | FastWithdrawalPayload],
    ):
        self._value = value

    async def from_outbox_message(self, ticket_service: TicketService) -> str:
        outbox_message = self._value

        try:
            transaction = outbox_message['message']['transactions'][0]
            parameters_micheline = transaction['parameters']

            micheline_expression = michelson_to_micheline(WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE)
            michelson_type = MichelsonType.match(micheline_expression)

            parameters_data = michelson_type.from_micheline_value(parameters_micheline).to_python_object()
            parameters: ExecuteOutboxMessageTicketerWithdrawParameter = ExecuteOutboxMessageTicketerWithdrawParameter.model_validate(parameters_data)

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
            raise ValueError(f"Can't get OutboxParametersHash from message: {outbox_message}") from None

        return self._hash_from_dto(comparable_data)


    async def from_fast_outbox_message(self, ticket_service: TicketService) -> str:
        outbox_message = self._value
        try:
            transaction = outbox_message['message']['transactions'][0]
            parameters_micheline = transaction['parameters']

            micheline_expression = michelson_to_micheline(FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE)
            michelson_type = MichelsonType.match(micheline_expression)

            parameters_data = michelson_type.from_micheline_value(parameters_micheline).to_python_object()
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
            raise ValueError(f"Can't get FastOutboxParametersHash from message: {outbox_message}, {e}") from None

        return self._hash_from_dto(comparable_data)

    async def from_event(self) -> str:
        if isinstance(self._value.payload, FAWithdrawalPayload):
            return await self._from_fa_event()
        if isinstance(self._value.payload, NativeWithdrawalPayload):
            return await self._from_native_event()
        if isinstance(self._value.payload, FastWithdrawalPayload):
            return await self._from_fast_native_event()
        raise TypeError('Unexpected Withdrawal Event type')

    async def _from_native_event(self) -> str:
        payload: NativeWithdrawalPayload = self._value.payload

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

        return self._hash_from_dto(comparable_data)

    async def _from_fast_native_event(self) -> str:
        payload: FastWithdrawalPayload = self._value.payload

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

        return self._hash_from_dto(comparable_data)

    async def _from_fa_event(self) -> str:
        payload: FAWithdrawalPayload = self._value.payload

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

        return self._hash_from_dto(comparable_data)

    @staticmethod
    def _hash_from_dto(dto: WithdrawalParametersHashableDTO | FastWithdrawalParametersHashableDTO) -> str:
        parameters_hash: str = uuid5(NAMESPACE_OID, orjson.dumps(dto.model_dump(), option=orjson.OPT_SORT_KEYS)).hex

        return parameters_hash
