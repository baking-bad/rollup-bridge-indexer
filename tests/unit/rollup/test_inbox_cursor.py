"""Contract: the inbox backfill asks for every message in the window, exactly once.

`RollupMessageIndex` walks TzKT's inbox with a single cursor and the filter
`v1/smart_rollups/inbox?id.gt=<cursor>`. Strictly greater — so the cursor means *the last id
already consumed*, and that is what the in-run advance stores. The two places that derive it
from persisted state have to mean the same thing, or the message at the boundary is requested
by nobody: not by the run that stopped short of it, not by the run that resumes above it.

Nothing reports such a hole. The index is `realtime`, `dipdup_head` is at the chain head, and
the missing message is simply a deposit that never matches, or an `external` whose outbox level
is never queued.

The two boundaries, one test each:

  * a restart, which resumes from the last saved row;
  * a fresh database, which starts from the first message of the window.

Offline: the fake TzKT here answers *from a universe* by applying the filter in the URL, so a
message that is never asked for is a row that is never written. That is the whole instrument —
the assertions are about the rows in the database, not about the URLs, because a recorded URL
cannot tell you whether anything was lost.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from rollup_bridge_indexer.handlers.rollup_message import RollupMessageIndex
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType

pytestmark = pytest.mark.anyio

ROLLUP = 'sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu'
ORIGINATION_LEVEL = 1
BLOCK_TIMESTAMP = '2026-01-01T00:00:00Z'

# Ids are contiguous in the real stream: TzKT numbers every inbox message, and the
# `type.in=transfer,external` filter only breaks the run at level boundaries. A universe
# without gaps is therefore the honest shape, and it is the one where a skip is visible.
UNIVERSE_IDS = range(100, 106)


def _message(message_id: int) -> dict[str, Any]:
    """A transfer to the rollup — indexed unconditionally, so presence is decided by the fetch."""
    return {
        'id': message_id,
        'level': message_id - 90,
        'index': 0,
        'type': RollupInboxMessageType.transfer.value,
        'target': {'address': ROLLUP},
        'parameter': {'entrypoint': 'default', 'value': {'int': str(message_id)}},
    }


UNIVERSE = [_message(i) for i in UNIVERSE_IDS]


class UniverseTzkt:
    """Serves the inbox from a fixed universe by applying the filter it is handed.

    The production fakes elsewhere in this suite hand back a canned page and ignore the URL;
    that is what makes them blind to which messages were requested. This one is the opposite:
    it holds every message that exists and lets the query decide.
    """

    def __init__(self, universe: list[dict[str, Any]], head_level: int = 10_000) -> None:
        self.universe = universe
        self.head_level = head_level
        self.urls: list[str] = []

    async def get_head_block(self) -> Any:
        return SimpleNamespace(level=self.head_level)

    @staticmethod
    def _param(url: str, name: str) -> int | None:
        for part in url.split('?', 1)[-1].split('&'):
            key, _, value = part.partition('=')
            if key == name:
                return int(value)
        return None

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url.startswith('v1/smart_rollups/inbox'):
            self.urls.append(url)
            id_gt = self._param(url, 'id.gt')
            level_ge = self._param(url, 'level.ge')
            limit = self._param(url, 'limit') or len(self.universe)
            page = [m for m in self.universe if (id_gt is None or m['id'] > id_gt) and (level_ge is None or m['level'] >= level_ge)]
            return page[:limit]
        if url.startswith('v1/smart_rollups/'):
            return {'firstActivity': ORIGINATION_LEVEL}
        if url.startswith('v1/blocks/'):
            return BLOCK_TIMESTAMP
        raise AssertionError(f'unexpected TzKT request: {url}')


class SilentRollupNode:
    """No outbox messages anywhere: these tests are about the inbox side only."""

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        return []


def _index(tzkt: Any) -> RollupMessageIndex:
    bridge: Any = SimpleNamespace(smart_rollup_address=ROLLUP)
    protocol: Any = SimpleNamespace(
        smart_rollup_commitment_period=60,
        smart_rollup_challenge_window=40,
        smart_rollup_max_outbox_messages_per_level=100,
    )
    # Stand-ins for the container's settings objects; nothing on the inbox path reads more
    # than the fields above, and no outbox message ever reaches the ticket service.
    rollup_node: Any = SilentRollupNode()
    ticket_service: Any = None
    return RollupMessageIndex(
        tzkt=tzkt,
        rollup_node=rollup_node,
        bridge=bridge,
        ticket_service=ticket_service,
        protocol=protocol,
        logger=logging.getLogger('test.rollup_message'),
    )


async def _indexed_ids() -> list[int]:
    return [m.id for m in await RollupInboxMessage.all().order_by('id')]


async def _seed(message_ids: list[int]) -> None:
    """Rows a previous run committed, written directly — the seam under test is the next run."""
    await RollupInboxMessage.bulk_create(
        [
            RollupInboxMessage(
                id=i,
                level=_message(i)['level'],
                index=0,
                type=RollupInboxMessageType.transfer,
                message=_message(i)['parameter'],
                parameters_hash=f'{i:032d}',
            )
            for i in message_ids
        ]
    )
    # The seam only exists if the rows are really there; a seed that silently wrote nothing
    # would send this test down the fresh-database path and pass for the wrong reason.
    assert await _indexed_ids() == message_ids


async def test_a_restart_asks_for_the_message_after_the_last_saved_one(db: Any) -> None:
    """The resume seam: the first unindexed message must not fall between the two runs.

    A previous run committed ids 100-102 and stopped — a page boundary, a crash, a redeploy.
    Whatever resumes has to pick up at 103.
    """
    await _seed([100, 101, 102])

    tzkt = UniverseTzkt(UNIVERSE)
    index = _index(tzkt)
    await index._prepare_new_index()
    await index._process()

    assert await _indexed_ids() == list(UNIVERSE_IDS), 'the restart lost the message it resumed on top of'


async def test_a_fresh_database_indexes_the_first_message_of_the_window(db: Any) -> None:
    """The other seam: the window's first message is found by a lookup, then used as a cursor.

    A cursor is the last id consumed, and that message has not been consumed by anyone yet.
    """
    tzkt = UniverseTzkt(UNIVERSE)
    index = _index(tzkt)
    index._sync_first_level = UNIVERSE[0]['level']
    await index._prepare_new_index()
    await index._process()

    assert await _indexed_ids() == list(UNIVERSE_IDS), 'the first message of the window was never requested'
