from __future__ import annotations

import time
from datetime import timedelta

from fastapi.testclient import TestClient

from backend.app.models import UploadJob, utcnow

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
