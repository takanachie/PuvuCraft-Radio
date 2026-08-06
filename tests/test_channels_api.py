from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.app.models import Channel, Track

from .conftest import csrf_headers


def _insert_track(client: TestClient, filename: str, digest: str) -> int:
    storage = client.app.state.settings.storage.locations[0]
    media_path = storage.root / filename
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"test-media")
    with client.app.state.database.session_factory.begin() as db:
        track = Track(
            storage_id=storage.id,
            storage_name=filename,
            original_filename=filename,
            sha256=digest,
            file_size_bytes=10,
            mime_type="audio/mpeg",
            audio_stream_index=0,
            duration_seconds=120,
            title=f"Track {filename}",
            artist="Test Artist",
            album="Test Album",
            available=True,
        )
        db.add(track)
        db.flush()
        return track.id


def _mark_hls_ready_without_ffmpeg(client: TestClient) -> None:
    async def ready(channel_id: int) -> None:
        assert channel_id == 1

    client.app.state.playback.ensure_hls_stream = ready


def test_channel_and_playlist_management(initialized_admin: TestClient) -> None:
    client = initialized_admin
    headers = csrf_headers(client)
    created = client.post(
        "/api/admin/channels",
        headers=headers,
        json={
            "name": "夜间频道",
            "slug": "night-radio",
            "description": "Night transmission",
            "enabled": True,
            "playback_mode": "sequential",
            "display_order": 2,
        },
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["channel"]["id"]

    first_id = _insert_track(client, "one.mp3", "1" * 64)
    second_id = _insert_track(client, "two.mp3", "2" * 64)
    first = client.post(
        f"/api/admin/channels/{channel_id}/playlist",
        headers=headers,
        json={"track_id": first_id},
    )
    second = client.post(
        f"/api/admin/channels/{channel_id}/playlist",
        headers=headers,
        json={"track_id": second_id},
    )
    assert first.status_code == second.status_code == 201
    first_item = first.json()["item"]["id"]
    second_item = second.json()["item"]["id"]

    reordered = client.post(
        f"/api/admin/channels/{channel_id}/playlist/reorder",
        headers=headers,
        json={"item_ids": [second_item, first_item]},
    )
    assert reordered.status_code == 200, reordered.text
    assert [item["id"] for item in reordered.json()] == [second_item, first_item]

    listener_view = client.get(f"/api/channels/{channel_id}/playlist")
    assert listener_view.status_code == 200
    assert len(listener_view.json()) == 2
    assert "track" in listener_view.json()[0]

    in_use = client.delete(f"/api/admin/tracks/{first_id}", headers=headers)
    assert in_use.status_code == 409
    removed = client.delete(
        f"/api/admin/channels/{channel_id}/playlist/{first_item}", headers=headers
    )
    assert removed.status_code == 204
    assert client.delete(f"/api/admin/tracks/{first_id}", headers=headers).status_code == 204

    disabled = client.patch(
        f"/api/admin/channels/{channel_id}",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert client.post(f"/api/admin/channels/{channel_id}/skip", headers=headers).status_code == 409


def test_batch_add_playlist_items_is_ordered_and_skips_existing(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    headers = csrf_headers(client)
    track_ids = [
        _insert_track(client, f"batch-{index}.mp3", f"{index + 10:064x}")
        for index in range(4)
    ]
    first = client.post(
        "/api/admin/channels/1/playlist",
        headers=headers,
        json={"track_id": track_ids[0]},
    )
    assert first.status_code == 201

    response = client.post(
        "/api/admin/channels/1/playlist/batch",
        headers=headers,
        json={"track_ids": track_ids},
    )
    assert response.status_code == 201, response.text
    assert response.json()["skipped_existing"] == 1
    assert [item["track"]["id"] for item in response.json()["items"]] == track_ids[1:]

    playlist = client.get("/api/admin/channels/1/playlist")
    assert playlist.status_code == 200
    assert [item["track"]["id"] for item in playlist.json()] == track_ids

    repeated = client.post(
        "/api/admin/channels/1/playlist/batch",
        headers=headers,
        json={"track_ids": track_ids},
    )
    assert repeated.status_code == 201
    assert repeated.json() == {"items": [], "skipped_existing": len(track_ids)}

    extra_track_id = _insert_track(client, "batch-atomic.mp3", "f" * 64)
    unavailable = client.post(
        "/api/admin/channels/1/playlist/batch",
        headers=headers,
        json={"track_ids": [extra_track_id, 999999]},
    )
    assert unavailable.status_code == 404
    playlist_after_failure = client.get("/api/admin/channels/1/playlist")
    assert extra_track_id not in [item["track"]["id"] for item in playlist_after_failure.json()]

    duplicated_input = client.post(
        "/api/admin/channels/1/playlist/batch",
        headers=headers,
        json={"track_ids": [extra_track_id, extra_track_id]},
    )
    assert duplicated_input.status_code == 422


def test_hls_is_protected_and_authorized_by_channel(initialized_admin: TestClient) -> None:
    client = initialized_admin
    _mark_hls_ready_without_ffmpeg(client)
    with client.app.state.database.session_factory.begin() as db:
        channel = db.get(Channel, 1)
        assert channel is not None and channel.playback_state is not None
        channel.playback_state.status = "live"
    anonymous = TestClient(client.app)
    with anonymous:
        assert anonymous.get("/hls/default/index.m3u8").status_code == 401

    allowed = client.get(
        "/api/internal/stream-auth",
        headers={"X-Original-URI": "/hls/default/index.m3u8"},
    )
    assert allowed.status_code == 204
    denied = client.get(
        "/api/internal/stream-auth",
        headers={"X-Original-URI": "/hls/not-found/index.m3u8"},
    )
    assert denied.status_code == 403
    internal_file = client.get(
        "/api/internal/stream-auth",
        headers={"X-Original-URI": "/hls/default/working.m3u8"},
    )
    assert internal_file.status_code == 403
    assert client.get("/api/internal/admin-auth").status_code == 204


def test_hls_authorization_reuses_bounded_session_and_channel_caches(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    _mark_hls_ready_without_ffmpeg(client)
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    event.listen(client.app.state.database.engine, "before_cursor_execute", record_statement)
    try:
        for path in ("/hls/default/index.m3u8", "/hls/default/g1-seg-1.ts"):
            response = client.get(
                "/api/internal/stream-auth",
                headers={"X-Original-URI": path},
            )
            assert response.status_code == 204
    finally:
        event.remove(client.app.state.database.engine, "before_cursor_execute", record_statement)

    session_reads = [sql for sql in statements if "FROM sessions" in sql]
    channel_reads = [sql for sql in statements if "FROM channels" in sql]
    assert len(session_reads) == 1, statements
    assert channel_reads == []


def test_logout_immediately_invalidates_hls_authorization_cache(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    _mark_hls_ready_without_ffmpeg(client)
    headers = {"X-Original-URI": "/hls/default/index.m3u8"}
    session_cookie = client.cookies["radio_session"]
    assert client.get("/api/internal/stream-auth", headers=headers).status_code == 204
    assert client.post("/api/auth/logout", headers=csrf_headers(client)).status_code == 204
    assert (
        client.get(
            "/api/internal/stream-auth",
            headers={**headers, "Cookie": f"radio_session={session_cookie}"},
        ).status_code
        == 401
    )


def test_hls_activity_is_exposed_as_current_listener_status(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    _mark_hls_ready_without_ffmpeg(client)
    current_user = client.get("/api/auth/me").json()["user"]
    with client.app.state.database.session_factory.begin() as db:
        channel = db.get(Channel, 1)
        assert channel is not None and channel.playback_state is not None
        channel_name = channel.name
        channel.playback_state.status = "live"

    users = client.get("/api/admin/users").json()
    current = next(user for user in users if user["id"] == current_user["id"])
    assert current["listening"] == {
        "online": False,
        "channels": [],
        "last_seen_at": None,
    }

    denied = client.get(
        "/api/internal/stream-auth",
        headers={"X-Original-URI": "/hls/not-found/index.m3u8"},
    )
    assert denied.status_code == 403
    assert client.app.state.listeners.count(1) == 0

    for path in ("/hls/default/index.m3u8", "/hls/default/g1-seg-1.ts"):
        allowed = client.get(
            "/api/internal/stream-auth",
            headers={"X-Original-URI": path},
        )
        assert allowed.status_code == 204

    assert client.app.state.listeners.count(1) == 1
    users = client.get("/api/admin/users").json()
    current = next(user for user in users if user["id"] == current_user["id"])
    assert current["listening"]["online"] is True
    assert current["listening"]["last_seen_at"].endswith("Z")
    assert current["listening"]["channels"] == [
        {
            "id": 1,
            "name": channel_name,
            "slug": "default",
            "last_seen_at": current["listening"]["last_seen_at"],
        }
    ]
    assert client.get("/api/admin/listeners").json() == [
        {
            "user_id": current_user["id"],
            **current["listening"],
        }
    ]


def test_upload_rejects_unsupported_extension(initialized_admin: TestClient) -> None:
    client = initialized_admin
    response = client.post(
        "/api/admin/uploads",
        headers=csrf_headers(client),
        json={
            "client_id": "a" * 32,
            "filename": "notes.txt",
            "size_bytes": 9,
        },
    )
    assert response.status_code == 415
