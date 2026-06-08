from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosOperationData


async def on_michelson_deposit(
    ctx: HandlerContext,
    transaction_0: TezosOperationData,
) -> None:
    # Stub: detection only (full handler is TODO below).
    # Discriminator: amount > 0 (a zero-amount Michelson ticket can't be created) and
    # has_internals is False (deposits to KT1 are kernel-forbidden, so a deposit to a tz1
    # never has internal ops). amount == 0 / has_internals == True => NAC forward, not a deposit.
    op = transaction_0
    if not op.amount or op.amount <= 0 or op.has_internals:
        ctx.logger.info(
            'Skipping non-deposit op from depositor: hash=%s amount=%s has_internals=%s',
            op.hash,
            op.amount,
            op.has_internals,
        )
        return

    ctx.logger.info(
        'L2 Michelson deposit candidate: level=%s hash=%s sender=%s target=%s amount=%s',
        op.level,
        op.hash,
        op.sender_address,
        op.target_address,
        op.amount,
    )
    # TODO(Milestone 2): fetch (inbox_level, inbox_msg_id) from the Michelson node
    # (aggregate /chains/main/blocks/{level}/operations), WARNING-log + skip if the deposit
    # event is absent (pre-kernel-update deposits), create the deposit row, then trigger
    # BridgeMatcherLocks.set_pending_etherlink_deposits().
