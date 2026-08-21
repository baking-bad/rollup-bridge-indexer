import logging
from collections import deque
from collections.abc import Callable
from collections.abc import Hashable

logger = logging.getLogger('rollup_bridge_indexer.handlers.candidate_pool')


class CandidatePool[R, K: Hashable]:
    """One side of a matching step, read with a single query and claimed row by row.

    A matcher step pairs two sides: it walks one of them and, for each row, looks for the
    counterpart. The counterpart side is this pool. Three rules make it a pool rather than
    a plain dict:

    - **Order is the tie-break.** The queryset's own `order_by` decides which candidate a
      key hands out first, so it is always spelled out at the call site.
    - **A taken row is gone.** Each candidate can be claimed once; `take` removes it, so
      two walked rows can never be paired with the same counterpart.
    - **A None key belongs to nobody** and never enters the pool, so rows with a missing
      key cannot collide on it.

    The query runs on the first `take`, not on construction: a step whose walk turns out
    to be empty pays nothing.
    """

    def __init__(self, name: str, queryset, key: Callable[[R], K | None], *, warn_on_tie: bool = True) -> None:
        self._name = name
        self._queryset = queryset
        self._key = key
        self._warn_on_tie = warn_on_tie
        self._queues: dict[K, deque[R]] | None = None

    async def _fill(self) -> None:
        self._queues = {}
        async for row in self._queryset:
            key = self._key(row)
            if key is not None:
                self._queues.setdefault(key, deque()).append(row)

    async def take(self, key: K, where: Callable[[R], bool] | None = None) -> R | None:
        """Claim the first candidate queued under `key`, or None if there is none.

        `where` narrows the queue by a condition the key cannot express — an inequality,
        typically a time window. It never reorders: the first queued row that satisfies it
        wins, which is the row the ordered query would have returned.
        """
        if self._queues is None:
            await self._fill()
        assert self._queues is not None

        queue = self._queues.get(key)
        if not queue:
            return None

        matched = [index for index, row in enumerate(queue) if where is None or where(row)]
        if not matched:
            return None
        if len(matched) > 1 and self._warn_on_tie:
            # Where a key identifies its counterpart, several candidates under it means the
            # pairing below is a guess — the order decides, and nobody chose that order for
            # this. Rare enough to be worth a line each time: mainnet has no two open bridge
            # deposits on one set of inbox coords in 34k deposits. Pools keyed by a hash of
            # the operation's own parameters are the exception and set `warn_on_tie=False`:
            # two identical operations in one block genuinely share a key, and either
            # counterpart is as good as the other.
            logger.warning('%s: %d candidates for one key, taking the first', self._name, len(matched))

        row = queue[matched[0]]
        del queue[matched[0]]
        return row
