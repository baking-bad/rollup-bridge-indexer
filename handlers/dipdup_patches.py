"""Runtime patches applied to the installed DipDup framework at indexer startup.

Each patch here works around a framework defect that has no released fix yet. A patch is a
liability, not a feature: it is guarded against the exact source it was written for, and it comes
with a removal condition stated next to it.

Applied from `hooks/on_restart.py`, which DipDup fires at the end of `_initialize_schema()` --
strictly before `_start_datasources()`, so no patched coroutine can already be running.
"""

import inspect
import logging
import textwrap
from typing import Any

# NOTE: `dipdup.datasources.*` cannot be the first dipdup module imported in a process -- it
# imports `dipdup.config`, which imports back into `dipdup.datasources`. Importing the config
# package first breaks the cycle. Under the indexer this is already true; the import keeps the
# module importable on its own (tests, `dipdup package verify`).
import dipdup.config  # noqa: F401
from dipdup.datasources.evm_node import EvmNodeDatasource
from dipdup.models.evm_node import EvmNodeSyncingData
from dipdup.subscriptions.evm_node import EvmNodeHeadSubscription
from dipdup.subscriptions.evm_node import EvmNodeLogsSubscription
from dipdup.subscriptions.evm_node import EvmNodeSubscription
from dipdup.subscriptions.evm_node import EvmNodeSyncingSubscription

_logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------------
# Patch: duplicate `newHeads` announcement kills EvmNodeDatasource._emitter_loop
#
# `_handle_subscription` keys realtime state by block hash in a `defaultdict` (`_level_data`) and
# pushes the *same* `LevelData` object onto `_emitter_queue` on every head frame. When the node
# announces one hash twice, the queue holds two entries for one key. `_emitter_loop` ends each
# pass with `del self._level_data[head.hash]`; the second pass finds nothing to delete and raises
# `KeyError` (`defaultdict` autocreates on `__getitem__`, never on `__delitem__`). That exception
# escapes `EvmNodeDatasource.run()`'s bare `asyncio.gather` and unwinds out of `DipDup.run()`,
# ending the process -- a crash loop, since the tip is re-announced right after every resync.
#
# The fix is producer-side on purpose. Tolerating the delete (`pop(hash, None)`) stops the crash
# but still runs the duplicate through the emitter, which -- because `head.level <= known_level` --
# fires `emit_rollback` on all four EVM channels, rewinding index state and re-running every
# handler for that block. A phantom reorg per duplicate head is worse than a loud crash. Dropping
# the second *enqueue* is what actually restores the invariant the `del` assumes: one queue entry
# per `_level_data` key, so each key is deleted exactly once by the pass that owns it.
#
# Guarding on `level_data.head is not None` (rather than on queue membership) also covers the
# duplicate that arrives while the emitter is mid-pass: the key is still in `_level_data` and its
# `head` is still set until the pass ends, so the frame is folded into the in-flight entry.
#
# REMOVAL: delete this patch and its `apply_dipdup_patches()` entry once a released DipDup ships
# the fix. `tests/unit/datasource/test_evm_node_duplicate_head.py` is the gate -- it must pass
# with the patch gone. Reported upstream against `dipdup-io/dipdup`.
# --------------------------------------------------------------------------------------------

# The exact `_handle_subscription` body this patch was written against (DipDup 8.6.1, and
# byte-identical in 8.6.0 and on the `next` branch). Matched exactly: any upstream edit to this
# method -- a fix, a refactor, even a reformat -- must be re-reviewed before we keep overriding
# it, and an exact match is the only check that cannot silently accept a changed method.
_KNOWN_BUGGY_SOURCE = '''\
async def _handle_subscription(self, subscription: EvmNodeSubscription, data: Any) -> None:
    if isinstance(subscription, EvmNodeHeadSubscription):
        level_data = self._level_data[data['hash']]
        level_data.head = data
        if subscription.transactions:
            level_data.fetch_transactions = True
        self._emitter_queue.put_nowait(level_data)
    elif isinstance(subscription, EvmNodeLogsSubscription):
        level_data = self._level_data[data['blockHash']]
        level_data.events.append(data)
    elif isinstance(subscription, EvmNodeSyncingSubscription):
        syncing = EvmNodeSyncingData.from_json(data)
        await self.emit_syncing(syncing)
    else:
        raise NotImplementedError
'''


async def _handle_subscription_deduped(self: EvmNodeDatasource, subscription: EvmNodeSubscription, data: Any) -> None:
    """`EvmNodeDatasource._handle_subscription` that enqueues each `LevelData` at most once."""
    if isinstance(subscription, EvmNodeHeadSubscription):
        level_data = self._level_data[data['hash']]
        already_queued = level_data.head is not None
        level_data.head = data
        if subscription.transactions:
            level_data.fetch_transactions = True
        if not already_queued:
            self._emitter_queue.put_nowait(level_data)
    elif isinstance(subscription, EvmNodeLogsSubscription):
        level_data = self._level_data[data['blockHash']]
        level_data.events.append(data)
    elif isinstance(subscription, EvmNodeSyncingSubscription):
        syncing = EvmNodeSyncingData.from_json(data)
        await self.emit_syncing(syncing)
    else:
        raise NotImplementedError


def _patch_evm_node_duplicate_head() -> None:
    if EvmNodeDatasource._handle_subscription is _handle_subscription_deduped:
        return

    try:
        installed = textwrap.dedent(inspect.getsource(EvmNodeDatasource._handle_subscription))
    except OSError:
        # Source unavailable (frozen/zipped install, or the method was replaced at runtime) --
        # the shape cannot be verified, so patching it would be guesswork.
        installed = ''

    if installed != _KNOWN_BUGGY_SOURCE:
        raise RuntimeError(
            'dipdup patch `evm_node_duplicate_head` does not recognise the installed '
            '`EvmNodeDatasource._handle_subscription`. Upstream changed it -- check whether the '
            'duplicate-head crash (KeyError in `_emitter_loop` on `del self._level_data[...]`) is '
            'fixed. If it is, drop the patch and its entry in `apply_dipdup_patches()`; if it is '
            'not, re-derive the patch against the new source. See handlers/dipdup_patches.py.'
        )

    EvmNodeDatasource._handle_subscription = _handle_subscription_deduped  # type: ignore[method-assign]
    _logger.info('Applied dipdup patch: evm_node_duplicate_head')


def apply_dipdup_patches() -> None:
    """Apply every runtime patch. Idempotent; raises if a patched framework method has changed."""
    _patch_evm_node_duplicate_head()
