"""L2 Michelson deposit — event-based variant.

Reads the kernel's `tag=deposit` event (inbox coords) from the node block receipt and
stores them directly, so the deposit flows through the standard coords-based matcher
(`BridgeMatcher.check_pending_l2_deposits`). The event is emitted from an
implicit (tz1) source, which TzKT does not index, so the coords come from the node.

Production uses the op-hash variant (`on_michelson_deposit_ophash.py` +
`BridgeMatcher.check_pending_michelson_deposits`), which needs no node call. This handler
is exercised by the stand case `tests/stand/cases/michelson_l2_deposit/` and its
`tezos_x_michelson_node` datasource.
"""

import aiohttp
from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosOperationData

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import L2DepositOperation
from rollup_bridge_indexer.models import L2Token
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models.enum import L2AccountKind
from rollup_bridge_indexer.models.enum import L2Kind

# Exists only for this variant's stand case — not in any prod config.
_NODE_DATASOURCE = 'tezos_x_michelson_node'


def _extract_inbox_coords(block_operations: list, op_hash: str) -> tuple[int, int] | None:
    """Pull (inbox_level, inbox_msg_id) from the deposit event in the op's node receipt.

    The event is emitted from the depositor (a tz-address), which TzKT never indexes, so it
    exists only in the node block receipt's `internal_operation_results`.
    """
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
    # Discriminator: amount > 0 (a zero-amount Michelson ticket can't be created) and
    # has_internals is False (deposits to KT1 are kernel-forbidden, so a deposit to a tz1
    # never has internal ops). amount == 0 / has_internals == True => NAC forward, not a deposit.
    if not op.amount or op.amount <= 0 or op.has_internals:
        return

    ctx.logger.info('L2 Michelson deposit found: %s (amount=%s -> %s)', op.hash, op.amount, op.target_address)

    # The (inbox_level, inbox_msg_id) needed for the deterministic match lives only in the node
    # block receipt; Tezos RPC has no lookup-by-hash, but DipDup already gives us the level.
    node = ctx.get_http_datasource(_NODE_DATASOURCE)
    try:
        block_operations = await node.request('GET', f'chains/main/blocks/{op.level}/operations')
    except (TimeoutError, aiohttp.ClientError) as exception:
        # TODO: a dropped node call loses this deposit until a re-sync; consider retry/backfill.
        ctx.logger.warning('Could not fetch Michelson block %s for %s: %s. Skipped.', op.level, op.hash, exception)
        return

    coords = _extract_inbox_coords(block_operations, op.hash)
    if coords is None:
        # Pre-kernel-update deposits carry no deposit event — expected, nothing to match on.
        ctx.logger.warning('No deposit event for %s @ level %s (pre-event kernel?). Skipped.', op.hash, op.level)
        return
    inbox_message_level, inbox_message_index = coords

    etherlink_token = await L2Token.get(id='xtz_michelson')
    tezos_ticket = await TezosTicket.get(token_id='xtz')
    assert op.target_address is not None  # a deposit always carries its tz1 receiver
    l2_account = await L2Account.get_or_create_for(op.target_address, L2AccountKind.tz)  # L2 receiver (tz1)

    deposit = await L2DepositOperation.create(
        timestamp=op.timestamp,
        level=op.level,
        address=op.sender_address,  # L2 sender = the depositor (tz1)
        transaction_hash=op.hash,
        transaction_index=op.counter,  # ordering key only; the match is by inbox coords
        log_index=None,
        l2_kind=L2Kind.michelson,
        account=l2_account,
        l2_token=etherlink_token,
        ticket=tezos_ticket,
        ticket_owner=etherlink_token.id,
        amount=str(op.amount),  # mutez — matches xtz_michelson's 6 decimals (and the L1 leg)
        inbox_message_level=inbox_message_level,
        inbox_message_index=inbox_message_index,
    )
    ctx.logger.info('L2 Michelson deposit registered: %s inbox=(%s,%s)', deposit.id, inbox_message_level, inbox_message_index)

    # This event-variant carries inbox coords, so it links via the coords step
    # (check_pending_l2_deposits), not the op-hash michelson step.
    BridgeMatcherLocks.set_pending_l2_deposits()
