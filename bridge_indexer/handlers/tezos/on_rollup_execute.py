from dipdup.context import HandlerContext
from dipdup.models.tezos_tzkt import TzktSmartRollupExecute
from dipdup.models.tezos_tzkt import TzktTransaction

from bridge_indexer.models import TezosWithdrawEvent
from bridge_indexer.types.output_proof.output_proof import OutputProofData
from bridge_indexer.types.ticketer.tezos_parameters.withdraw import WithdrawParameter
from bridge_indexer.types.ticketer.tezos_storage import TicketerStorage


async def on_rollup_execute(
    ctx: HandlerContext,
    execute: TzktSmartRollupExecute,
    withdraw: TzktTransaction[WithdrawParameter, TicketerStorage],
) -> None:
    ctx.logger.info(
        'Smart rollup %s has been called by contract %s with commitment %s',
        execute.data.target_address,
        execute.data.initiator_address,
        execute.commitment,
    )
    rpc = ctx.get_http_datasource('tezos_node')
    block_operations = await rpc.request('GET', f'chains/main/blocks/{execute.data.level}/operations')
    for group in block_operations[3]:
        if group['hash'] != execute.data.hash:
            continue
        for operation in group['contents']:
            if operation['kind'] != 'smart_rollup_execute_outbox_message':
                continue
            message_hex = operation['output_proof']
            break
    decoder = OutputProofData(bytes.fromhex(message_hex))
    try:
        output_proof, _ = decoder.unpack()
    except Exception as e:
        assert e

    import json
    d = json.dumps(output_proof)
    assert output_proof

    await TezosWithdrawEvent.create(
        timestamp=execute.data.timestamp,
        level=execute.data.level,
        operation_hash=execute.data.hash,
        counter=execute.data.counter,
        nonce=execute.data.nonce,
        initiator=execute.data.initiator_address,
        sender=execute.data.sender_address,
        target=execute.data.target_address,
        # l1_account=...,
        # ticket=...,
        # amount=...,
        outbox_level=output_proof['output_proof_output']['outbox_level'],
        outbox_msg_id=output_proof['output_proof_output']['message_index'],
    )
