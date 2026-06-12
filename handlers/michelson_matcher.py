"""Op-hash matcher step for L2 Michelson (tz1-receiver) XTZ deposits.

SEPARATED ON PURPOSE — this module exists because TzKT drops implicit-source events
(the kernel's `tag=deposit` event that would carry the inbox coords), so the L2 leg
cannot be matched by coords like every other deposit. This is the PRODUCTION
mechanism with no planned removal. The separation just keeps the option open: IF
TzKT/xTzKT ever serves those events, the event-based L2 handler
(`tezos_x/on_michelson_deposit.py`) can store inbox coords directly, these deposits
flow through the regular coords-based `BridgeMatcher.check_pending_etherlink_deposits`,
and this module + its `pending_michelson_deposits` lock + the `batch.py` call can be
deleted — but don't count on that happening.

How it matches: for every bridge deposit that has its L1 leg + inbox message but no
L2 leg yet, the expected L2 synthetic-op hash is reconstructed from the stored inbox
message alone (`expected_op_hash_from_inbox`, ~54µs CPU, no I/O) and compared with
the op-hash of recorded L2 Michelson deposits. Hash equality covers amount, receiver
and inbox coords at once — no further field checks are needed. On match the inbox
coords are backfilled onto the L2 row, so the data ends up shaped exactly like an
event-based match would leave it.

Michelson L2 rows are recognized by their base58 `o…` transaction_hash; EVM rows
store bare 64-hex, so the namespaces are disjoint.
"""

import logging

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.michelson_deposit import expected_op_hash_from_inbox
from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import EtherlinkDepositOperation

_logger = logging.getLogger('rollup_bridge_indexer.handlers.michelson_matcher')


async def check_pending_michelson_deposits(rollup_address: str) -> None:
    if not BridgeMatcherLocks.pending_michelson_deposits:
        return
    BridgeMatcherLocks.pending_michelson_deposits = False

    l2_deposits = await EtherlinkDepositOperation.filter(
        bridge_deposits=None,
        transaction_hash__startswith='o',
    ).order_by('level', 'transaction_index')
    if not l2_deposits:
        return

    # Expected L2 op-hash -> the bridge deposit whose L2 leg is still missing.
    expected: dict[str, BridgeDepositOperation] = {}
    qs = BridgeDepositOperation.filter(
        l2_transaction=None,
        inbox_message_id__isnull=False,
    ).prefetch_related('inbox_message')
    async for bridge_deposit in qs:
        inbox_message = bridge_deposit.inbox_message
        try:
            op_hash = expected_op_hash_from_inbox(inbox_message.message, inbox_message.level, inbox_message.index, rollup_address)
        except ValueError:
            continue  # not Michelson-routed (e.g. legacy/FA routing shapes)
        if op_hash is not None:
            expected[op_hash] = bridge_deposit

    matched = 0
    for l2_deposit in l2_deposits:
        bridge_deposit = expected.pop(l2_deposit.transaction_hash, None)
        if bridge_deposit is None:
            continue

        # Backfill the inbox coords so the row is indistinguishable from an event-based match.
        l2_deposit.inbox_message_level = bridge_deposit.inbox_message.level
        l2_deposit.inbox_message_index = bridge_deposit.inbox_message.index
        await l2_deposit.save()

        bridge_deposit.l2_transaction = l2_deposit
        await bridge_deposit.save()

        bridge_operation = await BridgeOperation.get(id=bridge_deposit.pk)
        bridge_operation.is_completed = True
        bridge_operation.is_successful = True
        bridge_operation.updated_at = l2_deposit.timestamp
        bridge_operation.status = BridgeOperationStatus.finished
        await bridge_operation.save()
        matched += 1

    unmatched = len(l2_deposits) - matched
    if matched:
        _logger.info('Matched %d L2 Michelson deposit(s) by op-hash', matched)
    if unmatched:
        # Transiently normal (L2 leg lands before its inbox message is backfilled).
        # PERSISTENT rows here mean a pre-event-era deposit or a kernel upgrade that
        # changed the op-hash derivation — see handlers/michelson_deposit.py docstring.
        _logger.warning('%d L2 Michelson deposit(s) still unmatched by op-hash', unmatched)
