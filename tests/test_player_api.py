from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from backend.app.models import Channel, User, utcnow
from backend.app.services.player_tokens import initialize_player_credential

from .conftest import csrf_headers


def _player_path(client: TestClient, stream_format: str = "aac") -> str:
    response = client.post(
        "/api/auth/player-key/url",
        headers=csrf_headers(client),
        json={"channel_id": 1, "stream_format": stream_format},
    )
    assert response.status_code == 200, response.text
    return urlsplit(response.json()["url"]).path


def _assert_empty(response, status_code: int) -> None:
    assert response.status_code == status_code
    assert response.content == b""
    assert "content-type" not in response.headers


def test_player_key_state_hides_credential_and_regeneration_requires_csrf(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    anonymous = TestClient(client.app)
    with closing(anonymous):
        assert anonymous.get("/api/auth/player-key").status_code == 401
        assert anonymous.post("/api/auth/player-key/regenerate").status_code == 401

    state = client.get("/api/auth/player-key")
    assert state.status_code == 200
    assert state.json()["configured"] is True
    assert state.json()["valid_for_new_connections"] is True
    assert state.json()["lossless_available"] is True
    assert state.json()["created_at"].endswith("Z")
    assert state.json()["connect_before"].endswith("Z")
    assert "token" not in state.text.lower()
    assert "/listen/" not in state.text

    with client.app.state.database.session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        previous_generation = user.player_key_generation

    assert client.post("/api/auth/player-key/regenerate").status_code == 403
    regenerated = client.post(
        "/api/auth/player-key/regenerate",
        headers=csrf_headers(client),
    )
    assert regenerated.status_code == 200
    assert "token" not in regenerated.text.lower()
    with client.app.state.database.session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        assert user.player_key_generation != previous_generation


def test_upgraded_user_remains_unconfigured_until_manual_regeneration(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    with client.app.state.database.session_factory.begin() as db:
        user = db.get(User, 1)
        assert user is not None
        user.player_key_created_at = None
        user.player_key_generation = None

    state = client.get("/api/auth/player-key")
    assert state.json() == {
        "configured": False,
        "created_at": None,
        "connect_before": None,
        "valid_for_new_connections": False,
        "lossless_available": True,
    }
    missing = client.post(
        "/api/auth/player-key/url",
        headers=csrf_headers(client),
        json={"channel_id": 1, "stream_format": "aac"},
    )
    assert missing.status_code == 409
    assert missing.json()["code"] == "player_key_missing"

    enabled = client.post(
        "/api/auth/player-key/regenerate",
        headers=csrf_headers(client),
    )
    assert enabled.status_code == 200
    assert enabled.json()["configured"] is True


def test_player_url_is_returned_only_for_an_explicit_copy_request(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    assert client.post(
        "/api/auth/player-key/url",
        json={"channel_id": 1, "stream_format": "aac"},
    ).status_code == 403

    standard = client.post(
        "/api/auth/player-key/url",
        headers=csrf_headers(client),
        json={"channel_id": 1, "stream_format": "aac"},
    )
    assert standard.status_code == 200
    assert standard.json()["url"].startswith("http://testserver/listen/aac/pv1_")
    assert standard.json()["url"].endswith("/default")
    assert "player_key" not in standard.json()

    lossless = client.post(
        "/api/auth/player-key/url",
        headers=csrf_headers(client),
        json={"channel_id": 1, "stream_format": "flac"},
    )
    assert lossless.status_code == 200
    assert "/listen/flac/pv1_" in lossless.json()["url"]


def test_every_invalid_player_request_is_an_empty_404(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    invalid_paths = (
        "/listen",
        "/listen/",
        "/listen/aac/not-a-real-key/default",
        "/listen/flac/not-a-real-key/default",
        "/listen/unknown/not-a-real-key/default",
        "/listen/aac/not/a/valid/path",
    )
    for path in invalid_paths:
        _assert_empty(client.get(path), 404)
        _assert_empty(client.head(path), 404)
    _assert_empty(client.post("/listen/aac/not-a-real-key/default"), 404)
    _assert_empty(client.options("/listen/aac/not-a-real-key/default"), 404)
    _assert_empty(
        client.request("PROPFIND", "/listen/aac/not-a-real-key/default"),
        404,
    )


def test_disabled_channel_returns_empty_503_only_after_valid_token(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    path = _player_path(client)
    with client.app.state.database.session_factory.begin() as db:
        channel = db.get(Channel, 1)
        assert channel is not None
        channel.enabled = False

    _assert_empty(client.get(path), 503)
    _assert_empty(client.get("/listen/aac/not-a-real-key/default"), 404)


def test_listener_lossless_denial_is_an_empty_404(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    now = utcnow()
    with client.app.state.database.session_factory.begin() as db:
        listener = User(
            username="listener",
            username_normalized="listener",
            email="listener@example.com",
            email_normalized="listener@example.com",
            password_hash="not-used",
            role="listener",
            status="approved",
            approved_at=now,
            created_at=now,
            updated_at=now,
        )
        initialize_player_credential(listener, now)
        db.add(listener)
        db.flush()
        token = client.app.state.player_tokens.issue(listener)

    _assert_empty(client.get(f"/listen/flac/{token}/default"), 404)


def test_expired_connect_before_rejects_new_stream_but_can_be_refreshed(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    path = _player_path(client)
    with client.app.state.database.session_factory.begin() as db:
        user = db.get(User, 1)
        assert user is not None
        user.player_key_created_at = utcnow() - timedelta(days=31)

    state = client.get("/api/auth/player-key").json()
    assert state["configured"] is True
    assert state["valid_for_new_connections"] is False
    _assert_empty(client.get(path), 404)

    expired_url = client.post(
        "/api/auth/player-key/url",
        headers=csrf_headers(client),
        json={"channel_id": 1, "stream_format": "aac"},
    )
    assert expired_url.status_code == 409
    assert expired_url.json()["code"] == "player_key_expired"

    refreshed = client.post(
        "/api/auth/player-key/regenerate",
        headers=csrf_headers(client),
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["valid_for_new_connections"] is True


def test_valid_player_stream_returns_audio_and_only_static_station_title(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    path = _player_path(client)

    class FakeSubscription:
        def __init__(self) -> None:
            self.chunks: list[bytes | None] = [b"first-aac-frame", b"second-aac-frame", None]
            self.closed = False
            self.closed_event = asyncio.Event()

        async def receive(self, timeout_seconds: float | None = None) -> bytes | None:
            return self.chunks.pop(0)

        def close(self) -> None:
            self.closed = True
            self.closed_event.set()

    subscription = FakeSubscription()

    class FakePlayback:
        async def open_audio_stream(self, channel_id: int, stream_format: str):
            assert channel_id == 1
            assert stream_format == "aac"
            return subscription

        def touch_demand(self, channel_id: int) -> None:
            assert channel_id == 1

    client.app.state.playback = FakePlayback()
    response = client.get(path)
    assert response.status_code == 200
    assert response.content == b"first-aac-framesecond-aac-frame"
    assert response.headers["content-type"].startswith("audio/aac")
    assert response.headers["icy-name"] == "PuvuFM"
    assert "icy-description" not in response.headers
    assert "icy-genre" not in response.headers
    assert "icy-url" not in response.headers
    assert subscription.closed is True
    assert client.app.state.listeners.count(1) == 1


def test_player_interfaces_are_registered_without_a_successful_head_route(
    client: TestClient,
) -> None:
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "get" in paths["/api/auth/player-key"]
    assert "post" in paths["/api/auth/player-key/regenerate"]
    assert "post" in paths["/api/auth/player-key/url"]
    stream = paths["/listen/{stream_format}/{player_key}/{channel_slug}"]
    assert "get" in stream
    assert "head" not in stream
