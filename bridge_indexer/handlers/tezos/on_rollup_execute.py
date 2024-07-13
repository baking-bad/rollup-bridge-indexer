from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosSmartRollupExecute
from tortoise.exceptions import DoesNotExist

from bridge_indexer.handlers import setup_handler_logger
from bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from bridge_indexer.models import TezosWithdrawOperation
from bridge_indexer.types.output_proof.output_proof import OutputProofData


async def on_rollup_execute(
    ctx: HandlerContext,
    execute: TezosSmartRollupExecute,
) -> None:
    setup_handler_logger(ctx)
    ctx.logger.info(f'Tezos Withdraw Transaction found: {execute.data.hash}')

    rpc = ctx.get_http_datasource('tezos_node')
    block_operations = await rpc.request('GET', f'chains/main/blocks/{execute.data.level}/operations')
    for group in block_operations[3]:
        if group['hash'] != execute.data.hash:
            continue
        for operation in group['contents']:
            if operation['kind'] != 'smart_rollup_execute_outbox_message':
                continue
            if int(operation['counter']) != execute.data.counter:
                continue
            message_hex = operation['output_proof']
            break
    decoder = OutputProofData(bytes.fromhex(message_hex))
    output_proof, _ = decoder.unpack()

    try:
        outbox_message = await ctx.container.outbox_message_service.find_by_index(
            output_proof['output_proof_output']['outbox_level'],
            output_proof['output_proof_output']['message_index'],
        )
    except DoesNotExist:
        ctx.logger.error(
            'Failed to fetch Outbox Message with level %d and index %d.',
            output_proof['output_proof_output']['outbox_level'],
            output_proof['output_proof_output']['message_index'],
        )
        return

    withdrawal = await TezosWithdrawOperation.create(
        timestamp=execute.data.timestamp,
        level=execute.data.level,
        operation_hash=execute.data.hash,
        counter=execute.data.counter,
        nonce=execute.data.nonce,
        initiator=execute.data.initiator_address,
        sender=execute.data.sender_address,
        target=execute.data.target_address,
        outbox_message=outbox_message,
    )

    ctx.logger.info(f'Tezos Withdraw Transaction registered: {withdrawal.id}')

    BridgeMatcher.set_pending_tezos_withdrawals()
