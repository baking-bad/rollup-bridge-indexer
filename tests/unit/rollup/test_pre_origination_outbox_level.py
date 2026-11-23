from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from dipdup.models import Meta

from rollup_bridge_indexer.handlers.rollup_message import PendingOutboxLevels
from rollup_bridge_indexer.handlers.rollup_message import RollupMessageIndex
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType
from rollup_bridge_indexer.models import RollupPendingOutboxLevel

pytestmark = pytest.mark.anyio

ROLLUP = 'sr1Ghq66tYK9y3r8CC1Tf8i8m5nxh8nTvZEf'

ORIGINATION_LEVEL = 5_518_507
PRE_ORIGINATION_LEVEL = 5_375_065
SERVABLE_LEVEL = 5_518_600

APPLIED_LEVEL = 5_518_700
UNAPPLIED_LEVEL = 5_518_900

UNIVERSE: list[dict[str, Any]] = [
    {'id': 1, 'level': PRE_ORIGINATION_LEVEL, 'index': 0, 'type': RollupInboxMessageType.external.value},
    {'id': 2, 'level': SERVABLE_LEVEL, 'index': 0, 'type': RollupInboxMessageType.external.value},
    {
        'id': 3,
        'level': SERVABLE_LEVEL + 1,
        'index': 0,
        'type': RollupInboxMessageType.transfer.value,
        'target': {'address': ROLLUP},
        'parameter': {'entrypoint': 'default', 'value': {}},
    },
]


class UniverseTzkt:

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
            limit = self._param(url, 'limit') or len(UNIVERSE)
            page = [m for m in UNIVERSE if (id_gt is None or m['id'] > id_gt) and (level_ge is None or m['level'] >= level_ge)]
            return page[:limit]
        if url.startswith('v1/smart_rollups/'):
            return {'firstActivity': ORIGINATION_LEVEL}
        raise AssertionError(f'unexpected TzKT request: {url}')


class FlooredRollupNode:

    def __init__(self, first_available_level: int, applied_level: int = 10**9) -> None:
        self.first_available_level = first_available_level
        self.applied_level = applied_level
        self.requested: list[int] = []
        self.refused: list[int] = []

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url == 'global/block/head/level':
            return self.applied_level
        level = int(url.split('/')[2])
        self.requested.append(level)
        if level < self.first_available_level:
            self.refused.append(level)
            raise aiohttp.ClientResponseError(
                request_info=None,  # type: ignore[arg-type]
                history=(),
                status=500,
                message=(
                    f'Failure("Could not load block directory for block {level}: Error: Attempting to access data for '
                    f'level {level}, which is before the first available level {self.first_available_level}")'
                ),
            )
        return []


def _index(node: FlooredRollupNode) -> RollupMessageIndex:
    tzkt: Any = UniverseTzkt()
    rollup_node: Any = node
    bridge: Any = SimpleNamespace(smart_rollup_address=ROLLUP)
    protocol: Any = SimpleNamespace(
        smart_rollup_commitment_period=60,
        smart_rollup_challenge_window=40,
        smart_rollup_max_outbox_messages_per_level=100,
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


async def _boot(node: FlooredRollupNode) -> int | None:
    index = _index(node)
    try:
        await index._prepare_new_index()
        await index._process()
    except aiohttp.ClientResponseError:
        return node.refused[-1]
    return None


async def _owed_levels() -> list[int]:
    assert await Meta.get_or_none(key=PendingOutboxLevels.key) is None, 'the obsolete `dipdup_meta` queue was written again'
    return [row.level for row in await RollupPendingOutboxLevel.all().order_by('level')]


async def test_a_pre_origination_outbox_level_does_not_wedge_the_backfill(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RollupMessageIndex, 'first_ticket_level', PRE_ORIGINATION_LEVEL)
    node = FlooredRollupNode(first_available_level=ORIGINATION_LEVEL)

    first_death = await _boot(node)
    owed_after_the_crash = await _owed_levels()

    second_death = await _boot(node)

    assert second_death is None, (
        f'the first boot died fetching outbox level {first_death}, {owed_after_the_crash} stayed owed in the queue, '
        f'and the restart died on level {second_death} again — the backfill can never leave this level'
    )
    assert await _owed_levels() == [], 'a level the node will never serve stays owed forever'
    assert SERVABLE_LEVEL in node.requested, 'the backfill never reached the levels the node can actually answer for'


async def _wedge_the_database() -> None:
    committed = UNIVERSE[-1]
    await RollupInboxMessage.create(
        id=committed['id'],
        level=committed['level'],
        index=committed['index'],
        type=RollupInboxMessageType.transfer,
        message=committed['parameter'],
        parameters_hash=None,
    )
    owed = PendingOutboxLevels(logging.getLogger('test.rollup_message'))
    for level in (PRE_ORIGINATION_LEVEL, PRE_ORIGINATION_LEVEL + 1, APPLIED_LEVEL, UNAPPLIED_LEVEL):
        owed.add(level)
    await owed.save()


async def test_a_wedged_database_heals_its_impossible_owed_levels_on_boot(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RollupMessageIndex, 'first_ticket_level', PRE_ORIGINATION_LEVEL)
    await _wedge_the_database()
    node = FlooredRollupNode(first_available_level=ORIGINATION_LEVEL, applied_level=APPLIED_LEVEL)

    index = _index(node)
    try:
        await index._prepare_new_index()
        await index._process()
    except aiohttp.ClientResponseError as error:
        pytest.fail(f'the boot died fetching outbox level {node.refused[-1]}, exactly as the previous one did: {error.message}')

    assert index._inbox_start_id is None, 'the database read as wiped, so the clamp ran and the floor was never asked for'
    assert await index._inbox_cursor() == UNIVERSE[-1]['id']
    assert node.refused == [], f'the node was asked for {node.refused}, levels below its origination that it can never answer'
    assert await _owed_levels() == [UNAPPLIED_LEVEL], (
        'what survives the boot must be exactly the owed work that is still possible: the impossible levels dropped, '
        'the one the node has not applied yet still owed'
    )
    assert node.requested == [APPLIED_LEVEL], 'the drain never got past the impossible levels to the real work behind them'


async def _wedge_the_cursor_below_origination() -> None:
    await RollupInboxMessage.create(
        id=UNIVERSE[0]['id'] - 1,
        level=0,
        index=0,
        message={},
        parameters_hash=None,
        type=RollupInboxMessageType.external,
    )
    owed = PendingOutboxLevels(logging.getLogger('test.rollup_message'))
    owed.add(PRE_ORIGINATION_LEVEL)
    await owed.save()


async def test_a_cursor_below_origination_cannot_re_add_an_impossible_level(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RollupMessageIndex, 'first_ticket_level', PRE_ORIGINATION_LEVEL)
    await _wedge_the_cursor_below_origination()
    node = FlooredRollupNode(first_available_level=ORIGINATION_LEVEL)

    death = await _boot(node)

    assert death is None, f'the boot died fetching outbox level {death} — the level `load` had just dropped, and the walk owed it again'
    assert node.refused == [], f'the node was asked for {node.refused}, levels below its origination that it can never answer'
    assert PRE_ORIGINATION_LEVEL not in await _owed_levels(), 'an impossible level was written back into the durable set for the next boot'
    assert SERVABLE_LEVEL in node.requested, 'the backfill never reached the levels the node can actually answer for'
