from dipdup.context import DipDupContext
from dipdup.datasources.http import HttpDatasource
from dipdup.datasources.tezos_tzkt import TezosTzktDatasource
from dipdup.datasources.tzip_metadata import TzipMetadataDatasource
from pydantic import BaseModel
from pydantic import Field
from pydantic_settings import BaseSettings

from bridge_indexer.handlers.rollup_message import OutboxMessageService
from bridge_indexer.handlers.rollup_message import RollupMessageIndex
from bridge_indexer.handlers.ticket import TicketService


class BridgeConstantStorage(BaseSettings):
    smart_rollup_address: str = Field(alias='SMART_ROLLUP_ADDRESS')
    native_ticketer: str = Field(alias='NATIVE_TICKETER')
    fa_ticketer_list: list[str] = Field(alias='FA_TICKETERS', default_factory=list[str])


class ProtocolConstantStorage(BaseModel):
    time_between_blocks: int = Field(validation_alias='minimal_block_delay')
    smart_rollup_commitment_period: int = Field(validation_alias='smart_rollup_commitment_period_in_blocks')
    smart_rollup_challenge_window: int = Field(validation_alias='smart_rollup_challenge_window_in_blocks')
    smart_rollup_timeout_period: int = Field(validation_alias='smart_rollup_timeout_period_in_blocks')
    smart_rollup_max_active_outbox_levels: int = Field(validation_alias='smart_rollup_max_active_outbox_levels')
    smart_rollup_max_outbox_messages_per_level: int = Field(validation_alias='smart_rollup_max_outbox_messages_per_level')


class ServiceContainer:
    protocol: ProtocolConstantStorage
    bridge: BridgeConstantStorage
    ticket_service: TicketService
    rollup_message_index: RollupMessageIndex
    outbox_message_service: OutboxMessageService
    tzkt: TezosTzktDatasource
    metadata: TzipMetadataDatasource

    def __init__(self, ctx: DipDupContext):
        self._ctx = ctx

    async def register(self):
        ctx = self._ctx
        if hasattr(ctx, 'container'):
            return
        tzkt: TezosTzktDatasource = ctx.get_tezos_tzkt_datasource('tzkt')
        tezos_node: HttpDatasource = ctx.get_http_datasource('tezos_node')
        rollup_node: HttpDatasource = ctx.get_http_datasource('rollup_node')
        metadata: TzipMetadataDatasource = ctx.get_metadata_datasource('metadata')

        bridge = BridgeConstantStorage()

        response = await tezos_node.request(method='GET', url='chains/main/blocks/head/context/constants')
        protocol = ProtocolConstantStorage.model_validate(response)

        ticket_service = TicketService(tzkt, metadata, bridge)

        rollup_message_index = RollupMessageIndex(
            tzkt=tzkt,
            rollup_node=rollup_node,
            bridge=bridge,
            protocol=protocol,
            logger=ctx.logger,
        )
        outbox_message_service = OutboxMessageService(
            tzkt=tzkt,
            rollup_node=rollup_node,
            protocol=protocol,
        )

        self.bridge = bridge
        self.ticket_service = ticket_service
        self.rollup_message_index = rollup_message_index
        self.outbox_message_service = outbox_message_service
        self.tzkt = tzkt
        self.metadata = metadata
        self.protocol = protocol

        DipDupContext.container = self
