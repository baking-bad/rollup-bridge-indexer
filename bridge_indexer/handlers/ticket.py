from eth_abi import decode
from pytezos import forge_micheline
from pytezos import unforge_micheline
from pytezos.michelson.forge import forge_address
from pytezos.michelson.micheline import micheline_value_to_python_object
from web3 import Web3

from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import TezosTicket
from bridge_indexer.models import TezosToken
from bridge_indexer.types.rollup.tezos_parameters.default import Data as TicketContent
from dipdup.context import DipDupContext


class TicketService:
    native_ticketer = 'KT1Q6aNZ9aGro4DvBKwhKvVdia2UmVGsS9zE'

    @classmethod
    async def register_fa_tickets(cls, ctx: DipDupContext):
        rollup = ctx.config.get_tezos_contract('tezos_smart_rollup').address
        for ticket_data in await ctx.get_tzkt_datasource('tzkt').request(
            'GET', f'v1/tickets/balances?account={rollup}&ticket.ticketer.ne={cls.native_ticketer}'
        ):
            await cls.fetch_ticket(
                ticket_data['ticket']['ticketer']['address'],
                TicketContent.parse_obj(ticket_data['ticket']['content']),
                ctx,
            )

    @classmethod
    async def fetch_ticket(cls, ticketer_address, ticket_content: TicketContent, ctx: DipDupContext):
        ticket_hash = cls.get_ticket_hash(ticketer_address, ticket_content)

        ticket = await TezosTicket.get_or_none(pk=ticket_hash)
        if ticket:
            return ticket

        ticket_metadata = cls.get_ticket_metadata(ticket_content)

        asset_id = '_'.join([ticket_metadata['contract_address'], str(ticket_metadata['token_id'])])
        token = await TezosToken.get_or_none(pk=asset_id)
        if not token:
            metadata_datasource = ctx.get_metadata_datasource('metadata')
            token_metadata = await metadata_datasource.get_token_metadata(
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

    @classmethod
    async def register_native_ticket(cls, ctx: DipDupContext):
        for ticket_data in await ctx.get_tzkt_datasource('tzkt').request('GET', f'v1/tickets?ticketer={cls.native_ticketer}'):
            ticket_hash = cls.get_ticket_hash(cls.native_ticketer, TicketContent.parse_obj(ticket_data['content']))
            xtz = await TezosToken.get(pk='xtz')
            ticket = await TezosTicket.create(
                hash=ticket_hash,
                ticketer_address=cls.native_ticketer,
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

    @classmethod
    def get_ticket_metadata(cls, ticket_content: TicketContent) -> dict:
        ticket_metadata_forged = bytes.fromhex(ticket_content.bytes)
        ticket_metadata_map = unforge_micheline(ticket_metadata_forged[1:])
        ticket_metadata = {}
        for pair in ticket_metadata_map:
            key = pair['args'][0]['string']
            value_forged = bytes.fromhex(pair['args'][1]['bytes'])
            value = micheline_value_to_python_object(unforge_micheline(value_forged[1:]))
            ticket_metadata.update({key: value})
        if 'token_id' not in ticket_metadata and ticket_metadata['token_type'] == 'FA1.2':
            ticket_metadata['token_id'] = 0
        return ticket_metadata

    @classmethod
    def get_ticket_hash(cls, ticketer_address, ticket_content: TicketContent) -> int:
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
