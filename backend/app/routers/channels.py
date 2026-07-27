from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..config import Settings
from ..dependencies import authenticate_once, get_current_user, get_db, get_settings
from ..errors import ApiError
from ..models import Channel, PlaybackState, PlaylistItem, User, utcnow
from ..serializers import channel_dict, iso, playlist_item_dict

router = APIRouter()
_HLS_URI = re.compile(
    r"^/hls/([a-z0-9]+(?:-[a-z0-9]+)*)/"
    r"(?:index\.m3u8|g[0-9]+-seg-[0-9]+\.ts)$"
)


def _enabled_channel(db: Session, channel_id: int) -> Channel:
    channel = db.scalar(
        select(Channel)
        .options(selectinload(Channel.playback_state))
        .where(Channel.id == channel_id, Channel.enabled.is_(True))
    )
    if channel is None:
        raise ApiError(404, "channel_not_found", "频道不存在或未启用")
    return channel


@router.get("/api/channels")
def list_channels(
    request: Request,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    channels = db.scalars(
        select(Channel)
        .options(selectinload(Channel.playback_state))
        .where(Channel.enabled.is_(True))
        .order_by(Channel.display_order, Channel.id)
    ).all()
    manager = request.app.state.playback
    return [channel_dict(db, channel, manager.snapshot(channel.id)) for channel in channels]


@router.get("/api/channels/{channel_id}")
def get_channel(
    channel_id: int,
    request: Request,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    channel = _enabled_channel(db, channel_id)
    return channel_dict(db, channel, request.app.state.playback.snapshot(channel.id))


@router.get("/api/channels/{channel_id}/playlist")
def get_playlist(
    channel_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    channel = _enabled_channel(db, channel_id)
    items = (
        db.scalars(
            select(PlaylistItem)
            .options(joinedload(PlaylistItem.track))
            .where(PlaylistItem.channel_id == channel_id)
            .order_by(PlaylistItem.position, PlaylistItem.id)
        )
        .unique()
        .all()
    )
    current_id = channel.playback_state.current_item_id if channel.playback_state else None
    return [playlist_item_dict(item, current_id) for item in items]


@router.get("/api/channels/{channel_id}/events")
async def channel_events(
    channel_id: int,
    request: Request,
    _session_id: int = Depends(authenticate_once),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    with request.app.state.database.session_factory() as db:
        _enabled_channel(db, channel_id)
    manager = request.app.state.playback

    async def stream():
        async with manager.events.subscribe(channel_id) as queue:
            auth_interval = min(settings.events.heartbeat_seconds, 10)
            next_auth_check = time.monotonic() + auth_interval
            initial = manager.snapshot(channel_id)
            if initial:
                yield _sse("playback", {"type": "playback", "playback": initial, **initial})
            while not await request.is_disconnected():
                timed_out = False
                try:
                    payload = await asyncio.wait_for(
                        queue.get(),
                        timeout=max(
                            0.1,
                            min(
                                settings.events.heartbeat_seconds,
                                next_auth_check - time.monotonic(),
                            ),
                        ),
                    )
                except TimeoutError:
                    timed_out = True
                    payload = {}
                if time.monotonic() >= next_auth_check:
                    with request.app.state.database.session_factory() as session_db:
                        try:
                            request.app.state.auth.authenticate_request(request, session_db)
                        except ApiError:
                            break
                    next_auth_check = time.monotonic() + auth_interval
                if timed_out:
                    yield _sse("heartbeat", {"type": "heartbeat", "server_time": iso(utcnow())})
                    continue
                event_name = str(payload.get("type", "message"))
                yield _sse(event_name, payload)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse(event: str, payload: dict[str, object]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


@router.get("/api/internal/stream-auth", status_code=204)
def stream_authorization(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    original_uri = request.headers.get("X-Original-URI", "")
    path = unquote(urlsplit(original_uri).path)
    match = _HLS_URI.fullmatch(path)
    if not match:
        raise ApiError(403, "invalid_stream_path", "无效的音频流地址")
    channel_id = db.scalar(
        select(Channel.id)
        .join(PlaybackState, PlaybackState.channel_id == Channel.id)
        .where(
            Channel.slug == match.group(1),
            Channel.enabled.is_(True),
            PlaybackState.status == "live",
        )
    )
    if channel_id is None:
        raise ApiError(403, "stream_unavailable", "频道当前不可用")
    request.app.state.listeners.touch(user.id, channel_id)
    return Response(status_code=204)


@router.get("/api/internal/admin-auth", status_code=204)
def admin_authorization(_user: User = Depends(get_current_user)) -> Response:
    if _user.role != "admin":
        raise ApiError(403, "admin_required", "需要管理员权限")
    return Response(status_code=204)


@router.get("/hls/{slug}/{file_path:path}")
def development_hls(
    slug: str,
    file_path: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ApiError(404, "stream_not_found", "音频流不存在")
    if not re.fullmatch(r"(?:index\.m3u8|g[0-9]+-seg-[0-9]+\.ts)", file_path):
        raise ApiError(404, "stream_not_found", "音频流不存在")
    channel_id = db.scalar(
        select(Channel.id)
        .join(PlaybackState, PlaybackState.channel_id == Channel.id)
        .where(
            Channel.slug == slug,
            Channel.enabled.is_(True),
            PlaybackState.status == "live",
        )
    )
    if channel_id is None:
        raise ApiError(404, "stream_not_found", "音频流不存在")
    root = (settings.paths.hls_dir / slug).resolve()
    target = (root / file_path).resolve()
    if root not in target.parents or not target.is_file():
        raise ApiError(404, "stream_not_found", "音频流尚未就绪")
    request.app.state.listeners.touch(user.id, channel_id)
    media_type = "application/vnd.apple.mpegurl" if target.suffix == ".m3u8" else "video/mp2t"
    return FileResponse(target, media_type=media_type)


@router.get("/api/covers/{cover_name}")
def cover_image(
    cover_name: str,
    _user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    if Path(cover_name).name != cover_name:
        raise ApiError(404, "cover_not_found", "封面不存在")
    target = settings.paths.cover_dir / cover_name
    if not target.is_file() or target.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ApiError(404, "cover_not_found", "封面不存在")
    return FileResponse(target)
