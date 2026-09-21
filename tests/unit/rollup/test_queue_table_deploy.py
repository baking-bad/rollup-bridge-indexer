"""Contract: the deploy that moves the owed outbox levels into a table loses none of them.

Moving `PendingOutboxLevels` out of `dipdup_meta` and into `rollup_pending_outbox_level` is a
change to `models/`, and this repository treats those as a reindex. This one is additive — a new
table, no column touched — and the boot sequence is what makes that different: `_initialize_schema`
(`dipdup/dipdup.py:785-833`) calls `generate_schema` at line 796, which is
`Tortoise.generate_schemas()` with its default `safe=True`, i.e. `CREATE TABLE IF NOT EXISTS`
(`dipdup/database.py:245-252`), and only *then* compares the schema hash at line 829. So the table
is already there, on the populated database, with every row kept, by the time the hash is looked
at. `advanced.reindex.schema_modified: ignore` for one boot nulls the stored hash instead of
raising (`dipdup/context.py:228-234`) and the boot after it adopts the new hash
(`dipdup/dipdup.py:825-827`). The repo CLAUDE.md carries the procedure; this file pins the halves
of it that are ours to keep true.

Two of them:

  * the table appears on a database that already has rows, and those rows survive it;
  * the levels the previous binary owed — which live in the `dipdup_meta` key and nowhere else —
    arrive in the new table on the first boot that reads them.

The database of the previous version is modelled by dropping exactly the table this change adds,
which is what that version's schema is.

Offline: no datasources at all. The only moving parts are DipDup's own schema generation and
`PendingOutboxLevels.load`.
"""

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
    """The additive half: `generate_schema` creates the missing table and touches nothing else."""
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
    """The carrying half: the `dipdup_meta` key is what the previous binary still owed.

    It is the only record of those levels — the inbox cursor sits above the externals that
    produced them — so the first boot that finds the table empty and the key populated has to
    move them across, under the origination floor, and then take the key out of circulation.
    """
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
    # A level below origination among them: a database wedged on one must not import its wedge.
    await Meta.update_or_create(key=PendingOutboxLevels.key, defaults={'value': [50, 1000, 1001]})

    await generate_schema(conn, SCHEMA_NAME)

    queue = PendingOutboxLevels(logging.getLogger('test.rollup_message'))
    await queue.load(floor=ORIGINATION_LEVEL)

    assert set(queue) == {1000, 1001}, 'the running index owes what the previous one still owed'
    assert [row.level for row in await RollupPendingOutboxLevel.all().order_by('level')] == [1000, 1001]
    assert await Meta.get_or_none(key=PendingOutboxLevels.key) is None, 'the key is emptied and removed, so no later boot reads it'
