from __future__ import annotations

import logging
from typing import Any

import aiohttp
import pytest

from rollup_bridge_indexer.handlers.tezos.on_rollup_execute import fetch_block_operations

pytestmark = pytest.mark.anyio

LEVEL = 15_834_471
OPERATIONS: list[list[Any]] = [[], [], [], [{'hash': 'oo1', 'contents': []}]]


def _status(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(request_info=None, history=(), status=status, message='')  # type: ignore[arg-type]


class FakeRpc:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.urls: list[str] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.urls.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def _fetch(rpc: FakeRpc, attempts: int = 5) -> Any:
    return await fetch_block_operations(rpc, LEVEL, logging.getLogger('test.execute'), attempts=attempts, delay=0)  # type: ignore[arg-type]


async def test_a_block_the_rpc_has_not_applied_yet_is_asked_for_again() -> None:
    rpc = FakeRpc([_status(404), _status(404), OPERATIONS])

    assert await _fetch(rpc) == OPERATIONS
    assert rpc.urls == [f'chains/main/blocks/{LEVEL}/operations'] * 3


async def test_a_block_that_never_appears_still_fails() -> None:
    rpc = FakeRpc([_status(404)] * 3)

    with pytest.raises(aiohttp.ClientResponseError):
        await _fetch(rpc, attempts=3)
    assert len(rpc.urls) == 3


async def test_other_client_errors_are_not_retried() -> None:
    rpc = FakeRpc([_status(400), OPERATIONS])

    with pytest.raises(aiohttp.ClientResponseError):
        await _fetch(rpc)
    assert len(rpc.urls) == 1
