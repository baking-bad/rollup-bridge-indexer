import pytest

from rollup_bridge_indexer.handlers.bridge_matcher import BridgeMatcher
from tests.unit.matcher.factories import ROLLUP


@pytest.fixture(autouse=True)
def matcher_rollup_address():
    BridgeMatcher.smart_rollup_address = ROLLUP
