from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models import Track

from .conftest import csrf_headers


def _reserve_and_upload(
    client: TestClient,
    source: Path,
    client_id: str = "integration-upload-client-0001",
) -> dict[str, object]:
    headers = csrf_headers(client)
    reserved = client.post(
        "/api/admin/uploads",
        headers=headers,
        json={
            "client_id": client_id,
            "filename": source.name,
            "size_bytes": source.stat().st_size,
        },
    )
    assert reserved.status_code == 201, reserved.text
    job_id = reserved.json()["job"]["id"]

    deadline = time.monotonic() + 10
    job: dict[str, object] | None = None
    while time.monotonic() < deadline:
        snapshot = client.get("/api/admin/uploads")
        assert snapshot.status_code == 200, snapshot.text
        job = next(item for item in snapshot.json()["jobs"] if item["id"] == job_id)
        if job["status"] == "ready":
            break
        client.post(
            "/api/admin/uploads/heartbeat",
            headers=headers,
            json={"client_id": client_id},
        )
        time.sleep(0.05)
    assert job is not None and job["status"] == "ready", job

    upload_headers = {
        **headers,
        "Content-Type": "application/octet-stream",
        "X-Upload-Client-ID": client_id,
    }
    uploaded = client.put(
        f"/api/admin/uploads/{job_id}/content",
        headers=upload_headers,
        content=source.read_bytes(),
    )
    assert uploaded.status_code == 202, uploaded.text

    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        snapshot = client.get("/api/admin/uploads")
        job = next(item for item in snapshot.json()["jobs"] if item["id"] == job_id)
        if job["status"] in {"completed", "failed", "rejected"}:
            break
        time.sleep(0.1)
    assert job is not None and job["status"] == "completed", job
    return job


@pytest.mark.integration
def test_uploaded_audio_produces_authenticated_hls(settings, tmp_path: Path) -> None:
    ffmpeg = Path(os.getenv("RADIO_TEST_FFMPEG", "/usr/bin/ffmpeg"))
    ffprobe = ffmpeg.with_name("ffprobe")
    if not ffmpeg.is_file() or not ffprobe.is_file():
        pytest.skip("FFmpeg integration binary is unavailable")

    settings.ffmpeg.binary = ffmpeg
    settings.ffmpeg.ffprobe_binary = ffprobe
    settings.streaming.always_on = True
    settings.streaming.hls.segment_duration_seconds = 1
    settings.streaming.hls.playlist_segments = 3
    source = tmp_path / "tone.mp3"
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            "-y",
            str(source),
        ],
        check=True,
        timeout=30,
    )

    app = create_app(settings)
    with TestClient(app) as client:
        token = settings.paths.bootstrap_token_file.read_text(encoding="utf-8").strip()
        setup = client.post(
            "/api/setup",
            json={
                "token": token,
                "username": "admin",
                "email": "admin@example.com",
                "password": "secure-admin-password",
            },
        )
        assert setup.status_code == 201, setup.text
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secure-admin-password"},
        )
        assert login.status_code == 200, login.text

        upload = _reserve_and_upload(client, source)
        track_id = upload["track_id"]
        with client.app.state.database.session_factory() as db:
            track = db.get(Track, int(track_id))
            assert track is not None
            assert track.normalized is False
            assert track.storage_name.endswith(".mp3")
        add = client.post(
            "/api/admin/channels/1/playlist",
            headers=csrf_headers(client),
            json={"track_id": track_id},
        )
        assert add.status_code == 201, add.text

        manifest = settings.paths.hls_dir / "default" / "index.m3u8"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not manifest.is_file():
            time.sleep(0.2)
        assert manifest.is_file()
        text = manifest.read_text(encoding="utf-8")
        assert "#EXTM3U" in text
        assert "#EXT-X-ENDLIST" not in text
        assert list(manifest.parent.glob("*.ts"))

        stream = client.get("/hls/default/index.m3u8")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("application/vnd.apple.mpegurl")


@pytest.mark.integration
def test_overspec_audio_is_normalized_to_playback_limits(
    initialized_admin: TestClient,
    settings,
    tmp_path: Path,
) -> None:
    ffmpeg = Path(os.getenv("RADIO_TEST_FFMPEG", "/usr/bin/ffmpeg"))
    ffprobe = ffmpeg.with_name("ffprobe")
    if not ffmpeg.is_file() or not ffprobe.is_file():
        pytest.skip("FFmpeg integration binary is unavailable")
    settings.ffmpeg.binary = ffmpeg
    settings.ffmpeg.ffprobe_binary = ffprobe

    source = tmp_path / "overspec.wav"
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:sample_rate=96000:duration=1",
            "-ar",
            "96000",
            "-ac",
            "6",
            "-c:a",
            "pcm_f64le",
            "-y",
            str(source),
        ],
        check=True,
        timeout=30,
    )

    job = _reserve_and_upload(
        initialized_admin,
        source,
        client_id="normalization-client-00000001",
    )
    with initialized_admin.app.state.database.session_factory() as db:
        track = db.get(Track, int(job["track_id"]))
        assert track is not None
        assert track.normalized is True
        assert track.sample_rate <= settings.streaming.output.sample_rate
        assert track.channels <= settings.streaming.output.channels
        assert track.bits_per_sample <= settings.streaming.output.sample_bits
        assert track.storage_name.endswith(".flac")
        retained = initialized_admin.app.state.storage.track_path(track)
        assert retained.is_file()

    probe = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels,bits_per_raw_sample,sample_fmt",
            "-of",
            "json",
            str(retained),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert int(stream["sample_rate"]) <= settings.streaming.output.sample_rate
    assert int(stream["channels"]) <= settings.streaming.output.channels
    assert not list(settings.uploads.temp_dir.rglob("*.*"))
