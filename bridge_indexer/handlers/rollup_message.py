from dipdup.context import DipDupContext
from dipdup.models.tezos_tzkt import TzktOperationData

from bridge_indexer.models import RollupInboxMessage
from bridge_indexer.models import RollupOutboxMessage


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
    @classmethod
    async def _fetch_outbox(cls, outbox_level: int, ctx: DipDupContext):
        datasource = ctx.get_http_datasource('rollup_node')
        for message_data in await datasource.request('GET', f'global/block/head/outbox/{outbox_level}/messages'):
            yield RollupOutboxMessage(
                level=message_data['outbox_level'],
                index=message_data['message_index'],
                message=message_data['message'],
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
        async for outbox_message in RollupOutboxMessage.filter(
            l1_withdrawals__isnull=True,
            l2_withdrawals__isnull=False,
        ):
            proof_data = await datasource.request(
                'GET',
                f'global/block/head/helpers/proofs/outbox/{outbox_message.level}/messages?index={outbox_message.index}',
            )
            outbox_message.proof = proof_data['proof']
            await outbox_message.save()

