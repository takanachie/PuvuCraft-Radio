from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("RADIO_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")

from backend.app.config import Settings, load_settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    value = load_settings(PROJECT_ROOT / "config.example.yaml")
    value.app.environment = "test"
    value.app.public_base_url = "http://testserver"
    value.paths.data_dir = tmp_path
    value.paths.media_dir = tmp_path / "media"
    value.paths.upload_temp_dir = tmp_path / "tmp" / "uploads"
    value.paths.cover_dir = tmp_path / "covers"
    value.paths.hls_dir = tmp_path / "runtime" / "hls"
    value.paths.log_dir = tmp_path / "logs"
    value.paths.bootstrap_token_file = tmp_path / "bootstrap.token"
    value.database.url = f"sqlite:///{tmp_path / 'radio.db'}"
    value.media.import_directories = [tmp_path / "import"]
    value.ffmpeg.binary = tmp_path / "missing-ffmpeg"
    value.ffmpeg.ffprobe_binary = tmp_path / "missing-ffprobe"
    value.streaming.always_on = False
    value.auth.rate_limits.login_per_minute = 100
    value.auth.rate_limits.register_per_hour = 100
    value.auth.rate_limits.setup_per_minute = 100
    value.logging.file = tmp_path / "logs" / "test.log"
    value.config_path = tmp_path / "config.yaml"
    return value


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["radio_csrf"]}


@pytest.fixture
def initialized_admin(client: TestClient, settings: Settings) -> TestClient:
    token = settings.paths.bootstrap_token_file.read_text(encoding="utf-8").strip()
    response = client.post(
        "/api/setup",
        json={
            "token": token,
            "username": "admin",
            "email": "admin@example.com",
            "password": "secure-admin-password",
        },
    )
    assert response.status_code == 201, response.text
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secure-admin-password"},
    )
    assert response.status_code == 200, response.text
    return client
