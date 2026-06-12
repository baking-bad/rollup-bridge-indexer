"""Bridge-matcher harness: real models and real matcher steps over in-memory sqlite.

The matcher is pure ORM logic, so the only infrastructure it needs is a Tortoise
connection plus DipDup's ``TransactionManager`` registration — without one,
``dipdup.models.Model.save()`` raises ``FrameworkException``. Registering a manager
with no open versioned transaction makes saves behave like plain Tortoise.

Tests drive the system the way production does: factories (``factories.py``) insert
the rows the indexer handlers would have written, ``run_deposit_matching()`` performs
one batch-handler pass, and assertions read the resulting ``bridge_*`` rows back.
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
