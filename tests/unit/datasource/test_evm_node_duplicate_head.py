import asyncio
from typing import TYPE_CHECKING
from typing import Any

import pytest
from dipdup.config.evm_node import EvmNodeDatasourceConfig
from dipdup.datasources.evm_node import NODE_LEVEL_TIMEOUT
from dipdup.datasources.evm_node import EvmNodeDatasource
from dipdup.models import MessageType
from dipdup.models.evm_node import EvmNodeHeadData
from dipdup.subscriptions.evm_node import EvmNodeHeadSubscription

if TYPE_CHECKING:
    from dipdup.datasources import IndexDatasource

pytestmark = pytest.mark.anyio

_DRAIN_TIMEOUT = max(1.0, NODE_LEVEL_TIMEOUT * 20)

_BLOCK_HASH = '0x' + 'ab' * 32


def _head_frame(*, block_hash: str, level: int) -> dict[str, Any]:
    return {
        'baseFeePerGas': '0x3b9aca00',
        'difficulty': '0x0',
        'extraData': '0x',
        'gasLimit': '0x1c9c380',
        'gasUsed': '0x5208',
        'hash': block_hash,
        'logsBloom': '0x' + '00' * 256,
        'miner': '0x' + '00' * 20,
        'mixHash': '0x' + '00' * 32,
        'nonce': '0x0000000000000000',
        'number': hex(level),
        'parentHash': '0x' + '11' * 32,
        'receiptsRoot': '0x' + '22' * 32,
        'sha3Uncles': '0x' + '33' * 32,
        'stateRoot': '0x' + '44' * 32,
        'timestamp': hex(1_750_000_000 + level),
        'transactionsRoot': '0x' + '55' * 32,
    }


class _Recorder:

    def __init__(self) -> None:
        self.heads: list[EvmNodeHeadData] = []
        self.rollbacks: list[tuple[str, int, int]] = []

    def attach(self, datasource: EvmNodeDatasource) -> None:
        async def _on_head(_datasource: EvmNodeDatasource, head: EvmNodeHeadData) -> None:
            self.heads.append(head)

        async def _on_rollback(
            _datasource: 'IndexDatasource[Any]',
            type_: MessageType,
            from_level: int,
            to_level: int,
        ) -> None:
            self.rollbacks.append((str(type_), from_level, to_level))

        datasource.call_on_head(_on_head)
        datasource.call_on_rollback(_on_rollback)


def _make_datasource() -> tuple[EvmNodeDatasource, _Recorder]:
    config = EvmNodeDatasourceConfig(kind='evm.node', url='http://127.0.0.1:0', ws_url='ws://127.0.0.1:0')
    config._name = 'evm_node_under_test'

    datasource = EvmNodeDatasource(config)
    recorder = _Recorder()
    recorder.attach(datasource)
    return datasource, recorder


async def _drain_emitter(datasource: EvmNodeDatasource) -> None:
    try:
        await asyncio.wait_for(datasource._emitter_loop(), timeout=_DRAIN_TIMEOUT)
    except TimeoutError:
        pass


async def test_single_head_drains_cleanly() -> None:
    datasource, recorder = _make_datasource()

    await datasource._handle_subscription(EvmNodeHeadSubscription(), _head_frame(block_hash=_BLOCK_HASH, level=42))
    await _drain_emitter(datasource)

    assert [head.level for head in recorder.heads] == [42]
    assert recorder.rollbacks == []
    assert dict(datasource._level_data) == {}


async def test_distinct_heads_are_all_emitted() -> None:
    datasource, recorder = _make_datasource()
    subscription = EvmNodeHeadSubscription()

    await datasource._handle_subscription(subscription, _head_frame(block_hash='0x' + 'a1' * 32, level=42))
    await datasource._handle_subscription(subscription, _head_frame(block_hash='0x' + 'a2' * 32, level=43))
    await _drain_emitter(datasource)

    assert [head.level for head in recorder.heads] == [42, 43]
    assert recorder.rollbacks == []
    assert dict(datasource._level_data) == {}


async def test_duplicate_head_before_drain_is_absorbed() -> None:
    datasource, recorder = _make_datasource()
    subscription = EvmNodeHeadSubscription()
    frame = _head_frame(block_hash=_BLOCK_HASH, level=42)

    await datasource._handle_subscription(subscription, dict(frame))
    await datasource._handle_subscription(subscription, dict(frame))

    await _drain_emitter(datasource)

    assert [head.level for head in recorder.heads] == [42], 'duplicate head was processed twice'
    assert recorder.rollbacks == [], 'duplicate head triggered a phantom reorg'
    assert dict(datasource._level_data) == {}, 'level data left dirty after draining a duplicate head'


async def test_duplicate_head_while_emitter_in_flight_is_absorbed() -> None:
    datasource, recorder = _make_datasource()
    subscription = EvmNodeHeadSubscription()
    frame = _head_frame(block_hash=_BLOCK_HASH, level=42)

    async def _re_announce(_datasource: EvmNodeDatasource, _head: EvmNodeHeadData) -> None:
        await datasource._handle_subscription(subscription, dict(frame))

    datasource.call_on_head(_re_announce)

    await datasource._handle_subscription(subscription, dict(frame))
    await _drain_emitter(datasource)

    assert [head.level for head in recorder.heads] == [42], 'in-flight duplicate was processed twice'
    assert recorder.rollbacks == [], 'in-flight duplicate triggered a phantom reorg'
    assert dict(datasource._level_data) == {}, 'level data left dirty after an in-flight duplicate'
