from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.models import MusicLibrary, PlaylistItem, Track

from .conftest import csrf_headers


def _insert_tracks(
    client: TestClient,
    count: int,
    *,
    library_group: str | None = None,
    title_prefix: str = "Track",
) -> list[int]:
    track_ids: list[int] = []
    with client.app.state.database.session_factory.begin() as db:
        resolved_library = library_group or "default"
        if db.get(MusicLibrary, resolved_library) is None:
            db.add(MusicLibrary(name=resolved_library))
            db.flush()
        for index in range(count):
            values = {
                "storage_id": "primary",
                "storage_name": f"{library_group or 'model-default'}-{title_prefix}-{index}.flac",
                "original_filename": f"{title_prefix}-{index}.flac",
                "sha256": (
                    f"{library_group or 'default'}-{title_prefix}-{index}"
                    .encode()
                    .hex()[:64]
                    .ljust(64, "0")
                ),
                "file_size_bytes": 100 + index,
                "mime_type": "audio/flac",
                "audio_stream_index": 0,
                "duration_seconds": 120 + index,
                "title": f"{title_prefix} {index}",
                "artist": "Test Artist",
                "album": "Test Album",
                "available": True,
            }
            if library_group is not None:
                values["library_group"] = library_group
            track = Track(**values)
            db.add(track)
            db.flush()
            track_ids.append(track.id)
    return track_ids


def test_track_query_combines_library_search_and_fixed_database_page(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    default_ids = _insert_tracks(client, 12, title_prefix="Needle")
    _insert_tracks(client, 13, title_prefix="Other")
    _insert_tracks(client, 4, library_group="archive", title_prefix="Needle")

    first_page = client.get(
        "/api/admin/tracks",
        params={"library_group": "default", "search": "needle", "page": 1},
    )
    assert first_page.status_code == 200, first_page.text
    payload = first_page.json()
    assert payload["page_size"] == 10
    assert payload["page"] == 1
    assert payload["total"] == 12
    assert payload["total_pages"] == 2
    assert len(payload["items"]) == 10
    assert all(track["library_group"] == "default" for track in payload["items"])
    assert all("needle" in track["title"].lower() for track in payload["items"])
    assert payload["library_groups"] == ["default", "archive"]

    second_page = client.get(
        "/api/admin/tracks",
        params={"library_group": "default", "search": "needle", "page": 2},
    )
    assert second_page.status_code == 200
    assert second_page.json()["total"] == 12
    assert len(second_page.json()["items"]) == 2

    archive = client.get(
        "/api/admin/tracks",
        params={"library_group": "archive", "search": "needle", "page": 1},
    )
    assert archive.status_code == 200
    assert archive.json()["total"] == 4
    assert all(track["library_group"] == "archive" for track in archive.json()["items"])

    with client.app.state.database.session_factory.begin() as db:
        db.add_all(
            [
                PlaylistItem(channel_id=1, track_id=track_id, position=position)
                for position, track_id in enumerate(default_ids[:3])
            ]
        )
    candidates = client.get(
        "/api/admin/tracks",
        params={
            "library_group": "default",
            "search": "needle",
            "available_only": True,
            "exclude_channel_id": 1,
        },
    )
    assert candidates.status_code == 200
    assert candidates.json()["total"] == 9
    assert len(candidates.json()["items"]) == 9
    assert not set(default_ids[:3]).intersection(
        track["id"] for track in candidates.json()["items"]
    )


def test_batch_move_tracks_between_libraries(initialized_admin: TestClient) -> None:
    client = initialized_admin
    headers = csrf_headers(client)
    track_ids = _insert_tracks(client, 12, title_prefix="Move")
    created = client.post(
        "/api/admin/track-libraries",
        headers=headers,
        json={"name": "favorites"},
    )
    assert created.status_code == 201, created.text

    moved = client.patch(
        "/api/admin/tracks/library",
        headers=headers,
        json={
            "source_library": "default",
            "target_library": "favorites",
            "track_ids": track_ids[:11],
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] == 11
    assert moved.json()["library_groups"] == ["default", "favorites"]

    destination = client.get(
        "/api/admin/tracks",
        params={"library_group": "favorites", "page": 1},
    )
    assert destination.status_code == 200
    assert destination.json()["total"] == 11
    assert len(destination.json()["items"]) == 10
    assert all(track["library_group"] == "favorites" for track in destination.json()["items"])

    source = client.get("/api/admin/tracks", params={"library_group": "default"})
    assert source.status_code == 200
    assert source.json()["total"] == 1

    stale_source = client.patch(
        "/api/admin/tracks/library",
        headers=headers,
        json={
            "source_library": "default",
            "target_library": "favorites",
            "track_ids": track_ids[:2],
        },
    )
    assert stale_source.status_code == 409

    invalid_name = client.patch(
        "/api/admin/tracks/library",
        headers=headers,
        json={
            "source_library": "default",
            "target_library": "../outside",
            "track_ids": [track_ids[-1]],
        },
    )
    assert invalid_name.status_code == 422

    renamed = client.patch(
        "/api/admin/track-libraries/favorites",
        headers=headers,
        json={"name": "favorites-renamed"},
    )
    assert renamed.status_code == 200, renamed.text
    renamed_destination = client.get(
        "/api/admin/tracks",
        params={"library_group": "favorites-renamed"},
    )
    assert renamed_destination.status_code == 200
    assert renamed_destination.json()["total"] == 11
    assert client.get(
        "/api/admin/tracks",
        params={"library_group": "favorites"},
    ).status_code == 404

    non_empty_delete = client.delete(
        "/api/admin/track-libraries/favorites-renamed",
        headers=headers,
    )
    assert non_empty_delete.status_code == 409

    moved_back = client.patch(
        "/api/admin/tracks/library",
        headers=headers,
        json={
            "source_library": "favorites-renamed",
            "target_library": "default",
            "track_ids": track_ids[:11],
        },
    )
    assert moved_back.status_code == 200
    removed = client.delete(
        "/api/admin/track-libraries/favorites-renamed",
        headers=headers,
    )
    assert removed.status_code == 204


def test_music_library_lifecycle_and_default_protection(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    headers = csrf_headers(client)

    created = client.post(
        "/api/admin/track-libraries",
        headers=headers,
        json={"name": "ambient"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["library"]["name"] == "ambient"
    assert created.json()["library"]["track_count"] == 0

    duplicate = client.post(
        "/api/admin/track-libraries",
        headers=headers,
        json={"name": "ambient"},
    )
    assert duplicate.status_code == 409

    listed = client.get("/api/admin/track-libraries")
    assert listed.status_code == 200
    assert [library["name"] for library in listed.json()] == ["default", "ambient"]

    renamed = client.patch(
        "/api/admin/track-libraries/ambient",
        headers=headers,
        json={"name": "ambient-night"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["library"]["name"] == "ambient-night"

    protected_rename = client.patch(
        "/api/admin/track-libraries/default",
        headers=headers,
        json={"name": "renamed-default"},
    )
    assert protected_rename.status_code == 409
    protected_delete = client.delete(
        "/api/admin/track-libraries/default",
        headers=headers,
    )
    assert protected_delete.status_code == 409

    removed = client.delete(
        "/api/admin/track-libraries/ambient-night",
        headers=headers,
    )
    assert removed.status_code == 204
    assert [library["name"] for library in client.get("/api/admin/track-libraries").json()] == [
        "default"
    ]
