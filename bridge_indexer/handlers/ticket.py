from typing import TYPE_CHECKING

from eth_abi import decode
from pytezos import forge_micheline
from pytezos import unforge_micheline
from pytezos.michelson.forge import forge_address
from web3 import Web3

if TYPE_CHECKING:
    from dipdup.datasources.tezos_tzkt import TezosTzktDatasource
    from dipdup.datasources.tzip_metadata import TzipMetadataDatasource
    from bridge_indexer.handlers.service_container import BridgeConstantStorage
from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import TezosTicket
from bridge_indexer.models import TezosToken
from bridge_indexer.types.rollup.tezos_parameters.default import Content as TicketContent


class TicketService:
    def __init__(self, tzkt: 'TezosTzktDatasource', metadata: 'TzipMetadataDatasource', bridge: 'BridgeConstantStorage'):
        self._tzkt: TezosTzktDatasource = tzkt
        self._metadata_client: TzipMetadataDatasource = metadata
        self._bridge: BridgeConstantStorage = bridge

    async def register_fa_tickets(self):
        for ticketer_address in self._bridge.fa_ticketer_list:
            for ticket_data in await self._tzkt.request(
                'GET', f'v1/tickets?ticketer.eq={ticketer_address}'
            ):
                await self.fetch_ticket(
                    ticket_data['ticketer']['address'],
                    TicketContent.parse_obj(ticket_data['content']),
                )

    async def fetch_ticket(self, ticketer_address, ticket_content: TicketContent):
        ticket_hash = self.get_ticket_hash(ticketer_address, ticket_content)

        ticket = await TezosTicket.get_or_none(pk=ticket_hash)
        if ticket:
            return ticket

        ticket_metadata = self.get_ticket_metadata(ticket_content)

        asset_id = '_'.join([ticket_metadata['contract_address'], str(ticket_metadata['token_id'])])
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
            ticket_id=ticket_content.nat,
            token=token,
        )

        return ticket

    async def register_native_ticket(self):
        for ticket_data in await self._tzkt.request('GET', f'v1/tickets?ticketer={self._bridge.native_ticketer}'):
            ticket_hash = self.get_ticket_hash(self._bridge.native_ticketer, TicketContent.parse_obj(ticket_data['content']))
            xtz = await TezosToken.get(pk='xtz')
            ticket = await TezosTicket.create(
                hash=ticket_hash,
                ticketer_address=self._bridge.native_ticketer,
                ticket_id=ticket_data['content']['nat'],
                token=xtz,
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

    def get_ticket_metadata(self, ticket_content: TicketContent) -> dict:
        ticket_metadata_forged = bytes.fromhex(ticket_content.bytes)
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

    def get_ticket_hash(self, ticketer_address, ticket_content: TicketContent) -> int:
        if ticket_content.bytes:
            bytes_micheline = {
                'prim': 'Some',
                'args': [
                    {
                        'bytes': ticket_content.bytes,
                    }
                ],
            }
        else:
            bytes_micheline = {'prim': 'None'}
        ticket_content_micheline = {
            'prim': 'Pair',
            'args': [
                {'int': ticket_content.nat},
                bytes_micheline,
            ],
        }

        data = Web3.solidity_keccak(
            ['bytes22', 'bytes'],
            [
                forge_address(ticketer_address),
                forge_micheline(ticket_content_micheline),
            ],
        )
        ticket_hash = decode(['uint256'], data)[0]
        return ticket_hash
