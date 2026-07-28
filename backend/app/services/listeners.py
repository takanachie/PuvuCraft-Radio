from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from ..models import utcnow


@dataclass(frozen=True)
class ListenerPresence:
    user_id: int
    channel_id: int
    last_seen_at: datetime


@dataclass(frozen=True)
class _PresenceEntry:
    seen_at: float
    presence: ListenerPresence


class ListenerRegistry:
    """Track recent authenticated audio activity without retaining database objects."""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("listener timeout must be positive")
        self.timeout_seconds = timeout_seconds
        self._monotonic = monotonic
        self._now = now
        self._entries: dict[tuple[int, int], _PresenceEntry] = {}
        self._lock = Lock()

    def touch(self, user_id: int, channel_id: int) -> None:
        seen_at = self._monotonic()
        entry = _PresenceEntry(
            seen_at=seen_at,
            presence=ListenerPresence(
                user_id=user_id,
                channel_id=channel_id,
                last_seen_at=self._now(),
            ),
        )
        with self._lock:
            self._prune_locked(seen_at)
            self._entries[(user_id, channel_id)] = entry

    def snapshot_by_user(self) -> dict[int, tuple[ListenerPresence, ...]]:
        current = self._monotonic()
        with self._lock:
            self._prune_locked(current)
            entries = tuple(entry.presence for entry in self._entries.values())

        grouped: dict[int, list[ListenerPresence]] = defaultdict(list)
        for presence in entries:
            grouped[presence.user_id].append(presence)
        return {
            user_id: tuple(
                sorted(
                    presences,
                    key=lambda presence: (presence.last_seen_at, presence.channel_id),
                    reverse=True,
                )
            )
            for user_id, presences in grouped.items()
        }

    def count(self, channel_id: int) -> int:
        current = self._monotonic()
        with self._lock:
            self._prune_locked(current)
            return sum(
                entry.presence.channel_id == channel_id for entry in self._entries.values()
            )

    def remove_user(self, user_id: int) -> None:
        with self._lock:
            for key in tuple(self._entries):
                if key[0] == user_id:
                    self._entries.pop(key, None)

    def remove_channel(self, channel_id: int) -> None:
        with self._lock:
            for key in tuple(self._entries):
                if key[1] == channel_id:
                    self._entries.pop(key, None)

    def _prune_locked(self, current: float) -> None:
        cutoff = current - self.timeout_seconds
        for key, entry in tuple(self._entries.items()):
            if entry.seen_at <= cutoff:
                self._entries.pop(key, None)
