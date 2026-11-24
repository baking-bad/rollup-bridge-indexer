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
    for name, value in vars(BridgeMatcherLocks).items():
        if name.startswith('pending_') and isinstance(value, bool):
            setattr(BridgeMatcherLocks, name, False)


async def init_empty_schema(db_url: str = DB_URL) -> None:
    await Tortoise.init(db_url=db_url, modules={'models': ['rollup_bridge_indexer.models', 'dipdup.models']})
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
