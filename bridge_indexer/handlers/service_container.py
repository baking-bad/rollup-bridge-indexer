from pydantic import BaseModel

from bridge_indexer.handlers.ticket import TicketService
from dipdup.context import DipDupContext
from dipdup.datasources.tezos_tzkt import TzktDatasource
from dipdup.datasources.tzip_metadata import TzipMetadataDatasource


class BridgeConstantStorage(BaseModel):
    smart_rollup_address: str
    native_ticketer: str


class ProtocolConstantStorage(BaseModel):
    smart_rollup_commitment_period: int  # 20
    smart_rollup_challenge_window: int  # 40
    smart_rollup_timeout_period: int  # 500


class ServiceContainerDTO(BaseModel):
    class Config:
        arbitrary_types_allowed = True

    # protocol: ProtocolConstantStorage
    bridge: BridgeConstantStorage
    ticket_service: TicketService
    # inbox_message_service: InboxMessageService
    # outbox_message_service: OutboxMessageService
    tzkt: TzktDatasource
    metadata: TzipMetadataDatasource


class ServiceContainer:
    def __init__(self, ctx: DipDupContext):
        try:
            ctx.container  # noqa
        except AttributeError:
            self.register(ctx)

    @staticmethod
    def register(ctx):
        tzkt = ctx.get_tzkt_datasource('tzkt')
        metadata = ctx.get_metadata_datasource('metadata')

        bridge = BridgeConstantStorage(
            smart_rollup_address=ctx.config.get_tezos_contract('tezos_smart_rollup').address,
            native_ticketer='KT1Q6aNZ9aGro4DvBKwhKvVdia2UmVGsS9zE',
        )

        ticket_service = TicketService(tzkt, metadata, bridge)

        container = ServiceContainerDTO(
            bridge=bridge,
            ticket_service=ticket_service,
            tzkt=tzkt,
            metadata=metadata,
        )
        DipDupContext.container = container
