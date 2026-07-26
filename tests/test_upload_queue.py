from __future__ import annotations

import time
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import AuditEvent, Track, UploadJob, utcnow

from .conftest import csrf_headers

CLIENT_ID = "queue-test-client-000000000001"


def _reserve(client: TestClient, index: int):
    return client.post(
        "/api/admin/uploads",
        headers=csrf_headers(client),
        json={
            "client_id": CLIENT_ID,
            "filename": f"track-{index}.mp3",
            "size_bytes": 1024 + index,
        },
    )


def _wait_for_job_status(
    client: TestClient,
    job_id: str,
    statuses: set[str],
    timeout: float = 5,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    job: dict[str, object] | None = None
    while time.monotonic() < deadline:
        snapshot = client.get("/api/admin/uploads")
        assert snapshot.status_code == 200, snapshot.text
        job = next(item for item in snapshot.json()["jobs"] if item["id"] == job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"upload job did not reach {statuses}: {job}")


def test_public_queue_enforces_capacity_and_parallel_limit(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    created = []
    for index in range(10):
        response = _reserve(client, index)
        assert response.status_code == 201, response.text
        created.append(response.json()["job"])

    full = _reserve(client, 10)
    assert full.status_code == 409
    assert full.json()["code"] == "upload_queue_full"

    deadline = time.monotonic() + 5
    snapshot = None
    while time.monotonic() < deadline:
        response = client.get("/api/admin/uploads")
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot["active_count"] == 3:
            break
        time.sleep(0.05)
    assert snapshot is not None
    created_ids = {job["id"] for job in created}
    statuses = [job["status"] for job in snapshot["jobs"] if job["id"] in created_ids]
    assert statuses.count("ready") == 3
    assert statuses.count("queued") == 7
    assert snapshot["queue_limit"] == 10
    assert snapshot["max_concurrent"] == 3
    assert snapshot["available_slots"] == 0

    cancelled = next(job for job in snapshot["jobs"] if job["status"] == "ready")
    response = client.delete(
        f"/api/admin/uploads/{cancelled['id']}",
        headers=csrf_headers(client),
    )
    assert response.status_code == 204
    replacement = _reserve(client, 11)
    assert replacement.status_code == 201, replacement.text


def test_stale_upload_page_expires_job_and_removes_temporary_file(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    client.app.state.settings.uploads.heartbeat_timeout_seconds = 1
    response = _reserve(client, 1)
    assert response.status_code == 201, response.text
    job_id = response.json()["job"]["id"]
    _wait_for_job_status(client, job_id, {"ready"})

    temp_name = f"{job_id}.part.mp3"
    temp_path = client.app.state.storage.upload_dir / temp_name
    temp_path.write_bytes(b"incomplete")
    with client.app.state.database.session_factory.begin() as db:
        job = db.get(UploadJob, job_id)
        assert job is not None
        job.status = "uploading"
        job.temp_name = temp_name
        job.client_seen_at = utcnow() - timedelta(seconds=10)
        job.updated_at = utcnow()

    expired = _wait_for_job_status(client, job_id, {"expired"}, timeout=4)
    assert expired["error_code"] == "client_disconnected"
    assert not temp_path.exists()


def test_page_close_expires_all_client_jobs(initialized_admin: TestClient) -> None:
    client = initialized_admin
    first = _reserve(client, 1)
    second = _reserve(client, 2)
    assert first.status_code == second.status_code == 201
    response = client.post(
        "/api/admin/uploads/expire",
        headers=csrf_headers(client),
        json={"client_id": CLIENT_ID},
    )
    assert response.status_code == 204

    snapshot = client.get("/api/admin/uploads").json()
    job_ids = {first.json()["job"]["id"], second.json()["job"]["id"]}
    jobs = [job for job in snapshot["jobs"] if job["id"] in job_ids]
    assert len(jobs) == 2
    assert all(job["status"] == "expired" for job in jobs)
    assert all(job["error_code"] == "page_closed" for job in jobs)


def test_similar_name_requires_confirmation_before_reservation(
    initialized_admin: TestClient,
) -> None:
    client = initialized_admin
    with client.app.state.database.session_factory.begin() as db:
        db.add(
            Track(
                storage_id="primary",
                storage_name="existing-track.flac",
                original_filename="01 - Example Artist - Example Song.flac",
                sha256="a" * 64,
                file_size_bytes=4096,
                mime_type="audio/flac",
                audio_stream_index=0,
                duration_seconds=180,
                sample_rate=48000,
                channels=2,
                bits_per_sample=24,
                normalized=False,
                title="Example Song",
                artist="Example Artist",
                album="Example Album",
                available=True,
            )
        )

    preflight = client.post(
        "/api/admin/uploads/preflight",
        headers=csrf_headers(client),
        json={
            "filenames": [
                "Example Artist - Example Song.mp3",
                "Completely Different Track.mp3",
            ],
        },
    )
    assert preflight.status_code == 200, preflight.text
    checked_files = preflight.json()["files"]
    assert checked_files[0]["filename"] == "Example Artist - Example Song.mp3"
    assert checked_files[0]["candidates"][0]["title"] == "Example Song"
    assert checked_files[1]["candidates"] == []
    assert client.get("/api/admin/uploads").json()["jobs"] == []

    response = client.post(
        "/api/admin/uploads",
        headers=csrf_headers(client),
        json={
            "client_id": CLIENT_ID,
            "filename": "Example Artist - Example Song.mp3",
            "size_bytes": 2048,
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "similar_tracks_found"
    assert response.json()["details"]["candidates"][0]["title"] == "Example Song"
    assert client.get("/api/admin/uploads").json()["jobs"] == []

    confirmed = client.post(
        "/api/admin/uploads",
        headers=csrf_headers(client),
        json={
            "client_id": CLIENT_ID,
            "filename": "Example Artist - Example Song.mp3",
            "size_bytes": 2048,
            "confirm_similar": True,
        },
    )
    assert confirmed.status_code == 201


def test_sha_duplicate_result_is_automatically_rejected(
    initialized_admin: TestClient,
    monkeypatch,
    tmp_path,
) -> None:
    client = initialized_admin
    owner_id = int(client.get("/api/auth/me").json()["user"]["id"])
    now = utcnow()
    with client.app.state.database.session_factory.begin() as db:
        track = Track(
            storage_id="primary",
            storage_name="existing.flac",
            original_filename="existing.flac",
            sha256="b" * 64,
            file_size_bytes=4096,
            mime_type="audio/flac",
            audio_stream_index=0,
            duration_seconds=180,
            sample_rate=48000,
            channels=2,
            bits_per_sample=24,
            normalized=False,
            title="Existing Song",
            artist="Example Artist",
            album="",
            available=True,
        )
        db.add(track)
        db.flush()
        track_id = track.id
        db.add(
            UploadJob(
                id="f" * 32,
                owner_user_id=owner_id,
                client_id=CLIENT_ID,
                original_filename="duplicate.flac",
                declared_size_bytes=4096,
                bytes_received=4096,
                status="verifying",
                temp_name="duplicate.part.flac",
                client_seen_at=now,
                started_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    def duplicate_result(*_args, **_kwargs):
        with client.app.state.database.session_factory() as db:
            return db.get(Track, track_id), True

    monkeypatch.setattr(client.app.state.media, "import_staged", duplicate_result)
    client.app.state.uploads._process_sync("f" * 32, tmp_path / "unused.flac")

    with client.app.state.database.session_factory() as db:
        job = db.get(UploadJob, "f" * 32)
        assert job is not None
        assert job.status == "rejected"
        assert job.duplicate is True
        assert job.track_id == track_id
        assert job.error_code == "duplicate_content"
        event = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "track.upload_rejected")
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None
        assert event.details["reason"] == "sha256_match"
