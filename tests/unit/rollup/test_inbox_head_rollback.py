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

HEAD_INDEX = 'tezos_head'
ROLLBACK_DEPTH = 2

FIRST_LEVEL = 10
ROLLED_BACK_LEVEL = 12


def _transfer(message_id: int, level: int) -> dict[str, Any]:
    return {
        'id': message_id,
        'level': level,
        'index': 0,
        'type': RollupInboxMessageType.transfer.value,
        'target': {'address': ROLLUP},
        'parameter': {'entrypoint': 'default', 'value': {'int': str(message_id)}},
    }


def _external(message_id: int, level: int) -> dict[str, Any]:
    return {'id': message_id, 'level': level, 'index': 0, 'type': RollupInboxMessageType.external.value}


class UniverseTzkt:

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

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url == 'global/block/head/level':
            return 10**9
        return []


def _index(tzkt: UniverseTzkt) -> RollupMessageIndex:
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
    manager = TransactionManager(depth=ROLLBACK_DEPTH)
    async with manager.register(), manager.in_transaction(level=level, index=HEAD_INDEX):
        yield


async def _roll_back_head(to_level: int) -> int:
    updates = await ModelUpdate.filter(level__gt=to_level, index=HEAD_INDEX).order_by('-id')
    for update in updates:
        await update.revert(getattr(models, update.model_name))
    return len(updates)


async def _rows() -> list[tuple[int, int]]:
    return [(message.id, message.level) for message in await RollupInboxMessage.all().order_by('id')]


async def test_the_message_of_a_rolled_back_head_is_asked_for_again(db: Any) -> None:
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

    async with _head_handler(ROLLED_BACK_LEVEL):
        await index.handle_realtime(ROLLED_BACK_LEVEL)

    assert await _rows() == [
        (100, FIRST_LEVEL),
        (101, 11),
        (102, ROLLED_BACK_LEVEL),
    ], 'the message of the rolled-back head was never requested again — the cursor stayed above the hole the revert opened'


async def test_a_rolled_back_sentinel_takes_the_cursor_back_with_it(db: Any) -> None:
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

    assert await _roll_back_head(to_level=ROLLED_BACK_LEVEL - 1) == 5, 'the rollback reverted nothing, so it proves nothing'
    assert await _rows() == [(100, FIRST_LEVEL), (101, 0)], 'the revert has to put the earlier sentinel back, not just drop the later one'

    async with _head_handler(ROLLED_BACK_LEVEL):
        await index.handle_realtime(ROLLED_BACK_LEVEL)

    assert await _rows() == [(100, FIRST_LEVEL), (102, ROLLED_BACK_LEVEL), (103, 0)], (
        'the pass after the rollback must re-walk exactly what the revert removed: the transfer back as a row, '
        'the cursor back on the external, and the untouched row left alone'
    )


async def test_a_revert_that_empties_the_table_resumes_from_the_window(db: Any) -> None:
    tzkt = UniverseTzkt([_transfer(50, FIRST_LEVEL - 5), _transfer(100, FIRST_LEVEL), _transfer(101, 11)])

    writer = _index(tzkt)
    writer._sync_first_level = FIRST_LEVEL
    async with _head_handler(ROLLED_BACK_LEVEL):
        await writer._prepare_new_index()
        await writer._process()
    assert await _rows() == [(100, FIRST_LEVEL), (101, 11)]

    index = _index(tzkt)
    index._sync_first_level = FIRST_LEVEL
    await index.synchronize()
    assert index._status is IndexStatus.realtime

    assert await _roll_back_head(to_level=ROLLED_BACK_LEVEL - 1) == 2, 'the rollback reverted nothing, so it proves nothing'
    assert await _rows() == [], 'the revert did not empty the table, so this is not the case under test'

    async with _head_handler(ROLLED_BACK_LEVEL):
        await index.handle_realtime(ROLLED_BACK_LEVEL)

    assert await _rows() == [
        (100, FIRST_LEVEL),
        (101, 11),
    ], 'the walk restarted from the head of the shared inbox instead of the configured window'
