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
from dipdup.models import ModelUpdateAction
from dipdup.transactions import TransactionManager
from pytezos import MichelsonType
from pytezos import michelson_to_micheline

from rollup_bridge_indexer import models
from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks
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

HEAD_INDEX = 'tezos_head'
ROLLBACK_DEPTH = 2

MAX_OUTBOX_MESSAGES_PER_LEVEL = 2

FIRST_LEVEL = 10
OUTBOX_LEVEL = 12
CONTINUATION_LEVEL = OUTBOX_LEVEL + 1
DRAIN_HEAD = OUTBOX_LEVEL + 2

LOGGER_NAME = 'test.rollup_message'

_FAST_WITHDRAW_TYPE = MichelsonType.match(michelson_to_micheline(FAST_WITHDRAW_MICHELSON_OUTBOX_MESSAGE_INTERFACE))
TICKETER = 'KT1MJxf4KVN3sosR99VRG7WBbWTJtAyWUJt9'
RECEIVER = 'tz1burnburnburnburnburnburnburjAYjjX'


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
        if url.startswith('v1/blocks/'):
            return BLOCK_TIMESTAMP
        raise AssertionError(f'unexpected TzKT request: {url}')


class FakeRollupNode:

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
        logger=logging.getLogger(LOGGER_NAME),
    )


async def _backfilled_index(tzkt: UniverseTzkt, rollup_node: FakeRollupNode) -> RollupMessageIndex:
    index = _index(tzkt, rollup_node)
    index._sync_first_level = FIRST_LEVEL
    await index.synchronize()
    assert index._status is IndexStatus.realtime, 'the empty page must end the backfill'
    return index


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


async def _outbox_rows() -> list[tuple[int, int]]:
    return [(row.level, row.index) for row in await RollupOutboxMessage.all().order_by('level', 'index')]


async def _inbox_rows() -> list[tuple[int, int]]:
    return [(message.id, message.level) for message in await RollupInboxMessage.all().order_by('id')]


async def _pending_levels() -> list[int]:
    assert await Meta.get_or_none(key=PendingOutboxLevels.key) is None, 'the obsolete `dipdup_meta` queue was written again'
    return [row.level for row in await RollupPendingOutboxLevel.all().order_by('level')]


async def test_the_outbox_of_a_drained_level_comes_back_after_a_rollback(db: Any, caplog: pytest.LogCaptureFixture) -> None:
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

    assert await _roll_back_head(to_level=DRAIN_HEAD - 1) == 2, 'the rollback reverted nothing, so it proves nothing'
    assert await _outbox_rows() == [], 'the journal revert is the instrument; it has to really remove the rows'

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        async with _head_handler(DRAIN_HEAD):
            await index.handle_realtime(DRAIN_HEAD)

    assert [record.getMessage() for record in caplog.records if 'rollback returned' in record.getMessage()] == [
        f'An L1 head rollback returned outbox level(s) [{OUTBOX_LEVEL}] to the queue.'
    ], 'a recovery nobody can grep for is a fix nobody can confirm in production'

    assert await _outbox_rows() == [
        (OUTBOX_LEVEL, 0)
    ], 'the outbox messages of the rolled-back drain were never fetched again — the level left a queue no revert can refill'
    assert await _pending_levels() == [], 'and nothing is left owed afterwards'


def _outbox_matcher_steps_armed() -> dict[str, bool]:
    return {
        'outbox': BridgeMatcherLocks.pending_outbox,
        'tezos_withdrawals': BridgeMatcherLocks.pending_tezos_withdrawals,
        'claimed_fast_withdrawals': BridgeMatcherLocks.pending_claimed_fast_withdrawals,
    }


def _disarm_outbox_matcher_steps() -> None:
    BridgeMatcherLocks.pending_outbox = False
    BridgeMatcherLocks.pending_tezos_withdrawals = False
    BridgeMatcherLocks.pending_claimed_fast_withdrawals = False


ARMED = {'outbox': True, 'tezos_withdrawals': True, 'claimed_fast_withdrawals': True}
DISARMED = {'outbox': False, 'tezos_withdrawals': False, 'claimed_fast_withdrawals': False}


async def test_only_a_pass_that_commits_outbox_rows_arms_the_outbox_matcher_steps(db: Any) -> None:
    tzkt = UniverseTzkt([_transfer(100, FIRST_LEVEL)])
    node = FakeRollupNode({OUTBOX_LEVEL: [_outbox_message(OUTBOX_LEVEL, 0)]}, processed_level=OUTBOX_LEVEL - 1)
    index = await _backfilled_index(tzkt, node)
    _disarm_outbox_matcher_steps()

    tzkt.universe.append(_external(101, OUTBOX_LEVEL))
    async with _head_handler(OUTBOX_LEVEL):
        await index.handle_realtime(OUTBOX_LEVEL)
    assert await _outbox_rows() == []
    assert _outbox_matcher_steps_armed() == DISARMED, 'a pass that committed no outbox rows armed the outbox matcher steps'

    node.processed_level = OUTBOX_LEVEL
    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)
    assert await _outbox_rows() == [(OUTBOX_LEVEL, 0)]
    assert _outbox_matcher_steps_armed() == ARMED, 'committed outbox rows left the outbox matcher steps disarmed'
    _disarm_outbox_matcher_steps()

    async with _head_handler(DRAIN_HEAD + 1):
        await index.handle_realtime(DRAIN_HEAD + 1)
    assert _outbox_matcher_steps_armed() == DISARMED, 'a pass with nothing to drain armed the outbox matcher steps'

    assert await _roll_back_head(to_level=DRAIN_HEAD - 1) == 2, 'the rollback reverted nothing, so it proves nothing'
    assert await _outbox_rows() == []

    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)
    assert await _outbox_rows() == [(OUTBOX_LEVEL, 0)]
    assert _outbox_matcher_steps_armed() == ARMED, 're-draining a level a rollback reverted left the outbox matcher steps disarmed'


async def test_a_rollback_before_the_drain_neither_loses_nor_duplicates_the_level(db: Any) -> None:
    tzkt = UniverseTzkt([_transfer(100, FIRST_LEVEL)])
    node = FakeRollupNode({OUTBOX_LEVEL: [_outbox_message(OUTBOX_LEVEL, 0)]}, processed_level=OUTBOX_LEVEL - 1)
    index = await _backfilled_index(tzkt, node)

    tzkt.universe.append(_external(101, OUTBOX_LEVEL))
    async with _head_handler(OUTBOX_LEVEL):
        await index.handle_realtime(OUTBOX_LEVEL)
    assert await _inbox_rows() == [(100, FIRST_LEVEL), (101, 0)], 'only a sentinel can carry a cursor that stopped on an external'
    assert await _pending_levels() == [OUTBOX_LEVEL]

    assert await _roll_back_head(to_level=OUTBOX_LEVEL - 1) == 2, 'the rollback reverted nothing, so it proves nothing'
    assert await _inbox_rows() == [(100, FIRST_LEVEL)], 'the sentinel that carried the cursor past the external is gone'
    assert await _pending_levels() == [], 'the queue row of that pass went with it — the external is what owes the level again'

    node.processed_level = OUTBOX_LEVEL
    async with _head_handler(OUTBOX_LEVEL + 1):
        await index.handle_realtime(OUTBOX_LEVEL + 1)

    assert await _outbox_rows() == [(OUTBOX_LEVEL, 0)], 'the level was drained exactly once, whichever half of the state asked for it'
    assert await _pending_levels() == [], 'and it is not owed a second time'


async def test_a_rollback_of_a_full_outbox_drain_recovers_the_continuation_too(db: Any) -> None:
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

    assert await _roll_back_head(to_level=DRAIN_HEAD - 1) == 4, 'the rollback reverted nothing, so it proves nothing'
    assert await _outbox_rows() == [], 'the journal revert is the instrument; it has to really remove the rows'

    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)

    assert await _outbox_rows() == expected, 'the rolled-back drain has to be redone whole: the full level and the continuation it implies'
    assert await _pending_levels() == []


async def test_two_consecutive_rollbacks_after_a_drain_still_leave_the_messages(db: Any) -> None:
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

    async with _head_handler(DRAIN_HEAD - 1):
        await index.handle_realtime(DRAIN_HEAD - 1)

    await _roll_back_head(to_level=DRAIN_HEAD - 2)
    assert await _outbox_rows() == [], 'anything recovered at the intermediate head is reverted with it'

    async with _head_handler(DRAIN_HEAD):
        await index.handle_realtime(DRAIN_HEAD)

    assert await _outbox_rows() == [(OUTBOX_LEVEL, 0)], 'the level is still owed after the second rollback, and has to be drained again'
    assert await _pending_levels() == []


async def test_a_restart_between_the_rollback_and_the_next_pass_still_owes_the_level(db: Any) -> None:
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

    restarted = _index(tzkt, node)
    await restarted._prepare_new_index()

    async with _head_handler(DRAIN_HEAD):
        await restarted._process()

    assert await _outbox_rows() == [(OUTBOX_LEVEL, 0)], 'the level is owed to the database, not to the process that drained it'
    assert await _pending_levels() == []
    assert set(restarted._pending_outbox_levels) == set(await _pending_levels()), 'memory and the stored queue must not diverge'


async def test_a_chain_of_full_levels_is_walked_once_per_pass_and_redone_whole(db: Any) -> None:
    full = {level: [_outbox_message(level, 0), _outbox_message(level, 1)] for level in (OUTBOX_LEVEL, OUTBOX_LEVEL + 1, OUTBOX_LEVEL + 2)}
    chain = [*full.keys(), OUTBOX_LEVEL + 3]
    pass_head = DRAIN_HEAD + 20

    tzkt = UniverseTzkt([_transfer(100, FIRST_LEVEL)])
    node = FakeRollupNode({**full, OUTBOX_LEVEL + 3: []}, processed_level=OUTBOX_LEVEL - 1)
    index = await _backfilled_index(tzkt, node)

    tzkt.universe.append(_external(101, OUTBOX_LEVEL))
    async with _head_handler(OUTBOX_LEVEL):
        await index.handle_realtime(OUTBOX_LEVEL)
    assert await _pending_levels() == [OUTBOX_LEVEL], 'only the external is owed; the rest of the chain is still unknown'

    node.processed_level = OUTBOX_LEVEL + 3
    async with _head_handler(pass_head):
        await index.handle_realtime(pass_head)

    assert node.requested == chain, 'the chain is walked in order, once each, and the short outbox ends it'
    stored = [(level, index_) for level in full for index_ in (0, 1)]
    assert await _outbox_rows() == stored
    assert await _pending_levels() == [], 'a terminated chain leaves nothing owed'

    assert await _roll_back_head(to_level=pass_head - 1) == 7, 'the rollback reverted nothing, so it proves nothing'
    assert await _outbox_rows() == []

    async with _head_handler(pass_head):
        await index.handle_realtime(pass_head)

    assert node.requested == chain * 2, 'the restored level re-derives the whole chain, and still asks each level once'
    assert await _outbox_rows() == stored, 'every message of the reverted run is back'
    assert await _pending_levels() == []


async def test_the_queue_never_journals_an_insert_of_a_row_that_is_already_there(db: Any) -> None:
    queue = PendingOutboxLevels(logging.getLogger(LOGGER_NAME))
    await queue.refresh()
    queue.add(OUTBOX_LEVEL)

    await RollupPendingOutboxLevel.create(level=OUTBOX_LEVEL)

    async with _head_handler(DRAIN_HEAD):
        await queue.save()

    journalled = await ModelUpdate.filter(model_name=RollupPendingOutboxLevel.__name__, action=ModelUpdateAction.INSERT).count()
    assert journalled == 0, 'the insert of a row that was already there was journalled, and its revert deletes an owed level'

    await _roll_back_head(to_level=DRAIN_HEAD - 1)
    assert await _pending_levels() == [OUTBOX_LEVEL], 'the level a rollback handed back was taken away again by a phantom journal entry'
