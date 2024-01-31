from dipdup.context import HandlerContext
from dipdup.models.tezos_tzkt import TzktTransaction

from bridge_indexer.models import EtherlinkToken
from bridge_indexer.models import TezosDepositEvent
from bridge_indexer.models import TezosTicket
from bridge_indexer.models import TezosToken
from bridge_indexer.types.rollup.tezos_parameters.default import DefaultParameter
from bridge_indexer.types.rollup.tezos_parameters.default import LL
from bridge_indexer.types.rollup.tezos_storage import RollupStorage
from pytezos.michelson.forge import forge_address
from pytezos.michelson.forge import forge_micheline
from pytezos.michelson.forge import unforge_micheline
from pytezos.michelson.micheline import micheline_value_to_python_object
from web3.main import Web3
from eth_abi import decode


async def validate_ticket(parameter: LL, ctx: HandlerContext):
    routing_info = bytes.fromhex(parameter.bytes)
    l2_receiver = routing_info[:20]
    l2_proxy = routing_info[20:40] or None
    ticket_identifier = parameter.ticket
    ticket_content = ticket_identifier.data

    if ticket_content.bytes is None:
        asset_id = 'xtz'
        ticket_content_micheline = {"prim": "Pair", "args": [
            {"int": ticket_content.nat}, {"prim": "None"}
        ]}
    else:
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

        asset_id = '_'.join([ticket_metadata['contract_address'], str(ticket_metadata['token_id'])])

        ticket_content_micheline = {"prim": "Pair", "args": [
            {
                "int": ticket_content.nat
            }, {"prim": "Some", "args": [{
                "bytes": ticket_content.bytes,
            }]}
        ]}
    ticket_id = f'{ticket_identifier.address}_{ticket_content.nat}'
    ticket = await TezosTicket.get_or_none(pk=ticket_id)
    if ticket:
        return ticket
    token = await TezosToken.get_or_none(pk=asset_id)
    if not token:
        metadata_datasource = ctx.get_metadata_datasource('metadata')
        token_metadata = await metadata_datasource.get_token_metadata(
            ticket_metadata['contract_address'],
            str(ticket_metadata['token_id']),
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
        )

    data = Web3.solidity_keccak(
        ['bytes22', 'bytes'],
        [
            forge_address(ticket_identifier.address),
            forge_micheline(ticket_content_micheline),
        ]
    )

    ticket_hash = decode(["uint256"], data)[0]

    ticket = await TezosTicket.create(
        id=ticket_id,
        token=token,
        ticketer_address=ticket_identifier.address,
        ticket_id=ticket_content.nat,
        ticket_hash=ticket_hash,
    )

    return ticket

async def on_rollup_call(
    ctx: HandlerContext,
    default: TzktTransaction[DefaultParameter, RollupStorage],
) -> None:
    parameter = default.parameter.__root__.LL
    ticket = await validate_ticket(parameter, ctx)

    routing_info = bytes.fromhex(parameter.bytes)
    l2_receiver = routing_info[:20]

    await TezosDepositEvent.create(
        timestamp=default.data.timestamp,
        level=default.data.level,
        operation_hash=default.data.hash,
        counter=default.data.counter,
        nonce=default.data.nonce,
        initiator=default.data.initiator_address,
        sender=default.data.sender_address,
        target=default.data.target_address,
        ticket=ticket,
        l2_receiver=l2_receiver.hex(),
        amount=parameter.ticket.amount,
    )

    ctx.logger.info(f'Deposit Call registered: {default}')
