"""Contract: an L1 head rollback cannot take an outbox message out of the index for good.

The twin of `test_inbox_head_rollback.py`, on the side the inbox cursor does not cover.
`RollupMessageIndex` is pumped from `handlers/tezos/on_head.py`, so the outbox rows a drain
writes in realtime are written *inside* the `tezos_head` handler and journalled as
`ModelUpdate` rows tagged `(index=tezos_head, level=<head of that pass>)` — the head the drain
ran at, never the L1 level the messages belong to. `hooks/on_index_rollback.py` reverts every
one of those above the new head.

What makes the outbox side structurally different is where the work is remembered. An outbox
level is learned from an inbox `external` message and queued in `PendingOutboxLevels`, and the
level leaves that queue once its rows are committed. While the queue lived in `dipdup_meta` —
a table the rollback is blind to — one rollback deleted the journalled rows and left the queue
empty, and the inbox cursor, immune since the fix in #51, rewound only to the last surviving
inbox row, which is *above* the external that queued the level. No reader of any of the three
pieces of state could tell that the level was owed again. So the queue is journalled too now:
one `rollup_pending_outbox_level` row per owed level, reverted with the rows it stands for.

A lost outbox row is not a gap that fills itself: `bridge_matcher.check_pending_outbox` has
nothing to link the L2 withdrawal to, and `on_rollup_execute` finds no row for
`(outbox_level, message_index)`, logs and returns without creating the L1 withdrawal.

Offline: TzKT answers from a mutable universe by applying the filter in the URL, the rollup
node serves a per-level outbox and reports how far it has applied, and the rollback is DipDup's
own `ModelUpdate.revert` over the journal, selected exactly as `ctx.rollback` selects.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any

import pytest
from dipdup.models import IndexStatus
from dipdup.models import Meta
from dipdup.models import ModelUpdate
from dipdup.transactions import TransactionManager
from pytezos import MichelsonType
from pytezos import michelson_to_micheline

from rollup_bridge_indexer import models
from rollup_bridge_indexer.handlers.rollup_message import PendingOutboxLevels
from rollup_bridge_indexer.handlers.rollup_message import RollupMessageIndex
from rollup_bridge_indexer.handlers.ticket import FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType
from rollup_bridge_indexer.models import RollupOutboxMessage
from rollup_bridge_indexer.models import RollupPendingOutboxLevel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.anyio

ROLLUP = 'sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu'
ORIGINATION_LEVEL = 1
BLOCK_TIMESTAMP = '2026-01-01T00:00:00Z'

# The index whose handler the rollup-message index is pumped from, and therefore the one whose
# journal entries a rollback of the L1 head reverts.
HEAD_INDEX = 'tezos_head'
# Any non-zero depth opens a versioned transaction; TzKT's own default is 2.
ROLLBACK_DEPTH = 2

# Small enough that a two-message outbox counts as full and asks for its continuation.
MAX_OUTBOX_MESSAGES_PER_LEVEL = 2

FIRST_LEVEL = 10
# L: the level an `external` puts in the queue, and whose outbox the node serves.
OUTBOX_LEVEL = 12
# L + 1: what a full outbox at L asks for. No inbox message stands behind it.
CONTINUATION_LEVEL = OUTBOX_LEVEL + 1
# L + 2: the head the node has caught up by, so the drain runs — and the head that rolls back.
DRAIN_HEAD = OUTBOX_LEVEL + 2

_FAST_WITHDRAW_TYPE = MichelsonType.match(michelson_to_micheline(FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE))
TICKETER = 'KT1MJxf4KVN3sosR99VRG7WBbWTJtAyWUJt9'
RECEIVER = 'tz1burnburnburnburnburnburnburjAYjjX'


def _transfer(message_id: int, level: int) -> dict[str, Any]:
    """A transfer to the rollup — the backfill row every test starts from."""
    return {
        'id': message_id,
        'level': level,
        'index': 0,
        'type': RollupInboxMessageType.transfer.value,
        'target': {'address': ROLLUP},
        'parameter': {'entrypoint': 'default', 'value': {'int': str(message_id)}},
    }


def _external(message_id: int, level: int) -> dict[str, Any]:
    """An external — the only door an outbox level comes in by, and it leaves no row of its own."""
    return {'id': message_id, 'level': level, 'index': 0, 'type': RollupInboxMessageType.external.value}


def _outbox_message(level: int, index: int) -> dict[str, Any]:
    """A fast-withdrawal outbox message the hasher *can* read, so it becomes a row.

    The fast-withdrawal shape is the one whose hash needs no ticket lookup, which keeps
    `ticket_service` out of these tests while still exercising the real insert path. The
    withdrawal id differs per message, so the rows differ by more than their coordinates.
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


class UniverseTzkt:
    """Serves the inbox from a mutable universe by applying the filter it is handed.

    The universe grows between the passes: what the chain has produced by the time a pass runs
    is the whole difference between the backfill and the realtime passes after it. A message
    that is never asked for is a row that is never written, so the assertions are about rows.
    """

    def __init__(self, universe: list[dict[str, Any]]) -> None:
        self.universe = universe

    @staticmethod
    def _param(url: str, name: str) -> int | None:
        for part in url.split('?', 1)[-1].split('&'):
            key, _, value = part.partition('=')
            if key == name:
                return int(value)
        return None

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url.startswith('v1/smart_rollups/inbox'):
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


class FakeRollupNode:
    """`global/block/<level>/outbox/<level>/messages`, per level, and how far the node applied.

    `processed_level` is what the node answers for `global/block/head/level` — the ceiling the
    drain may ask below. A level queued before the node has applied it simply stays owed.
    """

    def __init__(self, responses: dict[int, Any], processed_level: int) -> None:
        self.responses = responses
        self.processed_level = processed_level
        self.requested: list[int] = []

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url == 'global/block/head/level':
            return self.processed_level
        level = int(url.split('/')[2])
        self.requested.append(level)
        assert level <= self.processed_level, f'asked for level {level}, above the {self.processed_level} the node has applied'
        return self.responses[level]


def _index(tzkt: UniverseTzkt, rollup_node: FakeRollupNode) -> RollupMessageIndex:
    # Stand-ins for the container's settings objects; only the fields below are read on these
    # paths, and `ticket_service` is never reached (every message here hashes as a fast one).
    bridge: Any = SimpleNamespace(smart_rollup_address=ROLLUP)
    protocol: Any = SimpleNamespace(
        smart_rollup_commitment_period=60,
        smart_rollup_challenge_window=40,
        smart_rollup_max_outbox_messages_per_level=MAX_OUTBOX_MESSAGES_PER_LEVEL,
    )
    datasource: Any = tzkt
    node: Any = rollup_node
    ticket_service: Any = None
    return RollupMessageIndex(
        tzkt=datasource,
        rollup_node=node,
        bridge=bridge,
        ticket_service=ticket_service,
        protocol=protocol,
        logger=logging.getLogger('test.rollup_message'),
    )


async def _backfilled_index(tzkt: UniverseTzkt, rollup_node: FakeRollupNode) -> RollupMessageIndex:
    """An index that has walked the universe it was handed and reached realtime.

    The backfill runs outside any versioned transaction, as `on_restart` does, so its rows are
    not journalled and no rollback below can touch them — which is what leaves the realtime
    passes as the only writers under test.
    """
    index = _index(tzkt, rollup_node)
    index._sync_first_level = FIRST_LEVEL
    await index.synchronize()
    assert index._status is IndexStatus.realtime, 'the empty page must end the backfill'
    return index


@asynccontextmanager
async def _head_handler(level: int) -> AsyncIterator[None]:
    """Run the block inside the versioned transaction DipDup opens for an index handler.

    Without one `ModelUpdate.from_model` returns None and nothing is journalled at all, so this
    is what makes the rollback below have anything to revert.
    """
    manager = TransactionManager(depth=ROLLBACK_DEPTH)
    async with manager.register(), manager.in_transaction(level=level, index=HEAD_INDEX):
        yield


async def _roll_back_head(to_level: int) -> int:
    """`ctx.rollback` on this database, run by DipDup's own code rather than imitated.

    `dipdup/context.py` reverts every journalled update above `to_level` for the index, newest
    first, and outside any versioned transaction so the reverts are not journalled themselves.
    Returns how many were reverted, which the callers assert on — an instrument that reverted
    nothing would let the tests pass for the wrong reason.
    """
    updates = await ModelUpdate.filter(level__gt=to_level, index=HEAD_INDEX).order_by('-id')
    for update in updates:
        await update.revert(getattr(models, update.model_name))
    return len(updates)


async def _outbox_rows() -> list[tuple[int, int]]:
    """(level, index) of every outbox row — the coordinates `on_rollup_execute` looks one up by."""
    return [(row.level, row.index) for row in await RollupOutboxMessage.all().order_by('level', 'index')]


async def _inbox_rows() -> list[tuple[int, int]]:
    """(id, level) of every inbox row. Level 0 is the cursor sentinel, not a message."""
    return [(message.id, message.level) for message in await RollupInboxMessage.all().order_by('id')]


async def _pending_levels() -> list[int]:
    """The owed outbox levels as the database holds them — one row each, and no other copy.

    Read through the model the index writes, so a revert of those rows is visible here exactly
    as the next pass will see it. The `dipdup_meta` key below is the store this queue used to
    live in; nothing writes it any more, and a leftover there would be read by nobody.
    """
    assert await Meta.get_or_none(key=PendingOutboxLevels.key) is None, 'the obsolete `dipdup_meta` queue was written again'
    return [row.level for row in await RollupPendingOutboxLevel.all().order_by('level')]


async def test_the_outbox_of_a_drained_level_comes_back_after_a_rollback(db: Any) -> None:
    """The main loss: a level drained at one head, rolled back at that head, is owed by nobody.

    The external arrives at its own level and the node has not applied it yet, so the level
    waits in the queue. Two blocks later the drain writes its messages and the level leaves the
    queue — and the rows carry the drain's head, not the level's, so a one-block rollback of
    that head deletes them. What the level's departure from the queue is worth afterwards is
    the whole question: the inbox cursor sits above the external that filled it, so if the
    queue does not come back with the rows, nothing asks for the level again.
    """
    tzkt = UniverseTzkt([_transfer(100, FIRST_LEVEL)])
    node = FakeRollupNode({OUTBOX_LEVEL: [_outbox_message(OUTBOX_LEVEL, 0)]}, processed_level=OUTBOX_LEVEL - 1)
    index = await _backfilled_index(tzkt, node)

    tzkt.universe.append(_external(101, OUTBOX_LEVEL))
    async with _head_handler(OUTBOX_LEVEL):
        await index.handle_realtime(OUTBOX_LEVEL)
    assert await _pending_levels() == [OUTBOX_LEVEL], 'the external queued its level'
    assert node.requested == [], 'the node has not applied that level yet, so it must not be asked'

    node.processed_level = OUTBOX_LEVEL
    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)
    assert node.requested == [OUTBOX_LEVEL]
    assert await _outbox_rows() == [(OUTBOX_LEVEL, 0)]
    assert await _pending_levels() == [], 'the level stops being owed once its rows are committed'

    # One INSERT for the outbox message, one DELETE of the queue row the drain retired.
    assert await _roll_back_head(to_level=DRAIN_HEAD - 1) == 2, 'the rollback reverted nothing, so it proves nothing'
    assert await _outbox_rows() == [], 'the journal revert is the instrument; it has to really remove the rows'

    # The chain re-delivers the head; the node still serves the level, so whether the messages
    # come back is entirely about whether anything still knows they are owed.
    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)

    assert await _outbox_rows() == [
        (OUTBOX_LEVEL, 0)
    ], 'the outbox messages of the rolled-back drain were never fetched again — the level left a queue no revert can refill'
    assert await _pending_levels() == [], 'and nothing is left owed afterwards'


async def test_a_rollback_before_the_drain_neither_loses_nor_duplicates_the_level(db: Any) -> None:
    """The guard: a rollback that hits the pass which *queued* the level must be harmless.

    Here the level is still owed when the head rolls back, and both halves of the state are
    journalled: the sentinel that moved the cursor past the external goes, and so does the
    queue row that same pass wrote. They therefore agree afterwards — the restored cursor
    re-walks the external, which queues the level again — and the drain that follows must
    produce the messages exactly once, not twice and not never.
    """
    tzkt = UniverseTzkt([_transfer(100, FIRST_LEVEL)])
    node = FakeRollupNode({OUTBOX_LEVEL: [_outbox_message(OUTBOX_LEVEL, 0)]}, processed_level=OUTBOX_LEVEL - 1)
    index = await _backfilled_index(tzkt, node)

    tzkt.universe.append(_external(101, OUTBOX_LEVEL))
    async with _head_handler(OUTBOX_LEVEL):
        await index.handle_realtime(OUTBOX_LEVEL)
    assert await _inbox_rows() == [(100, FIRST_LEVEL), (101, 0)], 'only a sentinel can carry a cursor that stopped on an external'
    assert await _pending_levels() == [OUTBOX_LEVEL]

    # One INSERT for the queue row, one for the sentinel: the same pass wrote both.
    assert await _roll_back_head(to_level=OUTBOX_LEVEL - 1) == 2, 'the rollback reverted nothing, so it proves nothing'
    assert await _inbox_rows() == [(100, FIRST_LEVEL)], 'the sentinel that carried the cursor past the external is gone'
    assert await _pending_levels() == [], 'the queue row of that pass went with it — the external is what owes the level again'

    node.processed_level = OUTBOX_LEVEL
    async with _head_handler(OUTBOX_LEVEL + 1):
        await index.handle_realtime(OUTBOX_LEVEL + 1)

    assert await _outbox_rows() == [(OUTBOX_LEVEL, 0)], 'the level was drained exactly once, whichever half of the state asked for it'
    assert await _pending_levels() == [], 'and it is not owed a second time'


async def test_a_rollback_of_a_full_outbox_drain_recovers_the_continuation_too(db: Any) -> None:
    """The continuation level: queued by a full outbox, reachable through nothing else.

    A full outbox at L asks for L + 1, which has no inbox message behind it — the queue is its
    only record, and by the time the rollback lands both levels have left it. Restoring L is
    therefore not enough on its own: the drain has to walk the full outbox at L again and
    re-derive L + 1 from it, which is why no cursor of this process may decide that a level it
    remembers writing has nothing left to give.
    """
    full_outbox = [_outbox_message(OUTBOX_LEVEL, 0), _outbox_message(OUTBOX_LEVEL, 1)]
    assert len(full_outbox) == MAX_OUTBOX_MESSAGES_PER_LEVEL

    tzkt = UniverseTzkt([_transfer(100, FIRST_LEVEL)])
    node = FakeRollupNode(
        {OUTBOX_LEVEL: full_outbox, CONTINUATION_LEVEL: [_outbox_message(CONTINUATION_LEVEL, 0)]},
        processed_level=OUTBOX_LEVEL - 1,
    )
    index = await _backfilled_index(tzkt, node)

    tzkt.universe.append(_external(101, OUTBOX_LEVEL))
    async with _head_handler(OUTBOX_LEVEL):
        await index.handle_realtime(OUTBOX_LEVEL)
    assert await _pending_levels() == [OUTBOX_LEVEL]

    node.processed_level = CONTINUATION_LEVEL
    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)
    assert node.requested == [OUTBOX_LEVEL, CONTINUATION_LEVEL], 'the full outbox asked for its continuation in the same drain'
    expected = [(OUTBOX_LEVEL, 0), (OUTBOX_LEVEL, 1), (CONTINUATION_LEVEL, 0)]
    assert await _outbox_rows() == expected
    assert await _pending_levels() == []

    # Three INSERTs for the messages, one DELETE of the queue row of the level that had them.
    assert await _roll_back_head(to_level=DRAIN_HEAD - 1) == 4, 'the rollback reverted nothing, so it proves nothing'
    assert await _outbox_rows() == [], 'the journal revert is the instrument; it has to really remove the rows'

    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)

    assert await _outbox_rows() == expected, 'the rolled-back drain has to be redone whole: the full level and the continuation it implies'
    assert await _pending_levels() == []


async def test_two_consecutive_rollbacks_after_a_drain_still_leave_the_messages(db: Any) -> None:
    """Two rollbacks in a row: whatever recovers the level has to survive being reverted itself.

    Mainnet sees rollbacks in bursts, and a recovery written at the intermediate head is
    journalled at *that* head — so the second rollback takes it away again. Recovering once is
    therefore not enough; the state that drives the recovery has to outlive its own revert.
    """
    tzkt = UniverseTzkt([_transfer(100, FIRST_LEVEL)])
    node = FakeRollupNode({OUTBOX_LEVEL: [_outbox_message(OUTBOX_LEVEL, 0)]}, processed_level=OUTBOX_LEVEL - 1)
    index = await _backfilled_index(tzkt, node)

    tzkt.universe.append(_external(101, OUTBOX_LEVEL))
    async with _head_handler(OUTBOX_LEVEL):
        await index.handle_realtime(OUTBOX_LEVEL)

    node.processed_level = OUTBOX_LEVEL
    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)
    assert await _outbox_rows() == [(OUTBOX_LEVEL, 0)]

    assert await _roll_back_head(to_level=DRAIN_HEAD - 1) == 2, 'the rollback reverted nothing, so it proves nothing'
    assert await _outbox_rows() == []

    # The head the first rollback settled on runs a pass of its own before rolling back too.
    async with _head_handler(DRAIN_HEAD - 1):
        await index.handle_realtime(DRAIN_HEAD - 1)

    await _roll_back_head(to_level=DRAIN_HEAD - 2)
    assert await _outbox_rows() == [], 'anything recovered at the intermediate head is reverted with it'

    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)

    assert await _outbox_rows() == [(OUTBOX_LEVEL, 0)], 'the level is still owed after the second rollback, and has to be drained again'
    assert await _pending_levels() == []
