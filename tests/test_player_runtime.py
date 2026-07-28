from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.models import User
from backend.app.services.audio_streams import (
    AudioFanout,
    FlacAudioFanout,
    PlayerConnectionCapacityError,
    PlayerConnectionRegistry,
)
from backend.app.services.player_tokens import InvalidPlayerToken


def test_player_token_is_stable_for_one_generation_and_rejects_tampering(
    initialized_admin: TestClient,
) -> None:
    service = initialized_admin.app.state.player_tokens
    with initialized_admin.app.state.database.session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        first = service.issue(user)
        second = service.issue(user)
        assert first == second
        assert service.validate(db, first).user.id == user.id
        tamper_index = len(first) // 2
        replacement = "A" if first[tamper_index] != "A" else "B"
        tampered = f"{first[:tamper_index]}{replacement}{first[tamper_index + 1:]}"
        with pytest.raises(InvalidPlayerToken):
            service.validate(db, tampered)


@pytest.mark.asyncio
async def test_latest_connection_supersedes_previous_for_same_token() -> None:
    registry = PlayerConnectionRegistry(
        max_connections=1,
        takeover_timeout_seconds=10,
    )
    await registry.start()
    first = await registry.activate((1, b"a" * 16))
    second = await registry.activate((1, b"a" * 16))
    assert first.superseded.is_set()
    assert not second.superseded.is_set()

    with pytest.raises(PlayerConnectionCapacityError):
        await registry.activate((2, b"b" * 16))

    await registry.release(first)
    await registry.release(second)
    await registry.stop()


@pytest.mark.asyncio
async def test_slow_audio_subscriber_is_dropped_without_blocking_fanout() -> None:
    fanout = AudioFanout(max_buffer_bytes=8)
    slow = fanout.subscribe()
    healthy = fanout.subscribe()

    fanout.publish(b"1111")
    assert await healthy.receive() == b"1111"
    fanout.publish(b"2222")
    fanout.publish(b"3333")

    assert await healthy.receive() == b"2222"
    assert await healthy.receive() == b"3333"
    assert await slow.receive() is None
    assert fanout.listener_count == 1

    healthy.close()
    assert fanout.listener_count == 0


@pytest.mark.asyncio
async def test_late_flac_subscriber_receives_metadata_and_a_frame_boundary() -> None:
    fanout = FlacAudioFanout(max_buffer_bytes=1024)
    metadata = b"fLaC\x80\x00\x00\x00"
    first_frame = b"\xff\xf8\x10\x00first"
    fanout.publish(metadata + first_frame)

    subscription = fanout.subscribe()
    assert await subscription.receive() == metadata + first_frame
    subscription.close()
