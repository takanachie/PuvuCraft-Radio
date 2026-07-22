from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.database import Database

from .conftest import PROJECT_ROOT


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("bootstrap", "require_one_time_token", False),
        ("auth.csrf", "enabled", False),
        ("auth.session", "same_site", "none"),
        ("auth.registration", "require_approval", False),
    ],
)
def test_v1_security_invariants_cannot_be_disabled(
    settings: Settings,
    section: str,
    key: str,
    value: object,
) -> None:
    payload = settings.model_dump()
    target = payload
    for part in section.split("."):
        target = target[part]
    target[key] = value
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_seed_channel_slug_cannot_escape_hls_root(settings: Settings) -> None:
    payload = settings.model_dump()
    payload["seed"]["channels"][0]["slug"] = "../../media"
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_production_startup_refuses_an_unmigrated_database(settings: Settings) -> None:
    settings.app.environment = "production"
    settings.app.public_base_url = "https://radio.example.com"
    settings.auth.session.secure_cookie = True
    settings.config_path = PROJECT_ROOT / "config.yaml"
    database = Database(settings)
    try:
        with pytest.raises(RuntimeError, match="database migration is not current"):
            database.initialize()
    finally:
        database.close()
