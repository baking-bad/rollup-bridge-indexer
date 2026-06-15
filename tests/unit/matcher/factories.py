"""Rows as the indexer handlers would have written them, with one-deposit defaults.

Field shapes mirror the producing handlers (``on_rollup_call``, ``on_deposit``,
``tezos_x/on_michelson_deposit_ophash``, ``RollupMessageIndex``) so the matcher sees
production-shaped data. Every factory takes overrides for what a test cares about.
"""

from datetime import UTC
from datetime import datetime

from rollup_bridge_indexer.handlers.batch import run_matcher_steps
from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.michelson_deposit import WEI_PER_MUTEZ
from rollup_bridge_indexer.handlers.michelson_deposit import expected_op_hash_from_inbox
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from rollup_bridge_indexer.models import EtherlinkToken
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType
from rollup_bridge_indexer.models import TezosDepositOperation
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models import TezosToken

ROLLUP = 'sr1TCYofXUuJjmQvZ26XE4YAwXdfetQfZ6rR'
NATIVE_TICKETER = 'KT1FcWeWiEC7Ve5JMdZpKyvaFdsJv7n4GFzi'
TS = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def seed_xtz() -> EtherlinkToken:
    """The native token/ticket triple every network seeds on reindex."""
    token = await TezosToken.create(id='xtz', contract_address=NATIVE_TICKETER, name='Tezos', symbol='XTZ', decimals=6, type='native')
    ticket = await TezosTicket.create(hash='1', ticketer_address=NATIVE_TICKETER, token=token, whitelisted=True)
    return await EtherlinkToken.create(id='xtz', name='Tezos', symbol='XTZ', decimals=18, ticket=ticket)


async def l1_deposit(
    ticket: TezosTicket,
    *,
    level: int = 100,
    amount: str = '1000000',
    l2_account: str = 'tz1burnAddressXXXXXXXXXXXXXXXXXXXXXX',
    parameters_hash: str | None = 'a' * 32,
    timestamp: datetime = TS,
) -> TezosDepositOperation:
    return await TezosDepositOperation.create(
        timestamp=timestamp,
        level=level,
        operation_hash='o' + 'l1' * 25,
        counter=1,
        nonce=None,
        initiator='tz1initiatorXXXXXXXXXXXXXXXXXXXXXXXX',
        sender='tz1initiatorXXXXXXXXXXXXXXXXXXXXXXXX',
        target=ROLLUP,
        l1_account='tz1initiatorXXXXXXXXXXXXXXXXXXXXXXXX',
        l2_account=l2_account,
        ticket=ticket,
        amount=amount,
        parameters_hash=parameters_hash,
    )


async def inbox_message(
    *,
    id: int = 1,
    level: int = 100,
    index: int = 5,
    message: dict | None = None,
    parameters_hash: str | None = 'a' * 32,
) -> RollupInboxMessage:
    msg = message or {}
    try:
        # Mirrors rollup_message._handle_transfer_inbox_message: the op-hash is computed
        # and stored at inbox-indexing time (None for non-Michelson / legacy shapes).
        expected_l2_op_hash = expected_op_hash_from_inbox(msg, level, index, ROLLUP)
    except ValueError:
        expected_l2_op_hash = None
    return await RollupInboxMessage.create(
        id=id,
        level=level,
        index=index,
        type=RollupInboxMessageType.transfer,
        message=msg,
        parameters_hash=parameters_hash,
        expected_l2_op_hash=expected_l2_op_hash,
    )


async def evm_l2_deposit(
    l2_token: EtherlinkToken,
    *,
    level: int = 50,
    inbox_message_level: int | None = 100,
    inbox_message_index: int | None = 5,
    amount_wei: str = '1000000' + '0' * 12,
    l2_account: str = 'ab' * 20,
    timestamp: datetime = TS,
) -> EtherlinkDepositOperation:
    """An L2 deposit row as the EVM-side handlers store it (bare-hex tx hash)."""
    return await EtherlinkDepositOperation.create(
        timestamp=timestamp,
        level=level,
        address='cd' * 20,
        transaction_hash='ef' * 32,
        transaction_index=0,
        log_index=0,
        l2_account=l2_account,
        l2_token=l2_token,
        ticket=l2_token.ticket,
        ticket_owner=l2_token.id,
        amount=amount_wei,
        inbox_message_level=inbox_message_level,
        inbox_message_index=inbox_message_index,
    )


async def michelson_l2_deposit(
    xtz: EtherlinkToken,
    *,
    level: int = 50,
    op_hash: str = 'o' + 'mc' * 25,
    amount_mutez: int = 1000000,
    l2_account: str = 'tz1receiverXXXXXXXXXXXXXXXXXXXXXXXXX',
    timestamp: datetime = TS,
) -> EtherlinkDepositOperation:
    """The synthetic-op row tezos_x/on_michelson_deposit_ophash.py records: base58 hash, no coords."""
    return await EtherlinkDepositOperation.create(
        timestamp=timestamp,
        level=level,
        address=l2_account,
        transaction_hash=op_hash,
        transaction_index=1,
        log_index=None,
        l2_account=l2_account,
        l2_token=xtz,
        ticket=xtz.ticket,
        ticket_owner=xtz.id,
        amount=str(amount_mutez * WEI_PER_MUTEZ),
    )


async def run_matcher_pass() -> None:
    """One production matcher pass — the exact `batch()` sequence via `run_matcher_steps`.

    Locks are NOT touched: a step runs only if its flag is already up, exactly like
    production. Set the flags the scenario's producing handlers would have set first.
    """
    await run_matcher_steps()


async def run_deposit_matching() -> None:
    """One production batch pass with every deposit lock set — exactly the state
    on_restart/on_synchronized leave behind."""
    BridgeMatcherLocks.set_pending_tezos_deposits()
    BridgeMatcherLocks.set_pending_inbox()
    BridgeMatcherLocks.set_pending_etherlink_deposits()
    BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()
    BridgeMatcherLocks.set_pending_michelson_deposits()
    await run_matcher_pass()
