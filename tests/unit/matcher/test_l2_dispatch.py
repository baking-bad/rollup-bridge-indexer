"""L2-deposit dispatch — one matcher pass over a mixed batch of all three row classes."""

import pytest

from tests.unit.matcher.factories import DEPOSIT_CLASSES
from tests.unit.matcher.factories import assert_all_deposits_finished
from tests.unit.matcher.factories import build_deposit_op
from tests.unit.matcher.factories import run_deposit_matching
from tests.unit.matcher.factories import seed_xtz

pytestmark = pytest.mark.anyio


async def test_mixed_l2_classes_match_in_one_batch(db):
    # One deposit of each class (coords / value / op-hash), all rows present, one matcher
    # pass — all three link by their different keys without preempting each other.
    xtz = await seed_xtz()
    ops, actions = [], []
    for seq, kind in enumerate(DEPOSIT_CLASSES):
        state, reveals = build_deposit_op(seq, kind, xtz)
        ops.append(state)
        actions.extend(reveals)

    for reveal in actions:
        await reveal()
    await run_deposit_matching()

    await assert_all_deposits_finished(ops)
