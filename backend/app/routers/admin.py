from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from ..config import Settings
from ..dependencies import get_db, get_settings, require_admin, require_admin_read
from ..errors import ApiError
from ..models import (
    AuditEvent,
    Channel,
    PlaybackState,
    PlaylistItem,
    Track,
    User,
    utcnow,
)
from ..schemas import (
    ChannelCreate,
    ChannelUpdate,
    PlaylistAdd,
    PlaylistItemUpdate,
    PlaylistReorder,
    TrackUpdate,
    UserUpdate,
)
from ..security import AuthService
from ..serializers import channel_dict, playlist_item_dict, track_dict, user_dict
from ..services.media import MediaService
from ..services.playback import ChannelCommand

router = APIRouter(prefix="/api/admin")
logger = logging.getLogger(__name__)


def unlink_after_commit(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove obsolete media file: %s", path, exc_info=True)


def audit(
    db: Session,
    actor: User,
    action: str,
    target_type: str | None = None,
    target_id: object | None = None,
    details: dict[str, object] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=actor.id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=details or {},
        )
    )


@router.get("/users")
def list_users(
    _admin: User = Depends(require_admin_read),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return [user_dict(user) for user in users]


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "user_not_found", "用户不存在")
    if user.role == "admin" and payload.status != "approved":
        approved_admins = (
            db.scalar(
                select(func.count(User.id)).where(User.role == "admin", User.status == "approved")
            )
            or 0
        )
        if user.id == admin.id or approved_admins <= 1:
            raise ApiError(409, "last_admin", "不能停用当前或最后一个管理员")
    previous = user.status
    user.status = payload.status
    user.updated_at = utcnow()
    if payload.status == "approved":
        user.approved_at = utcnow()
    else:
        auth: AuthService = request.app.state.auth
        auth.revoke_user_sessions(db, user.id)
    audit(
        db,
        admin,
        "user.status_changed",
        "user",
        user.id,
        {"from": previous, "to": payload.status},
    )
    db.commit()
    db.refresh(user)
    return {"user": user_dict(user)}


@router.get("/channels")
def admin_channels(
    request: Request,
    _admin: User = Depends(require_admin_read),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    channels = db.scalars(
        select(Channel)
        .options(selectinload(Channel.playback_state))
        .order_by(Channel.display_order, Channel.id)
    ).all()
    manager = request.app.state.playback
    return [
        channel_dict(db, channel, manager.snapshot(channel.id), include_health=True)
        for channel in channels
    ]


@router.post("/channels", status_code=201)
def create_channel(
    payload: ChannelCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    channel = Channel(**payload.model_dump(), created_at=utcnow(), updated_at=utcnow())
    channel.playback_state = PlaybackState(status="starting" if channel.enabled else "stopped")
    db.add(channel)
    audit(db, admin, "channel.created", "channel", payload.slug)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "channel_slug_exists", "频道 slug 已存在") from exc
    db.refresh(channel)
    request.app.state.playback.publish_channel_change(
        channel.id,
        {
            "id": channel.id,
            "name": channel.name,
            "slug": channel.slug,
            "description": channel.description,
            "enabled": channel.enabled,
            "playback_mode": channel.playback_mode,
            "display_order": channel.display_order,
        },
    )
    request.app.state.playback.reconcile(channel.id)
    return {"channel": channel_dict(db, channel, None, include_health=True)}


@router.patch("/channels/{channel_id}")
def update_channel(
    channel_id: int,
    payload: ChannelUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise ApiError(404, "channel_not_found", "频道不存在")
    changes = payload.model_dump(exclude_unset=True)
    if "playback_mode" in changes and changes["playback_mode"] != channel.playback_mode:
        channel.playlist_version += 1
    for key, value in changes.items():
        setattr(channel, key, value)
    channel.updated_at = utcnow()
    audit(db, admin, "channel.updated", "channel", channel.id, {"fields": sorted(changes)})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "channel_slug_exists", "频道 slug 已存在") from exc
    db.refresh(channel)
    request.app.state.playback.publish_channel_change(
        channel.id,
        {
            "id": channel.id,
            "name": channel.name,
            "slug": channel.slug,
            "description": channel.description,
            "enabled": channel.enabled,
            "playback_mode": channel.playback_mode,
            "display_order": channel.display_order,
        },
    )
    request.app.state.playback.reconcile(channel.id)
    return {
        "channel": channel_dict(
            db,
            channel,
            request.app.state.playback.snapshot(channel.id),
            include_health=True,
        )
    }


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(
    channel_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise ApiError(404, "channel_not_found", "频道不存在")
    slug = channel.slug
    db.delete(channel)
    audit(db, admin, "channel.deleted", "channel", channel_id, {"slug": slug})
    db.commit()
    request.app.state.playback.remove(channel_id)


def _track_with_references(track: Track) -> dict[str, object]:
    result = track_dict(track)
    result["referenced_by"] = [
        {"id": item.channel.id, "name": item.channel.name} for item in track.playlist_items
    ]
    return result


@router.get("/tracks")
def list_tracks(
    _admin: User = Depends(require_admin_read),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    tracks = (
        db.scalars(
            select(Track)
            .options(selectinload(Track.playlist_items).joinedload(PlaylistItem.channel))
            .order_by(Track.created_at.desc())
        )
        .unique()
        .all()
    )
    return [_track_with_references(track) for track in tracks]


@router.post("/tracks/upload", status_code=201)
def upload_track(
    file: UploadFile,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    filename = Path(file.filename or "").name
    if not filename:
        raise ApiError(422, "missing_filename", "上传文件缺少名称")
    media: MediaService = request.app.state.media
    staged = media.stage_upload(file.file, filename)
    track, duplicate = media.import_staged(db, staged, filename)
    audit(db, admin, "track.uploaded", "track", track.id, {"duplicate": duplicate})
    db.commit()
    return {
        "track": _track_with_references(track),
        "duplicates": [_track_with_references(track)] if duplicate else [],
        "imported": 0 if duplicate else 1,
        "skipped": 1 if duplicate else 0,
    }


@router.post("/tracks/scan")
def scan_tracks(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = request.app.state.media.scan(db)
    audit(
        db,
        admin,
        "track.scan_completed",
        details={key: result[key] for key in ("examined", "imported", "skipped", "unavailable")},
    )
    db.commit()
    return {
        **{key: result[key] for key in ("examined", "imported", "skipped", "unavailable")},
        "tracks": [track_dict(track) for track in result["tracks"]],
        "duplicates": [track_dict(track) for track in result["duplicates"]],
    }


@router.patch("/tracks/{track_id}")
def update_track(
    track_id: int,
    payload: TrackUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    track = db.scalar(
        select(Track)
        .options(selectinload(Track.playlist_items).joinedload(PlaylistItem.channel))
        .where(Track.id == track_id)
    )
    if track is None:
        raise ApiError(404, "track_not_found", "歌曲不存在")
    changes = payload.model_dump(exclude_unset=True)
    if "cover_url" in changes:
        track.cover_url_override = changes.pop("cover_url")
    for key, value in changes.items():
        setattr(track, key, value or ("未知艺人" if key == "artist" else ""))
    track.updated_at = utcnow()
    audit(
        db,
        admin,
        "track.updated",
        "track",
        track.id,
        {"fields": sorted(payload.model_fields_set)},
    )
    db.commit()
    db.refresh(track)
    for item in track.playlist_items:
        request.app.state.playback.publish_playlist_change(item.channel_id)
    return {"track": _track_with_references(track)}


@router.post("/tracks/{track_id}/cover")
def upload_track_cover(
    track_id: int,
    file: UploadFile,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    track = db.scalar(
        select(Track)
        .options(selectinload(Track.playlist_items).joinedload(PlaylistItem.channel))
        .where(Track.id == track_id)
    )
    if track is None:
        raise ApiError(404, "track_not_found", "歌曲不存在")
    data = file.file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ApiError(413, "cover_too_large", "封面超过 10 MiB 上限")
    if data.startswith(b"\x89PNG"):
        extension = ".png"
    elif data.startswith(b"\xff\xd8\xff"):
        extension = ".jpg"
    elif data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        extension = ".webp"
    else:
        raise ApiError(415, "invalid_cover", "封面必须是 JPEG、PNG 或 WebP 图片")
    settings.paths.cover_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{extension}"
    fd, temporary_name = tempfile.mkstemp(prefix="cover-", dir=settings.paths.cover_dir)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
        os.replace(temporary_name, settings.paths.cover_dir / name)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    old_name = track.cover_name
    track.cover_name = name
    track.cover_url_override = None
    track.updated_at = utcnow()
    audit(db, admin, "track.cover_updated", "track", track.id)
    try:
        db.commit()
    except Exception:
        db.rollback()
        unlink_after_commit(settings.paths.cover_dir / name)
        raise
    if old_name:
        unlink_after_commit(settings.paths.cover_dir / old_name)
    return {"track": _track_with_references(track)}


@router.delete("/tracks/{track_id}", status_code=204)
def delete_track(
    track_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    track = db.scalar(
        select(Track).options(selectinload(Track.playlist_items)).where(Track.id == track_id)
    )
    if track is None:
        raise ApiError(404, "track_not_found", "歌曲不存在")
    if track.playlist_items:
        raise ApiError(409, "track_in_use", "歌曲仍被频道歌单引用，请先从歌单移除")
    media_path = settings.paths.media_dir / track.storage_name
    cover_path = settings.paths.cover_dir / track.cover_name if track.cover_name else None
    db.delete(track)
    audit(db, admin, "track.deleted", "track", track.id, {"filename": track.original_filename})
    db.commit()
    unlink_after_commit(media_path)
    if cover_path:
        unlink_after_commit(cover_path)


def _get_channel(db: Session, channel_id: int) -> Channel:
    channel = db.scalar(
        select(Channel)
        .options(selectinload(Channel.playback_state))
        .where(Channel.id == channel_id)
    )
    if channel is None:
        raise ApiError(404, "channel_not_found", "频道不存在")
    return channel


def _playlist(db: Session, channel_id: int) -> list[PlaylistItem]:
    return list(
        db.scalars(
            select(PlaylistItem)
            .options(joinedload(PlaylistItem.track))
            .where(PlaylistItem.channel_id == channel_id)
            .order_by(PlaylistItem.position, PlaylistItem.id)
        )
        .unique()
        .all()
    )


def _playlist_response(db: Session, channel: Channel) -> list[dict[str, object]]:
    current_id = channel.playback_state.current_item_id if channel.playback_state else None
    return [playlist_item_dict(item, current_id) for item in _playlist(db, channel.id)]


def _playlist_changed(request: Request, channel: Channel) -> None:
    request.app.state.playback.publish_playlist_change(channel.id)
    request.app.state.playback.reconcile(channel.id)


@router.get("/channels/{channel_id}/playlist")
def admin_playlist(
    channel_id: int,
    _admin: User = Depends(require_admin_read),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return _playlist_response(db, _get_channel(db, channel_id))


@router.post("/channels/{channel_id}/playlist", status_code=201)
def add_playlist_item(
    channel_id: int,
    payload: PlaylistAdd,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    channel = _get_channel(db, channel_id)
    track = db.get(Track, payload.track_id)
    if track is None or not track.available:
        raise ApiError(404, "track_unavailable", "歌曲不存在或不可用")
    max_position = db.scalar(
        select(func.max(PlaylistItem.position)).where(PlaylistItem.channel_id == channel_id)
    )
    position = (max_position if max_position is not None else -1) + 1
    item = PlaylistItem(channel_id=channel_id, track_id=track.id, position=position)
    db.add(item)
    channel.playlist_version += 1
    channel.updated_at = utcnow()
    audit(db, admin, "playlist.item_added", "channel", channel.id, {"track_id": track.id})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "track_already_in_playlist", "该歌曲已经在频道歌单中") from exc
    db.refresh(item)
    _playlist_changed(request, channel)
    current_id = channel.playback_state.current_item_id if channel.playback_state else None
    return {"item": playlist_item_dict(item, current_id)}


@router.patch("/channels/{channel_id}/playlist/{item_id}")
def update_playlist_item(
    channel_id: int,
    item_id: int,
    payload: PlaylistItemUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    channel = _get_channel(db, channel_id)
    item = db.scalar(
        select(PlaylistItem)
        .options(joinedload(PlaylistItem.track))
        .where(PlaylistItem.channel_id == channel_id, PlaylistItem.id == item_id)
    )
    if item is None:
        raise ApiError(404, "playlist_item_not_found", "歌单项目不存在")
    if payload.position is not None:
        items = _playlist(db, channel_id)
        reordered = [candidate for candidate in items if candidate.id != item.id]
        reordered.insert(min(payload.position, len(reordered)), item)
        _apply_order(db, reordered)
    channel.playlist_version += 1
    channel.updated_at = utcnow()
    audit(db, admin, "playlist.item_updated", "playlist_item", item.id)
    db.commit()
    _playlist_changed(request, channel)
    current_id = channel.playback_state.current_item_id if channel.playback_state else None
    return {"item": playlist_item_dict(item, current_id)}


@router.delete("/channels/{channel_id}/playlist/{item_id}", status_code=204)
def remove_playlist_item(
    channel_id: int,
    item_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    channel = _get_channel(db, channel_id)
    item = db.scalar(
        select(PlaylistItem).where(
            PlaylistItem.channel_id == channel_id, PlaylistItem.id == item_id
        )
    )
    if item is None:
        raise ApiError(404, "playlist_item_not_found", "歌单项目不存在")
    was_current = bool(channel.playback_state and channel.playback_state.current_item_id == item.id)
    db.delete(item)
    db.flush()
    remaining = _playlist(db, channel_id)
    _apply_order(db, remaining)
    channel.playlist_version += 1
    channel.updated_at = utcnow()
    audit(db, admin, "playlist.item_removed", "playlist_item", item_id)
    db.commit()
    _playlist_changed(request, channel)
    if was_current:
        request.app.state.playback.send_command(channel_id, ChannelCommand("skip"))


@router.post("/channels/{channel_id}/playlist/reorder")
def reorder_playlist(
    channel_id: int,
    payload: PlaylistReorder,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    channel = _get_channel(db, channel_id)
    items = _playlist(db, channel_id)
    by_id = {item.id: item for item in items}
    if set(payload.item_ids) != set(by_id) or len(payload.item_ids) != len(items):
        raise ApiError(422, "invalid_playlist_order", "排序必须包含歌单中的全部项目且不能有重复")
    ordered = [by_id[item_id] for item_id in payload.item_ids]
    _apply_order(db, ordered)
    channel.playlist_version += 1
    channel.updated_at = utcnow()
    audit(db, admin, "playlist.reordered", "channel", channel.id)
    db.commit()
    _playlist_changed(request, channel)
    return _playlist_response(db, channel)


def _apply_order(db: Session, items: list[PlaylistItem]) -> None:
    for index, item in enumerate(items):
        item.position = -(index + 1)
    db.flush()
    for index, item in enumerate(items):
        item.position = index
    db.flush()


@router.post("/channels/{channel_id}/skip")
def skip_channel(
    channel_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    channel = _get_channel(db, channel_id)
    if not channel.enabled:
        raise ApiError(409, "channel_disabled", "频道已停用，不能执行切歌")
    if not _playlist(db, channel_id):
        raise ApiError(409, "empty_playlist", "频道歌单为空")
    audit(db, admin, "playback.skipped", "channel", channel.id)
    db.commit()
    request.app.state.playback.send_command(channel_id, ChannelCommand("skip"))
    return {"status": "accepted", "message": "切歌命令已发送"}


@router.post("/channels/{channel_id}/play-now/{item_id}")
def play_now(
    channel_id: int,
    item_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    channel = _get_channel(db, channel_id)
    if not channel.enabled:
        raise ApiError(409, "channel_disabled", "频道已停用，不能立即播放")
    item = db.scalar(
        select(PlaylistItem)
        .join(PlaylistItem.track)
        .where(
            PlaylistItem.channel_id == channel_id,
            PlaylistItem.id == item_id,
            Track.available.is_(True),
        )
    )
    if item is None:
        raise ApiError(404, "playlist_item_unavailable", "歌单项目不存在或不可用")
    audit(db, admin, "playback.play_now", "playlist_item", item.id)
    db.commit()
    request.app.state.playback.send_command(channel_id, ChannelCommand("play_now", item_id=item.id))
    return {"status": "accepted", "message": "立即播放命令已发送"}
