"""L2 leg of a Tezos X Michelson (tz1-receiver) XTZ deposit — op-hash variant.

Records the L2 *synthetic* pseudo-Michelson `transaction` the kernel emits from
TEZLINK_DEPOSITOR for a tz1-target XTZ deposit. Unlike the node-polling variant
(`handlers/tezos_x/on_michelson_deposit.py`), this handler reads NOTHING extra:
no deposit event (TzKT drops it for implicit-source ops) and no Tezos node call.
The deterministic L1<->L2 link is the op-hash, reconstructed purely from L1 inbox
data by `expected_op_hash_from_inbox` (see `michelson_deposit` module docstring).

So here we only persist the observed L2 op (its op-hash, receiver, amount); the
match against the L1-reconstructed hash is demonstrated by the neighbouring stand
case `tests/stand/cases/michelson_l2_deposit_ophash/`. The row carries no inbox
coords and no `l2_token`, so the production matcher leaves it untouched — this is
the reconstruction-only demo, not a wired matcher step.

Discriminators (not expressible as index filters):
  * amount > 0       — a ticket can't carry a zero balance; also excludes NAC
                       reverse-flows (amount == 0) from the same source.
  * hasInternals == False — the kernel forbids deposits to KT1/contracts, so a
                       tz1 deposit never spawns internal ops.
"""

from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosOperationData

from rollup_bridge_indexer.models import EtherlinkDepositOperation


async def on_michelson_deposit(
    ctx: HandlerContext,
    transaction_0: TezosOperationData,
) -> None:
    op = transaction_0

    # Discriminate the synthetic deposit from NAC reverse-flows (same source).
    if not op.amount or op.amount <= 0 or op.has_internals:
        return

    ctx.logger.info('L2 Michelson deposit found: %s (amount=%s -> %s)', op.hash, op.amount, op.target_address)

    deposit = await EtherlinkDepositOperation.create(
        timestamp=op.timestamp,
        level=op.level,
        address=op.sender_address,  # L2 sender = the depositor (tz1)
        transaction_hash=op.hash,  # the op-hash — the deterministic match key
        transaction_index=op.counter,  # ordering key only
        log_index=None,
        l2_account=op.target_address,  # L2 receiver (tz1)
        l2_token=None,  # left unmatched: this is the reconstruction-only demo
        ticket=None,
        ticket_owner='',
        amount=str(op.amount),
    )

    ctx.logger.info('L2 Michelson deposit recorded (op-hash variant): %s %s', deposit.id, op.hash)
