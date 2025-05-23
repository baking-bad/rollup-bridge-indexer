from typing import TYPE_CHECKING

from eth_abi import decode
from eth_utils import remove_0x_prefix
from pytezos import forge_micheline
from pytezos import unforge_micheline
from pytezos.michelson.forge import forge_address
from web3 import Web3
from web3._utils.encoding import hex_encode_abi_type

if TYPE_CHECKING:
    from dipdup.datasources.tezos_tzkt import TezosTzktDatasource
    from dipdup.datasources.tzip_metadata import TzipMetadataDatasource

    from bridge_indexer.handlers.service_container import BridgeConstantStorage

from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import TezosTicket
from bridge_indexer.models import TezosToken
from bridge_indexer.types.rollup.tezos_parameters.default import TicketContent

WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE = 'pair (address %receiver) (pair %ticket (address %ticketer) (pair (pair %content (nat %ticket_id) (option %metadata bytes)) (nat %amount)))'
FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE = 'pair (nat %withdrawal_id) (pair (pair %ticket (address %address) (pair (pair %content (nat %nat) (option %bytes bytes)) (nat %amount))) (pair (timestamp %timestamp) (pair (address %base_withdrawer) (pair (bytes %payload) (bytes %l2_caller)))))'

class TicketService:
    def __init__(self, tzkt: 'TezosTzktDatasource', metadata: 'TzipMetadataDatasource', bridge: 'BridgeConstantStorage'):
        self._tzkt: TezosTzktDatasource = tzkt
        self._metadata_client: TzipMetadataDatasource = metadata
        self._bridge: BridgeConstantStorage = bridge

    async def register_fa_tickets(self):
        first_levels = []
        for ticketer_address in self._bridge.fa_ticketer_list:
            for ticket_data in await self._tzkt.request('GET', f'v1/tickets?ticketer.eq={ticketer_address}'):
                await self.fetch_ticket(
                    ticket_data['ticketer']['address'],
                    TicketContent.model_validate(ticket_data['content']),
                )
                first_levels.append(ticket_data['firstLevel'])

        if first_levels:
            from bridge_indexer.handlers.rollup_message import RollupMessageIndex

            RollupMessageIndex.first_ticket_level = min(first_levels)

    async def fetch_ticket(self, ticketer_address, ticket_content: TicketContent):
        ticket_hash = self.get_ticket_hash(ticketer_address, ticket_content)

        ticket = await TezosTicket.get_or_none(pk=ticket_hash)
        if ticket:
            return ticket

        if ticket_content.metadata_hex is None:
            raise ValueError('Tickets with empty TicketMetadata should have been registered')

        try:
            ticket_metadata: dict[str, str] = self.get_ticket_metadata(ticket_content)
            asset_id = '_'.join([ticket_metadata['contract_address'], str(ticket_metadata['token_id'])])
        except (AssertionError, KeyError):
            token=None
        else:
            token = await TezosToken.get_or_none(pk=asset_id)
            if not token:
                token_metadata = await self._metadata_client.get_token_metadata(
                    ticket_metadata['contract_address'],
                    int(ticket_metadata['token_id']),
                )
                if token_metadata is None:
                    token_metadata = {}
                token = await TezosToken.create(
                    id=asset_id,
                    contract_address=ticket_metadata['contract_address'],
                    token_id=ticket_metadata['token_id'],
                    name=token_metadata.get('name', None),
                    symbol=token_metadata.get('symbol', None),
                    decimals=token_metadata.get('decimals', 0),
                    type=ticket_metadata['token_type'],
                )

        ticket = await TezosTicket.create(
            hash=ticket_hash,
            ticketer_address=ticketer_address,
            ticket_id=ticket_content.ticket_id,
            token=token,
            metadata=ticket_content.metadata_hex,
            whitelisted=ticketer_address in self._bridge.fa_ticketer_list,
        )

        return ticket

    async def register_native_ticket(self):
        for ticket_data in await self._tzkt.request('GET', f'v1/tickets?ticketer={self._bridge.native_ticketer}'):
            ticket_content = TicketContent.model_validate(ticket_data['content'])
            ticket_hash = self.get_ticket_hash(self._bridge.native_ticketer, ticket_content)
            xtz = await TezosToken.get(pk='xtz')
            ticket = await TezosTicket.create(
                hash=ticket_hash,
                ticketer_address=self._bridge.native_ticketer,
                ticket_id=ticket_content.ticket_id,
                token=xtz,
                metadata=ticket_content.metadata_hex,
                whitelisted=True,
            )
            await EtherlinkToken.create(
                id=xtz.id,
                name=xtz.name,
                symbol=xtz.symbol,
                decimals=xtz.decimals + 12,
                ticket=ticket,
            )
            return ticket
        raise ValueError('No Native Ticketer found')

    @staticmethod
    def get_ticket_metadata(ticket_content: TicketContent) -> dict:
        if ticket_content.metadata_hex is None:
            return {}
        ticket_metadata_forged = bytes.fromhex(ticket_content.metadata_hex)
        ticket_metadata_map = unforge_micheline(ticket_metadata_forged[1:])
        ticket_metadata = {}
        for pair in ticket_metadata_map:
            key = pair['args'][0]['string']
            value_bytes = bytes.fromhex(pair['args'][1]['bytes'])
            value = value_bytes.decode()
            ticket_metadata.update({key: value})
        if 'token_id' not in ticket_metadata and ticket_metadata['token_type'] == 'FA1.2':
            ticket_metadata['token_id'] = 0
        return ticket_metadata

    @staticmethod
    def get_ticket_content_bytes(
        ticketer_address: str,
        ticket_content: TicketContent,
    ) -> bytes:
        if ticket_content.metadata_hex:
            ticket_metadata_micheline = {
                'prim': 'Some',
                'args': [
                    {
                        'bytes': ticket_content.metadata_hex,
                    }
                ],
            }
        else:
            ticket_metadata_micheline = {'prim': 'None'}
        ticket_content_micheline: dict = {
            'prim': 'Pair',
            'args': [
                {'int': ticket_content.ticket_id},
                ticket_metadata_micheline,
            ],
        }

        abi_types = ['bytes22', 'bytes']
        normalized_values = Web3.normalize_values(
            w3=Web3(),
            abi_types=abi_types,
            values=[
                forge_address(ticketer_address),
                forge_micheline(ticket_content_micheline),
            ],
        )

        ticket_content_hex = ''.join(
            remove_0x_prefix(hex_encode_abi_type(abi_type, value)) for abi_type, value in zip(abi_types, normalized_values, strict=True)
        )

        return bytes.fromhex(ticket_content_hex)

    def get_ticket_hash(
        self,
        ticketer_address: str,
        ticket_content: TicketContent,
    ) -> int:

        ticket_content_bytes = self.get_ticket_content_bytes(ticketer_address, ticket_content)

        digest = Web3.keccak(ticket_content_bytes)
        ticket_hash = decode(['uint256'], digest)[0]

        return ticket_hash
