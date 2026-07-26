from __future__ import annotations

from collections import namedtuple
from pathlib import Path

import pytest

from backend.app.config import StorageLocationConfig
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
