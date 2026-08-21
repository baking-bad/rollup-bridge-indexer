"""Seed the matcher's pools to the sizes measured on a production backfill.

The matcher's cost is not driven by how many operations arrive — it is driven by how many
unmatched rows have piled up, because every pass walks the whole pool. A block-window run
can never produce that state: it yields pools of a few rows. So the bench builds the state
directly.

The seeded state is a **fixed point**: no seeded row can ever match, so pass N does exactly
the same work as pass 1. That is what makes repeated passes comparable, and it is also the
honest shape of a backlog — the pool is large precisely because those rows have no
counterpart yet.

Row shapes mirror ``tests/unit/matcher/factories.py``, which is the canonical description of
what each handler writes. They are rebuilt here rather than reused because the factories
insert one row per call and this seeds ~200k rows; keep the two in sync by hand.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from rollup_bridge_indexer.models import EtherlinkToken
from rollup_bridge_indexer.models import EtherlinkWithdrawOperation
from rollup_bridge_indexer.models import L2Account
from rollup_bridge_indexer.models import OriginKind
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType
from rollup_bridge_indexer.models import RollupOutboxMessage
from rollup_bridge_indexer.models import RollupOutboxMessageBuilder
from rollup_bridge_indexer.models import RuntimeKind
from rollup_bridge_indexer.models import TezosDepositOperation
from rollup_bridge_indexer.models import TezosTicket
from rollup_bridge_indexer.models import TezosToken
from rollup_bridge_indexer.models import TezosWithdrawOperation

NATIVE_TICKETER = 'KT1FcWeWiEC7Ve5JMdZpKyvaFdsJv7n4GFzi'
FA_TICKETER = 'KT1AaaAaaAaaAaaAaaAaaAaaAaaAaaAaaAaa'
ROLLUP = 'sr1TCYofXUuJjmQvZ26XE4YAwXdfetQfZ6rR'
T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
ACCOUNTS = 64  # reused across rows; a realistic backlog is many operations by few accounts
CHUNK = 2000


@dataclass(frozen=True)
class PoolSizes:
    """Defaults are the pools measured on etherlink-bridge-mainnet-staging, 2026-08-05,
    28 hours into a reindex — see the table in README.md."""

    # Pools the matcher walks, one database query per row. This is the defect.
    tezos_withdrawals: int = 8886  # P1
    xtz_deposits: int = 2656  # P3
    claimed_fast: int = 950  # P4
    etherlink_deposits: int = 453  # P2

    # The counter-side pools those queries look into: small by construction, because a
    # bridge row is only created once its leading leg has arrived.
    open_bridge_withdrawals: int = 26
    open_bridge_deposits: int = 16

    # Settled rows. They match nothing and are never walked — but they are the bulk of the
    # table each per-row query scans, because neither `bridge_withdrawal.outbox_message_id`
    # nor `bridge_deposit.inbox_message_id` is indexed. Halve these and the per-row cost
    # halves with them.
    settled_bridge_withdrawals: int = 26000
    settled_bridge_deposits: int = 26000

    @classmethod
    def scaled(cls, factor: float) -> PoolSizes:
        measured = cls()
        return cls(**{f.name: max(1, round(getattr(measured, f.name) * factor)) for f in dataclass_fields(cls)})

    def as_dict(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in dataclass_fields(self)}


def _account(i: int) -> str:
    return format(i % ACCOUNTS, '040x')


async def _bulk(model, rows: list) -> None:
    for start in range(0, len(rows), CHUNK):
        await model.bulk_create(rows[start : start + CHUNK])


async def _seed_tokens() -> tuple[tuple[EtherlinkToken, str], tuple[EtherlinkToken, str]]:
    """The native XTZ triple every network seeds on reindex, plus one FA token.

    The FA token exists so the coords-keyed pool (P2) and the value-zip pool (P3) stay
    disjoint the way they are in production — P3's queryset selects on `xtz_evm`.
    """
    xtz_l1 = await TezosToken.create(id='xtz', contract_address=NATIVE_TICKETER, name='Tezos', symbol='XTZ', decimals=6, type='native')
    xtz_ticket = await TezosTicket.create(hash='1', ticketer_address=NATIVE_TICKETER, token=xtz_l1, whitelisted=True)
    await EtherlinkToken.create(id='xtz_michelson', name='Tezos', symbol='XTZ', decimals=6, ticket=xtz_ticket)
    xtz = await EtherlinkToken.create(id='xtz_evm', name='Tezos', symbol='XTZ', decimals=18, ticket=xtz_ticket)

    fa_l1 = await TezosToken.create(id='fa', contract_address=FA_TICKETER, name='Token', symbol='TKN', decimals=6, type='fa2')
    fa_ticket = await TezosTicket.create(hash='2', ticketer_address=FA_TICKETER, token=fa_l1, whitelisted=True)
    fa = await EtherlinkToken.create(id='fa_evm', name='Token', symbol='TKN', decimals=6, ticket=fa_ticket)

    await _bulk(
        L2Account,
        [
            L2Account(runtime_address=_account(i), origin=_account(i), kind=OriginKind.native, home_runtime=RuntimeKind.evm)
            for i in range(ACCOUNTS)
        ],
    )
    return (xtz, xtz_ticket.hash), (fa, fa_ticket.hash)


def _outbox(level: int, index: int, builder: RollupOutboxMessageBuilder, parameters_hash: str | None, message: dict | None = None):
    return RollupOutboxMessage(
        id=uuid.uuid4(),
        level=level,
        index=index,
        builder=builder,
        message=message or {},
        parameters_hash=parameters_hash,
        created_at=T0 + timedelta(seconds=level),
        cemented_at=T0 + timedelta(seconds=level),
        cemented_level=level + 40,
    )


def _l1_withdrawal(outbox: RollupOutboxMessage, level: int):
    return TezosWithdrawOperation(
        id=uuid.uuid4(),
        timestamp=T0 + timedelta(seconds=level),
        level=level,
        operation_hash=f'o{level:025d}{outbox.index:024d}',
        counter=1,
        nonce=None,
        initiator='tz1executorXXXXXXXXXXXXXXXXXXXXXXXXX',
        sender='tz1executorXXXXXXXXXXXXXXXXXXXXXXXXX',
        target=ROLLUP,
        amount='1000000',
        outbox_message_id=outbox.id,
    )


def _l2_withdrawal(token: EtherlinkToken, ticket_hash: str, i: int, *, fast: bool = False):
    return EtherlinkWithdrawOperation(
        id=uuid.uuid4(),
        timestamp=T0 + timedelta(seconds=i),
        level=i,
        address='cd' * 20,
        transaction_hash=format(i, '064x'),
        transaction_index=0,
        log_index=0,
        l2_account_id=_account(i),
        l1_account='tz1withdrawerXXXXXXXXXXXXXXXXXXXXXXX',
        l2_token_id=token.id,
        ticket_id=ticket_hash,
        l2_ticket_owner='cd' * 20,
        l1_ticket_owner=NATIVE_TICKETER,
        amount='1000000' + '0' * 12,
        fast_payload=b'\x01\x02' if fast else None,
        parameters_hash=None,
        kernel_withdrawal_id=i if fast else None,
    )


async def seed_withdrawal_side(sizes: PoolSizes, xtz: EtherlinkToken, xtz_ticket: str) -> None:
    settled, open_, outer, fast = (
        sizes.settled_bridge_withdrawals,
        sizes.open_bridge_withdrawals,
        sizes.tezos_withdrawals,
        sizes.claimed_fast,
    )

    # Settled: L2 leg, outbox and L1 leg all present. Never walked, always scanned.
    outboxes = [_outbox(1000 + i, 0, RollupOutboxMessageBuilder.kernel, None) for i in range(settled)]
    l2s = [_l2_withdrawal(xtz, xtz_ticket, i) for i in range(settled)]
    l1s = [_l1_withdrawal(outboxes[i], 1000 + i) for i in range(settled)]
    await _bulk(RollupOutboxMessage, outboxes)
    await _bulk(EtherlinkWithdrawOperation, l2s)
    await _bulk(TezosWithdrawOperation, l1s)
    await _bulk(
        BridgeWithdrawOperation,
        [
            BridgeWithdrawOperation(
                id=uuid.uuid4(),
                created_at=T0 + timedelta(seconds=i),
                l1_transaction_id=l1s[i].id,
                l2_transaction_id=l2s[i].id,
                outbox_message_id=outboxes[i].id,
            )
            for i in range(settled)
        ],
    )

    # The counter-side pool of `check_pending_tezos_withdrawals`: bridge rows still waiting
    # for their L1 leg. Their outbox messages are not shared with the walked pool below, so
    # nothing pairs and the state is stable across passes.
    open_outboxes = [_outbox(500_000 + i, 0, RollupOutboxMessageBuilder.kernel, None) for i in range(open_)]
    open_l2s = [_l2_withdrawal(xtz, xtz_ticket, 500_000 + i) for i in range(open_)]
    await _bulk(RollupOutboxMessage, open_outboxes)
    await _bulk(EtherlinkWithdrawOperation, open_l2s)
    await _bulk(
        BridgeWithdrawOperation,
        [
            BridgeWithdrawOperation(
                id=uuid.uuid4(),
                created_at=T0 + timedelta(seconds=500_000 + i),
                l1_transaction_id=None,
                l2_transaction_id=open_l2s[i].id,
                outbox_message_id=open_outboxes[i].id,
            )
            for i in range(open_)
        ],
    )

    # The walked pool of `check_pending_tezos_withdrawals` (P1): L1 executions whose L2 leg
    # has not been indexed yet, so no bridge row carries their outbox message.
    outer_outboxes = [_outbox(700_000 + i, 0, RollupOutboxMessageBuilder.kernel, None) for i in range(outer)]
    await _bulk(RollupOutboxMessage, outer_outboxes)
    await _bulk(TezosWithdrawOperation, [_l1_withdrawal(outer_outboxes[i], 700_000 + i) for i in range(outer)])

    # The walked pool of `check_pending_claimed_fast_withdrawals` (P4): service-provider
    # payouts whose L2 fast withdrawal is not indexed yet, so the lookup by
    # `kernel_withdrawal_id` finds nothing. One indexed lookup per row — the cost here is
    # round-trips, not scanning.
    payout_outboxes = [_outbox(900_000 + i, 0, RollupOutboxMessageBuilder.service_provider, str(9_000_000 + i)) for i in range(fast)]
    await _bulk(RollupOutboxMessage, payout_outboxes)
    await _bulk(TezosWithdrawOperation, [_l1_withdrawal(payout_outboxes[i], 900_000 + i) for i in range(fast)])


def _inbox(id_: int, level: int, index: int):
    return RollupInboxMessage(
        id=id_,
        level=level,
        index=index,
        type=RollupInboxMessageType.transfer,
        message={},
        parameters_hash=None,
        expected_l2_op_hash=None,
    )


def _l1_deposit(ticket_id: str, i: int, amount: str):
    return TezosDepositOperation(
        id=uuid.uuid4(),
        timestamp=T0 + timedelta(seconds=i),
        level=i,
        operation_hash=f'o{i:050d}',
        counter=1,
        nonce=None,
        initiator='tz1initiatorXXXXXXXXXXXXXXXXXXXXXXXX',
        sender='tz1initiatorXXXXXXXXXXXXXXXXXXXXXXXX',
        target=ROLLUP,
        l1_account='tz1initiatorXXXXXXXXXXXXXXXXXXXXXXXX',
        l2_account_id=_account(i),
        ticket_id=ticket_id,
        amount=amount,
        parameters_hash=None,
    )


def _l2_deposit(token: EtherlinkToken, ticket_hash: str, i: int, *, level: int, coords: tuple[int, int] | None, amount: str):
    return EtherlinkDepositOperation(
        id=uuid.uuid4(),
        timestamp=T0 + timedelta(seconds=i),
        level=level,
        address='cd' * 20,
        transaction_hash=format(i, '064x'),
        transaction_index=0,
        log_index=0,
        runtime_kind=RuntimeKind.evm,
        l2_account_id=_account(i),
        l2_token_id=token.id,
        ticket_id=ticket_hash,
        ticket_owner=token.id,
        amount=amount,
        inbox_message_level=coords[0] if coords else None,
        inbox_message_index=coords[1] if coords else None,
    )


async def seed_deposit_side(sizes: PoolSizes, xtz: EtherlinkToken, xtz_ticket: str, fa: EtherlinkToken, fa_ticket: str) -> None:
    settled, open_, xtz_pool, coords_pool = (
        sizes.settled_bridge_deposits,
        sizes.open_bridge_deposits,
        sizes.xtz_deposits,
        sizes.etherlink_deposits,
    )

    # Settled: L1 leg, inbox message and L2 leg all present.
    inboxes = [_inbox(i + 1, i, 0) for i in range(settled)]
    l1s = [_l1_deposit(xtz_ticket, i, '1000000') for i in range(settled)]
    l2s = [_l2_deposit(xtz, xtz_ticket, i, level=i, coords=(i, 0), amount='1000000' + '0' * 12) for i in range(settled)]
    await _bulk(RollupInboxMessage, inboxes)
    await _bulk(TezosDepositOperation, l1s)
    await _bulk(EtherlinkDepositOperation, l2s)
    await _bulk(
        BridgeDepositOperation,
        [
            BridgeDepositOperation(
                id=uuid.uuid4(),
                created_at=T0 + timedelta(seconds=i),
                l1_transaction_id=l1s[i].id,
                l2_transaction_id=l2s[i].id,
                inbox_message_id=inboxes[i].id,
            )
            for i in range(settled)
        ],
    )

    # The counter-side pool both deposit steps query into: L1 leg and inbox present, L2 leg
    # still missing. Their amounts are unique to them, so the value zip finds nothing either.
    open_inboxes = [_inbox(500_000 + i, 500_000 + i, 0) for i in range(open_)]
    open_l1s = [_l1_deposit(xtz_ticket, 500_000 + i, '7777777') for i in range(open_)]
    await _bulk(RollupInboxMessage, open_inboxes)
    await _bulk(TezosDepositOperation, open_l1s)
    await _bulk(
        BridgeDepositOperation,
        [
            BridgeDepositOperation(
                id=uuid.uuid4(),
                created_at=T0 + timedelta(seconds=500_000 + i),
                l1_transaction_id=open_l1s[i].id,
                l2_transaction_id=None,
                inbox_message_id=open_inboxes[i].id,
            )
            for i in range(open_)
        ],
    )

    # The walked pool of `check_pending_etherlink_xtz_deposits` (P3): XTZ credits on L2
    # whose L1 deposit is not indexed yet. No coords, so the coords step leaves them alone.
    await _bulk(
        EtherlinkDepositOperation,
        [_l2_deposit(xtz, xtz_ticket, 700_000 + i, level=700_000 + i, coords=None, amount='5555555' + '0' * 12) for i in range(xtz_pool)],
    )

    # The walked pool of `check_pending_etherlink_deposits` (P2): FA deposits carrying inbox
    # coordinates that no bridge deposit holds. FA rather than XTZ keeps it out of P3's pool.
    await _bulk(
        EtherlinkDepositOperation,
        [_l2_deposit(fa, fa_ticket, 900_000 + i, level=900_000 + i, coords=(900_000 + i, 0), amount='1000000') for i in range(coords_pool)],
    )


async def seed(sizes: PoolSizes) -> None:
    (xtz, xtz_ticket), (fa, fa_ticket) = await _seed_tokens()
    await seed_withdrawal_side(sizes, xtz, xtz_ticket)
    await seed_deposit_side(sizes, xtz, xtz_ticket, fa, fa_ticket)
