from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload
from starlette.requests import ClientDisconnect

from ..database import Database
from ..errors import ApiError
from ..models import AuditEvent, MusicLibrary, UploadJob, User, utcnow
from ..security import aware_utc
from ..serializers import iso
from .media import MediaService
from .storage import StorageManager

logger = logging.getLogger(__name__)

CAPACITY_STATUSES = {"queued", "ready", "uploading", "verifying", "normalizing", "placing"}
ACTIVE_STATUSES = {"ready", "uploading", "verifying", "normalizing", "placing"}
CLIENT_BOUND_STATUSES = {"queued", "ready", "uploading"}
PROCESSING_STATUSES = {"verifying", "normalizing", "placing"}
TERMINAL_STATUSES = {"completed", "failed", "rejected", "cancelled", "expired"}


class UploadEventBroker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, object]]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=50)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, payload: dict[str, object]) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(payload)


class UploadManager:
    def __init__(
        self,
        database: Database,
        media: MediaService,
        storage: StorageManager,
    ) -> None:
        self.database = database
        self.media = media
        self.storage = storage
        self.settings = database.settings
        self.events = UploadEventBroker()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake = asyncio.Event()
        self._scheduler_task: asyncio.Task[None] | None = None
        self._processing_tasks: set[asyncio.Task[None]] = set()
        self._upload_tasks: dict[str, asyncio.Task[object]] = {}
        self._reservation_lock = threading.Lock()
        self._stopping = False

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.storage.initialize()
        self._recover_interrupted_jobs()
        self._scheduler_task = asyncio.create_task(
            self._scheduler(), name="radio-upload-scheduler"
        )
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        upload_tasks = set(self._upload_tasks.values())
        for upload_task in upload_tasks:
            upload_task.cancel()
        if upload_tasks:
            await asyncio.gather(*upload_tasks, return_exceptions=True)
        task, self._scheduler_task = self._scheduler_task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._processing_tasks:
            done, pending = await asyncio.wait(
                set(self._processing_tasks),
                timeout=self.settings.streaming.process_control.shutdown_timeout_seconds,
            )
            for pending_task in pending:
                pending_task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._processing_tasks.difference_update(done | pending)

    def _recover_interrupted_jobs(self) -> None:
        temp_names: list[str] = []
        now = utcnow()
        with self.database.session_factory.begin() as db:
            jobs = list(
                db.scalars(select(UploadJob).where(UploadJob.status.in_(CAPACITY_STATUSES))).all()
            )
            for job in jobs:
                if job.temp_name:
                    temp_names.append(job.temp_name)
                    job.temp_name = None
                job.status = "expired"
                job.error_code = "service_restarted"
                job.error_message = "服务重启，未完成的上传任务已过期"
                job.completed_at = now
                job.updated_at = now
        for name in temp_names:
            self._temp_path(name).unlink(missing_ok=True)
        for directory in (self.storage.upload_dir, self.storage.normalized_dir):
            if directory.is_dir():
                for child in directory.iterdir():
                    if child.is_file() or child.is_symlink():
                        child.unlink(missing_ok=True)

    def _temp_path(self, name: str) -> Path:
        if Path(name).name != name:
            raise RuntimeError("invalid upload temporary filename")
        root = self.storage.upload_dir.resolve()
        candidate = (root / name).resolve()
        if candidate.parent != root:
            raise RuntimeError("upload temporary path escapes its root")
        return candidate

    @staticmethod
    def _job_dict(job: UploadJob, queue_position: int | None = None) -> dict[str, object]:
        owner = job.owner
        return {
            "id": job.id,
            "owner": {
                "id": owner.id,
                "username": owner.username,
            },
            "client_id": job.client_id,
            "original_filename": job.original_filename,
            "target_library": job.target_library,
            "declared_size_bytes": job.declared_size_bytes,
            "bytes_received": job.bytes_received,
            "status": job.status,
            "queue_position": queue_position,
            "storage_id": job.storage_id,
            "storage_name": job.storage_name,
            "sha256": job.sha256,
            "track_id": job.track_id,
            "duplicate": job.duplicate,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "ready_at": iso(job.ready_at),
            "lease_expires_at": iso(job.lease_expires_at),
            "started_at": iso(job.started_at),
            "completed_at": iso(job.completed_at),
            "created_at": iso(job.created_at),
            "updated_at": iso(job.updated_at),
        }

    def snapshot(self) -> dict[str, object]:
        with self.database.session_factory() as db:
            active_jobs = list(
                db.scalars(
                    select(UploadJob)
                    .options(joinedload(UploadJob.owner))
                    .where(UploadJob.status.in_(CAPACITY_STATUSES))
                    .order_by(UploadJob.created_at, UploadJob.id)
                )
                .unique()
                .all()
            )
            historical_jobs = list(
                db.scalars(
                    select(UploadJob)
                    .options(joinedload(UploadJob.owner))
                    .where(~UploadJob.status.in_(CAPACITY_STATUSES))
                    .order_by(UploadJob.created_at.desc(), UploadJob.id.desc())
                    .limit(self.settings.uploads.history_limit)
                )
                .unique()
                .all()
            )
        jobs = active_jobs + historical_jobs
        queued_ids = [
            job.id
            for job in active_jobs
            if job.status == "queued"
        ]
        positions = {job_id: index + 1 for index, job_id in enumerate(queued_ids)}
        active_count = sum(job.status in ACTIVE_STATUSES for job in active_jobs)
        occupied = len(active_jobs)
        return {
            "jobs": [self._job_dict(job, positions.get(job.id)) for job in jobs],
            "queue_limit": self.settings.uploads.queue_limit,
            "max_concurrent": self.settings.uploads.max_concurrent,
            "active_count": active_count,
            "available_slots": max(0, self.settings.uploads.queue_limit - occupied),
            "heartbeat_interval_seconds": self.settings.uploads.heartbeat_interval_seconds,
        }

    def _publish_snapshot(self) -> None:
        if self._loop is None:
            return

        def publish() -> None:
            self.events.publish({"type": "upload_queue", **self.snapshot()})

        self._loop.call_soon_threadsafe(publish)

    def refresh_snapshot(self) -> None:
        self._wake_scheduler()
        self._publish_snapshot()

    def reserve(
        self,
        owner: User,
        client_id: str,
        filename: str,
        size_bytes: int,
        target_library: str,
        *,
        confirm_similar: bool = False,
    ) -> dict[str, object]:
        filename = Path(filename).name
        if not filename:
            raise ApiError(422, "missing_filename", "上传文件缺少名称")
        self.media.validate_filename(filename)
        if size_bytes > self.settings.media.max_upload_bytes:
            raise ApiError(413, "file_too_large", "音频文件超过 500 MiB 上限")
        with self.database.session_factory() as db:
            if db.get(MusicLibrary, target_library) is None:
                raise ApiError(
                    404,
                    "target_music_library_not_found",
                    "目标音乐库不存在，请重新选择",
                )
            if not confirm_similar:
                candidates = self.media.similar_tracks(db, filename)
                if candidates:
                    raise ApiError(
                        409,
                        "similar_tracks_found",
                        "检测到名称相似的已有曲目，请确认后继续上传",
                        {"candidates": candidates},
                    )
        now = utcnow()
        with self._reservation_lock, self.database.session_factory.begin() as db:
            if db.get(MusicLibrary, target_library) is None:
                raise ApiError(
                    404,
                    "target_music_library_not_found",
                    "目标音乐库不存在，请重新选择",
                )
            occupied = (
                db.scalar(
                    select(func.count(UploadJob.id)).where(
                        UploadJob.status.in_(CAPACITY_STATUSES)
                    )
                )
                or 0
            )
            if occupied >= self.settings.uploads.queue_limit:
                raise ApiError(409, "upload_queue_full", "公共上传队列已满")
            job = UploadJob(
                id=uuid.uuid4().hex,
                owner_user_id=owner.id,
                client_id=client_id,
                original_filename=filename,
                target_library=target_library,
                declared_size_bytes=size_bytes,
                bytes_received=0,
                status="queued",
                client_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(job)
        self._wake_scheduler()
        self._publish_snapshot()
        return self.get_job(job.id)

    def preflight(self, filenames: list[str]) -> dict[str, object]:
        checked_names: list[str] = []
        for requested_name in filenames:
            filename = Path(requested_name).name
            if not filename:
                raise ApiError(422, "missing_filename", "上传文件缺少名称")
            self.media.validate_filename(filename)
            checked_names.append(filename)
        with self.database.session_factory() as db:
            similarities = self.media.similar_tracks_batch(db, checked_names)
        files = [
            {
                "filename": filename,
                "candidates": similarities[filename],
            }
            for filename in checked_names
        ]
        return {"files": files}

    def heartbeat(self, owner_id: int, client_id: str) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as db:
            jobs = list(
                db.scalars(
                    select(UploadJob).where(
                        UploadJob.owner_user_id == owner_id,
                        UploadJob.client_id == client_id,
                        UploadJob.status.in_(CLIENT_BOUND_STATUSES),
                    )
                ).all()
            )
            for job in jobs:
                job.client_seen_at = now
                job.updated_at = now
        self._wake_scheduler()

    def expire_client(self, owner_id: int, client_id: str) -> None:
        now = utcnow()
        cleanup: list[tuple[str, str | None]] = []
        with self.database.session_factory.begin() as db:
            jobs = list(
                db.scalars(
                    select(UploadJob).where(
                        UploadJob.owner_user_id == owner_id,
                        UploadJob.client_id == client_id,
                        UploadJob.status.in_(CLIENT_BOUND_STATUSES),
                    )
                ).all()
            )
            for job in jobs:
                cleanup.append((job.id, job.temp_name))
                job.status = "expired"
                job.temp_name = None
                job.error_code = "page_closed"
                job.error_message = "上传页面已关闭"
                job.completed_at = now
                job.updated_at = now
        for job_id, temp_name in cleanup:
            if job_id in self._upload_tasks:
                self._request_upload_cancel(job_id)
            elif temp_name:
                self._temp_path(temp_name).unlink(missing_ok=True)
        if cleanup:
            self._wake_scheduler()
            self._publish_snapshot()

    def get_job(self, job_id: str) -> dict[str, object]:
        with self.database.session_factory() as db:
            job = db.scalar(
                select(UploadJob)
                .options(joinedload(UploadJob.owner))
                .where(UploadJob.id == job_id)
            )
            if job is None:
                raise ApiError(404, "upload_job_not_found", "上传任务不存在")
            return self._job_dict(job)

    def cancel(self, job_id: str) -> None:
        now = utcnow()
        temp_name: str | None = None
        with self.database.session_factory.begin() as db:
            job = db.get(UploadJob, job_id)
            if job is None:
                raise ApiError(404, "upload_job_not_found", "上传任务不存在")
            if job.status in PROCESSING_STATUSES:
                raise ApiError(409, "upload_already_processing", "文件已上传完成，后台正在处理")
            if job.status in TERMINAL_STATUSES:
                return
            job.status = "cancelled"
            job.error_code = "cancelled"
            job.error_message = "上传任务已取消"
            job.completed_at = now
            job.updated_at = now
            temp_name = job.temp_name
            job.temp_name = None
        upload_task = self._upload_tasks.get(job_id)
        if upload_task:
            self._request_upload_cancel(job_id)
        elif temp_name:
            self._temp_path(temp_name).unlink(missing_ok=True)
        self._wake_scheduler()
        self._publish_snapshot()

    async def receive(
        self,
        request: Request,
        job_id: str,
        owner_id: int,
        client_id: str,
    ) -> dict[str, object]:
        upload_task = asyncio.current_task()
        if upload_task is None:
            raise RuntimeError("upload request is not running in an asyncio task")
        if job_id in self._upload_tasks:
            raise ApiError(409, "upload_already_started", "该上传任务已经开始传输")
        self._upload_tasks[job_id] = upload_task
        now = utcnow()
        try:
            with self.database.session_factory.begin() as db:
                job = db.get(UploadJob, job_id)
                if job is None:
                    raise ApiError(404, "upload_job_not_found", "上传任务不存在")
                if job.owner_user_id != owner_id or job.client_id != client_id:
                    raise ApiError(403, "upload_job_owner_mismatch", "上传任务不属于当前页面")
                if job.status != "ready":
                    raise ApiError(409, "upload_job_not_ready", "上传任务尚未获得传输位置")
                if job.lease_expires_at and aware_utc(job.lease_expires_at) <= now:
                    raise ApiError(409, "upload_lease_expired", "上传开始租约已过期")
                extension = self.media.validate_filename(job.original_filename)
                temp_name = f"{job.id}.part{extension}"
                job.status = "uploading"
                job.temp_name = temp_name
                job.started_at = now
                job.lease_expires_at = None
                job.updated_at = now
                declared_size = job.declared_size_bytes
        except Exception:
            if self._upload_tasks.get(job_id) is upload_task:
                self._upload_tasks.pop(job_id, None)
            raise

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                raw_length = int(content_length)
            except ValueError as exc:
                self._fail(job_id, "invalid_content_length", "上传长度无效")
                if self._upload_tasks.get(job_id) is upload_task:
                    self._upload_tasks.pop(job_id, None)
                raise ApiError(400, "invalid_content_length", "上传长度无效") from exc
            if raw_length != declared_size:
                self._fail(job_id, "size_mismatch", "上传长度与预约大小不一致")
                if self._upload_tasks.get(job_id) is upload_task:
                    self._upload_tasks.pop(job_id, None)
                raise ApiError(409, "size_mismatch", "上传长度与预约大小不一致")

        path = self._temp_path(temp_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        received = 0
        checkpoint = 0
        try:
            stream = path.open("xb")
        except Exception:
            self._fail(job_id, "upload_failed", "无法创建上传临时文件")
            if self._upload_tasks.get(job_id) is upload_task:
                self._upload_tasks.pop(job_id, None)
            raise
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                received += len(chunk)
                if received > declared_size or received > self.settings.media.max_upload_bytes:
                    raise ApiError(413, "file_too_large", "上传内容超过预约大小")
                await asyncio.to_thread(stream.write, chunk)
                if received - checkpoint >= self.settings.uploads.progress_checkpoint_bytes:
                    await asyncio.to_thread(self._progress, job_id, received)
                    checkpoint = received
            if received != declared_size:
                raise ApiError(409, "size_mismatch", "收到的文件大小与预约不一致")
            stream.flush()
            self._progress(job_id, received)
            self._set_stage(job_id, "verifying")
        except (ClientDisconnect, asyncio.CancelledError):
            self._expire_upload(job_id, "上传页面已关闭或连接中断")
            path.unlink(missing_ok=True)
            raise
        except ApiError as exc:
            self._fail(job_id, exc.code, exc.message)
            path.unlink(missing_ok=True)
            raise
        except Exception:
            self._fail(job_id, "upload_failed", "接收上传文件时发生错误")
            path.unlink(missing_ok=True)
            raise
        finally:
            stream.close()
            if self._upload_tasks.get(job_id) is upload_task:
                self._upload_tasks.pop(job_id, None)

        task = asyncio.create_task(self._process(job_id, path), name=f"upload-process-{job_id}")
        self._processing_tasks.add(task)
        task.add_done_callback(self._processing_tasks.discard)
        self._publish_snapshot()
        return self.get_job(job_id)

    @staticmethod
    def _fsync_path(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _progress(self, job_id: str, received: int) -> None:
        with self.database.session_factory.begin() as db:
            job = db.get(UploadJob, job_id)
            if job and job.status == "uploading":
                job.bytes_received = received
                job.updated_at = utcnow()
        self._publish_snapshot()

    async def _process(self, job_id: str, path: Path) -> None:
        try:
            await asyncio.to_thread(self._fsync_path, path)
            await asyncio.to_thread(self._process_sync, job_id, path)
        except ApiError as exc:
            self._fail(job_id, exc.code, exc.message)
        except Exception:
            logger.exception("Upload processing failed for job %s", job_id)
            self._fail(job_id, "processing_failed", "音频校验、规范化或迁移失败")
        finally:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            self._wake_scheduler()
            self._publish_snapshot()

    def _process_sync(self, job_id: str, path: Path) -> None:
        with self.database.session_factory() as db:
            job = db.get(UploadJob, job_id)
            if job is None or job.status not in PROCESSING_STATUSES:
                return
            filename = job.original_filename
            owner_id = job.owner_user_id
            target_library = job.target_library
            if target_library is None:
                raise ApiError(
                    409,
                    "target_music_library_removed",
                    "目标音乐库已被删除，请重新提交上传任务",
                )
            track, duplicate = self.media.import_staged(
                db,
                path,
                filename,
                library_group=target_library,
                job_id=job.id,
                status_callback=lambda status: self._set_stage(job_id, status),
            )
        now = utcnow()
        with self.database.session_factory.begin() as db:
            job = db.get(UploadJob, job_id)
            if job is None:
                return
            job.status = "rejected" if duplicate else "completed"
            job.bytes_received = job.declared_size_bytes
            job.temp_name = None
            job.storage_id = track.storage_id
            job.storage_name = track.storage_name
            job.sha256 = track.sha256
            job.track_id = track.id
            job.duplicate = duplicate
            job.error_code = "duplicate_content" if duplicate else None
            job.error_message = (
                f"内容与已有曲目“{track.title}”完全相同，已自动驳回"
                if duplicate
                else None
            )
            job.completed_at = now
            job.updated_at = now
            db.add(
                AuditEvent(
                    actor_user_id=owner_id,
                    action="track.upload_rejected" if duplicate else "track.uploaded",
                    target_type="track",
                    target_id=str(track.id),
                    details={
                        "duplicate": duplicate,
                        "upload_job_id": job_id,
                        "target_library": target_library,
                        "track_library": track.library_group,
                        **({"reason": "sha256_match"} if duplicate else {}),
                    },
                )
            )

    def _set_stage(self, job_id: str, status: str) -> None:
        if status not in PROCESSING_STATUSES:
            raise ValueError(f"unsupported upload processing status: {status}")
        allowed_current = (
            {"uploading", "verifying"} if status == "verifying" else PROCESSING_STATUSES
        )
        with self.database.session_factory.begin() as db:
            job = db.get(UploadJob, job_id)
            if job and job.status in allowed_current:
                job.status = status
                job.updated_at = utcnow()
        self._publish_snapshot()

    def _expire_upload(self, job_id: str, message: str) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as db:
            job = db.get(UploadJob, job_id)
            if job and job.status == "uploading":
                job.status = "expired"
                job.error_code = "client_disconnected"
                job.error_message = message
                job.completed_at = now
                job.updated_at = now
                job.temp_name = None
        self._wake_scheduler()
        self._publish_snapshot()

    def _fail(self, job_id: str, code: str, message: str) -> None:
        now = utcnow()
        temp_name: str | None = None
        with self.database.session_factory.begin() as db:
            job = db.get(UploadJob, job_id)
            if job and job.status not in TERMINAL_STATUSES:
                job.status = "failed"
                job.error_code = code
                job.error_message = message
                job.completed_at = now
                job.updated_at = now
                temp_name = job.temp_name
                job.temp_name = None
        if temp_name:
            self._temp_path(temp_name).unlink(missing_ok=True)
        self._wake_scheduler()
        self._publish_snapshot()

    def _wake_scheduler(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    async def _scheduler(self) -> None:
        while not self._stopping:
            try:
                await self._schedule_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A later tick retries reconciliation; individual upload errors are stored per job.
                logger.exception("Upload scheduler reconciliation failed")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=1)

    async def _schedule_once(self) -> None:
        changed = False
        abandoned_temp_names: list[str] = []
        now = utcnow()
        stale_before = now - timedelta(seconds=self.settings.uploads.heartbeat_timeout_seconds)
        with self.database.session_factory.begin() as db:
            jobs = list(
                db.scalars(
                    select(UploadJob)
                    .where(UploadJob.status.in_(CAPACITY_STATUSES))
                    .order_by(UploadJob.created_at, UploadJob.id)
                ).all()
            )
            for job in jobs:
                stale_client = (
                    job.status in CLIENT_BOUND_STATUSES
                    and aware_utc(job.client_seen_at) < stale_before
                )
                expired_lease = (
                    job.status == "ready"
                    and job.lease_expires_at is not None
                    and aware_utc(job.lease_expires_at) <= now
                )
                if stale_client or expired_lease:
                    job.status = "expired"
                    job.error_code = (
                        "client_disconnected" if stale_client else "upload_lease_expired"
                    )
                    job.error_message = (
                        "上传页面已关闭或失去心跳"
                        if stale_client
                        else "未在租约时间内开始上传"
                    )
                    job.completed_at = now
                    job.updated_at = now
                    temp_name = job.temp_name
                    job.temp_name = None
                    upload_task = self._upload_tasks.get(job.id)
                    if upload_task:
                        self._request_upload_cancel(job.id)
                    elif temp_name:
                        abandoned_temp_names.append(temp_name)
                    changed = True
            active = sum(job.status in ACTIVE_STATUSES for job in jobs)
            available = max(0, self.settings.uploads.max_concurrent - active)
            for job in (candidate for candidate in jobs if candidate.status == "queued"):
                if available <= 0:
                    break
                if aware_utc(job.client_seen_at) < stale_before:
                    continue
                job.status = "ready"
                job.ready_at = now
                job.lease_expires_at = now + timedelta(
                    seconds=self.settings.uploads.ready_lease_seconds
                )
                job.updated_at = now
                available -= 1
                changed = True
        for temp_name in abandoned_temp_names:
            self._temp_path(temp_name).unlink(missing_ok=True)
        if changed:
            self._publish_snapshot()

    def _request_upload_cancel(self, job_id: str) -> None:
        upload_task = self._upload_tasks.get(job_id)
        if upload_task is None:
            return
        if self._loop is None:
            upload_task.cancel()
        else:
            self._loop.call_soon_threadsafe(upload_task.cancel)
