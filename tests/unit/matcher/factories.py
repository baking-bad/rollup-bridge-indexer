from datetime import UTC
from datetime import datetime

from rollup_bridge_indexer.handlers.batch import run_matcher_steps
from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.handlers.michelson_deposit import expected_op_hash_from_inbox
from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import BridgeOperationType
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from rollup_bridge_indexer.models import EtherlinkToken
from rollup_bridge_indexer.models import EtherlinkWithdrawOperation
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType
from rollup_bridge_indexer.models import RollupOutboxMessage
from rollup_bridge_indexer.models import RollupOutboxMessageBuilder
from rollup_bridge_indexer.models import RuntimeKind
from rollup_bridge_indexer.models import TezosDepositOperation
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models import TezosToken
from rollup_bridge_indexer.models import TezosWithdrawOperation

ROLLUP = 'sr1TCYofXUuJjmQvZ26XE4YAwXdfetQfZ6rR'
NATIVE_TICKETER = 'KT1FcWeWiEC7Ve5JMdZpKyvaFdsJv7n4GFzi'
TS = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def _l2_account(address: str) -> L2Account:
    home_runtime = RuntimeKind.michelson if address.startswith(('tz', 'KT')) else RuntimeKind.evm
    return await L2Account.get_or_create_for(address, home_runtime)


async def seed_xtz(*, evm_decimals: int = 18) -> EtherlinkToken:
    token = await TezosToken.create(id='xtz', contract_address=NATIVE_TICKETER, name='Tezos', symbol='XTZ', decimals=6, type='native')
    ticket = await TezosTicket.create(hash='1', ticketer_address=NATIVE_TICKETER, token=token, whitelisted=True)
    await EtherlinkToken.create(id='xtz_michelson', name='Tezos', symbol='XTZ', decimals=6, ticket=ticket)
    return await EtherlinkToken.create(id='xtz_evm', name='Tezos', symbol='XTZ', decimals=evm_decimals, ticket=ticket)


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
    l2_token: EtherlinkToken | None,
    *,
    level: int = 50,
    inbox_message_level: int | None = 100,
    inbox_message_index: int | None = 5,
    amount_wei: str = '1000000' + '0' * 12,
    l2_account: str = 'ab' * 20,
    timestamp: datetime = TS,
    ticket: TezosTicket | None = None,
    ticket_owner: str | None = None,
    transaction_hash: str = 'ef' * 32,
) -> EtherlinkDepositOperation:
    return await EtherlinkDepositOperation.create(
        timestamp=timestamp,
        level=level,
        address='cd' * 20,
        transaction_hash=transaction_hash,
        transaction_index=0,
        log_index=0,
        l2_account=await _l2_account(l2_account),
        l2_token=l2_token,
        ticket=l2_token.ticket if l2_token is not None else ticket,
        ticket_owner=ticket_owner if ticket_owner is not None else (l2_token.id if l2_token is not None else ''),
        amount=amount_wei,
        inbox_message_level=inbox_message_level,
        inbox_message_index=inbox_message_index,
    )


async def bridge_deposit(
    l1: TezosDepositOperation,
    *,
    inbox: RollupInboxMessage | None = None,
    l2: EtherlinkDepositOperation | None = None,
    runtime_kind: RuntimeKind | None = None,
    created_at: datetime = TS,
) -> BridgeDepositOperation:
    receiver: str = l1.l2_account_id  # type: ignore[attr-defined]  # tortoise generates the FK id attr
    row = await BridgeDepositOperation.create(l1_transaction=l1, inbox_message=inbox, l2_transaction=l2, created_at=created_at)
    await BridgeOperation.create(
        id=row.id,
        type=BridgeOperationType.deposit,
        l1_account=l1.l1_account,
        l2_account_id=receiver,
        runtime_kind=runtime_kind or (RuntimeKind.michelson if receiver.startswith('tz') else RuntimeKind.evm),
        created_at=created_at,
        updated_at=l1.timestamp,
        status=BridgeOperationStatus.created,
    )
    return row


async def michelson_l2_deposit(
    xtz: EtherlinkToken,
    *,
    level: int = 50,
    op_hash: str = 'o' + 'mc' * 25,
    amount_mutez: int = 1000000,
    l2_account: str = 'tz1receiverXXXXXXXXXXXXXXXXXXXXXXXXX',
    timestamp: datetime = TS,
) -> EtherlinkDepositOperation:
    token = await EtherlinkToken.get(id='xtz_michelson')
    return await EtherlinkDepositOperation.create(
        timestamp=timestamp,
        level=level,
        address=l2_account,
        transaction_hash=op_hash,
        transaction_index=1,
        log_index=None,
        runtime_kind=RuntimeKind.michelson,
        l2_account=await _l2_account(l2_account),
        l2_token=token,
        ticket=xtz.ticket,
        ticket_owner=token.id,
        amount=str(amount_mutez),
    )


async def outbox_message(
    *,
    level: int = 200,
    index: int = 0,
    builder: RollupOutboxMessageBuilder = RollupOutboxMessageBuilder.kernel,
    message: dict | None = None,
    parameters_hash: str | None = 'b' * 32,
    created_at: datetime = TS,
) -> RollupOutboxMessage:
    return await RollupOutboxMessage.create(
        level=level,
        index=index,
        builder=builder,
        message=message if message is not None else {},
        parameters_hash=parameters_hash,
        created_at=created_at,
        cemented_at=created_at,
        cemented_level=level + 40,
    )


async def l2_withdrawal(
    l2_token: EtherlinkToken,
    *,
    level: int = 150,
    amount_wei: str = '1000000' + '0' * 12,
    l2_account: str = 'ab' * 20,
    l1_account: str = 'tz1withdrawerXXXXXXXXXXXXXXXXXXXXXXX',
    parameters_hash: str | None = 'b' * 32,
    fast_payload: bytes | None = None,
    kernel_withdrawal_id: int | None = None,
    transaction_index: int = 0,
    log_index: int = 0,
    timestamp: datetime = TS,
) -> EtherlinkWithdrawOperation:
    return await EtherlinkWithdrawOperation.create(
        timestamp=timestamp,
        level=level,
        address='cd' * 20,
        transaction_hash='ab' * 32,
        transaction_index=transaction_index,
        log_index=log_index,
        l2_account=await _l2_account(l2_account),
        l1_account=l1_account,
        l2_token=l2_token,
        ticket=l2_token.ticket,
        l2_ticket_owner='cd' * 20,
        l1_ticket_owner=l2_token.ticket.ticketer_address,
        amount=amount_wei,
        fast_payload=fast_payload,
        parameters_hash=parameters_hash,
        kernel_withdrawal_id=kernel_withdrawal_id,
    )


async def l1_withdrawal(
    outbox: RollupOutboxMessage,
    *,
    level: int = 210,
    amount: str | None = '1000000',
    counter: int = 1,
    timestamp: datetime = TS,
) -> TezosWithdrawOperation:
    return await TezosWithdrawOperation.create(
        timestamp=timestamp,
        level=level,
        operation_hash=f'o{outbox.level:025d}{outbox.index:024d}',
        counter=counter,
        nonce=None,
        initiator='tz1executorXXXXXXXXXXXXXXXXXXXXXXXXX',
        sender='tz1executorXXXXXXXXXXXXXXXXXXXXXXXXX',
        target=ROLLUP,
        amount=amount,
        outbox_message=outbox,
    )


async def bridge_withdrawal(
    l2: EtherlinkWithdrawOperation,
    *,
    outbox: RollupOutboxMessage | None = None,
    l1: TezosWithdrawOperation | None = None,
    created_at: datetime = TS,
) -> BridgeWithdrawOperation:
    bridge = await BridgeWithdrawOperation.create(
        created_at=created_at,
        l2_transaction=l2,
        outbox_message=outbox,
        l1_transaction=l1,
    )
    await BridgeOperation.create(
        id=bridge.id,
        type=BridgeOperationType.withdrawal,
        l1_account=l2.l1_account,
        l2_account_id=l2.l2_account_id,  # type: ignore[attr-defined]  # tortoise generates the FK id attr
        runtime_kind=l2.runtime_kind,
        created_at=created_at,
        updated_at=created_at,
        status=BridgeOperationStatus.created,
    )
    return bridge


def fast_payout_message(
    l2: EtherlinkWithdrawOperation,
    *,
    service_provider: str = 'tz1providerXXXXXXXXXXXXXXXXXXXXXXXXX',
) -> dict:
    assert l2.fast_payload is not None, 'a payout pairs with a FAST withdrawal'
    return {
        'withdrawal': {
            'withdrawal_id': str(l2.kernel_withdrawal_id),
            'full_amount': str(int(l2.amount) // int(1e12)),
            'ticketer': l2.l1_ticket_owner,
            'content': {'nat': '0', 'bytes': None},
            'timestamp': l2.timestamp.isoformat(),
            'base_withdrawer': l2.l1_account,
            'payload': l2.fast_payload.hex(),
            'l2_caller': l2.l2_account_id,  # type: ignore[attr-defined]  # tortoise generates the FK id attr
        },
        'service_provider': service_provider,
    }


async def run_matcher_pass() -> None:
    await run_matcher_steps()


async def run_deposit_matching() -> None:
    BridgeMatcherLocks.set_pending_tezos_deposits()
    BridgeMatcherLocks.set_pending_inbox()
    BridgeMatcherLocks.set_pending_etherlink_deposits()
    BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()
    BridgeMatcherLocks.set_pending_michelson_deposits()
    await run_matcher_pass()


async def run_withdrawal_matching() -> None:
    BridgeMatcherLocks.set_pending_etherlink_withdrawals()
    BridgeMatcherLocks.set_pending_outbox()
    BridgeMatcherLocks.set_pending_tezos_withdrawals()
    BridgeMatcherLocks.set_pending_claimed_fast_withdrawals()
    await run_matcher_pass()


GOLDEN_RECEIVER = 'tz1PSJR6wBtoiv56Uz1w1bBxeoBnWpDYMwV7'
GOLDEN_ROUTING = '01dad80196000029a8a3205033f6d4f0fb7c218e4a7e8bc12a798cc0'

DEPOSIT_CLASSES = ('coords', 'value', 'op_hash')


def _michelson_payload(amount_mutez: int) -> dict:
    return {
        'LL': {
            'bytes': GOLDEN_ROUTING,
            'ticket': {'amount': str(amount_mutez), 'address': NATIVE_TICKETER, 'content': {'nat': '0', 'bytes': None}},
        }
    }


def build_deposit_op(seq: int, kind: str, xtz):
    level, index, params_hash, inbox_id = 1000 + seq, seq, format(seq, '032d'), 100 + seq
    amount = 1_000_000 + seq * 7
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
            BridgeMatcherLocks.set_pending_etherlink_deposits()
        else:
            row = await evm_l2_deposit(
                xtz, level=level, inbox_message_level=None, inbox_message_index=None, amount_wei=f'{amount}{"0" * 12}', l2_account=receiver
            )
            BridgeMatcherLocks.set_pending_etherlink_xtz_deposits()
        state['l2_id'] = row.id

    return state, [reveal_l1, reveal_inbox, reveal_l2]


async def assert_all_deposits_finished(ops):
    for op in ops:
        bridge = await BridgeDepositOperation.get(l1_transaction_id=op['l1_id'])
        assert bridge.l2_transaction_id == op['l2_id'], f"{op['kind']} linked to the wrong L2 leg"
        operation = await BridgeOperation.get(id=bridge.id)
        assert operation.is_completed, op['kind']
        assert operation.status == BridgeOperationStatus.finished, op['kind']
    assert await EtherlinkDepositOperation.filter(bridge_deposits=None).count() == 0, 'an L2 row was left unmatched'
