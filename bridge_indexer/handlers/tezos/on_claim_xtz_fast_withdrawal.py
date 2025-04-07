from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosTransaction

from bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from bridge_indexer.models import TezosWithdrawOperation
from bridge_indexer.types.fast_withdrawal.tezos_parameters.default import DefaultParameter
from bridge_indexer.types.fast_withdrawal.tezos_storage import FastWithdrawalStorage


async def on_claim_xtz_fast_withdrawal(
    ctx: HandlerContext,
    default: TezosTransaction[DefaultParameter, FastWithdrawalStorage],
) -> None:
    assert default

    withdrawal = await TezosWithdrawOperation.create(
        timestamp=default.data.timestamp,
        level=default.data.level,
        operation_hash=default.data.hash,
        counter=default.data.counter,
        nonce=default.data.nonce,
        initiator=default.data.initiator_address,
        sender=default.data.sender_address,
        target=default.data.target_address,
        outbox_message=None,
        amount=default.data.amount,
    )
    ctx.logger.info('Tezos PayoutFastWithdrawal Transaction registered: %s', withdrawal.id)

    BridgeMatcherLocks.set_pending_tezos_withdrawals()
