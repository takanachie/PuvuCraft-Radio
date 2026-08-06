from __future__ import annotations

import hashlib
import threading
from collections import namedtuple
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.config import StorageLocationConfig
from backend.app.database import Database
from backend.app.models import MusicLibrary
from backend.app.services.media import ExtractedMetadata, MediaService, ProbeResult
from backend.app.services.storage import StorageManager, StorageUnavailable

DiskUsage = namedtuple("DiskUsage", "total used free")


def test_storage_uses_priority_then_falls_back_at_usage_limit(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preferred = tmp_path / "preferred"
    fallback = tmp_path / "fallback"
    preferred.mkdir()
    fallback.mkdir()
    settings.storage.locations = [
        StorageLocationConfig(
            id="preferred",
            root=preferred,
            priority=100,
            max_usage_percent=80,
            enabled=True,
            create_if_missing=False,
        ),
        StorageLocationConfig(
            id="fallback",
            root=fallback,
            priority=10,
            max_usage_percent=80,
            enabled=True,
            create_if_missing=False,
        ),
    ]
    usage = {
        preferred: DiskUsage(total=1000, used=795, free=205),
        fallback: DiskUsage(total=1000, used=100, free=900),
    }
    monkeypatch.setattr(
        "backend.app.services.storage.shutil.disk_usage",
        lambda path: usage[Path(path)],
    )

    source = tmp_path / "source.flac"
    source.write_bytes(b"x" * 10)
    manager = StorageManager(settings)
    digest = manager.sha256(source)
    placement = manager.place(source, ".flac", digest, "a" * 32)

    assert placement.storage_id == "fallback"
    assert placement.path.parent == fallback
    assert placement.path.read_bytes() == source.read_bytes()
    assert manager.resolve(placement.storage_id, placement.storage_name) == placement.path


def test_storage_resolver_rejects_paths_outside_configured_root(
    settings,
    tmp_path: Path,
) -> None:
    settings.storage.locations[0].root = tmp_path / "media"
    manager = StorageManager(settings)
    with pytest.raises(StorageUnavailable):
        manager.resolve(settings.storage.locations[0].id, "../outside.mp3")


def test_large_media_copy_does_not_hold_the_only_database_connection(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.database.pool_size = 1
    settings.database.pool_timeout_seconds = 1
    database = Database(settings)
    database.initialize()
    storage = StorageManager(settings)
    storage.initialize()
    media = MediaService(settings, storage)
    staged = tmp_path / "staged.mp3"
    content = b"test-audio-payload"
    staged.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(
        media,
        "_probe",
        lambda _path: ProbeResult(30, 0, "audio/mpeg", 44100, 2, 16),
    )
    monkeypatch.setattr(
        media,
        "_extract_metadata",
        lambda _path, _name: ExtractedMetadata("Test", "Artist", "", None, None),
    )
    monkeypatch.setattr(storage, "sha256", lambda _path: digest)
    original_place = storage.place
    copying = threading.Event()
    continue_copy = threading.Event()

    def slow_place(*args, **kwargs):
        copying.set()
        assert continue_copy.wait(3)
        return original_place(*args, **kwargs)

    monkeypatch.setattr(storage, "place", slow_place)
    failures: list[BaseException] = []

    def import_media() -> None:
        try:
            with database.session_factory() as db:
                media.import_staged(db, staged, staged.name)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=import_media)
    worker.start()
    assert copying.wait(3)
    try:
        with database.session_factory() as db:
            assert db.scalar(select(MusicLibrary.name)) == "default"
    finally:
        continue_copy.set()
        worker.join(5)
        database.close()
    assert not worker.is_alive()
    assert failures == []
