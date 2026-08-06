"""Shared unit-test fixtures: Tortoise + DipDup TransactionManager.

``dipdup.models.Model.save()`` needs a registered ``TransactionManager`` or it raises
``FrameworkException``; registering one with no open versioned transaction makes saves
behave like plain Tortoise.

The backend is in-memory sqlite by default — that is what ``make test`` runs and what CI
gates on. Point ``TEST_DB_URL`` at a Postgres to run the same suite on the production
backend (``make test-pg``): the matcher's implicit tie-breaks depend on how the backend
orders rows, and sqlite and Postgres disagree on where NULLs sort, so a rewrite that
picks the right candidate on sqlite can pick the wrong one in production.
"""

import os

import pytest
from dipdup.transactions import TransactionManager
from tortoise import Tortoise

from rollup_bridge_indexer.handlers.bridge_matcher_locks import BridgeMatcherLocks

DB_URL = os.environ.get('TEST_DB_URL', 'sqlite://:memory:')


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def _reset_matcher_locks() -> None:
    # Class-level flags leak between tests otherwise.
    for name, value in vars(BridgeMatcherLocks).items():
        if name.startswith('pending_') and isinstance(value, bool):
            setattr(BridgeMatcherLocks, name, False)


async def init_empty_schema(db_url: str = DB_URL) -> None:
    """Open `db_url` with the real models and leave every table empty.

    A fresh in-memory sqlite is empty by construction; a server-backed database outlives
    the test, so drop and recreate the schema to get the same starting point.
    """
    await Tortoise.init(db_url=db_url, modules={'models': ['rollup_bridge_indexer.models']})
    if not db_url.startswith('sqlite'):
        await Tortoise.get_connection('default').execute_script('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')
    await Tortoise.generate_schemas()


@pytest.fixture
async def db():
    await init_empty_schema()
    _reset_matcher_locks()
    async with TransactionManager().register():
        yield
    await Tortoise.close_connections()
