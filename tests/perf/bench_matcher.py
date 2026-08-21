"""Per-step cost of one matcher pass over a production-sized backlog.

    make bench-matcher [SCALE=1.0] [PASSES=3] [LABEL=name]

Reports two numbers per step. **Queries** is the verdict: it is exact, deterministic and
independent of the machine, and every patch in this set is an attempt to make one step's
query count stop tracking its pool size. **Milliseconds** is the corroboration — it is what
the database actually spends, and it is the only number that reflects the unindexed foreign
keys the per-row queries scan against.

Postgres, not sqlite: the defect is round-trips to a database server plus server-side
sequential scans, and an in-process sqlite has neither. Measuring this on sqlite would
understate the win by exactly the mechanism being fixed.

The bench does not check correctness. The seeded backlog is a fixed point where nothing
matches, so a patch that silently stopped matching would look excellent here. Correctness
lives in the pinning tests under tests/unit/matcher/.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

from dipdup.transactions import TransactionManager
from tortoise import Tortoise

from rollup_bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
from rollup_bridge_indexer.models import BridgeDepositOperation
from rollup_bridge_indexer.models import BridgeWithdrawOperation
from rollup_bridge_indexer.models import EtherlinkDepositOperation
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupOutboxMessage
from rollup_bridge_indexer.models import RollupOutboxMessageBuilder
from rollup_bridge_indexer.models import RuntimeKind
from rollup_bridge_indexer.models import TezosWithdrawOperation
from tests.perf.seed import PoolSizes
from tests.perf.seed import seed
from tests.unit.conftest import DB_URL
from tests.unit.conftest import init_empty_schema

# (name, step, lock setter). Order and lock names copy `handlers/batch.run_matcher_steps`;
# the bench arms one step at a time so each step's cost is attributed to that step alone.
STEPS = (
    ('tezos_deposits', BridgeMatcher.check_pending_tezos_deposits, BridgeMatcherLocks.set_pending_tezos_deposits),
    ('inbox', BridgeMatcher.check_pending_inbox, BridgeMatcherLocks.set_pending_inbox),
    ('michelson_deposits', BridgeMatcher.check_pending_michelson_deposits, BridgeMatcherLocks.set_pending_michelson_deposits),
    ('etherlink_deposits', BridgeMatcher.check_pending_etherlink_deposits, BridgeMatcherLocks.set_pending_etherlink_deposits),
    (
        'etherlink_xtz_deposits',
        BridgeMatcher.check_pending_etherlink_xtz_deposits,
        BridgeMatcherLocks.set_pending_etherlink_xtz_deposits,
    ),
    (
        'etherlink_withdrawals',
        BridgeMatcher.check_pending_etherlink_withdrawals,
        BridgeMatcherLocks.set_pending_etherlink_withdrawals,
    ),
    ('outbox', BridgeMatcher.check_pending_outbox, BridgeMatcherLocks.set_pending_outbox),
    ('tezos_withdrawals', BridgeMatcher.check_pending_tezos_withdrawals, BridgeMatcherLocks.set_pending_tezos_withdrawals),
    (
        'claimed_fast_withdrawals',
        BridgeMatcher.check_pending_claimed_fast_withdrawals,
        BridgeMatcherLocks.set_pending_claimed_fast_withdrawals,
    ),
)

_EXECUTORS = ('execute_query', 'execute_query_dict', 'execute_insert', 'execute_many', 'execute_script')


class QueryCounter:
    """Counts statements issued through the Tortoise client for the duration of a block."""

    def __init__(self) -> None:
        self.count = 0
        self._connection = Tortoise.get_connection('default')
        self._originals: dict = {}

    def __enter__(self) -> 'QueryCounter':
        for name in _EXECUTORS:
            original = getattr(self._connection, name)
            self._originals[name] = original

            def wrapper(*args, _original=original, **kwargs):
                self.count += 1
                return _original(*args, **kwargs)

            setattr(self._connection, name, wrapper)
        return self

    def __exit__(self, *_) -> None:
        for name, original in self._originals.items():
            setattr(self._connection, name, original)


async def pool_census() -> dict[str, int]:
    """Count the pools using the matcher's own filters.

    A patch that drains a pool instead of walking it cheaper would report a beautiful time
    for a step that no longer does the work; this is what makes that visible.
    """
    return {
        'tezos_withdrawals': await TezosWithdrawOperation.filter(
            bridge_withdrawals=None, outbox_message__builder=RollupOutboxMessageBuilder.kernel
        ).count(),
        'claimed_fast': await TezosWithdrawOperation.filter(
            bridge_withdrawals=None,
            outbox_message__builder=RollupOutboxMessageBuilder.service_provider,
            outbox_message__parameters_hash__isnull=False,
        ).count(),
        'xtz_deposits': await EtherlinkDepositOperation.filter(
            bridge_deposits=None, l2_token_id='xtz_evm', runtime_kind=RuntimeKind.evm
        ).count(),
        'etherlink_deposits': await EtherlinkDepositOperation.filter(
            bridge_deposits=None, inbox_message_level__isnull=False, inbox_message_index__isnull=False
        ).count(),
        'pending_inbox_deposits': await BridgeDepositOperation.filter(inbox_message=None).count(),
        'pending_outbox_withdrawals': await BridgeWithdrawOperation.filter(outbox_message=None).count(),
        'open_bridge_withdrawals': await BridgeWithdrawOperation.filter(l1_transaction_id__isnull=True).count(),
        # The deposit steps' counter side is `l2_transaction=None` *with a message attached*
        # — an unattached bridge deposit has no coords to key on and never enters that pool.
        'open_bridge_deposits': await BridgeDepositOperation.filter(l2_transaction_id__isnull=True, inbox_message_id__isnull=False).count(),
        # The counter sides of the two attach steps: a message that still carries a hash is
        # one nobody has claimed. Attaching nulls the hash, so an attach shows up right here.
        'unclaimed_inbox_messages': await RollupInboxMessage.filter(parameters_hash__isnull=False).count(),
        'unclaimed_outbox_messages': await RollupOutboxMessage.filter(parameters_hash__isnull=False, bridge_withdrawals=None).count(),
    }


async def measure(passes: int) -> dict:
    per_step: dict[str, dict] = {name: {'queries': [], 'ms': []} for name, _, _ in STEPS}
    for _ in range(passes):
        for name, step, arm in STEPS:
            arm()
            with QueryCounter() as counter:
                started = time.perf_counter()
                await step()
                elapsed = (time.perf_counter() - started) * 1000
            per_step[name]['queries'].append(counter.count)
            per_step[name]['ms'].append(round(elapsed, 1))

    return {
        name: {
            'queries': statistics.median(data['queries']),
            'ms': round(statistics.median(data['ms']), 1),
            'ms_all': data['ms'],
        }
        for name, data in per_step.items()
    }


async def run(scale: float, passes: int, label: str | None) -> dict:
    if DB_URL.startswith('sqlite'):
        raise SystemExit('TEST_DB_URL must point at a Postgres — sqlite has neither the round-trips nor the scans this measures.')

    sizes = PoolSizes.scaled(scale)
    await init_empty_schema()
    async with TransactionManager().register():
        seed_started = time.perf_counter()
        await seed(sizes)
        seed_seconds = round(time.perf_counter() - seed_started, 1)

        before = await pool_census()
        steps = await measure(passes)
        after = await pool_census()
    await Tortoise.close_connections()

    total_queries = sum(s['queries'] for s in steps.values())
    total_ms = round(sum(s['ms'] for s in steps.values()), 1)
    return {
        'label': label,
        'scale': scale,
        'passes': passes,
        'seed_seconds': seed_seconds,
        'requested_pools': sizes.as_dict(),
        'pools': before,
        # Equal to `pools` iff the seeded backlog really is a fixed point: every pass did
        # the same work, so the medians above are comparable across runs and across patches.
        'pools_after': after,
        'fixed_point': before == after,
        'steps': steps,
        'total': {'queries': total_queries, 'ms': total_ms},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scale', type=float, default=1.0, help='fraction of the measured production pool sizes')
    parser.add_argument('--passes', type=int, default=3, help='matcher passes to time; the median is reported')
    parser.add_argument('--label', default=None, help='what is being measured, e.g. "baseline" or "P1"')
    parser.add_argument('--out', type=Path, default=None, help='also write the JSON here')
    args = parser.parse_args()

    result = asyncio.run(run(args.scale, args.passes, args.label))
    rendered = json.dumps(result, indent=1)
    if args.out:
        args.out.write_text(rendered)
    print(rendered)

    if not result['fixed_point']:
        print('WARNING: the pools changed between passes — the medians are not comparable.', file=sys.stderr)


if __name__ == '__main__':
    main()
