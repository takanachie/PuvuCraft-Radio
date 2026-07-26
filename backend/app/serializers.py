from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from .models import Channel, PlaybackHistory, PlaylistItem, Track, User, utcnow
from .security import aware_utc


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def user_dict(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "enabled": user.status == "approved",
        "is_active": user.status == "approved",
        "created_at": iso(user.created_at),
        "updated_at": iso(user.updated_at),
        "approved_at": iso(user.approved_at),
        "last_login_at": iso(user.last_login_at),
    }


def track_dict(track: Track) -> dict[str, object]:
    cover_url = track.cover_url_override
    if not cover_url and track.cover_name:
        cover_url = f"/api/covers/{track.cover_name}"
    return {
        "id": track.id,
        "title": track.title,
        "artist": track.artist or None,
        "album": track.album or None,
        "duration_seconds": track.duration_seconds,
        "sample_rate": track.sample_rate,
        "channels": track.channels,
        "bits_per_sample": track.bits_per_sample,
        "normalized": track.normalized,
        "storage_id": track.storage_id,
        "cover_url": cover_url,
        "available": track.available,
        "original_filename": track.original_filename,
        "file_size_bytes": track.file_size_bytes,
        "sha256": track.sha256,
        "mime_type": track.mime_type,
        "created_at": iso(track.created_at),
        "updated_at": iso(track.updated_at),
        "unavailable_reason": track.unavailable_reason,
    }


def playlist_item_dict(item: PlaylistItem, current_item_id: int | None = None) -> dict[str, object]:
    return {
        "id": item.id,
        "item_id": item.id,
        "position": item.position,
        "track_id": item.track_id,
        "track": track_dict(item.track),
        "is_current": item.id == current_item_id,
        "added_at": iso(item.added_at),
    }


def channel_dict(
    db: Session,
    channel: Channel,
    runtime: dict[str, object] | None = None,
    include_health: bool = False,
) -> dict[str, object]:
    state = channel.playback_state
    item = None
    if state and state.current_item_id:
        item = db.scalar(
            select(PlaylistItem)
            .options(joinedload(PlaylistItem.track))
            .where(PlaylistItem.id == state.current_item_id)
        )
    now = utcnow()
    position = state.position_seconds if state else 0.0
    if state and state.status == "live" and state.anchor_at:
        position += max(0.0, (now - aware_utc(state.anchor_at)).total_seconds())
    playback: dict[str, object] = {
        "channel_id": channel.id,
        "status": state.status if state else ("starting" if channel.enabled else "stopped"),
        "current_item_id": item.id if item else None,
        "current_track": track_dict(item.track) if item else None,
        "position_seconds": position,
        "duration_seconds": item.track.duration_seconds if item else None,
        "server_time": iso(now),
        "started_at": iso(state.anchor_at) if state else None,
        "last_error": state.last_error if state else None,
        "listener_count": 0,
    }
    if runtime:
        playback.update(runtime)
    result: dict[str, object] = {
        "id": channel.id,
        "name": channel.name,
        "slug": channel.slug,
        "description": channel.description,
        "enabled": channel.enabled,
        "playback_mode": channel.playback_mode,
        "display_order": channel.display_order,
        "status": playback["status"],
        "current_track": playback.get("current_track"),
        "playback": playback,
        "playback_state": playback,
        "listener_count": playback.get("listener_count", 0),
        "last_error": playback.get("last_error"),
        "created_at": iso(channel.created_at),
        "updated_at": iso(channel.updated_at),
    }
    if include_health:
        history_rows = list(
            db.scalars(
                select(PlaybackHistory)
                .options(joinedload(PlaybackHistory.track))
                .where(PlaybackHistory.channel_id == channel.id)
                .order_by(desc(PlaybackHistory.started_at))
                .limit(5)
            ).all()
        )
        result["health"] = {
            "status": playback["status"],
            "ffmpeg_running": bool(playback.get("ffmpeg_running")),
            "last_started_at": playback.get("last_started_at")
            or (iso(state.last_started_at) if state else None),
            "restart_count": playback.get("restart_count")
            if runtime
            else (state.restart_count if state else 0),
            "last_error": playback.get("last_error"),
            "recent_history": [
                {
                    "id": entry.id,
                    "track": track_dict(entry.track) if entry.track else None,
                    "started_at": iso(entry.started_at),
                    "ended_at": iso(entry.ended_at),
                    "reason": entry.end_reason,
                }
                for entry in history_rows
            ],
        }
    return result
