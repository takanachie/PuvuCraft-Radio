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


def test_production_sqlite_requires_wal(settings: Settings) -> None:
    payload = settings.model_dump()
    payload["app"]["environment"] = "production"
    payload["app"]["public_base_url"] = "https://radio.example.com"
    payload["auth"]["session"]["secure_cookie"] = True
    payload["database"]["sqlite_wal"] = False
    with pytest.raises(ValidationError, match="requires WAL mode"):
        Settings.model_validate(payload)


def test_upload_queue_limit_is_fixed_at_twenty(settings: Settings) -> None:
    payload = settings.model_dump()
    payload["uploads"]["queue_limit"] = 21
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_listener_presence_timeout_has_a_safe_lower_bound(settings: Settings) -> None:
    payload = settings.model_dump()
    payload["stream_access"]["listener_timeout_seconds"] = 4
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("connect_before_days", 31),
        ("takeover_timeout_seconds", 9),
    ],
)
def test_player_security_windows_are_fixed(
    settings: Settings,
    key: str,
    value: int,
) -> None:
    payload = settings.model_dump()
    payload["player_api"][key] = value
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_storage_location_ids_must_be_unique(settings: Settings) -> None:
    payload = settings.model_dump()
    payload["storage"]["locations"].append(payload["storage"]["locations"][0].copy())
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_sqlite_runtime_is_durable_and_memory_bounded(settings: Settings) -> None:
    database = Database(settings)
    try:
        database.initialize()
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
            assert (
                connection.exec_driver_sql("PRAGMA mmap_size").scalar_one()
                == settings.database.sqlite_mmap_size_bytes
            )
            assert (
                connection.exec_driver_sql("PRAGMA cache_size").scalar_one()
                == -settings.database.sqlite_cache_size_kib
            )
            assert (
                connection.exec_driver_sql("PRAGMA journal_size_limit").scalar_one()
                == settings.database.sqlite_journal_size_limit_bytes
            )
        assert database.engine.pool.size() == settings.database.pool_size
        assert database.engine.pool._max_overflow == 0
    finally:
        database.close()


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
