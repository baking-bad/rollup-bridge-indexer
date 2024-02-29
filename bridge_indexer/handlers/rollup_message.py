from typing import TYPE_CHECKING

from bridge_indexer.models import BridgeOperation
from bridge_indexer.models import BridgeWithdrawOperation
from bridge_indexer.models import RollupCementedCommitment
from bridge_indexer.models import RollupInboxMessage
from bridge_indexer.models import RollupOutboxMessage
from dipdup.datasources.http import HttpDatasource
from dipdup.datasources.tezos_tzkt import TzktDatasource
from dipdup.models.tezos_tzkt import TzktOperationData

if TYPE_CHECKING:
    from bridge_indexer.handlers.service_container import BridgeConstantStorage
    from bridge_indexer.handlers.service_container import ProtocolConstantStorage


class InboxMessageService:
    def __init__(self, tzkt: TzktDatasource, bridge: 'BridgeConstantStorage'):
        self._tzkt = tzkt
        self._bridge = bridge

    def _validate_message(self, message_data: dict) -> None:
        match message_data['type']:
            case 'transfer':
                if message_data['target']['address'] != self._bridge.smart_rollup_address:
                    raise TypeError('Message target must be Bridge Rollup address, not {}.', message_data['target']['address'])

            case 'external':
                raise NotImplementedError
            case _:
                raise TypeError('Unsupported Inbox Message Type: {}.', message_data['type'])

    async def _fetch_inbox(self, inbox_level: int):
        index = -1
        for message_data in await self._tzkt.request('GET', f'v1/smart_rollups/inbox?level={inbox_level}'):
            index += 1
            try:
                self._validate_message(message_data)
                yield RollupInboxMessage(
                    id=message_data['id'],
                    level=message_data['level'],
                    index=index,
                    type=message_data['type'],
                    parameter=message_data.get('parameter'),
                    payload=message_data.get('payload'),
                )
            except (TypeError, NotImplementedError):
                continue

    async def _prepare_inbox(self, inbox_level):
        if await RollupInboxMessage.filter(level=inbox_level).count() == 0:
            inbox: list[RollupInboxMessage] = []
            async for inbox_message in self._fetch_inbox(inbox_level):
                inbox.append(inbox_message)
            await RollupInboxMessage.bulk_create(inbox)

    async def _read_inbox(self, inbox_level: int):
        await self._prepare_inbox(inbox_level)
        async for inbox_message in RollupInboxMessage.filter(level=inbox_level, l1_deposits__isnull=True).order_by('id'):
            yield inbox_message

    async def match_transaction_with_inbox(self, data: TzktOperationData) -> RollupInboxMessage:
        async for inbox_message in self._read_inbox(data.level):
            if data.parameter_json == inbox_message.parameter:
                return inbox_message

        raise TypeError('Transaction not matched')

    async def find_by_index(self, inbox_level: int, index: int):
        await self._prepare_inbox(inbox_level)

        return await RollupInboxMessage.get(level=inbox_level, index=index)


class OutboxMessageService:
    def __init__(self, tzkt: TzktDatasource, rollup_node: HttpDatasource, protocol: 'ProtocolConstantStorage'):
        self._tzkt = tzkt
        self._rollup_node = rollup_node
        self._protocol = protocol

    @staticmethod
    def _estimate_outbox_message_cemented_level(outbox_level: int, lcc_level: int, commitment_period: int, challenge_window: int):
        return outbox_level + (lcc_level - outbox_level) % commitment_period + challenge_window + (
            commitment_period - challenge_window % commitment_period) % commitment_period

    async def _fetch_outbox(self, outbox_level: int):
        for message_data in await self._rollup_node.request('GET', f'global/block/{outbox_level}/outbox/{outbox_level}/messages'):
            outbox_level = message_data['outbox_level']
            created_at = await self._tzkt.request('GET', f'v1/blocks/{outbox_level}/timestamp')

            lcc = await RollupCementedCommitment.filter(inbox_level__lt=outbox_level).order_by('-inbox_level').first()
            cemented_level = self._estimate_outbox_message_cemented_level(
                outbox_level,
                lcc.inbox_level,
                self._protocol.smart_rollup_commitment_period,
                self._protocol.smart_rollup_challenge_window,
            )
            cemented_at = await self._tzkt.request('GET', f'v1/blocks/{cemented_level}/timestamp')

            yield RollupOutboxMessage(
                level=message_data['outbox_level'],
                index=message_data['message_index'],
                message=message_data['message'],
                created_at=created_at,
                cemented_at=cemented_at,
            )

    async def _prepare_outbox(self, outbox_level):
        if await RollupOutboxMessage.filter(level=outbox_level).count() == 0:
            outbox: list[RollupOutboxMessage] = []
            async for outbox_message in self._fetch_outbox(outbox_level):
                outbox.append(outbox_message)
            await RollupOutboxMessage.bulk_create(outbox)

    async def find_by_index(self, outbox_level: int, index: int):
        await self._prepare_outbox(outbox_level)

        return await RollupOutboxMessage.get(level=outbox_level, index=index)

    async def update_proof(self):
        head_data = await self._tzkt.get_head_block()
        async for outbox_message in RollupOutboxMessage.filter(
            l1_withdrawals__isnull=True,
            l2_withdrawals__isnull=False,
        ):
            if head_data.level - outbox_message.level > self._protocol.smart_rollup_timeout_period:
                # todo: mark expired transaction with terminal status "failed"
                continue

            if await RollupCementedCommitment.filter(inbox_level__gte=outbox_message.level).count() == 0:
                continue

            proof_data = await self._rollup_node.request(
                'GET',
                f'global/block/head/helpers/proofs/outbox/{outbox_message.level}/messages?index={outbox_message.index}',
            )

            outbox_message.proof = proof_data['proof']
            commitment = await RollupCementedCommitment.get(hash=proof_data['commitment'])
            outbox_message.commitment = commitment
            outbox_message.updated_at = commitment.created_at
            await outbox_message.save()

            bridge_withdraw_operation = await BridgeWithdrawOperation.get(l2_transaction__outbox_message=outbox_message)
            bridge_withdraw_operation.updated_at = commitment.created_at
            await bridge_withdraw_operation.save()

            bridge_operation = await BridgeOperation.get(id=bridge_withdraw_operation.id)
            bridge_operation.updated_at = commitment.created_at
            await bridge_operation.save()
