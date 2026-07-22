from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.models import Channel, Track

from .conftest import csrf_headers


def _insert_track(client: TestClient, filename: str, digest: str) -> int:
    media_path = client.app.state.settings.paths.media_dir / filename
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"test-media")
    with client.app.state.database.session_factory.begin() as db:
        track = Track(
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


def test_hls_is_protected_and_authorized_by_channel(initialized_admin: TestClient) -> None:
    client = initialized_admin
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


def test_upload_rejects_unsupported_extension(initialized_admin: TestClient) -> None:
    client = initialized_admin
    response = client.post(
        "/api/admin/tracks/upload",
        headers=csrf_headers(client),
        files={"file": ("notes.txt", b"not audio", "text/plain")},
    )
    assert response.status_code == 415
