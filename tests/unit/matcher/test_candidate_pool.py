"""The rules `CandidatePool` gives every matching step that pairs two sides."""

import logging

import pytest

from rollup_bridge_indexer.handlers.candidate_pool import CandidatePool
from rollup_bridge_indexer.models import RollupInboxMessage
from tests.unit.matcher.factories import inbox_message

pytestmark = pytest.mark.anyio


class _Recorded:
    """Stands in for a queryset and remembers whether anyone iterated it."""

    def __init__(self, rows):
        self.rows = rows
        self.iterated = False

    async def __aiter__(self):
        self.iterated = True
        for row in self.rows:
            yield row


def _pool(rows, key=lambda row: row.parameters_hash, **kwargs) -> tuple[CandidatePool, _Recorded]:
    queryset = _Recorded(rows)
    return CandidatePool('test', queryset, key, **kwargs), queryset


async def test_a_candidate_is_handed_out_once(db):
    first = await inbox_message(id=1, level=100, index=5, parameters_hash='a' * 32)
    second = await inbox_message(id=2, level=100, index=6, parameters_hash='a' * 32)
    pool, _ = _pool([first, second])

    assert await pool.take('a' * 32) == first
    assert await pool.take('a' * 32) == second
    assert await pool.take('a' * 32) is None


async def test_the_queryset_order_is_the_tie_break(db):
    newest = await inbox_message(id=1, level=200, index=0, parameters_hash='a' * 32)
    oldest = await inbox_message(id=2, level=100, index=0, parameters_hash='a' * 32)
    # The pool preserves what the query returned; it never sorts on its own.
    pool, _ = _pool([newest, oldest])

    assert await pool.take('a' * 32) == newest


async def test_a_row_without_a_key_never_enters_the_pool(db):
    keyless = await inbox_message(id=1, parameters_hash=None)
    pool, _ = _pool([keyless])

    assert await pool.take(None) is None


async def test_where_narrows_the_queue_without_reordering(db):
    low = await inbox_message(id=1, level=100, index=1, parameters_hash='a' * 32)
    high = await inbox_message(id=2, level=100, index=9, parameters_hash='a' * 32)
    pool, _ = _pool([low, high])

    assert await pool.take('a' * 32, where=lambda message: message.index > 5) == high
    # Rejecting a candidate must not consume it.
    assert await pool.take('a' * 32) == low


async def test_nothing_is_read_before_the_first_take(db):
    message = await inbox_message(id=1)
    pool, queryset = _pool([message])
    assert not queryset.iterated

    await pool.take('a' * 32)
    assert queryset.iterated


async def test_a_tie_is_reported(db, caplog):
    await inbox_message(id=1, level=100, index=5, parameters_hash='a' * 32)
    await inbox_message(id=2, level=100, index=6, parameters_hash='a' * 32)
    pool: CandidatePool[RollupInboxMessage, str] = CandidatePool(
        'test',
        RollupInboxMessage.filter(parameters_hash__isnull=False).order_by('level', 'index'),
        key=lambda message: message.parameters_hash,
    )

    with caplog.at_level(logging.WARNING):
        await pool.take('a' * 32)

    assert '2 candidates for one key' in caplog.text


async def test_a_tie_is_silent_where_it_is_expected(db, caplog):
    await inbox_message(id=1, level=100, index=5, parameters_hash='a' * 32)
    await inbox_message(id=2, level=100, index=6, parameters_hash='a' * 32)
    pool, _ = _pool(await RollupInboxMessage.all().order_by('level', 'index'), warn_on_tie=False)

    with caplog.at_level(logging.WARNING):
        await pool.take('a' * 32)

    assert caplog.text == ''
