"""Property fuzz: deposit matching under independent-indexer lag.

Each indexer is its own FIFO stream (L1 deposits, rollup inbox, L2-EVM, L2-Michelson). Each
tick advances ONE random stream by a random batch off its front, then runs one matcher pass —
so the streams drain at independent rates and overtake each other, like real indexers at
different speeds. Locks are raised only via the producing handlers' setters, so the lock
cascade — not a forced re-run — must settle every match.

Not under test: how leg rows are read (amount scaling, parameters_hash, op-hash format) — only
the matching. Will grow to cover aliases and the rest of the pipeline.
"""

import random

import pytest

from tests.unit.matcher.factories import DEPOSIT_CLASSES
from tests.unit.matcher.factories import assert_all_deposits_finished
from tests.unit.matcher.factories import build_deposit_op
from tests.unit.matcher.factories import run_matcher_pass
from tests.unit.matcher.factories import seed_xtz

pytestmark = pytest.mark.anyio

# Few cases, each large: many concurrently-pending ops is where cross-matches and
# ordering races surface. Sized so the fuzz runs in roughly the same time as the rest
# of the unit suite combined.
N_OPS = 30


@pytest.mark.parametrize('seed', range(5))
async def test_deposits_match_under_independent_indexer_lag(db, seed):
    rng = random.Random(seed)
    xtz = await seed_xtz()

    ops = []
    streams: dict[str, list] = {'l1': [], 'inbox': [], 'l2_evm': [], 'l2_michelson': []}
    for seq in range(N_OPS):
        kind = rng.choice(DEPOSIT_CLASSES)
        state, (reveal_l1, reveal_inbox, reveal_l2) = build_deposit_op(seq, kind, xtz)
        ops.append(state)
        streams['l1'].append(reveal_l1)
        streams['inbox'].append(reveal_inbox)
        streams['l2_michelson' if kind == 'op_hash' else 'l2_evm'].append(reveal_l2)

    while any(streams.values()):
        name = rng.choice([k for k, v in streams.items() if v])
        take = rng.randint(1, len(streams[name]))
        for reveal in streams[name][:take]:
            await reveal()
        streams[name] = streams[name][take:]
        await run_matcher_pass()

    await assert_all_deposits_finished(ops)

    # Idempotence: a further pass over the settled state links and breaks nothing.
    await run_matcher_pass()
    await assert_all_deposits_finished(ops)
