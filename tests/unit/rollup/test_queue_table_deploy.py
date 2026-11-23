from __future__ import annotations

import logging
from typing import Any

import pytest
from dipdup.database import generate_schema
from dipdup.database import get_connection
from dipdup.database import get_tables
from dipdup.models import Meta

from rollup_bridge_indexer.handlers.rollup_message import PendingOutboxLevels
from rollup_bridge_indexer.models import RollupInboxMessage
from rollup_bridge_indexer.models import RollupInboxMessageType
from rollup_bridge_indexer.models import RollupPendingOutboxLevel

pytestmark = pytest.mark.anyio

QUEUE_TABLE = 'rollup_pending_outbox_level'
SCHEMA_NAME = 'public'
ORIGINATION_LEVEL = 100


async def test_the_queue_table_arrives_on_a_populated_database_with_its_rows_intact(db: Any) -> None:
    conn = get_connection()
    await conn.execute_script(f'DROP TABLE {QUEUE_TABLE}')
    assert QUEUE_TABLE not in await get_tables(), 'the database of the previous version has no such table'

    await RollupInboxMessage.create(
        id=500,
        level=ORIGINATION_LEVEL,
        index=0,
        type=RollupInboxMessageType.transfer,
        message={},
        parameters_hash=None,
    )

    await generate_schema(conn, SCHEMA_NAME)

    assert QUEUE_TABLE in await get_tables(), 'the boot creates the table it is missing'
    assert await RollupInboxMessage.all().count() == 1, 'and keeps the history — this is a create, not a rebuild'
    assert await RollupPendingOutboxLevel.all().count() == 0


async def test_the_owed_levels_of_the_previous_version_arrive_with_the_table(db: Any) -> None:
    conn = get_connection()
    await conn.execute_script(f'DROP TABLE {QUEUE_TABLE}')
    await RollupInboxMessage.create(
        id=500,
        level=ORIGINATION_LEVEL,
        index=0,
        type=RollupInboxMessageType.transfer,
        message={},
        parameters_hash=None,
    )
    await Meta.update_or_create(key=PendingOutboxLevels.key, defaults={'value': [50, 1000, 1001]})

    await generate_schema(conn, SCHEMA_NAME)

    queue = PendingOutboxLevels(logging.getLogger('test.rollup_message'))
    await queue.load(floor=ORIGINATION_LEVEL)

    assert set(queue) == {1000, 1001}, 'the running index owes what the previous one still owed'
    assert [row.level for row in await RollupPendingOutboxLevel.all().order_by('level')] == [1000, 1001]
    assert await Meta.get_or_none(key=PendingOutboxLevels.key) is None, 'the key is emptied and removed, so no later boot reads it'
