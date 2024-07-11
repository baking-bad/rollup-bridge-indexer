from dipdup.context import DipDupContext
from dipdup.datasources.tezos_tzkt import TezosTzktDatasource
from dipdup.datasources.tzip_metadata import TzipMetadataDatasource
from pydantic import BaseModel

from bridge_indexer.handlers.rollup_message import InboxMessageService
from bridge_indexer.handlers.rollup_message import OutboxMessageService
from bridge_indexer.handlers.ticket import TicketService


class BridgeConstantStorage(BaseModel):
    smart_rollup_address: str
    native_ticketer: str


class ProtocolConstantStorage(BaseModel):
    smart_rollup_commitment_period: int
    smart_rollup_challenge_window: int
    smart_rollup_timeout_period: int


class ServiceContainerDTO(BaseModel):
    class Config:
        arbitrary_types_allowed = True

    protocol: ProtocolConstantStorage
    bridge: BridgeConstantStorage
    ticket_service: TicketService
    inbox_message_service: InboxMessageService
    outbox_message_service: OutboxMessageService
    tzkt: TezosTzktDatasource
    metadata: TzipMetadataDatasource


class ServiceContainer:
    def __init__(self, ctx: DipDupContext):
        try:
            ctx.container  # noqa
        except AttributeError:
            self.register(ctx)

    @staticmethod
    def register(ctx):
        tzkt = ctx.get_tezos_tzkt_datasource('tzkt')
        rollup_node = ctx.get_http_datasource('rollup_node')
        metadata = ctx.get_metadata_datasource('metadata')

        bridge = BridgeConstantStorage(
            smart_rollup_address=ctx.config.get_tezos_contract('tezos_smart_rollup').address,
            native_ticketer=ctx.config.get_tezos_contract('tezos_native_ticketer').address,
        )
        protocol = ProtocolConstantStorage(
            smart_rollup_commitment_period=20,
            smart_rollup_challenge_window=40,
            smart_rollup_timeout_period=500,
        )

        ticket_service = TicketService(tzkt, metadata, bridge)
        inbox_message_service = InboxMessageService(
            tzkt=tzkt,
            bridge=bridge,
        )
        outbox_message_service = OutboxMessageService(
            tzkt=tzkt,
            rollup_node=rollup_node,
            protocol=protocol,
        )

        container = ServiceContainerDTO(
            bridge=bridge,
            ticket_service=ticket_service,
            inbox_message_service=inbox_message_service,
            outbox_message_service=outbox_message_service,
            tzkt=tzkt,
            metadata=metadata,
            protocol=protocol,
        )
        DipDupContext.container = container
