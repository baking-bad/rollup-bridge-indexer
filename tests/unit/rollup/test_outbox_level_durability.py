"""Contract: a pending outbox level survives the process that queued it.

`RollupMessageIndex` learns which outbox levels to fetch from the inbox page it is walking
(`_handle_external_inbox_message`) and from a full outbox asking for its continuation
(`_handle_outbox_level`, `outbox_level + 1`). Both used to live only in the in-memory
`PendingOutboxLevels`, while the resume cursor, derived from the last saved inbox row, was
written *before* the drain. A drain that died halfway therefore resumed above the external
messages it had not served yet, and those outbox messages were gone for good.

The fix stores the queue in DipDup's own `dipdup_meta` key-value table (no package model, no
schema hash change, no reindex) and drops a level from it only once the messages it produced
are committed.

These tests cover the paths the block-bounded stand case (`tests/stand/cases/
outbox_fetch_failure/`) cannot reach:

  * the continuation level of a full outbox, which has no inbox message behind it at all —
    both when the chain has not reached it yet and when its own fetch fails;
  * the deferral of a level the rollup node has not applied yet, and the order the drain
    serves the queue in.

Offline: no TzKT, no rollup node — both datasources are fakes, and the database is the
in-memory sqlite of the `db` fixture.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from dipdup.models import IndexStatus
from dipdup.models import Meta
from pytezos import MichelsonType
from pytezos import michelson_to_micheline

from rollup_bridge_indexer.handlers.rollup_message import PendingOutboxLevels
from rollup_bridge_indexer.handlers.rollup_message import RollupMessageIndex
from rollup_bridge_indexer.handlers.ticket import FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupOutboxMessage

pytestmark = pytest.mark.anyio

ROLLUP = 'sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu'
ORIGINATION_LEVEL = 1
BLOCK_TIMESTAMP = '2026-01-01T00:00:00Z'

# Small enough that a two-message outbox counts as full and asks for its continuation.
MAX_OUTBOX_MESSAGES_PER_LEVEL = 2

# An outbox message the hasher cannot read: it is skipped with a warning, which keeps these
# tests about the queue rather than about ticket hashing.
UNHASHABLE_MESSAGE: dict[str, Any] = {'outbox_level': 0, 'message_index': 0, 'message': {}}

_FAST_WITHDRAW_TYPE = MichelsonType.match(michelson_to_micheline(FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE))
TICKETER = 'KT1MJxf4KVN3sosR99VRG7WBbWTJtAyWUJt9'
RECEIVER = 'tz1burnburnburnburnburnburnburjAYjjX'


def _outbox_message(level: int, index: int) -> dict[str, Any]:
    """A fast-withdrawal outbox message the hasher *can* read, so it becomes a row.

    The fast-withdrawal shape is the one whose hash needs no ticket lookup, which keeps
    `ticket_service` out of these tests while still exercising the real insert path.
    """
    parameters = _FAST_WITHDRAW_TYPE.from_python_object(
        {
            'withdrawal_id': level * 10 + index,
            'ticket': {'address': TICKETER, 'content': {'nat': 0, 'bytes': None}, 'amount': 1},
            'timestamp': 0,
            'base_withdrawer': RECEIVER,
            'payload': b'',
            'l2_caller': b'',
        }
    ).to_micheline_value()
    return {
        'outbox_level': level,
        'message_index': index,
        'message': {'transactions': [{'parameters': parameters, 'destination': TICKETER, 'entrypoint': 'default'}]},
    }


async def _outbox_rows() -> list[tuple[int, int]]:
    return [(row.level, row.index) for row in await RollupOutboxMessage.all().order_by('level', 'index')]


class FakeTzkt:
    """Answers only the three URLs `RollupMessageIndex` asks TzKT for, plus the head block."""

    def __init__(self, inbox_pages: list[list[dict[str, Any]]] | None = None, head_level: int = 0) -> None:
        self.inbox_pages = list(inbox_pages or [])
        self.head_level = head_level

    async def get_head_block(self) -> Any:
        return SimpleNamespace(level=self.head_level)

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url.startswith('v1/smart_rollups/inbox'):
            return self.inbox_pages.pop(0) if self.inbox_pages else []
        if url.startswith('v1/smart_rollups/'):
            return {'firstActivity': ORIGINATION_LEVEL}
        if url.startswith('v1/blocks/'):
            return BLOCK_TIMESTAMP
        raise AssertionError(f'unexpected TzKT request: {url}')


class FakeRollupNode:
    """`global/block/<level>/outbox/<level>/messages`, per level, and a record of who asked.

    `processed_level` is what the node says it has applied (`global/block/head/level`). A node
    that stopped applying blocks keeps answering RPC and reports its frozen level; every level
    above it has no hash it can resolve, so it answers 500.
    """

    def __init__(self, responses: dict[int, Any], processed_level: int = 10**9) -> None:
        self.responses = responses
        self.processed_level = processed_level
        self.requested: list[int] = []

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url == 'global/block/head/level':
            return self.processed_level
        level = int(url.split('/')[2])
        self.requested.append(level)
        if level > self.processed_level:
            raise _server_error()
        response = self.responses[level]
        if isinstance(response, Exception):
            raise response
        return response


def _server_error() -> aiohttp.ClientResponseError:
    """The shape a wedged rollup node has: 500 for every level above the one it processed."""
    return aiohttp.ClientResponseError(request_info=None, history=(), status=500, message='Internal Server Error')  # type: ignore[arg-type]


def _index(tzkt: Any, rollup_node: Any) -> RollupMessageIndex:
    # Stand-ins for the container's settings objects; only the fields below are read on
    # these paths, and `ticket_service` is never reached (every message here is unhashable).
    bridge: Any = SimpleNamespace(smart_rollup_address=ROLLUP)
    protocol: Any = SimpleNamespace(
        smart_rollup_commitment_period=60,
        smart_rollup_challenge_window=40,
        smart_rollup_max_outbox_messages_per_level=MAX_OUTBOX_MESSAGES_PER_LEVEL,
    )
    ticket_service: Any = None
    return RollupMessageIndex(
        tzkt=tzkt,
        rollup_node=rollup_node,
        bridge=bridge,
        ticket_service=ticket_service,
        protocol=protocol,
        logger=logging.getLogger('test.rollup_message'),
    )


async def _pending_levels() -> list[int]:
    meta = await Meta.get_or_none(key=PendingOutboxLevels.key)
    return list(meta.value) if meta and meta.value else []


async def test_full_outbox_continuation_level_is_deferred_and_stored(db: Any) -> None:
    """F6 + F7: `outbox_level + 1` above what the node has applied is stored, not fetched.

    A level queued by a full outbox has no inbox message behind it, so the resume cursor
    cannot describe it — the stored queue is its only record. And it must not be fetched
    before the node can answer for it.
    """
    node = FakeRollupNode(
        {
            # A full outbox at 100 -> the drain queues 101 for the rest of the messages.
            100: [UNHASHABLE_MESSAGE] * MAX_OUTBOX_MESSAGES_PER_LEVEL,
            101: [],
        },
        processed_level=100,
    )
    index = _index(FakeTzkt(), node)
    index._status = IndexStatus.realtime
    index._pending_outbox_levels.add(100)

    await index._drain_outbox_levels()

    assert node.requested == [100], 'level 101 is above what the node has applied and must not be fetched yet'
    assert await _pending_levels() == [101], 'the continuation level must outlive the process that queued it'

    # A restart takes it over — and still defers it until the node catches up.
    restarted = _index(FakeTzkt(), node)
    await restarted._pending_outbox_levels.load()
    assert set(restarted._pending_outbox_levels) == {101}

    restarted._status = IndexStatus.realtime
    await restarted._drain_outbox_levels()
    assert node.requested == [100], 'a restored level the node cannot answer for must stay deferred'

    node.processed_level = 101
    await restarted._drain_outbox_levels()
    assert node.requested == [100, 101], 'once the node applies it, the restored level is fetched'
    assert await _pending_levels() == []


async def test_pending_levels_survive_a_failed_drain(db: Any) -> None:
    """F5: the queue is stored before the inbox rows that move the cursor past its externals.

    The page here is the shape that made the production loss permanent: two external
    messages followed by a transfer, so the committed transfer's id is above both externals
    and the resume cursor can never reach them again.
    """
    page = [
        {'id': 100, 'level': 10, 'index': 0, 'type': 'external'},
        {'id': 101, 'level': 11, 'index': 0, 'type': 'external'},
        {
            'id': 102,
            'level': 12,
            'index': 0,
            'type': 'transfer',
            'target': {'address': ROLLUP},
            'parameter': {'entrypoint': 'default', 'value': {}},
        },
    ]
    node = FakeRollupNode({10: [], 11: _server_error()})
    index = _index(FakeTzkt([page], head_level=12), node)
    index._status = IndexStatus.syncing
    # What `_prepare_new_index` would have seeded on the way into `syncing`; without it the
    # drain would refuse every level as unreached and nothing would be fetched at all.

    with pytest.raises(aiohttp.ClientResponseError):
        await index._process()

    # The cursor is already past both externals...
    assert await RollupInboxMessage.filter(id=102).exists()
    # ...and the queue is what still knows the work is owed. Level 10 was drained but its
    # messages were never flushed, so it stays pending too.
    assert await _pending_levels() == [10, 11]

    # Restart on the same database: the cursor cannot reach the externals, the queue can.
    restarted = _index(FakeTzkt([[]], head_level=12), FakeRollupNode({10: [], 11: []}))
    await restarted._prepare_new_index()
    # At or above the last external, so `id.gt=` can never return either of them again. The
    # exact resume arithmetic is not this test's business — being out of reach is.
    assert restarted._inbox_id_cursor >= 101
    assert set(restarted._pending_outbox_levels) == {10, 11}

    await restarted._process()
    assert restarted._rollup_node.requested == [10, 11]  # type: ignore[attr-defined]
    assert await _pending_levels() == []


async def test_full_outbox_continuation_level_survives_its_failed_fetch(db: Any) -> None:
    """The continuation level of a full outbox is recoverable when its own fetch dies.

    `outbox_level + 1` is queued *during* the drain, after the durability write of the page
    that started it, so it is never in `dipdup_meta` under its own name. What keeps it
    reachable is its parent: level L only leaves the stored set once `bulk_create` has
    committed the rows it produced, and a drain that dies before that flush leaves L owed.
    The restart re-fetches L, the full outbox re-derives L+1, and both land.

    That is the invariant a per-level flush would break — committing L's rows mid-drain drops
    L from the stored set while L+1 exists nowhere but in the dead process's memory.
    """
    head = 101
    page = [
        # The external message that puts level 100 in the queue...
        {'id': 100, 'level': 100, 'index': 0, 'type': 'external'},
        # ...followed by a transfer, so the committed cursor is above it.
        {
            'id': 101,
            'level': 100,
            'index': 1,
            'type': 'transfer',
            'target': {'address': ROLLUP},
            'parameter': {'entrypoint': 'default', 'value': {}},
        },
    ]
    full_outbox = [_outbox_message(100, 0), _outbox_message(100, 1)]
    assert len(full_outbox) == MAX_OUTBOX_MESSAGES_PER_LEVEL

    node = FakeRollupNode({100: full_outbox, 101: _server_error()})
    index = _index(FakeTzkt([page], head_level=head), node)
    index._status = IndexStatus.syncing

    with pytest.raises(aiohttp.ClientResponseError):
        await index._process()

    # The full outbox at 100 asked for 101, and the chain had reached it — so it was fetched
    # in the same drain, and died there.
    assert node.requested == [100, 101]
    # Nothing was committed: the batch of level 100 went down with the process.
    assert await _outbox_rows() == []
    # ...which is exactly why level 100 must still be owed. It is the only reachable record
    # of level 101 as well.
    assert await _pending_levels() == [100]

    # Restart on the same database, with a rollup node that now answers for both levels.
    restarted_node = FakeRollupNode({100: full_outbox, 101: [_outbox_message(101, 0)]})
    restarted = _index(FakeTzkt([[]], head_level=head), restarted_node)
    await restarted._prepare_new_index()
    assert restarted._inbox_id_cursor >= 100, 'the cursor is past the external message of the failed page'
    assert set(restarted._pending_outbox_levels) == {100}, 'the stored set is what the cursor cannot describe'

    await restarted._process()

    assert restarted_node.requested == [100, 101], 'the continuation level is re-derived from its parent and picked back up'
    assert await _outbox_rows() == [(100, 0), (100, 1), (101, 0)], 'the messages of both levels land'
    assert await _pending_levels() == [], 'nothing is owed once the rows are committed'


async def test_drain_serves_the_lowest_level_and_stops_at_the_head(db: Any) -> None:
    """The drain asks only for what the chain has reached — and takes the lowest level first.

    Both levels are queued up front here, which is what a restored queue looks like: the
    ordering is a real choice, not a side effect of one level queueing the next. An arbitrary
    `pop()` would reach above the head and ask the rollup node for a block it cannot answer
    for, which is how a wedged node starts answering 500 for everything.
    """
    # 207/208 rather than any round pair: these are levels for which an unordered `pop()`
    # hands back the *higher* one first, so the wrong implementation is observably wrong
    # here instead of getting the right answer by luck.
    head = 207
    node = FakeRollupNode({207: [_outbox_message(207, 0)], 208: [_outbox_message(208, 0)]}, processed_level=head)
    index = _index(FakeTzkt(head_level=head), node)
    index._status = IndexStatus.realtime
    index._pending_outbox_levels.add(207)
    index._pending_outbox_levels.add(208)

    await index._drain_outbox_levels()

    assert node.requested == [207], 'level 208 is above what the node has applied and must not be asked for'
    assert await _outbox_rows() == [(207, 0)], 'only the reached level produced rows'
    assert await _pending_levels() == [208], 'the unreached level stays owed'

    # Once the node applies it, the same queue drains the rest.
    node.processed_level = 208
    await index._drain_outbox_levels()

    assert node.requested == [207, 208]
    assert await _outbox_rows() == [(207, 0), (208, 0)]
    assert await _pending_levels() == []


async def test_a_wedged_node_is_not_asked_for_levels_it_cannot_answer(db: Any) -> None:
    """The 2026-08-20 fault: a node stops applying L1 blocks and still answers RPC.

    Its `head/level` freezes and every level above that answers 500. The L1 chain, meanwhile,
    keeps producing — so a ceiling taken from the L1 head says "go ahead" for levels the node
    demonstrably cannot serve, and the drain dies on the first one.
    """
    node = FakeRollupNode({300: [_outbox_message(300, 0)], 301: [_outbox_message(301, 0)]}, processed_level=300)
    index = _index(FakeTzkt(head_level=999), node)
    index._status = IndexStatus.realtime
    index._pending_outbox_levels.add(300)
    index._pending_outbox_levels.add(301)

    await index._drain_outbox_levels()

    assert node.requested == [300], 'only the level the node has applied was asked for'
    assert await _outbox_rows() == [(300, 0)], 'the served level produced rows'
    assert await _pending_levels() == [301], 'the level the node cannot answer for stays owed'
