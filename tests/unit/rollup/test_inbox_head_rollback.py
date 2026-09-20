"""Contract: an L1 head rollback cannot take an inbox message out of the index for good.

`RollupMessageIndex` is pumped from `handlers/tezos/on_head.py`, so every row it writes in
realtime is written *inside* the `tezos_head` index handler. DipDup journals those writes as
`ModelUpdate` rows tagged `(index=tezos_head, level=<head level>)` — `bulk_create`, `create`
and `filter().delete()` alike. When TzKT rolls the head back, `hooks/on_index_rollback.py`
calls `ctx.rollback`, which reverts every journalled update above the new level: the page's
inbox rows are deleted again and the level-0 cursor sentinel goes back to what it was.

So the database has a second writer, and it writes *backwards*. A cursor that lives only in
the process stays where the reverted pass left it, `id.gt=` asks for messages above the hole,
and the reverted messages are never requested by anybody again. Nothing reports it: the index
is `realtime`, `dipdup_head` is at the chain head, and the L1 deposit — re-delivered and
re-created by TzKT's own rollback handling — simply never matches, because every L2-deposit
matcher step needs `bridge_deposit.inbox_message` attached first.

`dipdup_meta` is not journalled, so `PendingOutboxLevels` does not move; the divergence is
between the cursor and `rollup_inbox_message` alone.

Offline: the fake TzKT answers *from a universe* by applying the filter in the URL, so a
message that is never asked for is a row that is never written — the assertions are about the
rows, not about the URLs. The rollback is not imitated either: the tests open the same
versioned transaction DipDup opens for a handler, and then run DipDup's own
`ModelUpdate.revert` over the journal exactly as `ctx.rollback` does.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any

import pytest
from dipdup.models import IndexStatus
from dipdup.models import ModelUpdate
from dipdup.transactions import TransactionManager

from rollup_bridge_indexer import models
from rollup_bridge_indexer.handlers.rollup_message import RollupMessageIndex
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.anyio

ROLLUP = 'sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu'
ORIGINATION_LEVEL = 1

# The index whose handler the rollup-message index is pumped from, and therefore the one whose
# journal entries a rollback of the L1 head reverts.
HEAD_INDEX = 'tezos_head'
# Any non-zero depth opens a versioned transaction; TzKT's own default is 2.
ROLLBACK_DEPTH = 2

FIRST_LEVEL = 10
ROLLED_BACK_LEVEL = 12


def _transfer(message_id: int, level: int) -> dict[str, Any]:
    """A transfer to the rollup — indexed unconditionally, so presence is decided by the fetch."""
    return {
        'id': message_id,
        'level': level,
        'index': 0,
        'type': RollupInboxMessageType.transfer.value,
        'target': {'address': ROLLUP},
        'parameter': {'entrypoint': 'default', 'value': {'int': str(message_id)}},
    }


def _external(message_id: int, level: int) -> dict[str, Any]:
    """An external — no row of its own, so the cursor it moves is carried by the sentinel."""
    return {'id': message_id, 'level': level, 'index': 0, 'type': RollupInboxMessageType.external.value}


class UniverseTzkt:
    """Serves the inbox from a mutable universe by applying the filter it is handed.

    The universe grows between the passes: what the chain has produced by the time a pass runs
    is the whole difference between a backfill pass and the realtime pass after it.
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
        raise AssertionError(f'unexpected TzKT request: {url}')


class SilentRollupNode:
    """No outbox messages at any level, and every level applied: the inbox side is under test."""

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url == 'global/block/head/level':
            return 10**9
        return []


def _index(tzkt: UniverseTzkt) -> RollupMessageIndex:
    # Stand-ins for the container's settings objects; nothing on the inbox path reads more than
    # the fields below, and no outbox message ever reaches the ticket service.
    bridge: Any = SimpleNamespace(smart_rollup_address=ROLLUP)
    protocol: Any = SimpleNamespace(
        smart_rollup_commitment_period=60,
        smart_rollup_challenge_window=40,
        smart_rollup_max_outbox_messages_per_level=100,
    )
    datasource: Any = tzkt
    rollup_node: Any = SilentRollupNode()
    ticket_service: Any = None
    return RollupMessageIndex(
        tzkt=datasource,
        rollup_node=rollup_node,
        bridge=bridge,
        ticket_service=ticket_service,
        protocol=protocol,
        logger=logging.getLogger('test.rollup_message'),
    )


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


async def _rows() -> list[tuple[int, int]]:
    """(id, level) of every inbox row. Level 0 is the cursor sentinel, not a message."""
    return [(message.id, message.level) for message in await RollupInboxMessage.all().order_by('id')]


async def test_the_message_of_a_rolled_back_head_is_asked_for_again(db: Any) -> None:
    """The plain shape: one transfer arrives at a head, the head is rolled back, it must return.

    The backfill happens outside any versioned transaction, as `on_restart` does, so those rows
    are not journalled and the rollback cannot touch them — which is also what makes them the
    resume point the next pass has to find.
    """
    tzkt = UniverseTzkt([_transfer(100, FIRST_LEVEL), _transfer(101, 11)])
    index = _index(tzkt)
    index._sync_first_level = FIRST_LEVEL
    await index.synchronize()

    assert index._status is IndexStatus.realtime, 'the empty page must end the backfill'
    assert await _rows() == [(100, FIRST_LEVEL), (101, 11)]

    tzkt.universe.append(_transfer(102, ROLLED_BACK_LEVEL))
    async with _head_handler(ROLLED_BACK_LEVEL):
        await index.handle_realtime(ROLLED_BACK_LEVEL)
    assert await _rows() == [(100, FIRST_LEVEL), (101, 11), (102, ROLLED_BACK_LEVEL)]

    assert await _roll_back_head(to_level=ROLLED_BACK_LEVEL - 1) == 1, 'the rollback reverted nothing, so it proves nothing'
    assert await _rows() == [(100, FIRST_LEVEL), (101, 11)], 'the journal revert is the instrument; it has to really remove the row'

    # The chain re-delivers the head; the fake still serves the message, so whether it comes
    # back is entirely about which id the walk resumes from.
    async with _head_handler(ROLLED_BACK_LEVEL):
        await index.handle_realtime(ROLLED_BACK_LEVEL)

    assert await _rows() == [
        (100, FIRST_LEVEL),
        (101, 11),
        (102, ROLLED_BACK_LEVEL),
    ], 'the message of the rolled-back head was never requested again — the cursor stayed above the hole the revert opened'


async def test_a_rolled_back_sentinel_takes_the_cursor_back_with_it(db: Any) -> None:
    """The sentinel shape: the reverted pass ended on an `external`, so no row carries its cursor.

    A level-0 sentinel is how such a cursor is stored, and writing it deletes the previous one —
    both journalled, so the revert restores the earlier sentinel. That restored row is the only
    record of where the walk may resume, and the pass after the rollback has to honour it
    without re-inserting anything the revert left alone (the ids are the primary key).
    """
    tzkt = UniverseTzkt([_transfer(100, FIRST_LEVEL), _external(101, 11)])
    index = _index(tzkt)
    index._sync_first_level = FIRST_LEVEL
    await index.synchronize()

    assert await _rows() == [(100, FIRST_LEVEL), (101, 0)], 'the external moved the cursor, and only a sentinel can carry it'

    tzkt.universe += [_transfer(102, ROLLED_BACK_LEVEL), _external(103, ROLLED_BACK_LEVEL)]
    async with _head_handler(ROLLED_BACK_LEVEL):
        await index.handle_realtime(ROLLED_BACK_LEVEL)
    assert await _rows() == [
        (100, FIRST_LEVEL),
        (102, ROLLED_BACK_LEVEL),
        (103, 0),
    ], 'the sentinel moved up to the external of the new page'

    # One INSERT for the row, one DELETE of the old sentinel and one INSERT of the new one.
    assert await _roll_back_head(to_level=ROLLED_BACK_LEVEL - 1) == 3, 'the rollback reverted nothing, so it proves nothing'
    assert await _rows() == [(100, FIRST_LEVEL), (101, 0)], 'the revert has to put the earlier sentinel back, not just drop the later one'

    async with _head_handler(ROLLED_BACK_LEVEL):
        await index.handle_realtime(ROLLED_BACK_LEVEL)

    assert await _rows() == [(100, FIRST_LEVEL), (102, ROLLED_BACK_LEVEL), (103, 0)], (
        'the pass after the rollback must re-walk exactly what the revert removed: the transfer back as a row, '
        'the cursor back on the external, and the untouched row left alone'
    )
