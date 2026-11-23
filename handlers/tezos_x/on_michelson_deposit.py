import aiohttp
from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosOperationData

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from rollup_bridge_indexer.models import EtherlinkToken
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models.enum import RuntimeKind

_NODE_DATASOURCE = 'tezos_x_michelson_node'


def _extract_inbox_coords(block_operations: list, op_hash: str) -> tuple[int, int] | None:
    for pass_operations in block_operations:
        for group in pass_operations:
            if group.get('hash') != op_hash:
                continue
            for content in group.get('contents', []):
                for result in content.get('metadata', {}).get('internal_operation_results', []):
                    if result.get('tag') == 'deposit':
                        args = result['payload']['args']
                        return int(args[0]['int']), int(args[1]['int'])
    return None


async def on_michelson_deposit(
    ctx: HandlerContext,
    transaction_0: TezosOperationData,
) -> None:
    op = transaction_0
    if not op.amount or op.amount <= 0 or op.has_internals:
        return

    ctx.logger.info('L2 Michelson deposit found: %s (amount=%s -> %s)', op.hash, op.amount, op.target_address)

    node = ctx.get_http_datasource(_NODE_DATASOURCE)
    try:
        block_operations = await node.request('GET', f'chains/main/blocks/{op.level}/operations')
    except (TimeoutError, aiohttp.ClientError) as exception:
        ctx.logger.warning('Could not fetch Michelson block %s for %s: %s. Skipped.', op.level, op.hash, exception)
        return

    coords = _extract_inbox_coords(block_operations, op.hash)
    if coords is None:
        ctx.logger.warning('No deposit event for %s @ level %s (pre-event kernel?). Skipped.', op.hash, op.level)
        return
    inbox_message_level, inbox_message_index = coords

    etherlink_token = await EtherlinkToken.get(id='xtz_michelson')
    tezos_ticket = await TezosTicket.get(token_id='xtz')
    assert op.target_address is not None
    l2_account = await L2Account.get_or_create_for(op.target_address, RuntimeKind.michelson)

    deposit = await EtherlinkDepositOperation.create(
        timestamp=op.timestamp,
        level=op.level,
        address=op.sender_address,
        transaction_hash=op.hash,
        transaction_index=op.counter,
        log_index=None,
        runtime_kind=RuntimeKind.michelson,
        l2_account=l2_account,
        l2_token=etherlink_token,
        ticket=tezos_ticket,
        ticket_owner=etherlink_token.id,
        amount=str(op.amount),
        inbox_message_level=inbox_message_level,
        inbox_message_index=inbox_message_index,
    )
    ctx.logger.info('L2 Michelson deposit registered: %s inbox=(%s,%s)', deposit.id, inbox_message_level, inbox_message_index)

    BridgeMatcherLocks.set_pending_etherlink_deposits()
