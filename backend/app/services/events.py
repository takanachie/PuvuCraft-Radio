from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[dict[str, object]]]] = defaultdict(set)

    @asynccontextmanager
    async def subscribe(self, channel_id: int) -> AsyncIterator[asyncio.Queue[dict[str, object]]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=10)
        self._subscribers[channel_id].add(queue)
        try:
            yield queue
        finally:
            subscribers = self._subscribers.get(channel_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(channel_id, None)

    def publish(self, channel_id: int, payload: dict[str, object]) -> None:
        for queue in tuple(self._subscribers.get(channel_id, ())):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(payload)

    def listener_count(self, channel_id: int) -> int:
        return len(self._subscribers.get(channel_id, ()))
