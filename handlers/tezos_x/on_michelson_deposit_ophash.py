from dipdup.context import HandlerContext
from dipdup.models.tezos import TezosOperationData

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from rollup_bridge_indexer.models import EtherlinkToken
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models.enum import RuntimeKind


async def on_michelson_deposit_ophash(
    ctx: HandlerContext,
    transaction_0: TezosOperationData,
) -> None:
    op = transaction_0

    if not op.amount or op.amount <= 0 or op.has_internals:
        return

    ctx.logger.info('L2 Michelson deposit found: %s (amount=%s -> %s)', op.hash, op.amount, op.target_address)

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
    )

    ctx.logger.info('L2 Michelson deposit registered: %s %s', deposit.id, op.hash)

    BridgeMatcherLocks.set_pending_michelson_deposits()
