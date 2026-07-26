from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, StorageLocationConfig


class StorageUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class StoragePlacement:
    storage_id: str
    storage_name: str
    path: Path
    size_bytes: int


class StorageManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._locations = {location.id: location for location in settings.storage.locations}
        self._reserved: dict[str, int] = {location.id: 0 for location in settings.storage.locations}
        self._reservation_lock = threading.Lock()

    @property
    def upload_dir(self) -> Path:
        return self.settings.uploads.temp_dir / "uploads"

    @property
    def normalized_dir(self) -> Path:
        return self.settings.uploads.temp_dir / "normalized"

    def initialize(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        for location in self.settings.storage.locations:
            if location.enabled and location.create_if_missing:
                location.root.mkdir(parents=True, exist_ok=True)
            incoming = location.root / ".incoming"
            if not location.enabled or incoming.is_symlink() or not incoming.is_dir():
                continue
            with contextlib.suppress(OSError):
                for child in incoming.iterdir():
                    if child.name.endswith(".part") and (
                        child.is_file() or child.is_symlink()
                    ):
                        child.unlink(missing_ok=True)

    def location(self, storage_id: str) -> StorageLocationConfig:
        try:
            return self._locations[storage_id]
        except KeyError as exc:
            raise StorageUnavailable(f"未知存储位置：{storage_id}") from exc

    def resolve(self, storage_id: str, storage_name: str) -> Path:
        location = self.location(storage_id)
        root = location.root.resolve()
        relative = Path(storage_name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise StorageUnavailable("媒体文件相对路径无效")
        candidate = (root / relative).resolve()
        if root not in candidate.parents:
            raise StorageUnavailable("媒体文件路径超出配置的存储位置")
        return candidate

    def track_path(self, track: object) -> Path:
        return self.resolve(str(track.storage_id), str(track.storage_name))

    def delete(self, storage_id: str, storage_name: str) -> None:
        self.resolve(storage_id, storage_name).unlink(missing_ok=True)

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _select_location(self, size_bytes: int) -> StorageLocationConfig:
        candidates: list[tuple[int, float, str, StorageLocationConfig]] = []
        for location in self.settings.storage.locations:
            if not location.enabled:
                continue
            if not location.root.exists():
                if not location.create_if_missing:
                    continue
                try:
                    location.root.mkdir(parents=True, exist_ok=True)
                except OSError:
                    continue
            if not location.root.is_dir() or not os.access(location.root, os.W_OK | os.X_OK):
                continue
            try:
                usage = shutil.disk_usage(location.root)
            except OSError:
                continue
            reserved = self._reserved.get(location.id, 0)
            projected = usage.used + reserved + size_bytes
            projected_percent = projected / usage.total * 100 if usage.total else 100.0
            if projected_percent > location.max_usage_percent:
                continue
            candidates.append((-location.priority, projected_percent, location.id, location))
        if not candidates:
            raise StorageUnavailable("没有满足容量阈值的可写存储位置")
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]

    def place(
        self,
        source: Path,
        extension: str,
        expected_sha256: str,
        job_id: str,
    ) -> StoragePlacement:
        size_bytes = source.stat().st_size
        with self._reservation_lock:
            location = self._select_location(size_bytes)
            self._reserved[location.id] = self._reserved.get(location.id, 0) + size_bytes
        incoming: Path | None = None
        try:
            root = location.root.resolve()
            incoming_dir = root / ".incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            final_name = f"{uuid.uuid4().hex}{extension.lower()}"
            incoming = incoming_dir / f"{job_id}-{final_name}.part"
            final = root / final_name
            with source.open("rb") as reader, incoming.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            if self.sha256(incoming) != expected_sha256:
                raise OSError("迁移后的媒体文件校验失败")
            os.replace(incoming, final)
            directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return StoragePlacement(location.id, final_name, final, size_bytes)
        finally:
            if incoming is not None:
                incoming.unlink(missing_ok=True)
            with self._reservation_lock:
                self._reserved[location.id] = max(
                    0, self._reserved.get(location.id, 0) - size_bytes
                )
