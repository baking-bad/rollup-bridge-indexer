from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosSmartRollupExecute
from eth_abi.exceptions import ParseError
from tortoise.exceptions import DoesNotExist

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from bridge_indexer.models import BridgeOperation
from bridge_indexer.models import BridgeOperationKind
from bridge_indexer.models import BridgeOperationStatus
from bridge_indexer.models import BridgeOperationType
from bridge_indexer.models import BridgeWithdrawOperation
from bridge_indexer.models import TezosWithdrawOperation
from bridge_indexer.types.output_proof.output_proof import OutputProofData


async def on_rollup_execute(
    ctx: HandlerContext,
    execute: TezosSmartRollupExecute,
) -> None:
    ctx.logger.info('Tezos Withdraw Transaction found: %s', execute.data.hash)

    if execute.data.parameter_json and 'withdrawal_id' in execute.data.parameter_json:
        bridge_withdrawals_qs = BridgeWithdrawOperation.filter(
            l2_transaction__kernel_withdrawal_id=execute.data.parameter_json['withdrawal_id'],
            outbox_message_id__isnull=False,
        )
        async for bridge_withdrawal in bridge_withdrawals_qs:
            if await BridgeOperation.exists(
                id=bridge_withdrawal.id,
                type=BridgeOperationType.withdrawal,
                kind__in=[BridgeOperationKind.fast_withdrawal_service_provider, BridgeOperationKind.fast_withdrawal],
                status__in=[BridgeOperationStatus.created, BridgeOperationStatus.sealed],
            ):
                outbox_message = bridge_withdrawal.outbox_message
                break

        assert outbox_message
    else:
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
                output_proof_hex = operation['output_proof']
                break

        try:
            assert output_proof_hex
        except AssertionError:
            ctx.logger.error('Outbox Message execution not found in block operations.')
            return

        try:
            decoder = OutputProofData(bytes.fromhex(output_proof_hex))
            output_proof, _ = decoder.unpack()
        except ParseError:
            ctx.logger.error('Failed to decode Outbox Message output_proof.')
            return

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

    ctx.logger.info('Tezos Withdraw Transaction registered: %s', withdrawal.id)

    BridgeMatcherLocks.set_pending_tezos_withdrawals()
