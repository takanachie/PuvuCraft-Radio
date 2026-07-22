from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from mutagen import File as MutagenFile
from mutagen.flac import Picture
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import ApiError
from ..models import Track, utcnow


@dataclass(frozen=True)
class ProbeResult:
    duration_seconds: float
    audio_stream_index: int
    mime_type: str | None


@dataclass(frozen=True)
class ExtractedMetadata:
    title: str
    artist: str
    album: str
    cover: bytes | None
    cover_extension: str | None


class MediaService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _validate_extension(self, filename: str) -> str:
        extension = Path(filename).suffix.lower()
        if extension not in self.settings.media.allowed_extensions:
            raise ApiError(415, "unsupported_media", "不支持该音频文件格式")
        return extension

    def stage_upload(self, source: BinaryIO, filename: str) -> Path:
        extension = self._validate_extension(filename)
        target_dir = self.settings.paths.upload_temp_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(prefix="upload-", suffix=extension, dir=target_dir)
        size = 0
        try:
            with os.fdopen(fd, "wb") as destination:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.settings.media.max_upload_bytes:
                        raise ApiError(413, "file_too_large", "音频文件超过 500 MiB 上限")
                    destination.write(chunk)
            if size == 0:
                raise ApiError(422, "empty_file", "上传文件为空")
            return Path(raw_path)
        except Exception:
            Path(raw_path).unlink(missing_ok=True)
            raise

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _probe(self, path: Path) -> ProbeResult:
        binary = self.settings.ffmpeg.ffprobe_binary
        if not binary.exists():
            raise ApiError(503, "ffprobe_unavailable", f"找不到 ffprobe：{binary}")
        try:
            result = subprocess.run(
                [
                    str(binary),
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            payload = json.loads(result.stdout)
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as exc:
            raise ApiError(422, "invalid_audio", "ffprobe 无法解析该音频文件") from exc
        streams = payload.get("streams") or []
        format_names = set(str((payload.get("format") or {}).get("format_name", "")).split(","))
        allowed_formats = {"aac", "flac", "mp3", "mov", "mp4", "m4a", "ogg", "wav"}
        if format_names.isdisjoint(allowed_formats):
            raise ApiError(415, "unsupported_container", "音频容器格式不受支持")
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        if audio is None:
            raise ApiError(422, "no_audio_stream", "文件中没有可播放的音频流")
        raw_duration = audio.get("duration") or (payload.get("format") or {}).get("duration")
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError) as exc:
            raise ApiError(422, "missing_duration", "无法确定音频时长") from exc
        if not 0 < duration <= 24 * 60 * 60:
            raise ApiError(422, "invalid_duration", "音频时长无效或超过 24 小时")
        mime_type, _ = mimetypes.guess_type(path.name)
        return ProbeResult(duration, int(audio.get("index", 0)), mime_type)

    @staticmethod
    def _first_tag(tags: object, names: tuple[str, ...]) -> str:
        if not tags:
            return ""
        for name in names:
            try:
                value = tags.get(name)  # type: ignore[union-attr]
            except (AttributeError, TypeError):
                continue
            if isinstance(value, (list, tuple)) and value:
                return str(value[0]).strip()
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _cover_extension(data: bytes, mime: str | None = None) -> str:
        if mime == "image/png" or data.startswith(b"\x89PNG"):
            return ".png"
        if mime == "image/webp" or data.startswith(b"RIFF") and b"WEBP" in data[:16]:
            return ".webp"
        return ".jpg"

    def _extract_metadata(self, path: Path, fallback_name: str) -> ExtractedMetadata:
        title = Path(fallback_name).stem.strip() or "未知曲目"
        artist = "未知艺人"
        album = ""
        cover: bytes | None = None
        cover_extension: str | None = None
        if not self.settings.media.metadata.extract_tags:
            return ExtractedMetadata(title, artist, album, cover, cover_extension)
        try:
            easy = MutagenFile(path, easy=True)
            if easy is not None:
                title = self._first_tag(easy.tags, ("title",)) or title
                artist = self._first_tag(easy.tags, ("artist", "albumartist")) or artist
                album = self._first_tag(easy.tags, ("album",))
            if self.settings.media.metadata.extract_embedded_cover:
                raw = MutagenFile(path, easy=False)
                if raw is not None:
                    pictures = getattr(raw, "pictures", None)
                    if pictures:
                        cover = bytes(pictures[0].data)
                        cover_extension = self._cover_extension(
                            cover, getattr(pictures[0], "mime", None)
                        )
                    tags = getattr(raw, "tags", None)
                    if cover is None and tags is not None and hasattr(tags, "getall"):
                        images = tags.getall("APIC")
                        if images:
                            cover = bytes(images[0].data)
                            cover_extension = self._cover_extension(
                                cover, getattr(images[0], "mime", None)
                            )
                    if cover is None and tags is not None:
                        values = tags.get("covr") if hasattr(tags, "get") else None
                        if values:
                            cover = bytes(values[0])
                            cover_extension = self._cover_extension(cover)
                    if cover is None and tags is not None and hasattr(tags, "get"):
                        values = tags.get("metadata_block_picture")
                        if values:
                            picture = Picture(base64.b64decode(values[0]))
                            cover = bytes(picture.data)
                            cover_extension = self._cover_extension(cover, picture.mime)
        except Exception:
            # Broken tags must not prevent an otherwise valid audio stream from importing.
            pass
        if cover is not None and len(cover) > 10 * 1024 * 1024:
            cover = None
            cover_extension = None
        return ExtractedMetadata(title, artist, album, cover, cover_extension)

    def import_staged(
        self,
        db: Session,
        staged_path: Path,
        original_filename: str,
    ) -> tuple[Track, bool]:
        extension = self._validate_extension(original_filename)
        try:
            digest = self._sha256(staged_path)
            existing = db.scalar(select(Track).where(Track.sha256 == digest))
            if existing is not None:
                existing_path = self.settings.paths.media_dir / existing.storage_name
                if not existing_path.is_file() or not existing.available:
                    self._probe(staged_path)
                    self.settings.paths.media_dir.mkdir(parents=True, exist_ok=True)
                    os.replace(staged_path, existing_path)
                    existing.file_size_bytes = existing_path.stat().st_size
                    existing.available = True
                    existing.unavailable_reason = None
                    existing.decode_failures = 0
                    existing.updated_at = utcnow()
                    db.commit()
                else:
                    staged_path.unlink(missing_ok=True)
                return existing, True
            probe = self._probe(staged_path)
            metadata = self._extract_metadata(staged_path, original_filename)
            storage_name = f"{uuid.uuid4().hex}{extension}"
            media_path = self.settings.paths.media_dir / storage_name
            self.settings.paths.media_dir.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, media_path)
            cover_name: str | None = None
            if metadata.cover and metadata.cover_extension:
                self.settings.paths.cover_dir.mkdir(parents=True, exist_ok=True)
                cover_name = f"{uuid.uuid4().hex}{metadata.cover_extension}"
                (self.settings.paths.cover_dir / cover_name).write_bytes(metadata.cover)
            now = utcnow()
            track = Track(
                storage_name=storage_name,
                original_filename=Path(original_filename).name,
                sha256=digest,
                file_size_bytes=media_path.stat().st_size,
                mime_type=probe.mime_type,
                audio_stream_index=probe.audio_stream_index,
                duration_seconds=probe.duration_seconds,
                title=metadata.title,
                artist=metadata.artist,
                album=metadata.album,
                cover_name=cover_name,
                available=True,
                created_at=now,
                updated_at=now,
            )
            db.add(track)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                media_path.unlink(missing_ok=True)
                if cover_name:
                    (self.settings.paths.cover_dir / cover_name).unlink(missing_ok=True)
                winner = db.scalar(select(Track).where(Track.sha256 == digest))
                if winner is not None:
                    return winner, True
                raise
            except Exception:
                db.rollback()
                media_path.unlink(missing_ok=True)
                if cover_name:
                    (self.settings.paths.cover_dir / cover_name).unlink(missing_ok=True)
                raise
            db.refresh(track)
            return track, False
        finally:
            staged_path.unlink(missing_ok=True)

    def import_server_file(self, db: Session, source: Path) -> tuple[Track, bool]:
        self._validate_extension(source.name)
        if source.stat().st_size > self.settings.media.max_upload_bytes:
            raise ApiError(413, "file_too_large", f"{source.name} 超过 500 MiB 上限")
        self.settings.paths.upload_temp_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix="scan-",
            suffix=source.suffix.lower(),
            dir=self.settings.paths.upload_temp_dir,
        )
        os.close(fd)
        staged = Path(temp_name)
        try:
            shutil.copyfile(source, staged)
            return self.import_staged(db, staged, source.name)
        except Exception:
            staged.unlink(missing_ok=True)
            raise

    def scan(self, db: Session) -> dict[str, object]:
        examined = imported = skipped = unavailable = 0
        tracks: list[Track] = []
        duplicates: list[Track] = []
        for existing in db.scalars(select(Track)).all():
            media_path = self.settings.paths.media_dir / existing.storage_name
            if not media_path.is_file() and existing.available:
                existing.available = False
                existing.unavailable_reason = "媒体文件不存在"
                existing.updated_at = utcnow()
                unavailable += 1
            elif (
                media_path.is_file()
                and not existing.available
                and existing.unavailable_reason == "媒体文件不存在"
            ):
                try:
                    self._probe(media_path)
                except ApiError:
                    pass
                else:
                    existing.available = True
                    existing.unavailable_reason = None
                    existing.decode_failures = 0
                    existing.updated_at = utcnow()
        db.commit()
        for root in self.settings.media.import_directories:
            if not root.is_dir():
                continue
            for source in sorted(path for path in root.rglob("*") if path.is_file()):
                if source.is_symlink() or root.resolve() not in source.resolve().parents:
                    continue
                if source.suffix.lower() not in self.settings.media.allowed_extensions:
                    continue
                examined += 1
                try:
                    track, duplicate = self.import_server_file(db, source)
                except (ApiError, OSError):
                    skipped += 1
                    continue
                if duplicate:
                    skipped += 1
                    duplicates.append(track)
                else:
                    imported += 1
                    tracks.append(track)
        return {
            "examined": examined,
            "imported": imported,
            "skipped": skipped,
            "unavailable": unavailable,
            "tracks": tracks,
            "duplicates": duplicates,
        }
