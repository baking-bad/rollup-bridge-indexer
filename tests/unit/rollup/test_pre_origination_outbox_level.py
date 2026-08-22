"""Contract: the inbox backfill survives an outbox level the rollup node will never serve.

A rollup node is complete from the origination of the rollup and no further back — asking it
for an outbox at an earlier level is not a transient fault, it is a question it can never
answer. The backfill can reach that far back: `first_ticket_level` is derived from ticket
activity, which predates the rollup, and TzKT's `external` inbox messages carry no rollup
field at all (the inbox is shared), so `_handle_external_inbox_message` queues the level of
every external it walks — including the ones below origination.

That is the level the drain serves first, because `take_lowest` serves the lowest. The node
answers 500, the error escapes `_drain_outbox_levels` and `_process`, and the process dies.

What makes it permanent rather than a crash is `PendingOutboxLevels`: the set is durable, so
the next boot restores exactly the same lowest level and dies on it again. Nothing in the
inbox cursor can move past it, and a reindex does not help — a fresh database walks the same
externals back into the same set. The backfill never reaches `dipdup_index` at all.

Offline: TzKT is a universe fake that applies the filter in the URL, and the rollup node is a
fake with the one property that matters here — a floor it cannot see below.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from dipdup.models import Meta

from rollup_bridge_indexer.handlers.rollup_message import PendingOutboxLevels
from rollup_bridge_indexer.handlers.rollup_message import RollupMessageIndex
from rollup_bridge_indexer.models import RollupInboxMessageType

pytestmark = pytest.mark.anyio

ROLLUP = 'sr1Ghq66tYK9y3r8CC1Tf8i8m5nxh8nTvZEf'

# The mainnet numbers this was observed with: the rollup was originated 143_442 levels above
# the first activity of the whitelisted tickets, and that is where the backfill starts.
ORIGINATION_LEVEL = 5_518_507
PRE_ORIGINATION_LEVEL = 5_375_065
SERVABLE_LEVEL = 5_518_600

UNIVERSE: list[dict[str, Any]] = [
    # An external below origination — no field on it says which rollup it belongs to, and none
    # can: externals are a shared-inbox construct.
    {'id': 1, 'level': PRE_ORIGINATION_LEVEL, 'index': 0, 'type': RollupInboxMessageType.external.value},
    # An external the node can answer for, so the backfill has visible work left to do.
    {'id': 2, 'level': SERVABLE_LEVEL, 'index': 0, 'type': RollupInboxMessageType.external.value},
    # A transfer, so the first boot commits an inbox row: that row is what makes the restart
    # take the resume branch of `_prepare_new_index` — the one that restores the pending set.
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
    """Serves the inbox from a fixed universe by applying the filter it is handed."""

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
    """A healthy, fully synced node — with the floor every rollup node has: its origination.

    `head/level` is high, so the drain's ceiling says go ahead. The refusal below the floor is
    not the wedged-node 500 of `test_outbox_level_durability`: no amount of waiting, retrying
    or reindexing turns it into an answer, because the data never existed.
    """

    def __init__(self, first_available_level: int) -> None:
        self.first_available_level = first_available_level
        self.requested: list[int] = []
        self.refused: list[int] = []

    async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
        if url == 'global/block/head/level':
            return 10**9
        level = int(url.split('/')[2])
        self.requested.append(level)
        if level < self.first_available_level:
            self.refused.append(level)
            # dipdup's `HttpDatasource` raises for status, retries `retry_count` times and then
            # re-raises this out of `request` — a 500 is not special-cased anywhere.
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
    # Stand-ins for the container's settings objects and the two datasources; only the fields
    # and URLs above are ever read on these paths, so the fakes are bound through `Any`.
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
    """One process lifetime: build the index the way a boot does, run one pass.

    Returns the outbox level the run died on, or None if it survived. The error is caught
    *here*, outside `_process`, precisely because it is not caught anywhere inside: in
    production it escapes `on_restart` and takes the process with it. Catching it is what lets
    this test ask the question a crash loop is about — what the NEXT boot does.
    """
    index = _index(node)
    try:
        await index._prepare_new_index()
        await index._process()
    except aiohttp.ClientResponseError:
        return node.refused[-1]
    return None


async def _owed_levels() -> list[int]:
    meta = await Meta.get_or_none(key=PendingOutboxLevels.key)
    return list(meta.value) if meta and meta.value else []


async def test_a_pre_origination_outbox_level_does_not_wedge_the_backfill(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A restart must get past a level the node will never serve. Today it cannot.

    `first_ticket_level` is a class attribute written by `TicketService`; monkeypatch restores
    it, or it would leak into every later test in the process.
    """
    monkeypatch.setattr(RollupMessageIndex, 'first_ticket_level', PRE_ORIGINATION_LEVEL)
    node = FlooredRollupNode(first_available_level=ORIGINATION_LEVEL)

    first_death = await _boot(node)
    owed_after_the_crash = await _owed_levels()

    # A second boot on the same database — the shape of a restart, and of every restart after
    # it. The durable pending set is what it inherits.
    second_death = await _boot(node)

    assert second_death is None, (
        f'the first boot died fetching outbox level {first_death}, {owed_after_the_crash} stayed owed in dipdup_meta, '
        f'and the restart died on level {second_death} again — the backfill can never leave this level'
    )
    assert await _owed_levels() == [], 'a level the node will never serve stays owed forever'
    assert SERVABLE_LEVEL in node.requested, 'the backfill never reached the levels the node can actually answer for'
