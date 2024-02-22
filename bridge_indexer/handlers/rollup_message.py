from bridge_indexer.models import BridgeOperation
from bridge_indexer.models import BridgeWithdrawOperation
from bridge_indexer.models import RollupCementedCommitment
from bridge_indexer.models import RollupInboxMessage
from bridge_indexer.models import RollupOutboxMessage
from dipdup.context import DipDupContext
from dipdup.models.tezos_tzkt import TzktOperationData


class InboxMessageService:
    @classmethod
    def _validate_message(cls, message_data: dict, ctx: DipDupContext) -> None:
        match message_data['type']:
            case 'transfer':
                rollup = ctx.config.get_tezos_contract('tezos_smart_rollup')
                if message_data['target']['address'] != rollup.address:
                    raise TypeError

            case 'external':
                pass
            case _:
                raise TypeError

    @classmethod
    async def _fetch_inbox(cls, inbox_level: int, ctx: DipDupContext):
        index = -1
        datasource = ctx.get_tzkt_datasource('tzkt')
        for message_data in await datasource.request('GET', f'v1/smart_rollups/inbox?level={inbox_level}'):
            index += 1
            try:
                cls._validate_message(message_data, ctx)
                yield RollupInboxMessage(
                    id=message_data['id'],
                    level=message_data['level'],
                    index=index,
                    type=message_data['type'],
                    parameter=message_data.get('parameter'),
                    payload=message_data.get('payload'),
                )
            except TypeError:
                continue

    @classmethod
    async def _prepare_inbox(cls, ctx, inbox_level):
        if await RollupInboxMessage.filter(level=inbox_level).count() == 0:
            inbox: list[RollupInboxMessage] = []
            async for inbox_message in cls._fetch_inbox(inbox_level, ctx):
                inbox.append(inbox_message)
            await RollupInboxMessage.bulk_create(inbox)

    @classmethod
    async def _read_inbox(cls, inbox_level: int, ctx: DipDupContext):
        await cls._prepare_inbox(ctx, inbox_level)
        async for inbox_message in RollupInboxMessage.filter(level=inbox_level, l1_deposits__isnull=True).order_by('id'):
            yield inbox_message

    @classmethod
    async def match_transaction_with_inbox(cls, data: TzktOperationData, ctx: DipDupContext) -> RollupInboxMessage:
        async for inbox_message in cls._read_inbox(data.level, ctx):
            if data.parameter_json == inbox_message.parameter:
                return inbox_message

        raise TypeError('Transaction not matched')

    @classmethod
    async def find_by_index(cls, inbox_level: int, index: int, ctx: DipDupContext):
        await cls._prepare_inbox(ctx, inbox_level)

        return await RollupInboxMessage.get(level=inbox_level, index=index)


class OutboxMessageService:
    @staticmethod
    def _estimate_outbox_message_cemented_level(outbox_level: int, lcc_level: int, commitment_period: int, challenge_window: int):
        return outbox_level + (lcc_level - outbox_level) % commitment_period + challenge_window + (
            commitment_period - challenge_window % commitment_period) % commitment_period

    @classmethod
    async def _fetch_outbox(cls, outbox_level: int, ctx: DipDupContext):
        datasource = ctx.get_http_datasource('rollup_node')
        tzkt = ctx.get_tzkt_datasource('tzkt')

        for message_data in await datasource.request('GET', f'global/block/{outbox_level}/outbox/{outbox_level}/messages'):
            outbox_level = message_data['outbox_level']
            created_at = await tzkt.request('GET', f'v1/blocks/{outbox_level}/timestamp')

            lcc = await RollupCementedCommitment.filter(inbox_level__lt=outbox_level).order_by('-inbox_level').first()
            cemented_level = cls._estimate_outbox_message_cemented_level(outbox_level, lcc.inbox_level, 20, 40)
            cemented_at = await tzkt.request('GET', f'v1/blocks/{cemented_level}/timestamp')

            yield RollupOutboxMessage(
                level=message_data['outbox_level'],
                index=message_data['message_index'],
                message=message_data['message'],
                created_at=created_at,
                cemented_at=cemented_at,
            )

    @classmethod
    async def _prepare_outbox(cls, outbox_level, ctx):
        if await RollupOutboxMessage.filter(level=outbox_level).count() == 0:
            outbox: list[RollupOutboxMessage] = []
            async for outbox_message in cls._fetch_outbox(outbox_level, ctx):
                outbox.append(outbox_message)
            await RollupOutboxMessage.bulk_create(outbox)

    @classmethod
    async def find_by_index(cls, outbox_level: int, index: int, ctx: DipDupContext):
        await cls._prepare_outbox(outbox_level, ctx)

        return await RollupOutboxMessage.get(level=outbox_level, index=index)

    @classmethod
    async def update_proof(cls, ctx: DipDupContext):
        datasource = ctx.get_http_datasource('rollup_node')

        sync_level = await ctx.get_tzkt_datasource('tzkt').get_head_block()
        async for outbox_message in RollupOutboxMessage.filter(
            l1_withdrawals__isnull=True,
            l2_withdrawals__isnull=False,
        ):
            if sync_level - outbox_message.level > 80640:  # todo: avoid magic numbers
                # todo: mark expired transaction with terminal status "failed"
                continue

            if await RollupCementedCommitment.filter(inbox_level__gte=outbox_message.level).count() == 0:
                continue

            proof_data = await datasource.request(
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
