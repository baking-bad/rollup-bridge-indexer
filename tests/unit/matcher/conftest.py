"""Bridge-matcher harness: ``factories.py`` inserts the handler-shaped rows the indexer
would have written, ``run_*_matching()`` performs one batch-handler pass, and assertions
read the resulting ``bridge_*`` rows back. Shared fixtures (``db``, ``anyio_backend``) live
in the parent ``tests/unit/conftest.py``.
"""

import pytest

from rollup_bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from tests.unit.matcher.factories import ROLLUP


@pytest.fixture(autouse=True)
def matcher_rollup_address():
    # Production sets this while building the service container, before any matcher pass.
    BridgeMatcher.smart_rollup_address = ROLLUP
