from __future__ import annotations

import asyncio
import json
import re
import time

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from ..dependencies import require_admin_read_once, require_admin_write_once
from ..errors import ApiError
from ..models import utcnow
from ..schemas import UploadHeartbeatInput, UploadPreflightInput, UploadReservationInput
from ..serializers import iso

router = APIRouter(prefix="/api/admin/uploads")
_JOB_ID = re.compile(r"^[a-f0-9]{32}$")


def _manager(request: Request):
    return request.app.state.uploads


@router.get("")
def list_uploads(
    request: Request,
    _admin_id: int = Depends(require_admin_read_once),
) -> dict[str, object]:
    return _manager(request).snapshot()


@router.post("", status_code=201)
def reserve_upload(
    payload: UploadReservationInput,
    request: Request,
    admin_id: int = Depends(require_admin_write_once),
) -> dict[str, object]:
    return {
        "job": _manager(request).reserve(
            admin_id,
            payload.client_id,
            payload.filename,
            payload.size_bytes,
            payload.target_library,
            confirm_similar=payload.confirm_similar,
        )
    }


@router.post("/preflight")
def preflight_uploads(
    payload: UploadPreflightInput,
    request: Request,
    _admin_id: int = Depends(require_admin_write_once),
) -> dict[str, object]:
    return _manager(request).preflight(payload.filenames)


@router.post("/heartbeat", status_code=204)
def upload_heartbeat(
    payload: UploadHeartbeatInput,
    request: Request,
    admin_id: int = Depends(require_admin_write_once),
) -> None:
    _manager(request).heartbeat(admin_id, payload.client_id)


@router.post("/expire", status_code=204)
def expire_upload_page(
    payload: UploadHeartbeatInput,
    request: Request,
    admin_id: int = Depends(require_admin_write_once),
) -> None:
    _manager(request).expire_client(admin_id, payload.client_id)


@router.get("/events")
async def upload_events(
    request: Request,
    _admin_id: int = Depends(require_admin_read_once),
) -> StreamingResponse:
    manager = _manager(request)

    async def stream():
        async with manager.events.subscribe() as queue:
            yield _sse("upload_queue", {"type": "upload_queue", **manager.snapshot()})
            next_auth_check = time.monotonic() + 10
            while not await request.is_disconnected():
                timed_out = False
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    timed_out = True
                    payload = {}
                if time.monotonic() >= next_auth_check:
                    try:
                        await asyncio.to_thread(require_admin_read_once, request)
                    except ApiError:
                        break
                    next_auth_check = time.monotonic() + 10
                if timed_out:
                    yield _sse(
                        "heartbeat",
                        {"type": "heartbeat", "server_time": iso(utcnow())},
                    )
                else:
                    yield _sse(str(payload.get("type", "upload_queue")), payload)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.put("/{job_id}/content", status_code=202)
async def upload_content(
    job_id: str,
    request: Request,
    client_id: str = Header(alias="X-Upload-Client-ID"),
    admin_id: int = Depends(require_admin_write_once),
) -> dict[str, object]:
    _validate_job_id(job_id)
    job = await _manager(request).receive(request, job_id, admin_id, client_id)
    return {"job": job}


@router.delete("/{job_id}", status_code=204)
def cancel_upload(
    job_id: str,
    request: Request,
    _admin_id: int = Depends(require_admin_write_once),
) -> None:
    _validate_job_id(job_id)
    _manager(request).cancel(job_id)


def _validate_job_id(job_id: str) -> None:
    if not _JOB_ID.fullmatch(job_id):
        raise ApiError(404, "upload_job_not_found", "上传任务不存在")


def _sse(event: str, payload: dict[str, object]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"
