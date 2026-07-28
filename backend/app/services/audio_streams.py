from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

_STREAM_CLOSED = object()


class AudioSubscription:
    def __init__(
        self,
        fanout: AudioFanout,
        queue: asyncio.Queue[bytes | object],
    ) -> None:
        self._fanout = fanout
        self._queue = queue
        self._closed = False
        self.closed_event = asyncio.Event()

    async def receive(self, timeout_seconds: float | None = None) -> bytes | None:
        if self._closed:
            return None
        if timeout_seconds is None:
            item = await self._queue.get()
        else:
            item = await asyncio.wait_for(
                self._queue.get(),
                timeout=timeout_seconds,
            )
        if item is _STREAM_CLOSED:
            self._closed = True
            self.closed_event.set()
            return None
        assert isinstance(item, bytes)
        self._fanout.consumed(self, len(item))
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.closed_event.set()
        self._fanout.unsubscribe(self)


@dataclass
class _SubscriberState:
    queue: asyncio.Queue[bytes | object]
    buffered_bytes: int
    published_at: deque[float]


class AudioFanout:
    """Fan out one encoded channel stream without allowing slow clients to block it."""

    def __init__(self, max_buffer_bytes: int, max_buffer_seconds: float = 5) -> None:
        if max_buffer_bytes <= 0 or max_buffer_seconds <= 0:
            raise ValueError("audio fanout buffer limits must be positive")
        self._max_buffer_bytes = max_buffer_bytes
        self._max_buffer_seconds = max_buffer_seconds
        self._subscribers: dict[AudioSubscription, _SubscriberState] = {}

    @property
    def listener_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self, initial: bytes | None = None) -> AudioSubscription:
        queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        subscription = AudioSubscription(self, queue)
        state = _SubscriberState(
            queue=queue,
            buffered_bytes=len(initial) if initial else 0,
            published_at=deque([time.monotonic()] if initial else []),
        )
        self._subscribers[subscription] = state
        if initial:
            queue.put_nowait(initial)
        return subscription

    def unsubscribe(self, subscription: AudioSubscription) -> None:
        self._subscribers.pop(subscription, None)

    def consumed(self, subscription: AudioSubscription, size: int) -> None:
        state = self._subscribers.get(subscription)
        if state is None:
            return
        state.buffered_bytes = max(0, state.buffered_bytes - size)
        if state.published_at:
            state.published_at.popleft()

    def publish(self, chunk: bytes) -> None:
        if not chunk:
            return
        published_at = time.monotonic()
        for subscription, state in tuple(self._subscribers.items()):
            queue_too_old = bool(
                state.published_at
                and published_at - state.published_at[0] >= self._max_buffer_seconds
            )
            if (
                queue_too_old
                or state.buffered_bytes + len(chunk) > self._max_buffer_bytes
            ):
                self._close_subscription(subscription, state)
                continue
            state.queue.put_nowait(chunk)
            state.buffered_bytes += len(chunk)
            state.published_at.append(published_at)

    def close(self) -> None:
        for subscription, state in tuple(self._subscribers.items()):
            self._close_subscription(subscription, state)

    def _close_subscription(
        self,
        subscription: AudioSubscription,
        state: _SubscriberState,
    ) -> None:
        self._subscribers.pop(subscription, None)
        subscription.closed_event.set()
        while not state.queue.empty():
            state.queue.get_nowait()
        state.buffered_bytes = 0
        state.published_at.clear()
        state.queue.put_nowait(_STREAM_CLOSED)


class FlacAudioFanout:
    """Cache the FLAC metadata header and a short frame-aligned join window."""

    def __init__(self, max_buffer_bytes: int, max_buffer_seconds: float = 5) -> None:
        self._fanout = AudioFanout(max_buffer_bytes, max_buffer_seconds)
        self._tail_limit = max(4096, max_buffer_bytes // 5)
        self._metadata = bytearray()
        self._header: bytes | None = None
        self._tail = bytearray()

    @property
    def listener_count(self) -> int:
        return self._fanout.listener_count

    def subscribe(self) -> AudioSubscription:
        return self._fanout.subscribe(self._join_prefix())

    def unsubscribe(self, subscription: AudioSubscription) -> None:
        self._fanout.unsubscribe(subscription)

    def publish(self, chunk: bytes) -> None:
        self._fanout.publish(chunk)
        if self._header is not None:
            self._append_tail(chunk)
            return
        self._metadata.extend(chunk)
        self._extract_header()

    def close(self) -> None:
        self._fanout.close()
        self._metadata.clear()
        self._tail.clear()
        self._header = None

    def _extract_header(self) -> None:
        data = self._metadata
        if len(data) < 4:
            return
        if bytes(data[:4]) != b"fLaC":
            return
        cursor = 4
        while len(data) >= cursor + 4:
            block_header = data[cursor]
            block_length = int.from_bytes(data[cursor + 1 : cursor + 4], "big")
            block_end = cursor + 4 + block_length
            if len(data) < block_end:
                return
            cursor = block_end
            if block_header & 0x80:
                self._header = bytes(data[:cursor])
                self._append_tail(bytes(data[cursor:]))
                data.clear()
                return

    def _append_tail(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._tail.extend(chunk)
        if len(self._tail) > self._tail_limit:
            del self._tail[: len(self._tail) - self._tail_limit]

    def _join_prefix(self) -> bytes | None:
        if self._header is None:
            return bytes(self._metadata) or None
        tail = bytes(self._tail)
        frame_start = next(
            (
                index
                for index in range(max(0, len(tail) - 3))
                if self._looks_like_frame_header(tail, index)
            ),
            len(tail),
        )
        return self._header + tail[frame_start:]

    @staticmethod
    def _looks_like_frame_header(data: bytes, index: int) -> bool:
        if data[index] != 0xFF or data[index + 1] & 0xFE != 0xF8:
            return False
        block_size_code = data[index + 2] >> 4
        sample_rate_code = data[index + 2] & 0x0F
        channel_assignment = data[index + 3] >> 4
        reserved_bit = data[index + 3] & 0x01
        return (
            block_size_code != 0
            and sample_rate_code != 0x0F
            and channel_assignment <= 10
            and reserved_bit == 0
        )


@dataclass(eq=False)
class PlayerConnectionLease:
    identity: tuple[int, bytes]
    user_id: int
    superseded: asyncio.Event
    task: asyncio.Task[object] | None = None
    cancel_handle: asyncio.TimerHandle | None = None
    closed: bool = False


class PlayerConnectionCapacityError(RuntimeError):
    pass


class PlayerConnectionRegistry:
    """Keep only the latest connection for each player-token generation."""

    def __init__(self, max_connections: int, takeover_timeout_seconds: float) -> None:
        if max_connections <= 0 or takeover_timeout_seconds <= 0:
            raise ValueError("player connection limits must be positive")
        self.max_connections = max_connections
        self.takeover_timeout_seconds = takeover_timeout_seconds
        self._connections: dict[tuple[int, bytes], PlayerConnectionLease] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def stop(self) -> None:
        async with self._lock:
            leases = tuple(self._connections.values())
            self._connections.clear()
            for lease in leases:
                self._supersede(lease)

    async def activate(self, identity: tuple[int, bytes]) -> PlayerConnectionLease:
        async with self._lock:
            previous = self._connections.get(identity)
            if previous is None and len(self._connections) >= self.max_connections:
                raise PlayerConnectionCapacityError
            lease = PlayerConnectionLease(
                identity=identity,
                user_id=identity[0],
                superseded=asyncio.Event(),
            )
            self._connections[identity] = lease
            if previous is not None:
                self._supersede(previous)
            return lease

    async def attach_current_task(self, lease: PlayerConnectionLease) -> bool:
        task = asyncio.current_task()
        if task is None:
            return False
        async with self._lock:
            if lease.closed or self._connections.get(lease.identity) is not lease:
                return False
            lease.task = task
            if lease.superseded.is_set():
                task.cancel()
                return False
            return True

    async def release(self, lease: PlayerConnectionLease) -> None:
        async with self._lock:
            if self._connections.get(lease.identity) is lease:
                self._connections.pop(lease.identity, None)
            lease.closed = True
            if lease.cancel_handle is not None:
                lease.cancel_handle.cancel()
                lease.cancel_handle = None

    def revoke_user(self, user_id: int) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._revoke_user(user_id))
        )

    async def _revoke_user(self, user_id: int) -> None:
        async with self._lock:
            for identity, lease in tuple(self._connections.items()):
                if lease.user_id != user_id:
                    continue
                self._connections.pop(identity, None)
                self._supersede(lease)

    def _supersede(self, lease: PlayerConnectionLease) -> None:
        if lease.closed:
            return
        lease.superseded.set()
        loop = self._loop
        if loop is None or loop.is_closed() or lease.cancel_handle is not None:
            return
        lease.cancel_handle = loop.call_later(
            self.takeover_timeout_seconds,
            self._force_cancel,
            lease,
        )

    @staticmethod
    def _force_cancel(lease: PlayerConnectionLease) -> None:
        if not lease.closed and lease.task is not None and not lease.task.done():
            lease.task.cancel()
