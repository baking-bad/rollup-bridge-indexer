from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from dipdup.models import IndexStatus

from rollup_bridge_indexer.handlers.rollup_message import RollupMessageIndex
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType

pytestmark = pytest.mark.anyio

ROLLUP = 'sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu'
FOREIGN_ROLLUP = 'sr1TCYofXUuJjmQvZ26XE4YAwXdfetQfZ6rR'
ORIGINATION_LEVEL = 1
FIRST_LEVEL = 10


def _transfer(message_id: int, target: str) -> dict[str, Any]:
    return {
        'id': message_id,
        'level': message_id - 90,
        'index': 0,
        'type': RollupInboxMessageType.transfer.value,
        'target': {'address': target},
        'parameter': {'entrypoint': 'default', 'value': {'int': str(message_id)}},
    }


class SharedInboxTzkt:

    def __init__(self, universe: list[dict[str, Any]]) -> None:
        self.universe = universe
        self.served_ids: set[int] = set()

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
            page = page[:limit]
            self.served_ids.update(m['id'] for m in page)
            return page
        if url.startswith('v1/smart_rollups/'):
            return {'firstActivity': ORIGINATION_LEVEL}
        raise AssertionError(f'unexpected TzKT request: {url}')


class SilentRollupNode:

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url == 'global/block/head/level':
            return 10**9
        return []


def _index(tzkt: SharedInboxTzkt) -> RollupMessageIndex:
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


async def _transfer_rows() -> list[int]:
    return [m.id for m in await RollupInboxMessage.filter(type=RollupInboxMessageType.transfer).order_by('id')]


async def test_a_transfer_to_another_rollup_never_becomes_our_inbox_message(db: Any) -> None:
    tzkt = SharedInboxTzkt(
        [
            _transfer(100, ROLLUP),
            _transfer(101, FOREIGN_ROLLUP),
            _transfer(102, ROLLUP),
        ]
    )
    index = _index(tzkt)
    index._sync_first_level = FIRST_LEVEL
    await index.synchronize()

    assert index._status is IndexStatus.realtime
    assert 101 in tzkt.served_ids, 'the fake inbox never served the foreign transfer, so the guard was not exercised'
    assert await _transfer_rows() == [100, 102], 'the backfill stored a transfer addressed to another rollup'

    tzkt.universe += [_transfer(103, FOREIGN_ROLLUP), _transfer(104, ROLLUP), _transfer(105, FOREIGN_ROLLUP)]
    await index.handle_realtime(tzkt.universe[-1]['level'])

    assert {103, 105} <= tzkt.served_ids, 'the fake inbox never served the foreign transfers, so the guard was not exercised'
    assert await _transfer_rows() == [100, 102, 104], 'the realtime pass stored a transfer addressed to another rollup'
