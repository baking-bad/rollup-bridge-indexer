"""Rows as the indexer handlers would have written them, with one-deposit defaults.

Field shapes mirror the producing handlers (``on_rollup_call``, ``on_deposit``,
``tezos_x/on_michelson_deposit_ophash``, ``RollupMessageIndex``) so the matcher sees
production-shaped data. Every factory takes overrides for what a test cares about.
"""

from datetime import UTC
from datetime import datetime

from rollup_bridge_indexer.handlers.batch import run_matcher_steps
from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.michelson_deposit import expected_op_hash_from_inbox
from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import L2AccountKind
from rollup_bridge_indexer.models import L2DepositOperation
from rollup_bridge_indexer.models import L2Kind
from rollup_bridge_indexer.models import L2Token
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType
from rollup_bridge_indexer.models import TezosDepositOperation
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models import TezosToken

ROLLUP = 'sr1TCYofXUuJjmQvZ26XE4YAwXdfetQfZ6rR'
NATIVE_TICKETER = 'KT1FcWeWiEC7Ve5JMdZpKyvaFdsJv7n4GFzi'
TS = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def _l2_account(address: str) -> L2Account:
    """Resolve the FK row the way the handlers do (kind by address shape — test-only)."""
    kind = L2AccountKind.tz if address.startswith(('tz', 'KT')) else L2AccountKind.evm
    return await L2Account.get_or_create_for(address, kind)


async def seed_xtz() -> L2Token:
    """The native token/ticket triple every network seeds on reindex.

    XTZ surfaces as two L2 tokens on the same native ticket — `xtz_evm` (18 decimals) and
    `xtz_michelson` (6 decimals). Returns the EVM token (its `.ticket` is loaded in-memory);
    `michelson_l2_deposit` pulls `xtz_michelson` itself.
    """
    token = await TezosToken.create(id='xtz', contract_address=NATIVE_TICKETER, name='Tezos', symbol='XTZ', decimals=6, type='native')
    ticket = await TezosTicket.create(hash='1', ticketer_address=NATIVE_TICKETER, token=token, whitelisted=True)
    await L2Token.create(id='xtz_michelson', name='Tezos', symbol='XTZ', decimals=6, ticket=ticket)
    return await L2Token.create(id='xtz_evm', name='Tezos', symbol='XTZ', decimals=18, ticket=ticket)


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
        l2_account=await _l2_account(l2_account),
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
    l2_token: L2Token,
    *,
    level: int = 50,
    inbox_message_level: int | None = 100,
    inbox_message_index: int | None = 5,
    amount_wei: str = '1000000' + '0' * 12,
    l2_account: str = 'ab' * 20,
    timestamp: datetime = TS,
) -> L2DepositOperation:
    """An L2 deposit row as the EVM-side handlers store it (bare-hex tx hash)."""
    return await L2DepositOperation.create(
        timestamp=timestamp,
        level=level,
        address='cd' * 20,
        transaction_hash='ef' * 32,
        transaction_index=0,
        log_index=0,
        l2_account=await _l2_account(l2_account),
        l2_token=l2_token,
        ticket=l2_token.ticket,
        ticket_owner=l2_token.id,
        amount=amount_wei,
        inbox_message_level=inbox_message_level,
        inbox_message_index=inbox_message_index,
    )


async def michelson_l2_deposit(
    xtz: L2Token,
    *,
    level: int = 50,
    op_hash: str = 'o' + 'mc' * 25,
    amount_mutez: int = 1000000,
    l2_account: str = 'tz1receiverXXXXXXXXXXXXXXXXXXXXXXXXX',
    timestamp: datetime = TS,
) -> L2DepositOperation:
    """The synthetic-op row tezos_x/on_michelson_deposit_ophash.py records: base58 hash, no coords."""
    token = await L2Token.get(id='xtz_michelson')
    return await L2DepositOperation.create(
        timestamp=timestamp,
        level=level,
        address=l2_account,
        transaction_hash=op_hash,
        transaction_index=1,
        log_index=None,
        l2_kind=L2Kind.michelson,  # as the ophash handler sets it
        l2_account=await _l2_account(l2_account),
        l2_token=token,
        ticket=xtz.ticket,  # same native ticket as the EVM handle, already loaded
        ticket_owner=token.id,
        amount=str(amount_mutez),  # mutez — matches xtz_michelson's 6 decimals
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
    BridgeMatcherLocks.set_pending_l2_deposits()
    BridgeMatcherLocks.set_pending_l2_xtz_deposits()
    BridgeMatcherLocks.set_pending_michelson_deposits()
    await run_matcher_pass()


# --- Deposit scenario builder (shared by the dispatch + fuzz tests) ----------------
# Golden routing bytes -> tz1PSJ… receiver; reused so every op-hash op resolves to a real
# kernel-derived hash (varying amount/level/index keeps each hash distinct).
GOLDEN_RECEIVER = 'tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7'
GOLDEN_ROUTING = '01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0'

# The three L2-deposit row classes, each with one match key.
DEPOSIT_CLASSES = ('coords', 'value', 'op_hash')


def _michelson_payload(amount_mutez: int) -> dict:
    return {
        'LL': {
            'bytes': GOLDEN_ROUTING,
            'ticket': {'amount': str(amount_mutez), 'address': NATIVE_TICKETER, 'content': {'nat': '0', 'bytes': None}},
        }
    }


def build_deposit_op(seq: int, kind: str, xtz):
    """One deposit of class `kind`; `seq` (its index in the batch) seeds globally-distinct keys.

    Returns (state, [reveal_l1, reveal_inbox, reveal_l2]); each reveal writes a leg row and
    raises a lock.

    !! DUPLICATION (by design): the `set_pending_*` calls below copy each producing handler's
    lock-arming (`on_rollup_call`, `RollupMessageIndex`, `on_deposit`, `on_xtz_deposit`,
    ophash). The lock-arming IS matcher logic and must be kept in sync with those handlers by
    hand — we accept the copy rather than a shared abstraction layer.

    NOT under test here (mirrored, not asserted — these belong to how rows are *read*, not to
    matching): the L1↔L2 amount scaling (wei = mutez*10**12) and `parameters_hash` derivation.
    The op-hash (michelson) class is discriminated by the `l2_kind` column the producing
    handlers set — `michelson_l2_deposit` stamps `michelson`, `evm_l2_deposit` the `evm` default.
    """
    level, index, params_hash, inbox_id = 1000 + seq, seq, format(seq, '032d'), 100 + seq
    amount = 1_000_000 + seq * 7  # distinct across all ops -> the value heuristic is unambiguous
    receiver = GOLDEN_RECEIVER if kind == 'op_hash' else format(seq, '040x')
    payload = _michelson_payload(amount) if kind == 'op_hash' else None
    state: dict = {'kind': kind}

    async def reveal_l1():
        row = await l1_deposit(xtz.ticket, level=level, amount=str(amount), l2_account=receiver, parameters_hash=params_hash)
        state['l1_id'] = row.id
        BridgeMatcherLocks.set_pending_tezos_deposits()

    async def reveal_inbox():
        await inbox_message(id=inbox_id, level=level, index=index, parameters_hash=params_hash, message=payload)
        BridgeMatcherLocks.set_pending_inbox()
        BridgeMatcherLocks.set_pending_michelson_deposits()

    async def reveal_l2():
        if kind == 'op_hash':
            op_hash = expected_op_hash_from_inbox(payload, level, index, ROLLUP)
            row = await michelson_l2_deposit(xtz, level=level, op_hash=op_hash, amount_mutez=amount, l2_account=receiver)
            BridgeMatcherLocks.set_pending_michelson_deposits()
        elif kind == 'coords':
            row = await evm_l2_deposit(
                xtz,
                level=level,
                inbox_message_level=level,
                inbox_message_index=index,
                amount_wei=f'{amount}{"0" * 12}',
                l2_account=receiver,
            )
            BridgeMatcherLocks.set_pending_l2_deposits()
        else:
            row = await evm_l2_deposit(
                xtz, level=level, inbox_message_level=None, inbox_message_index=None, amount_wei=f'{amount}{"0" * 12}', l2_account=receiver
            )
            BridgeMatcherLocks.set_pending_l2_xtz_deposits()
        state['l2_id'] = row.id

    return state, [reveal_l1, reveal_inbox, reveal_l2]


async def assert_all_deposits_finished(ops):
    """Every op links to its OWN L2 leg, finishes, and no L2 row is left unmatched."""
    for op in ops:
        bridge = await BridgeDepositOperation.get(l1_transaction_id=op['l1_id'])
        assert bridge.l2_transaction_id == op['l2_id'], f"{op['kind']} linked to the wrong L2 leg"
        operation = await BridgeOperation.get(id=bridge.id)
        assert operation.is_completed, op['kind']
        assert operation.status == BridgeOperationStatus.finished, op['kind']
    assert await L2DepositOperation.filter(bridge_deposits=None).count() == 0, 'an L2 row was left unmatched'
