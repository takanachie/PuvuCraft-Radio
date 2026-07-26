from __future__ import annotations

import base64
import contextlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import Picture
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import ApiError
from ..models import Track, utcnow
from .storage import StorageManager, StorageUnavailable


@dataclass(frozen=True)
class ProbeResult:
    duration_seconds: float
    audio_stream_index: int
    mime_type: str | None
    sample_rate: int
    channels: int
    bits_per_sample: int


@dataclass(frozen=True)
class ExtractedMetadata:
    title: str
    artist: str
    album: str
    cover: bytes | None
    cover_extension: str | None


StatusCallback = Callable[[str], None]
NAME_SIMILARITY_THRESHOLD = 0.82
NAME_SIMILARITY_LIMIT = 5


class MediaService:
    def __init__(self, settings: Settings, storage: StorageManager) -> None:
        self.settings = settings
        self.storage = storage
        self._digest_locks = tuple(threading.Lock() for _ in range(64))

    def validate_filename(self, filename: str) -> str:
        extension = Path(filename).suffix.lower()
        if extension not in self.settings.media.allowed_extensions:
            raise ApiError(415, "unsupported_media", "不支持该音频文件格式")
        return extension

    @staticmethod
    def _normalized_name(value: str, *, strip_extension: bool = False) -> str:
        text = Path(value).stem if strip_extension else value
        text = unicodedata.normalize("NFKC", text).casefold()
        text = re.sub(r"^\s*\d{1,3}(?:\s*[-_.、]\s*|\s+)", "", text)
        text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
        return " ".join(text.split())

    def similar_tracks(
        self,
        db: Session,
        filename: str,
        *,
        threshold: float = NAME_SIMILARITY_THRESHOLD,
        limit: int = NAME_SIMILARITY_LIMIT,
    ) -> list[dict[str, object]]:
        requested = self._normalized_name(filename, strip_extension=True)
        if not requested:
            return []
        matches: list[tuple[float, int, dict[str, object]]] = []
        for track in db.scalars(select(Track)).all():
            names = {
                self._normalized_name(track.original_filename, strip_extension=True),
                self._normalized_name(track.title),
                self._normalized_name(f"{track.artist} {track.title}"),
                self._normalized_name(f"{track.title} {track.artist}"),
            }
            score = max(
                (
                    SequenceMatcher(None, requested, candidate).ratio()
                    for candidate in names
                    if candidate
                ),
                default=0.0,
            )
            if score < threshold:
                continue
            matches.append(
                (
                    score,
                    track.id,
                    {
                        "id": track.id,
                        "title": track.title,
                        "artist": track.artist,
                        "album": track.album,
                        "original_filename": track.original_filename,
                        "duration_seconds": track.duration_seconds,
                        "similarity": round(score, 3),
                    },
                )
            )
        matches.sort(key=lambda item: (-item[0], item[1]))
        return [candidate for _score, _track_id, candidate in matches[:limit]]

    @staticmethod
    def _sample_bits(audio: dict[str, object]) -> int:
        for key in ("bits_per_raw_sample", "bits_per_sample"):
            try:
                value = int(str(audio.get(key) or "0"))
            except ValueError:
                value = 0
            if value > 0:
                return value
        sample_format = str(audio.get("sample_fmt") or "").removesuffix("p")
        return {
            "u8": 8,
            "s16": 16,
            "s32": 32,
            "s64": 64,
            "flt": 32,
            "dbl": 64,
        }.get(sample_format, 32)

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
        if not isinstance(audio, dict):
            raise ApiError(422, "no_audio_stream", "文件中没有可播放的音频流")
        raw_duration = audio.get("duration") or (payload.get("format") or {}).get("duration")
        try:
            duration = float(raw_duration)
            sample_rate = int(audio.get("sample_rate") or 0)
            channels = int(audio.get("channels") or 0)
        except (TypeError, ValueError) as exc:
            raise ApiError(422, "invalid_audio_parameters", "无法确定音频采样参数") from exc
        if not 0 < duration <= 24 * 60 * 60:
            raise ApiError(422, "invalid_duration", "音频时长无效或超过 24 小时")
        if sample_rate <= 0 or channels <= 0:
            raise ApiError(422, "invalid_audio_parameters", "音频采样率或声道数无效")
        mime_type, _ = mimetypes.guess_type(path.name)
        return ProbeResult(
            duration_seconds=duration,
            audio_stream_index=int(audio.get("index", 0)),
            mime_type=mime_type,
            sample_rate=sample_rate,
            channels=channels,
            bits_per_sample=self._sample_bits(audio),
        )

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

    def _requires_normalization(self, probe: ProbeResult) -> bool:
        output = self.settings.streaming.output
        return (
            probe.sample_rate > output.sample_rate
            or probe.channels > output.channels
            or probe.bits_per_sample > output.sample_bits
        )

    def _normalize(self, source: Path, probe: ProbeResult) -> tuple[Path, ProbeResult]:
        binary = self.settings.ffmpeg.binary
        if not binary.is_file():
            raise ApiError(503, "ffmpeg_unavailable", f"找不到 FFmpeg：{binary}")
        self.storage.normalized_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(
            prefix="normalized-", suffix=".flac", dir=self.storage.normalized_dir
        )
        os.close(fd)
        target = Path(raw_path)
        output = self.settings.streaming.output
        target_rate = min(probe.sample_rate, output.sample_rate)
        target_channels = min(probe.channels, output.channels)
        target_sample_format = "s16" if probe.bits_per_sample <= 16 else "s32"
        timeout = max(300, min(7200, int(probe.duration_seconds * 4)))
        try:
            subprocess.run(
                [
                    str(binary),
                    "-hide_banner",
                    "-loglevel",
                    self.settings.ffmpeg.log_level,
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    f"0:{probe.audio_stream_index}",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-map_metadata",
                    "-1",
                    "-c:a",
                    "flac",
                    "-compression_level",
                    "8",
                    "-ar",
                    str(target_rate),
                    "-ac",
                    str(target_channels),
                    "-sample_fmt",
                    target_sample_format,
                    str(target),
                ],
                check=True,
                capture_output=True,
                timeout=timeout,
            )
            normalized_probe = self._probe(target)
        except subprocess.TimeoutExpired as exc:
            raise ApiError(422, "normalization_timeout", "音频规范化处理超时") from exc
        except subprocess.CalledProcessError as exc:
            raise ApiError(422, "normalization_failed", "FFmpeg 无法规范化该音频") from exc
        output = self.settings.streaming.output
        if (
            normalized_probe.sample_rate > output.sample_rate
            or normalized_probe.channels > output.channels
            or normalized_probe.bits_per_sample > output.sample_bits
        ):
            raise ApiError(422, "normalization_invalid", "规范化后的音频仍超过推流采样限制")
        return target, normalized_probe

    def _write_cover(self, metadata: ExtractedMetadata) -> str | None:
        if not metadata.cover or not metadata.cover_extension:
            return None
        self.settings.paths.cover_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{metadata.cover_extension}"
        target = self.settings.paths.cover_dir / name
        fd, temporary_name = tempfile.mkstemp(prefix="cover-", dir=self.settings.paths.cover_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(metadata.cover)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
            return name
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise

    def import_staged(
        self,
        db: Session,
        staged_path: Path,
        original_filename: str,
        *,
        job_id: str | None = None,
        status_callback: StatusCallback | None = None,
    ) -> tuple[Track, bool]:
        original_extension = self.validate_filename(original_filename)
        normalized_path: Path | None = None
        placement_path: Path | None = None
        cover_name: str | None = None
        digest_lock = None
        try:
            probe = self._probe(staged_path)
            metadata = self._extract_metadata(staged_path, original_filename)
            normalized = self._requires_normalization(probe)
            retained_path = staged_path
            final_extension = original_extension
            if normalized:
                if status_callback:
                    status_callback("normalizing")
                normalized_path, probe = self._normalize(staged_path, probe)
                retained_path = normalized_path
                final_extension = ".flac"
            digest = self.storage.sha256(retained_path)
            digest_lock = self._digest_locks[int(digest[:2], 16) % len(self._digest_locks)]
            digest_lock.acquire()
            existing = db.scalar(select(Track).where(Track.sha256 == digest))
            if existing is not None:
                try:
                    existing_path = self.storage.track_path(existing)
                except StorageUnavailable:
                    existing_path = None
                if existing_path is not None and existing_path.is_file() and existing.available:
                    return existing, True
                if status_callback:
                    status_callback("placing")
                try:
                    placement = self.storage.place(
                        retained_path,
                        final_extension,
                        digest,
                        job_id or uuid.uuid4().hex,
                    )
                except StorageUnavailable as exc:
                    raise ApiError(507, "storage_unavailable", str(exc)) from exc
                placement_path = placement.path
                old_location = (existing.storage_id, existing.storage_name)
                existing.storage_id = placement.storage_id
                existing.storage_name = placement.storage_name
                existing.file_size_bytes = placement.size_bytes
                existing.mime_type = probe.mime_type
                existing.audio_stream_index = probe.audio_stream_index
                existing.duration_seconds = probe.duration_seconds
                existing.sample_rate = probe.sample_rate
                existing.channels = probe.channels
                existing.bits_per_sample = probe.bits_per_sample
                existing.normalized = normalized
                existing.available = True
                existing.unavailable_reason = None
                existing.decode_failures = 0
                existing.updated_at = utcnow()
                db.commit()
                placement_path = None
                if old_location != (existing.storage_id, existing.storage_name):
                    with contextlib.suppress(OSError, StorageUnavailable):
                        self.storage.delete(*old_location)
                return existing, True

            if status_callback:
                status_callback("placing")
            try:
                placement = self.storage.place(
                    retained_path,
                    final_extension,
                    digest,
                    job_id or uuid.uuid4().hex,
                )
            except StorageUnavailable as exc:
                raise ApiError(507, "storage_unavailable", str(exc)) from exc
            placement_path = placement.path
            cover_name = self._write_cover(metadata)
            now = utcnow()
            track = Track(
                storage_id=placement.storage_id,
                storage_name=placement.storage_name,
                original_filename=Path(original_filename).name,
                sha256=digest,
                file_size_bytes=placement.size_bytes,
                mime_type=probe.mime_type,
                audio_stream_index=probe.audio_stream_index,
                duration_seconds=probe.duration_seconds,
                sample_rate=probe.sample_rate,
                channels=probe.channels,
                bits_per_sample=probe.bits_per_sample,
                normalized=normalized,
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
                placement_path.unlink(missing_ok=True)
                placement_path = None
                if cover_name:
                    (self.settings.paths.cover_dir / cover_name).unlink(missing_ok=True)
                    cover_name = None
                winner = db.scalar(select(Track).where(Track.sha256 == digest))
                if winner is not None:
                    return winner, True
                raise
            db.refresh(track)
            placement_path = None
            cover_name = None
            return track, False
        except Exception:
            db.rollback()
            if placement_path is not None:
                placement_path.unlink(missing_ok=True)
            if cover_name:
                (self.settings.paths.cover_dir / cover_name).unlink(missing_ok=True)
            raise
        finally:
            if digest_lock is not None:
                digest_lock.release()
            staged_path.unlink(missing_ok=True)
            if normalized_path is not None:
                normalized_path.unlink(missing_ok=True)

    def import_server_file(self, db: Session, source: Path) -> tuple[Track, bool]:
        self.validate_filename(source.name)
        if source.stat().st_size > self.settings.media.max_upload_bytes:
            raise ApiError(413, "file_too_large", f"{source.name} 超过 500 MiB 上限")
        self.storage.upload_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix="scan-",
            suffix=source.suffix.lower(),
            dir=self.storage.upload_dir,
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
            try:
                media_path = self.storage.track_path(existing)
            except StorageUnavailable:
                media_path = None
            if (media_path is None or not media_path.is_file()) and existing.available:
                existing.available = False
                existing.unavailable_reason = "媒体文件不存在或存储位置未挂载"
                existing.updated_at = utcnow()
                unavailable += 1
            elif (
                media_path is not None
                and media_path.is_file()
                and not existing.available
                and existing.unavailable_reason
                in {"媒体文件不存在", "媒体文件不存在或存储位置未挂载"}
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
