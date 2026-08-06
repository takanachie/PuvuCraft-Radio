from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..dependencies import get_current_user, get_db, get_settings, require_csrf
from ..errors import ApiError
from ..models import Channel, User, utcnow
from ..schemas import PlayerUrlInput
from ..serializers import iso
from ..services.audio_streams import (
    AudioSubscription,
    PlayerConnectionCapacityError,
    PlayerConnectionLease,
)
from ..services.playback import StreamUnavailable
from ..services.player_tokens import (
    InvalidPlayerToken,
    PlayerTokenService,
    initialize_player_credential,
)

router = APIRouter(tags=["player"])
logger = logging.getLogger(__name__)

_ALL_HTTP_METHODS = [
    "CONNECT",
    "DELETE",
    "GET",
    "HEAD",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
    "TRACE",
]


def _plain_status(status_code: int) -> Response:
    return Response(
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _resolve_external_player(
    request: Request,
    stream_format: str,
    player_key: str,
    channel_slug: str,
) -> tuple[int, int, tuple[int, bytes]] | int:
    token_service: PlayerTokenService = request.app.state.player_tokens
    with request.app.state.database.session_factory() as db:
        try:
            validated = token_service.validate(db, player_key)
        except InvalidPlayerToken:
            return 404
        if stream_format == "flac" and validated.user.role != "admin":
            return 404
        channel = db.scalar(select(Channel).where(Channel.slug == channel_slug))
        if channel is None or not channel.enabled:
            return 503
        return validated.user.id, channel.id, validated.identity


def _player_key_state(
    user: User,
    token_service: PlayerTokenService,
) -> dict[str, object]:
    connect_before = token_service.connect_before(user)
    configured = (
        user.player_key_created_at is not None
        and user.player_key_generation is not None
    )
    return {
        "configured": configured,
        "created_at": iso(user.player_key_created_at),
        "connect_before": iso(connect_before),
        "valid_for_new_connections": bool(connect_before and utcnow() <= connect_before),
        "lossless_available": user.role == "admin",
    }


@router.get("/api/auth/player-key")
def get_player_key(
    request: Request,
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    return _player_key_state(user, request.app.state.player_tokens)


@router.post("/api/auth/player-key/regenerate")
def regenerate_player_key(
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, object]:
    initialize_player_credential(user)
    user.updated_at = utcnow()
    db.commit()
    db.refresh(user)
    request.app.state.player_connections.revoke_user(user.id)
    return _player_key_state(user, request.app.state.player_tokens)


@router.post("/api/auth/player-key/url")
def create_player_url(
    payload: PlayerUrlInput,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    token_service: PlayerTokenService = request.app.state.player_tokens
    connect_before = token_service.connect_before(user)
    if user.player_key_generation is None or connect_before is None:
        raise ApiError(409, "player_key_missing", "请先启用外部播放器连接")
    if utcnow() > connect_before:
        raise ApiError(409, "player_key_expired", "连接有效期已结束，请刷新有效日期")
    if payload.stream_format == "flac" and user.role != "admin":
        raise ApiError(403, "lossless_player_admin_only", "高保真播放器连接仅供管理员使用")
    channel = db.scalar(
        select(Channel).where(
            Channel.id == payload.channel_id,
            Channel.enabled.is_(True),
        )
    )
    if channel is None:
        raise ApiError(404, "channel_not_found", "频道不存在或未启用")
    token = token_service.issue(user)
    base_url = settings.app.public_base_url.rstrip("/")
    url = (
        f"{base_url}/listen/{payload.stream_format}/"
        f"{quote(token, safe='')}/{quote(channel.slug, safe='')}"
    )
    return {
        "url": url,
        "stream_format": payload.stream_format,
        "channel_id": channel.id,
        "created_at": iso(user.player_key_created_at),
        "connect_before": iso(connect_before),
    }


async def _stream_body(
    request: Request,
    subscription: AudioSubscription,
    lease: PlayerConnectionLease,
    first_chunk: bytes,
    *,
    user_id: int,
    channel_id: int,
    presence_interval_seconds: float,
) -> AsyncIterator[bytes]:
    connections = request.app.state.player_connections
    listeners = request.app.state.listeners
    playback = request.app.state.playback
    attached = await connections.attach_current_task(lease)
    if not attached:
        subscription.close()
        await connections.release(lease)
        return
    stream_task = asyncio.current_task()
    if stream_task is None or subscription.closed_event.is_set():
        subscription.close()
        await connections.release(lease)
        return

    async def cancel_closed_stream() -> None:
        await subscription.closed_event.wait()
        if not stream_task.done():
            stream_task.cancel()

    close_watcher = asyncio.create_task(cancel_closed_stream())
    last_presence = 0.0
    try:
        chunk: bytes | None = first_chunk
        while chunk is not None and not lease.superseded.is_set():
            now = time.monotonic()
            if now - last_presence >= presence_interval_seconds:
                listeners.touch(user_id, channel_id)
                playback.touch_demand(channel_id)
                last_presence = now
            yield chunk
            if lease.superseded.is_set() or await request.is_disconnected():
                break
            try:
                chunk = await subscription.receive(
                    timeout_seconds=(
                        request.app.state.settings.player_api.takeover_timeout_seconds
                    )
                )
            except TimeoutError:
                break
    except Exception:
        logger.exception(
            "external player stream ended unexpectedly",
            extra={"user_id": user_id, "channel_id": channel_id},
        )
    finally:
        subscription.close()
        close_watcher.cancel()
        await asyncio.gather(close_watcher, return_exceptions=True)
        await connections.release(lease)


@router.get(
    "/listen/{stream_format}/{player_key}/{channel_slug}",
    response_class=StreamingResponse,
)
async def external_player_stream(
    stream_format: str,
    player_key: str,
    channel_slug: str,
    request: Request,
) -> Response:
    if stream_format not in {"aac", "flac"}:
        return _plain_status(404)

    resolved = await asyncio.to_thread(
        _resolve_external_player,
        request,
        stream_format,
        player_key,
        channel_slug,
    )
    if isinstance(resolved, int):
        return _plain_status(resolved)
    user_id, channel_id, identity = resolved

    subscription: AudioSubscription | None = None
    try:
        subscription = await request.app.state.playback.open_audio_stream(
            channel_id,
            stream_format,
        )
        first_chunk = await subscription.receive(
            timeout_seconds=request.app.state.settings.player_api.startup_timeout_seconds
        )
        if first_chunk is None:
            subscription.close()
            return _plain_status(503)
    except (StreamUnavailable, TimeoutError):
        if subscription is not None:
            subscription.close()
        return _plain_status(503)

    try:
        lease = await request.app.state.player_connections.activate(identity)
    except PlayerConnectionCapacityError:
        subscription.close()
        return _plain_status(503)

    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",
        "icy-name": request.app.state.settings.player_api.title,
    }
    media_type = "audio/aac" if stream_format == "aac" else "audio/flac"
    return StreamingResponse(
        _stream_body(
            request,
            subscription,
            lease,
            first_chunk,
            user_id=user_id,
            channel_id=channel_id,
            presence_interval_seconds=max(
                1,
                request.app.state.settings.stream_access.listener_timeout_seconds / 2,
            ),
        ),
        media_type=media_type,
        headers=headers,
    )


@router.api_route(
    "/listen",
    methods=_ALL_HTTP_METHODS,
    response_class=Response,
    include_in_schema=False,
)
@router.api_route(
    "/listen/{unmatched_path:path}",
    methods=_ALL_HTTP_METHODS,
    response_class=Response,
    include_in_schema=False,
)
def reject_unmatched_player_request(unmatched_path: str = "") -> Response:
    return _plain_status(404)
