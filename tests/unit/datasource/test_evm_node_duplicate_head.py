"""Contract: a duplicate `newHeads` announcement must not disturb the EVM node datasource.

The node may announce the same head hash twice (sequencer replay, a reconnect that re-delivers the
tip). `EvmNodeDatasource` keys realtime state by block hash in a `defaultdict` (`_level_data`) and
pushes the *same* `LevelData` object onto `_emitter_queue` on every head frame, so two
announcements of one hash leave two queue entries pointing at one key. The emitter drains them one
by one and drops the key at the end of each pass -- the second pass finds nothing to drop.
`defaultdict` autocreates on `__getitem__`, never on `__delitem__`.

That matters because `EvmNodeDatasource.run()` gathers `_emitter_loop()` with the websocket loop
and the watchdog (plain `asyncio.gather`, no `return_exceptions`), and `DipDup.run()` in turn
gathers the per-datasource run tasks -- so an exception escaping the emitter loop unwinds all the
way out and ends the process.

Surviving the delete is not enough, so these tests do not assert it. A duplicate that reaches the
emitter is processed a second time, and since `head.level <= known_level` it fires `emit_rollback`
on all four EVM channels: a phantom one-level reorg that rewinds index state and re-runs every
handler for that block. The contract asserted here is therefore *the duplicate is absorbed* --
exactly one head emitted, no rollback, no leftover level data -- which a merely tolerant delete
would fail. Both orderings are covered: the duplicate arriving before the emitter drains, and the
duplicate arriving while the emitter is mid-pass.

Offline: no node, no websocket, no HTTP -- frames are fed straight into `_handle_subscription` and
the real `_emitter_loop` drains them.

The patch under test lives in `handlers/dipdup_patches.py`. When upstream ships the fix and that
module is deleted, drop the `_patched` fixture below; the rest of the file is the permanent
regression guard and must stay green against stock DipDup.
"""

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

from rollup_bridge_indexer.handlers.dipdup_patches import apply_dipdup_patches

if TYPE_CHECKING:
    from dipdup.datasources import IndexDatasource

pytestmark = pytest.mark.anyio

# The emitter loop only ever exits by raising; when healthy it parks on an empty queue. So "drain
# it and see" is spelled as "run it under a timeout and expect the timeout". Each queued head costs
# at most NODE_LEVEL_TIMEOUT (LevelData.wait_level), so this is a wide margin over two heads.
_DRAIN_TIMEOUT = max(1.0, NODE_LEVEL_TIMEOUT * 20)

_BLOCK_HASH = '0x' + 'ab' * 32


@pytest.fixture(autouse=True)
def _patched() -> None:
    """Apply the shipped runtime patch, exactly as `hooks/on_restart.py` does."""
    apply_dipdup_patches()


def _head_frame(*, block_hash: str, level: int) -> dict[str, Any]:
    """A `newHeads` result as an Etherlink EVM node sends it (hex-quantity fields)."""
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
    """Records everything the emitter emits, via the datasource's public callback API."""

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
    """A datasource that is never started: no session, no websocket, no web3 client.

    Only `_handle_subscription` and `_emitter_loop` are exercised, and neither touches the network
    for a head-only subscription (`transactions=False` keeps `get_block_by_level` out of the path).
    """
    config = EvmNodeDatasourceConfig(kind='evm.node', url='http://127.0.0.1:0', ws_url='ws://127.0.0.1:0')
    # Datasource configs get their name from the config loader; there's no public setter.
    config._name = 'evm_node_under_test'

    datasource = EvmNodeDatasource(config)
    recorder = _Recorder()
    recorder.attach(datasource)
    return datasource, recorder


async def _drain_emitter(datasource: EvmNodeDatasource) -> None:
    """Run the real emitter loop until it parks on an empty queue.

    Anything the loop raises propagates to the caller; a clean drain shows up as the timeout.
    """
    try:
        await asyncio.wait_for(datasource._emitter_loop(), timeout=_DRAIN_TIMEOUT)
    except TimeoutError:
        pass


async def test_single_head_drains_cleanly() -> None:
    """Control: the harness really drives the emitter, and one head is the well-behaved case."""
    datasource, recorder = _make_datasource()

    await datasource._handle_subscription(EvmNodeHeadSubscription(), _head_frame(block_hash=_BLOCK_HASH, level=42))
    await _drain_emitter(datasource)

    assert [head.level for head in recorder.heads] == [42]
    assert recorder.rollbacks == []
    assert dict(datasource._level_data) == {}


async def test_distinct_heads_are_all_emitted() -> None:
    """Control: deduplication must not swallow genuinely different blocks."""
    datasource, recorder = _make_datasource()
    subscription = EvmNodeHeadSubscription()

    await datasource._handle_subscription(subscription, _head_frame(block_hash='0x' + 'a1' * 32, level=42))
    await datasource._handle_subscription(subscription, _head_frame(block_hash='0x' + 'a2' * 32, level=43))
    await _drain_emitter(datasource)

    assert [head.level for head in recorder.heads] == [42, 43]
    assert recorder.rollbacks == []
    assert dict(datasource._level_data) == {}


async def test_duplicate_head_before_drain_is_absorbed() -> None:
    """Same head announced twice back to back, before the emitter gets to it."""
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
    """Same head re-announced while the emitter is already inside the pass for it.

    The `on_head` callback fires from within `emit_head`, i.e. after the queue entry was taken but
    before the level data is dropped -- exactly the window a queue-level dedup would miss.
    """
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
