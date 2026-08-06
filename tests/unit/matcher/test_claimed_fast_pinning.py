"""What `check_pending_claimed_fast_withdrawals` does today, pinned leg by leg.

A service provider fronts a user's fast withdrawal on L1 and later claims the kernel's
outbox message for itself. This is the only step that turns one L2 withdrawal into *two*
bridge operations, and its six-field payload comparison is the only thing keeping an
unrelated payout from being stitched onto someone else's withdrawal.

Every scenario builds a losing candidate alongside the winning one, so an assertion that
something matched is also an assertion that the *right* thing matched.
"""

from datetime import timedelta

import pytest

from rollup_bridge_indexer.models import BridgeOperation
from rollup_bridge_indexer.models import BridgeOperationKind
from rollup_bridge_indexer.models import BridgeOperationStatus
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import RollupOutboxMessageBuilder
from tests.unit.matcher import factories as f

pytestmark = [pytest.mark.anyio]

PROVIDER = 'tz1providerXXXXXXXXXXXXXXXXXXXXXXXXX'
PAYOUT_TS = f.TS + timedelta(hours=1)


async def _claimed_fast_rows(
    xtz,
    *,
    seq: int = 0,
    withdrawal_overrides: dict | None = None,
    service_provider: str = PROVIDER,
    settle_kernel: bool = False,
):
    """The four rows a claimed fast withdrawal consists of, all keyed off `seq` so several
    independent scenarios coexist in one database.

    `withdrawal_overrides` breaks named keys of the payout payload; `settle_kernel` adds the
    ordinary L1 execute of the kernel message, i.e. the case where the user was already paid
    the slow way before the payout was indexed.

    Returns (l2, kernel_outbox, payout_outbox, payout).
    """
    withdrawal_id = 7 + seq
    params_hash = f'c{seq:031d}'
    l2 = await f.l2_withdrawal(
        xtz,
        level=150 + seq,
        parameters_hash=params_hash,
        fast_payload=bytes([1, 2, seq]),
        kernel_withdrawal_id=withdrawal_id,
        transaction_index=seq,
    )
    kernel_outbox = await f.outbox_message(level=200 + seq * 10, parameters_hash=params_hash)
    if settle_kernel:
        await f.l1_withdrawal(kernel_outbox, level=203 + seq * 10)

    message = f.fast_payout_message(l2, service_provider=service_provider)
    message['withdrawal'].update(withdrawal_overrides or {})
    payout_outbox = await f.outbox_message(
        level=205 + seq * 10,
        builder=RollupOutboxMessageBuilder.service_provider,
        message=message,
        parameters_hash=str(withdrawal_id),
    )
    payout = await f.l1_withdrawal(payout_outbox, level=210 + seq * 10, timestamp=PAYOUT_TS)
    return l2, kernel_outbox, payout_outbox, payout


async def _assert_payout_untouched(l2, kernel_outbox, payout_outbox, payout) -> None:
    """The user keeps the kernel message and stays open; the payout is neither consumed nor
    re-armed — its `parameters_hash` must survive so a later pass can retry it."""
    customer = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id)
    assert customer.outbox_message_id == kernel_outbox.id  # type: ignore[attr-defined]  # tortoise generates the FK id attr
    assert customer.l1_transaction_id is None  # type: ignore[attr-defined]
    assert await BridgeWithdrawOperation.filter(l1_transaction_id=payout.id).count() == 0

    await payout_outbox.refresh_from_db()
    assert payout_outbox.parameters_hash == str(l2.kernel_withdrawal_id)


async def test_claimed_fast_split_repoints_the_customer_and_hands_the_kernel_message_to_the_provider(db):
    """The split, pinned against a decoy fast withdrawal that must not be touched: the
    customer's row moves onto the payout, a second row is created for the provider carrying
    the ORIGINAL kernel message, and the payout message is consumed."""
    xtz = await f.seed_xtz()
    l2, kernel_outbox, payout_outbox, payout = await _claimed_fast_rows(xtz)
    decoy_l2, decoy_kernel_outbox, _, _ = await _claimed_fast_rows(xtz, seq=1)

    await f.run_withdrawal_matching()

    customer = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id, l1_transaction_id=payout.id)
    assert customer.outbox_message_id == payout_outbox.id
    customer_operation = await BridgeOperation.get(id=customer.id)
    assert customer_operation.kind == BridgeOperationKind.fast_withdrawal_claimed
    assert customer_operation.status == BridgeOperationStatus.finished
    assert customer_operation.is_completed and customer_operation.is_successful

    provider = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id, l1_transaction_id=None)
    assert provider.outbox_message_id == kernel_outbox.id
    provider_operation = await BridgeOperation.get(id=provider.id)
    assert provider_operation.kind == BridgeOperationKind.fast_withdrawal_service_provider
    assert provider_operation.l1_account == PROVIDER
    assert provider_operation.l2_account_id == l2.l2_account_id
    assert provider_operation.runtime_kind == l2.runtime_kind
    assert provider_operation.status == BridgeOperationStatus.created
    assert not provider_operation.is_completed
    # `created_at` is auto_now_add and keeps an explicitly passed value — which is what makes
    # the L2 timestamp survive here, and it is the implicit `Meta.ordering` key every candidate
    # tie-break falls back to. `updated_at` is auto_now and does NOT: the payout timestamp the
    # step assigns is overwritten with wall clock on save.
    assert provider_operation.created_at == l2.timestamp
    assert customer_operation.updated_at > PAYOUT_TS

    # The payout message is consumed, so the step cannot pick it up again.
    await payout_outbox.refresh_from_db()
    assert payout_outbox.parameters_hash is None

    # The decoy's own payout resolved against the decoy, never against `l2`.
    decoy = await BridgeWithdrawOperation.get(l2_transaction_id=decoy_l2.id, l1_transaction_id=None)
    assert decoy.outbox_message_id == decoy_kernel_outbox.id
    assert await BridgeWithdrawOperation.filter(l2_transaction_id=l2.id).count() == 2


async def test_ticketer_mismatch_pairs_nothing(db):
    xtz = await f.seed_xtz()
    rows = await _claimed_fast_rows(xtz, withdrawal_overrides={'ticketer': 'KT1BrokenTicketerXXXXXXXXXXXXXXXXXXX'})

    await f.run_withdrawal_matching()

    await _assert_payout_untouched(*rows)


async def test_payload_mismatch_pairs_nothing(db):
    xtz = await f.seed_xtz()
    rows = await _claimed_fast_rows(xtz, withdrawal_overrides={'payload': 'deadbeef'})

    await f.run_withdrawal_matching()

    await _assert_payout_untouched(*rows)


async def test_l2_caller_mismatch_pairs_nothing(db):
    xtz = await f.seed_xtz()
    rows = await _claimed_fast_rows(xtz, withdrawal_overrides={'l2_caller': 'ff' * 20})

    await f.run_withdrawal_matching()

    await _assert_payout_untouched(*rows)


async def test_timestamp_mismatch_pairs_nothing(db):
    """Compared truncated to whole seconds, so one second off is the smallest real break."""
    xtz = await f.seed_xtz()
    rows = await _claimed_fast_rows(xtz, withdrawal_overrides={'timestamp': (f.TS + timedelta(seconds=1)).isoformat()})

    await f.run_withdrawal_matching()

    await _assert_payout_untouched(*rows)


async def test_full_amount_mismatch_pairs_nothing(db):
    """L1 mutez against L2 wei: the payload amount is scaled by 1e12 before comparing, so
    one mutez off must not pair."""
    xtz = await f.seed_xtz()
    rows = await _claimed_fast_rows(xtz, withdrawal_overrides={'full_amount': '1000001'})

    await f.run_withdrawal_matching()

    await _assert_payout_untouched(*rows)


async def test_base_withdrawer_mismatch_pairs_nothing(db):
    xtz = await f.seed_xtz()
    rows = await _claimed_fast_rows(xtz, withdrawal_overrides={'base_withdrawer': 'tz1someoneElseXXXXXXXXXXXXXXXXXXXXXX'})

    await f.run_withdrawal_matching()

    await _assert_payout_untouched(*rows)


async def test_payout_for_an_already_settled_withdrawal_is_dropped_not_split(db):
    """The user was already paid the slow way: the provider's claim is worthless, so the
    payout message is disarmed (`parameters_hash` nulled) and no second operation appears."""
    xtz = await f.seed_xtz()
    l2, kernel_outbox, payout_outbox, payout = await _claimed_fast_rows(xtz, settle_kernel=True)

    await f.run_withdrawal_matching()

    customer = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id)
    assert customer.outbox_message_id == kernel_outbox.id
    assert customer.l1_transaction_id is not None
    assert customer.l1_transaction_id != payout.id

    assert await BridgeWithdrawOperation.all().count() == 1
    await payout_outbox.refresh_from_db()
    assert payout_outbox.parameters_hash is None


async def test_payout_without_an_l2_leg_keeps_its_parameters_hash(db):
    """An unknown `kernel_withdrawal_id` is a not-yet-indexed L2 leg, not a dead payout: the
    step must leave the message armed so a later pass can still resolve it."""
    xtz = await f.seed_xtz()
    l2, kernel_outbox, _, _ = await _claimed_fast_rows(xtz)
    orphan_outbox = await f.outbox_message(
        level=400,
        builder=RollupOutboxMessageBuilder.service_provider,
        message=f.fast_payout_message(l2),
        parameters_hash='999',
    )
    orphan_payout = await f.l1_withdrawal(orphan_outbox, level=410)

    await f.run_withdrawal_matching()

    await orphan_outbox.refresh_from_db()
    assert orphan_outbox.parameters_hash == '999'
    assert await BridgeWithdrawOperation.filter(l1_transaction_id=orphan_payout.id).count() == 0

    # The real payout of the same pass still resolved — the orphan did not abort the walk.
    customer = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id, l1_transaction_id__isnull=False)
    assert customer.outbox_message_id != kernel_outbox.id


async def test_two_payouts_in_one_pass_each_resolve_against_their_own_l2_leg(db):
    """Two independent claims settle in a single walk without borrowing each other's legs."""
    xtz = await f.seed_xtz()
    first = await _claimed_fast_rows(xtz, seq=0, service_provider='tz1firstProviderXXXXXXXXXXXXXXXXXXXX')
    second = await _claimed_fast_rows(xtz, seq=1, service_provider='tz1secondProviderXXXXXXXXXXXXXXXXXXX')

    await f.run_withdrawal_matching()

    for (l2, kernel_outbox, payout_outbox, payout), provider_account in (
        (first, 'tz1firstProviderXXXXXXXXXXXXXXXXXXXX'),
        (second, 'tz1secondProviderXXXXXXXXXXXXXXXXXXX'),
    ):
        customer = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id, l1_transaction_id=payout.id)
        assert customer.outbox_message_id == payout_outbox.id
        provider = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id, l1_transaction_id=None)
        assert provider.outbox_message_id == kernel_outbox.id
        assert (await BridgeOperation.get(id=provider.id)).l1_account == provider_account

    assert await BridgeWithdrawOperation.all().count() == 4


async def test_second_pass_over_a_split_withdrawal_creates_no_third_operation(db):
    """The consumed `parameters_hash` is what makes the step idempotent."""
    xtz = await f.seed_xtz()
    l2, _, _, payout = await _claimed_fast_rows(xtz)

    await f.run_withdrawal_matching()
    assert await BridgeWithdrawOperation.all().count() == 2

    await f.run_withdrawal_matching()

    assert await BridgeWithdrawOperation.all().count() == 2
    assert await BridgeOperation.all().count() == 2
    customer = await BridgeWithdrawOperation.get(l2_transaction_id=l2.id, l1_transaction_id=payout.id)
    assert (await BridgeOperation.get(id=customer.id)).kind == BridgeOperationKind.fast_withdrawal_claimed
