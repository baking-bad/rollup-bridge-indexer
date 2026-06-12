"""L2 Michelson deposit — EVENT/NODE-POLLING variant. NOT wired into production.

Production uses the op-hash variant (`handlers/tezos_x/on_michelson_deposit_ophash.py`
+ `handlers/michelson_matcher.py`). This handler is the event-based alternative: it
reads the kernel's `tag=deposit` event (inbox coords) so deposits flow through the
regular coords-based matcher. It could replace the op-hash path IF TzKT ever serves
implicit-source events — that is not committed anywhere, so don't plan around it
(today TzKT drops them, and the per-op node poll costs ~715 ms and loses a deposit
on a dropped call).

Used ONLY by the stand case `tests/stand/cases/michelson_l2_deposit/`, which also
needs the `tezos_x_michelson_node` datasource below — both exist solely for this
variant. IF TzKT ships the events: point this at the TzKT payload instead of the
node receipt, promote it to prod config, and retire the op-hash matcher.
"""

import aiohttp
from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosOperationData

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.michelson_deposit import WEI_PER_MUTEZ
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from rollup_bridge_indexer.models import EtherlinkToken
from rollup_bridge_indexer.models import TezosTicket

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

    etherlink_token = await EtherlinkToken.get(id='xtz')
    tezos_ticket = await TezosTicket.get(token_id='xtz')

    deposit = await EtherlinkDepositOperation.create(
        timestamp=op.timestamp,
        level=op.level,
        address=op.sender_address,  # L2 sender = the depositor (tz1)
        transaction_hash=op.hash,
        transaction_index=op.counter,  # ordering key only; the match is by inbox coords
        log_index=None,
        l2_account=op.target_address,  # L2 receiver (tz1)
        l2_token=etherlink_token,
        ticket=tezos_ticket,
        ticket_owner=etherlink_token.id,
        amount=str(op.amount * WEI_PER_MUTEZ),  # mutez -> wei, like every other xtz l2_deposit row
        inbox_message_level=inbox_message_level,
        inbox_message_index=inbox_message_index,
    )
    ctx.logger.info('L2 Michelson deposit registered: %s inbox=(%s,%s)', deposit.id, inbox_message_level, inbox_message_index)

    # TODO(rename): EtherlinkDepositOperation now also holds Michelson deposits — rename to
    # L2DepositOperation / TezosxDepositOperation; likewise set_pending_etherlink_deposits ->
    # set_pending_l2_deposits (and check_pending_etherlink_deposits + the lock field).
    BridgeMatcherLocks.set_pending_etherlink_deposits()
