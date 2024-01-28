from dipdup.context import HandlerContext
from dipdup.models.tezos_tzkt import TzktTransaction

from tezos_indexer.models import DepositEvent
from tezos_indexer.types.rollup.tezos_parameters.default import DefaultParameter
from tezos_indexer.types.rollup.tezos_storage import RollupStorage
from pytezos.michelson.forge import forge_address
from pytezos.michelson.forge import forge_micheline
from pytezos.michelson.forge import unforge_micheline
from pytezos.michelson.micheline import micheline_value_to_python_object
from web3.main import Web3
from eth_abi import decode


async def on_rollup_call(
    ctx: HandlerContext,
    default: TzktTransaction[DefaultParameter, RollupStorage],
) -> None:
    print(default.data.level)

    parameter = default.parameter.__root__.LL
    routing_info = bytes.fromhex(parameter.bytes)
    l2_receiver = routing_info[:20]
    l2_proxy = routing_info[20:40] or None
    ticket_identifier = parameter.ticket
    ticket_content = ticket_identifier.data

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

    data = Web3.solidity_keccak(
        ['bytes22', 'bytes'],
        [
            forge_address(ticket_identifier.address),
            forge_micheline(ticket_content_micheline),
        ]
    )

    ticket_hash = decode(["uint256"], data)[0]

    await DepositEvent.create(
        timestamp=default.data.timestamp,
        level=default.data.level,
        operation_hash=default.data.hash,
        counter=default.data.counter,
        nonce=default.data.nonce,
        initiator=default.data.initiator_address,
        sender=default.data.sender_address,
        target=default.data.target_address,
        ticket_hash=ticket_hash,
        ticket_owner=default.data.initiator_address,
        ticketer=ticket_identifier.address,
        asset_id=asset_id,
        l2_receiver=l2_receiver.hex(),
        l2_proxy=l2_proxy.hex(),
        amount=ticket_identifier.amount,
    )

    ctx.logger.info(f'Deposit Call registered: {default}')
