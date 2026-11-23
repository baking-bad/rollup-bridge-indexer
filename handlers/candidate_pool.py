import logging
from collections import deque
from collections.abc import Callable
from collections.abc import Hashable

logger = logging.getLogger('rollup_bridge_indexer.handlers.candidate_pool')


class CandidatePool[R, K: Hashable]:

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
            logger.warning('%s: %d candidates for one key, taking the first', self._name, len(matched))

        row = queue[matched[0]]
        del queue[matched[0]]
        return row
