"""L2 leg of a Tezos X Michelson (tz1-receiver) XTZ deposit — production op-hash path.

Records the L2 *synthetic* pseudo-Michelson `transaction` the kernel emits from
TEZLINK_DEPOSITOR for a tz1-target XTZ deposit. Reads NOTHING beyond the TzKT op:
no deposit event (TzKT drops it for implicit-source ops) and no Tezos node call.
The deterministic L1<->L2 link is the op-hash, reconstructed from L1 inbox data by
the separated matcher step (`handlers/michelson_matcher.py`); inbox coords are
backfilled onto this row at match time.

The row is stored fully consumer-visible, shaped like the EVM XTZ path
(`etherlink/on_xtz_deposit.py`): xtz token + ticket, and the amount scaled
mutez -> wei so it agrees with the xtz EtherlinkToken's 18 decimals.

Discriminators (not expressible as index filters):
  * amount > 0       — a ticket can't carry a zero balance; also excludes NAC
                       reverse-flows (amount == 0) from the same source.
  * hasInternals == False — the kernel forbids deposits to KT1/contracts, so a
                       tz1 deposit never spawns internal ops.
"""

from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosOperationData

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.michelson_deposit import WEI_PER_MUTEZ
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from rollup_bridge_indexer.models import EtherlinkToken
from rollup_bridge_indexer.models import TezosTicket


async def on_michelson_deposit(
    ctx: HandlerContext,
    transaction_0: TezosOperationData,
) -> None:
    op = transaction_0

    # Discriminate the synthetic deposit from NAC reverse-flows (same source).
    if not op.amount or op.amount <= 0 or op.has_internals:
        return

    ctx.logger.info('L2 Michelson deposit found: %s (amount=%s -> %s)', op.hash, op.amount, op.target_address)

    etherlink_token = await EtherlinkToken.get(id='xtz')
    tezos_ticket = await TezosTicket.get(token_id='xtz')

    deposit = await EtherlinkDepositOperation.create(
        timestamp=op.timestamp,
        level=op.level,
        address=op.sender_address,  # L2 sender = the depositor (tz1)
        transaction_hash=op.hash,  # the op-hash — the deterministic match key
        transaction_index=op.counter,  # ordering key only
        log_index=None,
        l2_account=op.target_address,  # L2 receiver (tz1)
        l2_token=etherlink_token,
        ticket=tezos_ticket,
        ticket_owner=etherlink_token.id,
        amount=str(op.amount * WEI_PER_MUTEZ),  # mutez -> wei, like every other xtz l2_deposit row
    )

    ctx.logger.info('L2 Michelson deposit registered: %s %s', deposit.id, op.hash)

    BridgeMatcherLocks.set_pending_michelson_deposits()
