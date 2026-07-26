from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from ..config import Settings
from ..dependencies import get_db, get_settings, require_admin, require_admin_read
from ..errors import ApiError
from ..models import (
    AuditEvent,
    Channel,
    MusicLibrary,
    PlaybackState,
    PlaylistItem,
    Track,
    UploadJob,
    User,
    utcnow,
)
from ..schemas import (
    ChannelCreate,
    ChannelUpdate,
    MusicLibraryCreate,
    MusicLibraryUpdate,
    PlaylistAdd,
    PlaylistBatchAdd,
    PlaylistItemUpdate,
    PlaylistReorder,
    TrackLibraryBatchMove,
    TrackUpdate,
    UserRoleUpdate,
    UserUpdate,
)
from ..security import AuthService
from ..serializers import channel_dict, iso, playlist_item_dict, track_dict, user_dict
from ..services.playback import ChannelCommand
from ..services.storage import StorageUnavailable
from ..services.uploads import CAPACITY_STATUSES

router = APIRouter(prefix="/api/admin")
logger = logging.getLogger(__name__)
TRACK_LIBRARY_PAGE_SIZE = 10


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


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "user_not_found", "用户不存在")
    if user.role == payload.role:
        return {"user": user_dict(user)}
    if user.role != "listener":
        raise ApiError(409, "invalid_role_transition", "当前用户角色不能执行该提权操作")
    if user.status != "approved":
        raise ApiError(409, "user_not_approved", "请先批准并启用该用户，再授予管理员权限")

    previous = user.role
    user.role = payload.role
    user.updated_at = utcnow()
    auth: AuthService = request.app.state.auth
    auth.revoke_user_sessions(db, user.id)
    audit(
        db,
        admin,
        "user.role_changed",
        "user",
        user.id,
        {"from": previous, "to": payload.role},
    )
    db.commit()
    db.refresh(user)
    return {"user": user_dict(user)}


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "user_not_found", "用户不存在")
    if user.id == admin.id:
        raise ApiError(409, "cannot_delete_self", "不能删除当前登录的管理员账号")
    if user.role == "admin" and user.status == "approved":
        approved_admins = (
            db.scalar(
                select(func.count(User.id)).where(
                    User.role == "admin",
                    User.status == "approved",
                )
            )
            or 0
        )
        if approved_admins <= 1:
            raise ApiError(409, "last_admin", "不能删除最后一个已批准的管理员")
    active_upload = db.scalar(
        select(UploadJob.id)
        .where(
            UploadJob.owner_user_id == user.id,
            UploadJob.status.in_(CAPACITY_STATUSES),
        )
        .limit(1)
    )
    if active_upload is not None:
        raise ApiError(409, "user_has_active_uploads", "该用户仍有活跃上传任务，请先结束上传")

    target = {
        "username": user.username,
        "role": user.role,
        "status": user.status,
    }
    auth: AuthService = request.app.state.auth
    auth.revoke_user_sessions(db, user.id)
    audit(db, admin, "user.deleted", "user", user.id, target)
    db.delete(user)
    db.commit()
    request.app.state.uploads.refresh_snapshot()


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


def _library_groups(db: Session) -> list[str]:
    groups = list(
        db.scalars(
            select(MusicLibrary.name).order_by(MusicLibrary.name.asc())
        ).all()
    )
    if "default" in groups:
        groups.remove("default")
    return ["default", *groups]


def _music_library_dict(db: Session, library: MusicLibrary) -> dict[str, object]:
    track_count = (
        db.scalar(
            select(func.count(Track.id)).where(Track.library_group == library.name)
        )
        or 0
    )
    return {
        "name": library.name,
        "track_count": track_count,
        "created_at": iso(library.created_at),
        "updated_at": iso(library.updated_at),
    }


@router.get("/track-libraries")
def list_music_libraries(
    _admin: User = Depends(require_admin_read),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    libraries = db.scalars(
        select(MusicLibrary).order_by(MusicLibrary.name.asc())
    ).all()
    libraries.sort(key=lambda library: (library.name != "default", library.name))
    return [_music_library_dict(db, library) for library in libraries]


@router.post("/track-libraries", status_code=201)
def create_music_library(
    payload: MusicLibraryCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if db.get(MusicLibrary, payload.name) is not None:
        raise ApiError(409, "music_library_exists", "同名音乐库已经存在")
    library = MusicLibrary(name=payload.name)
    db.add(library)
    audit(
        db,
        admin,
        "music_library.created",
        "music_library",
        payload.name,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "music_library_exists", "同名音乐库已经存在") from exc
    db.refresh(library)
    return {"library": _music_library_dict(db, library)}


@router.patch("/track-libraries/{library_name}")
def rename_music_library(
    library_name: str,
    payload: MusicLibraryUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    library = db.get(MusicLibrary, library_name)
    if library is None:
        raise ApiError(404, "music_library_not_found", "音乐库不存在")
    if library_name == "default":
        raise ApiError(409, "default_library_protected", "default 音乐库不能重命名")
    if payload.name == library_name:
        return {"library": _music_library_dict(db, library)}
    if db.get(MusicLibrary, payload.name) is not None:
        raise ApiError(409, "music_library_exists", "同名音乐库已经存在")

    now = utcnow()
    try:
        db.execute(
            update(MusicLibrary)
            .where(MusicLibrary.name == library_name)
            .values(name=payload.name, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        db.execute(
            update(Track)
            .where(Track.library_group == payload.name)
            .values(updated_at=now)
        )
        audit(
            db,
            admin,
            "music_library.renamed",
            "music_library",
            payload.name,
            {"from": library_name, "to": payload.name},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "music_library_exists", "同名音乐库已经存在") from exc
    renamed = db.get(MusicLibrary, payload.name)
    if renamed is None:
        raise ApiError(500, "music_library_rename_failed", "音乐库重命名失败")
    request.app.state.uploads.refresh_snapshot()
    return {"library": _music_library_dict(db, renamed)}


@router.delete("/track-libraries/{library_name}", status_code=204)
def delete_music_library(
    library_name: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    library = db.get(MusicLibrary, library_name)
    if library is None:
        raise ApiError(404, "music_library_not_found", "音乐库不存在")
    if library_name == "default":
        raise ApiError(409, "default_library_protected", "default 音乐库不能删除")
    track_count = (
        db.scalar(
            select(func.count(Track.id)).where(Track.library_group == library_name)
        )
        or 0
    )
    if track_count:
        raise ApiError(409, "music_library_not_empty", "音乐库非空，请先迁移其中的曲目")
    active_upload = db.scalar(
        select(UploadJob.id)
        .where(
            UploadJob.target_library == library_name,
            UploadJob.status.in_(CAPACITY_STATUSES),
        )
        .limit(1)
    )
    if active_upload is not None:
        raise ApiError(
            409,
            "music_library_has_active_uploads",
            "仍有上传任务以该音乐库为目标，请先等待或取消这些任务",
        )
    audit(
        db,
        admin,
        "music_library.deleted",
        "music_library",
        library_name,
    )
    db.delete(library)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            409,
            "music_library_not_empty",
            "音乐库非空，请先迁移其中的曲目",
        ) from exc
    request.app.state.uploads.refresh_snapshot()


@router.get("/tracks")
def list_tracks(
    page: int = Query(default=1, ge=1),
    library_group: str = Query(default="default", min_length=1, max_length=80),
    search: str = Query(default="", max_length=512),
    available_only: bool = Query(default=False),
    exclude_channel_id: int | None = Query(default=None, ge=1),
    _admin: User = Depends(require_admin_read),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if db.get(MusicLibrary, library_group) is None:
        raise ApiError(404, "music_library_not_found", "音乐库不存在")
    filters = [Track.library_group == library_group]
    search = search.strip()
    if search:
        filters.append(
            or_(
                Track.title.icontains(search, autoescape=True),
                Track.artist.icontains(search, autoescape=True),
                Track.album.icontains(search, autoescape=True),
                Track.original_filename.icontains(search, autoescape=True),
            )
        )
    if available_only:
        filters.append(Track.available.is_(True))
    if exclude_channel_id is not None:
        filters.append(
            ~Track.playlist_items.any(PlaylistItem.channel_id == exclude_channel_id)
        )

    total = db.scalar(select(func.count(Track.id)).where(*filters)) or 0
    total_pages = max(1, (total + TRACK_LIBRARY_PAGE_SIZE - 1) // TRACK_LIBRARY_PAGE_SIZE)
    resolved_page = min(page, total_pages)
    tracks = (
        db.scalars(
            select(Track)
            .options(selectinload(Track.playlist_items).joinedload(PlaylistItem.channel))
            .where(*filters)
            .order_by(Track.created_at.desc(), Track.id.desc())
            .offset((resolved_page - 1) * TRACK_LIBRARY_PAGE_SIZE)
            .limit(TRACK_LIBRARY_PAGE_SIZE)
        )
        .unique()
        .all()
    )
    library_total = (
        db.scalar(
            select(func.count(Track.id)).where(Track.library_group == library_group)
        )
        or 0
    )
    available_count = (
        db.scalar(
            select(func.count(Track.id)).where(
                Track.library_group == library_group,
                Track.available.is_(True),
            )
        )
        or 0
    )
    return {
        "items": [_track_with_references(track) for track in tracks],
        "page": resolved_page,
        "page_size": TRACK_LIBRARY_PAGE_SIZE,
        "total": total,
        "total_pages": total_pages,
        "library_group": library_group,
        "library_groups": _library_groups(db),
        "available_count": available_count,
        "unavailable_count": library_total - available_count,
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


@router.patch("/tracks/library")
def move_tracks_to_library(
    payload: TrackLibraryBatchMove,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if payload.source_library == payload.target_library:
        raise ApiError(409, "same_track_library", "源音乐库与目标音乐库不能相同")
    if db.get(MusicLibrary, payload.source_library) is None:
        raise ApiError(404, "source_music_library_not_found", "源音乐库不存在")
    if db.get(MusicLibrary, payload.target_library) is None:
        raise ApiError(404, "target_music_library_not_found", "目标音乐库不存在")

    now = utcnow()
    moved = 0
    for offset in range(0, len(payload.track_ids), 400):
        track_ids = payload.track_ids[offset : offset + 400]
        result = db.execute(
            update(Track)
            .where(
                Track.id.in_(track_ids),
                Track.library_group == payload.source_library,
            )
            .values(library_group=payload.target_library, updated_at=now)
        )
        moved += result.rowcount or 0
    if moved != len(payload.track_ids):
        db.rollback()
        found_ids: set[int] = set()
        for offset in range(0, len(payload.track_ids), 400):
            track_ids = payload.track_ids[offset : offset + 400]
            found_ids.update(db.scalars(select(Track.id).where(Track.id.in_(track_ids))).all())
        if len(found_ids) != len(payload.track_ids):
            raise ApiError(404, "track_not_found", "所选曲目中包含不存在的记录")
        raise ApiError(409, "track_library_changed", "部分曲目的所属音乐库已经发生变化")

    audit(
        db,
        admin,
        "track.library_moved",
        "track_library",
        payload.target_library,
        {
            "source_library": payload.source_library,
            "target_library": payload.target_library,
            "track_ids": payload.track_ids,
            "count": moved,
        },
    )
    db.commit()
    return {
        "moved": moved,
        "source_library": payload.source_library,
        "target_library": payload.target_library,
        "library_groups": _library_groups(db),
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
    request: Request,
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
    try:
        media_path = request.app.state.storage.track_path(track)
    except StorageUnavailable:
        media_path = None
    cover_path = settings.paths.cover_dir / track.cover_name if track.cover_name else None
    db.delete(track)
    audit(db, admin, "track.deleted", "track", track.id, {"filename": track.original_filename})
    db.commit()
    if media_path:
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


@router.post("/channels/{channel_id}/playlist/batch", status_code=201)
def add_playlist_items(
    channel_id: int,
    payload: PlaylistBatchAdd,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    channel = _get_channel(db, channel_id)
    existing_track_ids = set(
        db.scalars(
            select(PlaylistItem.track_id).where(
                PlaylistItem.channel_id == channel_id,
                PlaylistItem.track_id.in_(payload.track_ids),
            )
        ).all()
    )
    pending_track_ids = [
        track_id for track_id in payload.track_ids if track_id not in existing_track_ids
    ]
    if not pending_track_ids:
        return {"items": [], "skipped_existing": len(existing_track_ids)}

    tracks = list(db.scalars(select(Track).where(Track.id.in_(pending_track_ids))).all())
    tracks_by_id = {track.id: track for track in tracks}
    unavailable_track_ids = [
        track_id
        for track_id in pending_track_ids
        if track_id not in tracks_by_id or not tracks_by_id[track_id].available
    ]
    if unavailable_track_ids:
        raise ApiError(
            404,
            "tracks_unavailable",
            "部分曲目不存在或当前不可用",
            {"track_ids": unavailable_track_ids},
        )

    max_position = db.scalar(
        select(func.max(PlaylistItem.position)).where(PlaylistItem.channel_id == channel_id)
    )
    start_position = (max_position if max_position is not None else -1) + 1
    items = [
        PlaylistItem(
            channel_id=channel_id,
            track=tracks_by_id[track_id],
            position=start_position + offset,
        )
        for offset, track_id in enumerate(pending_track_ids)
    ]
    db.add_all(items)
    channel.playlist_version += 1
    channel.updated_at = utcnow()
    audit(
        db,
        admin,
        "playlist.items_added",
        "channel",
        channel.id,
        {
            "track_ids": pending_track_ids,
            "count": len(items),
            "skipped_existing": len(existing_track_ids),
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            409,
            "playlist_batch_conflict",
            "播放列表已被其他管理员更新，请刷新后重试",
        ) from exc
    _playlist_changed(request, channel)
    current_id = channel.playback_state.current_item_id if channel.playback_state else None
    return {
        "items": [playlist_item_dict(item, current_id) for item in items],
        "skipped_existing": len(existing_track_ids),
    }


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
