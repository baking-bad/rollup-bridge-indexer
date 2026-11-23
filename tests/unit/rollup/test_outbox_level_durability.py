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
from rollup_bridge_indexer.models import RollupInboxMessageType
from rollup_bridge_indexer.models import RollupOutboxMessage
from rollup_bridge_indexer.models import RollupPendingOutboxLevel

pytestmark = pytest.mark.anyio

ROLLUP = 'sr1BvTT7tVNneG2TeP18sixq57TxsZELebLu'
ORIGINATION_LEVEL = 1
BLOCK_TIMESTAMP = '2026-01-01T00:00:00Z'

MAX_OUTBOX_MESSAGES_PER_LEVEL = 2

UNHASHABLE_MESSAGE: dict[str, Any] = {'outbox_level': 0, 'message_index': 0, 'message': {}}

_FAST_WITHDRAW_TYPE = MichelsonType.match(michelson_to_micheline(FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE))
TICKETER = 'KT1MJxf4KVN3sosR99VRG7WBbWTJtAyWUJt9'
RECEIVER = 'tz1burnburnburnburnburnburnburjAYjjX'


def _outbox_message(level: int, index: int) -> dict[str, Any]:
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
    return aiohttp.ClientResponseError(request_info=None, history=(), status=500, message='Internal Server Error')  # type: ignore[arg-type]


class ProcessDied(Exception): ...


def _index(tzkt: Any, rollup_node: Any) -> RollupMessageIndex:
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
    assert await Meta.get_or_none(key=PendingOutboxLevels.key) is None, 'the obsolete `dipdup_meta` queue was written again'
    return [row.level for row in await RollupPendingOutboxLevel.all().order_by('level')]


async def test_full_outbox_continuation_level_is_deferred_and_stored(db: Any) -> None:
    node = FakeRollupNode(
        {
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

    restarted = _index(FakeTzkt(), node)
    await restarted._pending_outbox_levels.load(floor=ORIGINATION_LEVEL)
    assert set(restarted._pending_outbox_levels) == {101}

    restarted._status = IndexStatus.realtime
    await restarted._drain_outbox_levels()
    assert node.requested == [100], 'a restored level the node cannot answer for must stay deferred'

    node.processed_level = 101
    await restarted._drain_outbox_levels()
    assert node.requested == [100, 101], 'once the node applies it, the restored level is fetched'
    assert await _pending_levels() == []


async def test_pending_levels_survive_a_failed_drain(db: Any) -> None:
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
    node = FakeRollupNode({10: [], 11: ProcessDied()})
    index = _index(FakeTzkt([page], head_level=12), node)
    index._status = IndexStatus.syncing
    index._inbox_start_id = 0

    with pytest.raises(ProcessDied):
        await index._process()

    assert await RollupInboxMessage.filter(id=102).exists()
    assert await _pending_levels() == [10, 11]

    restarted = _index(FakeTzkt([[]], head_level=12), FakeRollupNode({10: [], 11: []}))
    await restarted._prepare_new_index()
    assert await restarted._inbox_cursor() >= 101
    assert set(restarted._pending_outbox_levels) == {10, 11}

    await restarted._process()
    assert restarted._rollup_node.requested == [10, 11]  # type: ignore[attr-defined]
    assert await _pending_levels() == []


async def test_full_outbox_continuation_level_survives_its_failed_fetch(db: Any) -> None:
    head = 101
    page = [
        {'id': 100, 'level': 100, 'index': 0, 'type': 'external'},
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

    node = FakeRollupNode({100: full_outbox, 101: ProcessDied()})
    index = _index(FakeTzkt([page], head_level=head), node)
    index._status = IndexStatus.syncing
    index._inbox_start_id = 0

    with pytest.raises(ProcessDied):
        await index._process()

    assert node.requested == [100, 101]
    assert await _outbox_rows() == []
    assert await _pending_levels() == [100]

    restarted_node = FakeRollupNode({100: full_outbox, 101: [_outbox_message(101, 0)]})
    restarted = _index(FakeTzkt([[]], head_level=head), restarted_node)
    await restarted._prepare_new_index()
    assert await restarted._inbox_cursor() >= 100, 'the cursor is past the external message of the failed page'
    assert set(restarted._pending_outbox_levels) == {100}, 'the stored set is what the cursor cannot describe'

    await restarted._process()

    assert restarted_node.requested == [100, 101], 'the continuation level is re-derived from its parent and picked back up'
    assert await _outbox_rows() == [(100, 0), (100, 1), (101, 0)], 'the messages of both levels land'
    assert await _pending_levels() == [], 'nothing is owed once the rows are committed'


async def test_drain_serves_the_lowest_level_and_stops_at_the_head(db: Any) -> None:
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

    node.processed_level = 208
    await index._drain_outbox_levels()

    assert node.requested == [207, 208]
    assert await _outbox_rows() == [(207, 0), (208, 0)]
    assert await _pending_levels() == []


async def test_a_wedged_node_is_not_asked_for_levels_it_cannot_answer(db: Any) -> None:
    node = FakeRollupNode({300: [_outbox_message(300, 0)], 301: [_outbox_message(301, 0)]}, processed_level=300)
    index = _index(FakeTzkt(head_level=999), node)
    index._status = IndexStatus.realtime
    index._pending_outbox_levels.add(300)
    index._pending_outbox_levels.add(301)

    await index._drain_outbox_levels()

    assert node.requested == [300], 'only the level the node has applied was asked for'
    assert await _outbox_rows() == [(300, 0)], 'the served level produced rows'
    assert await _pending_levels() == [301], 'the level the node cannot answer for stays owed'


async def test_a_full_level_owed_a_second_time_re_derives_its_continuation(db: Any) -> None:
    full_outbox = [_outbox_message(100, 0), _outbox_message(100, 1)]
    assert len(full_outbox) == MAX_OUTBOX_MESSAGES_PER_LEVEL

    node = FakeRollupNode({100: full_outbox, 101: [_outbox_message(101, 0)]}, processed_level=101)
    index = _index(FakeTzkt(), node)
    index._status = IndexStatus.realtime
    index._pending_outbox_levels.add(100)

    await index._drain_outbox_levels()
    stored = [(100, 0), (100, 1), (101, 0)]
    assert await _outbox_rows() == stored

    index._pending_outbox_levels.add(100)
    await index._drain_outbox_levels()

    assert node.requested == [100, 101, 100, 101], 'the second walk of the full level has to ask for its continuation again'
    assert await _outbox_rows() == stored, 'and it must not double the rows it walks past on the way'
    assert await _pending_levels() == [], 'nothing is owed once the rows are committed'


async def test_a_chain_of_full_outbox_levels_ends_at_the_first_level_that_is_not_full(db: Any) -> None:
    node = FakeRollupNode(
        {
            100: [_outbox_message(100, 0), _outbox_message(100, 1)],
            101: [_outbox_message(101, 0), _outbox_message(101, 1)],
            102: [_outbox_message(102, 0), _outbox_message(102, 1)],
            103: [],
        },
        processed_level=103,
    )
    index = _index(FakeTzkt(), node)
    index._status = IndexStatus.realtime
    index._pending_outbox_levels.add(100)

    await index._drain_outbox_levels()

    assert node.requested == [100, 101, 102, 103], 'each level of the chain is asked for exactly once, and the short one ends it'
    assert await _outbox_rows() == [(100, 0), (100, 1), (101, 0), (101, 1), (102, 0), (102, 1)]
    assert await _pending_levels() == [], 'a level whose outbox is empty owes nothing after it'


async def test_an_unbroken_chain_of_full_levels_stops_at_what_the_node_has_applied(db: Any) -> None:
    node = FakeRollupNode(
        {
            100: [_outbox_message(100, 0), _outbox_message(100, 1)],
            101: [_outbox_message(101, 0), _outbox_message(101, 1)],
        },
        processed_level=101,
    )
    index = _index(FakeTzkt(), node)
    index._status = IndexStatus.realtime
    index._pending_outbox_levels.add(100)

    await index._drain_outbox_levels()

    assert node.requested == [100, 101], 'the drain stops at the ceiling instead of walking past it'
    assert await _pending_levels() == [102], 'the continuation the node cannot answer for yet is owed, and waits for it'


async def test_the_queue_of_the_dipdup_meta_version_is_adopted_on_the_first_boot(db: Any) -> None:
    await RollupInboxMessage.create(
        id=500,
        level=ORIGINATION_LEVEL,
        index=0,
        type=RollupInboxMessageType.transfer,
        message={},
        parameters_hash=None,
    )
    await Meta.update_or_create(key=PendingOutboxLevels.key, defaults={'value': [50, 100, 101]})

    index = _index(FakeTzkt(), FakeRollupNode({}))
    index._origination_level = 100
    await index._prepare_new_index()

    assert await _pending_levels() == [100, 101], 'the owed levels are rows now, and the key they came from is gone'
    assert set(index._pending_outbox_levels) == {100, 101}, 'and the running index owes what the rows say'

    restarted = _index(FakeTzkt(), FakeRollupNode({}))
    restarted._origination_level = 100
    await restarted._prepare_new_index()

    assert await _pending_levels() == [100, 101], 'the second boot is a no-op'
    assert set(restarted._pending_outbox_levels) == {100, 101}


def _connection_refused() -> aiohttp.ClientConnectorError:
    return aiohttp.ClientConnectorError(SimpleNamespace(ssl=None, host='rollup-node', port=443), OSError(111, 'Connection refused'))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    'failure',
    [_server_error, _connection_refused, TimeoutError],
    ids=['500', 'connection-refused', 'timeout'],
)
async def test_a_failed_level_fetch_ends_the_pass_and_stays_owed(db: Any, failure: Any) -> None:
    node = FakeRollupNode({10: [_outbox_message(10, 0)], 11: failure(), 12: [_outbox_message(12, 0)]})
    index = _index(FakeTzkt(head_level=12), node)
    index._status = IndexStatus.realtime
    for level in (10, 11, 12):
        index._pending_outbox_levels.add(level)

    assert await index._drain_outbox_levels() is True, 'the rows of level 10 were committed, so the matcher has work'

    assert node.requested == [10, 11], 'the pass ends at the level the node failed on'
    assert await _outbox_rows() == [(10, 0)]
    assert await _pending_levels() == [11, 12]

    node.responses[11] = [_outbox_message(11, 0)]
    await index._drain_outbox_levels()

    assert node.requested == [10, 11, 11, 12]
    assert await _outbox_rows() == [(10, 0), (11, 0), (12, 0)]
    assert await _pending_levels() == []


async def test_a_failed_continuation_fetch_keeps_the_continuation_owed(db: Any) -> None:
    full_outbox = [_outbox_message(100, 0), _outbox_message(100, 1)]
    node = FakeRollupNode({100: full_outbox, 101: _server_error()})
    index = _index(FakeTzkt(head_level=101), node)
    index._status = IndexStatus.realtime
    index._pending_outbox_levels.add(100)

    await index._drain_outbox_levels()

    assert node.requested == [100, 101]
    assert await _outbox_rows() == [(100, 0), (100, 1)]
    assert await _pending_levels() == [101]

    restarted_node = FakeRollupNode({101: [_outbox_message(101, 0)]})
    restarted = _index(FakeTzkt(head_level=101), restarted_node)
    restarted._status = IndexStatus.realtime
    await restarted._drain_outbox_levels()

    assert restarted_node.requested == [101]
    assert await _outbox_rows() == [(100, 0), (100, 1), (101, 0)]
    assert await _pending_levels() == []


async def test_a_failed_head_level_request_leaves_the_queue_untouched(db: Any) -> None:

    class NodeWithoutHead(FakeRollupNode):
        async def request(self, method: Any = None, url: Any = None, **kwargs: Any) -> Any:
            if url == 'global/block/head/level':
                raise _connection_refused()
            return await super().request(method, url, **kwargs)

    node = NodeWithoutHead({10: [_outbox_message(10, 0)]})
    index = _index(FakeTzkt(head_level=10), node)
    index._status = IndexStatus.realtime
    index._pending_outbox_levels.add(10)

    assert await index._drain_outbox_levels() is False

    assert node.requested == []
    assert await _pending_levels() == [10]
