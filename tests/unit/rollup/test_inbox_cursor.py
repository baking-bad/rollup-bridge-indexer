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

UNIVERSE_IDS = range(100, 106)


def _message(message_id: int) -> dict[str, Any]:
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

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        return []


def _index(tzkt: Any) -> RollupMessageIndex:
    bridge: Any = SimpleNamespace(smart_rollup_address=ROLLUP)
    protocol: Any = SimpleNamespace(
        smart_rollup_commitment_period=60,
        smart_rollup_challenge_window=40,
        smart_rollup_max_outbox_messages_per_level=100,
    )
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
    assert await _indexed_ids() == message_ids


async def test_a_restart_asks_for_the_message_after_the_last_saved_one(db: Any) -> None:
    await _seed([100, 101, 102])

    tzkt = UniverseTzkt(UNIVERSE)
    index = _index(tzkt)
    await index._prepare_new_index()
    await index._process()

    assert await _indexed_ids() == list(UNIVERSE_IDS), 'the restart lost the message it resumed on top of'


async def test_a_fresh_database_indexes_the_first_message_of_the_window(db: Any) -> None:
    tzkt = UniverseTzkt(UNIVERSE)
    index = _index(tzkt)
    index._sync_first_level = UNIVERSE[0]['level']
    await index._prepare_new_index()
    await index._process()

    assert await _indexed_ids() == list(UNIVERSE_IDS), 'the first message of the window was never requested'
