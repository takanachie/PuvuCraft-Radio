from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app

from .conftest import csrf_headers


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

        with source.open("rb") as audio:
            upload = client.post(
                "/api/admin/tracks/upload",
                headers=csrf_headers(client),
                files={"file": (source.name, audio, "audio/mpeg")},
            )
        assert upload.status_code == 201, upload.text
        track_id = upload.json()["track"]["id"]
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
