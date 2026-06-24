"""Shared unit-test fixtures: in-memory Tortoise + DipDup TransactionManager.

``dipdup.models.Model.save()`` needs a registered ``TransactionManager`` or it raises
``FrameworkException``; registering one with no open versioned transaction makes saves
behave like plain Tortoise against in-memory sqlite.
"""

import pytest
from dipdup.transactions import TransactionManager
from tortoise import Tortoise

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def _reset_matcher_locks() -> None:
    # Class-level flags leak between tests otherwise.
    for name, value in vars(BridgeMatcherLocks).items():
        if name.startswith('pending_') and isinstance(value, bool):
            setattr(BridgeMatcherLocks, name, False)


@pytest.fixture
async def db():
    await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['rollup_bridge_indexer.models']})
    await Tortoise.generate_schemas()
    _reset_matcher_locks()
    async with TransactionManager().register():
        yield
    await Tortoise.close_connections()
