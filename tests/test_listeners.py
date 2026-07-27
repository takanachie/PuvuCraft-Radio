from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.services.listeners import ListenerRegistry


def test_listener_registry_deduplicates_refreshes_and_expires_presence() -> None:
    elapsed = [100.0]
    started_at = datetime(2026, 7, 28, tzinfo=UTC)
    registry = ListenerRegistry(
        10,
        monotonic=lambda: elapsed[0],
        now=lambda: started_at + timedelta(seconds=elapsed[0] - 100),
    )

    registry.touch(user_id=1, channel_id=1)
    elapsed[0] += 4
    registry.touch(user_id=1, channel_id=2)
    registry.touch(user_id=2, channel_id=1)

    assert registry.count(1) == 2
    assert [item.channel_id for item in registry.snapshot_by_user()[1]] == [2, 1]

    elapsed[0] += 5
    registry.touch(user_id=1, channel_id=1)
    assert registry.count(1) == 2

    elapsed[0] += 6
    assert registry.count(1) == 1
    assert [item.channel_id for item in registry.snapshot_by_user()[1]] == [1]

    registry.remove_user(1)
    assert registry.snapshot_by_user() == {}


def test_listener_registry_can_clear_a_removed_channel() -> None:
    registry = ListenerRegistry(20)
    registry.touch(user_id=1, channel_id=1)
    registry.touch(user_id=2, channel_id=1)
    registry.touch(user_id=2, channel_id=2)

    registry.remove_channel(1)

    assert registry.count(1) == 0
    assert registry.count(2) == 1
    assert list(registry.snapshot_by_user()) == [2]
