from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete, desc, select, update
from sqlalchemy.orm import Session, joinedload

from ..config import Settings
from ..database import Database
from ..models import (
    PLAYBACK_HISTORY_LIMIT,
    Channel,
    PlaybackHistory,
    PlaybackState,
    PlaylistItem,
    Track,
    utcnow,
)
from ..security import aware_utc
from ..serializers import iso, track_dict
from .audio_streams import AudioFanout, AudioSubscription, FlacAudioFanout
from .events import EventBroker
from .listeners import ListenerRegistry
from .storage import StorageManager, StorageUnavailable
from .timeline import (
    TimelineSelection,
    force_timeline_item,
    playable_items,
    recover_timeline,
    select_next,
)

logger = logging.getLogger(__name__)


class StreamUnavailable(RuntimeError):
    pass


def _bitrate_bytes_per_second(value: str) -> int:
    normalized = value.strip().lower()
    multiplier = 1
    if normalized.endswith("k"):
        normalized = normalized[:-1]
        multiplier = 1000
    elif normalized.endswith("m"):
        normalized = normalized[:-1]
        multiplier = 1_000_000
    try:
        bits_per_second = float(normalized) * multiplier
    except ValueError:
        bits_per_second = 320_000
    return max(1, int(bits_per_second / 8))


def prune_playback_history(db: Session, channel_id: int) -> None:
    stale_ids = (
        select(PlaybackHistory.id)
        .where(PlaybackHistory.channel_id == channel_id)
        .order_by(desc(PlaybackHistory.id))
        .offset(PLAYBACK_HISTORY_LIMIT)
    )
    db.execute(
        delete(PlaybackHistory)
        .where(PlaybackHistory.id.in_(stale_ids))
        .execution_options(synchronize_session=False)
    )


@dataclass(frozen=True)
class ChannelCommand:
    kind: Literal["refresh", "skip", "play_now"]
    item_id: int | None = None


class PlaybackManager:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        storage: StorageManager,
        listeners: ListenerRegistry,
    ) -> None:
        self.settings = settings
        self.database = database
        self.storage = storage
        self.listeners = listeners
        self.events = EventBroker()
        self._supervisors: dict[int, ChannelSupervisor] = {}
        self._snapshots: dict[int, dict[str, object]] = {}
        self._snapshot_updated_at: dict[int, float] = {}
        self._enabled_channels_by_slug: dict[str, int] = {}
        self._channel_slugs: dict[int, str] = {}
        self._channel_routes_lock = threading.Lock()
        self._channel_locks: dict[int, asyncio.Lock] = {}
        self._management_tasks: set[asyncio.Task[object]] = set()
        self._demand_at: dict[int, float] = {}
        self._idle_refresh_at: dict[int, float] = {}
        self._demand_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._clean_stale_hls()
        with self.database.session_factory.begin() as db:
            channel_ids = db.scalars(select(PlaybackHistory.channel_id).distinct()).all()
            for channel_id in channel_ids:
                prune_playback_history(db, channel_id)
        with self.database.session_factory() as db:
            enabled_channels = list(
                db.execute(
                    select(Channel.id, Channel.slug).where(Channel.enabled.is_(True))
                ).all()
            )
        for channel_id, slug in enabled_channels:
            self.set_channel_route(channel_id, slug, enabled=True)
        channel_ids = [channel_id for channel_id, _slug in enabled_channels]
        self._demand_task = asyncio.create_task(
            self._monitor_demand(),
            name="radio-playback-demand",
        )
        for channel_id in channel_ids:
            if self.settings.streaming.always_on:
                await self._reconcile(channel_id, force_active=True)
            else:
                self._refresh_idle_timeline(channel_id)

    async def stop(self) -> None:
        self._stopping = True
        demand_task, self._demand_task = self._demand_task, None
        if demand_task is not None:
            demand_task.cancel()
            await asyncio.gather(demand_task, return_exceptions=True)
        if self._management_tasks:
            await asyncio.gather(*tuple(self._management_tasks), return_exceptions=True)
        supervisors = list(self._supervisors.values())
        self._supervisors.clear()
        if supervisors:
            await asyncio.gather(
                *(supervisor.stop(preserve_timeline=True) for supervisor in supervisors)
            )
        self._management_tasks.clear()
        self._channel_locks.clear()
        self._idle_refresh_at.clear()
        self._snapshot_updated_at.clear()

    def _clean_stale_hls(self) -> None:
        if not self.settings.streaming.process_control.stale_output_cleanup:
            return
        root = self.settings.paths.hls_dir
        root.mkdir(parents=True, exist_ok=True)
        for child in root.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                shutil.rmtree(child)

    def _schedule(self, callback: Callable[[], Coroutine[Any, Any, object]]) -> None:
        if self._loop is None or self._stopping:
            return

        def create() -> None:
            if self._stopping:
                return
            task = asyncio.create_task(callback())
            self._management_tasks.add(task)

            def finished(done_task: asyncio.Task[object]) -> None:
                self._management_tasks.discard(done_task)
                self._log_task_failure(done_task)

            task.add_done_callback(finished)

        self._loop.call_soon_threadsafe(create)

    @staticmethod
    def _log_task_failure(task: asyncio.Task[object]) -> None:
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            error = task.exception()
            if error:
                logger.error(
                    "background playback operation failed",
                    exc_info=(type(error), error, error.__traceback__),
                )

    def touch_demand(self, channel_id: int) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or self._stopping:
            return
        loop.call_soon_threadsafe(self._touch_demand, channel_id)

    def _touch_demand(self, channel_id: int) -> None:
        self._demand_at[channel_id] = time.monotonic()

    async def ensure_hls_stream(self, channel_id: int) -> None:
        self._touch_demand(channel_id)
        supervisor = self._supervisors.get(channel_id)
        if supervisor is None:
            await self._reconcile(channel_id, force_active=True)
            supervisor = self._supervisors.get(channel_id)
        if supervisor is None:
            raise StreamUnavailable
        await supervisor.wait_hls_ready(self.settings.player_api.startup_timeout_seconds)

    async def open_audio_stream(
        self,
        channel_id: int,
        stream_format: str,
    ) -> AudioSubscription:
        if stream_format not in {"aac", "flac"}:
            raise StreamUnavailable
        self._touch_demand(channel_id)
        supervisor = self._supervisors.get(channel_id)
        if supervisor is None:
            await self._reconcile(channel_id, force_active=True)
            supervisor = self._supervisors.get(channel_id)
        if supervisor is None:
            raise StreamUnavailable
        try:
            return await supervisor.open_audio_stream(
                stream_format,
                self.settings.player_api.startup_timeout_seconds,
            )
        except (FileNotFoundError, RuntimeError, TimeoutError) as exc:
            raise StreamUnavailable from exc

    async def _monitor_demand(self) -> None:
        try:
            while not self._stopping:
                await asyncio.sleep(1)
                now = time.monotonic()
                for channel_id, supervisor in tuple(self._supervisors.items()):
                    await supervisor.stop_lossless_if_idle(
                        now,
                        self.settings.streaming.idle_shutdown_seconds,
                    )
                    if self.settings.streaming.always_on:
                        continue
                    if (
                        self.listeners.count(channel_id) > 0
                        or supervisor.audio_listener_count > 0
                    ):
                        continue
                    last_demand = self._demand_at.setdefault(channel_id, now)
                    if (
                        now - last_demand
                        >= self.settings.streaming.idle_shutdown_seconds
                    ):
                        await self._deactivate_if_idle(channel_id, supervisor)
                for channel_id, refresh_at in tuple(self._idle_refresh_at.items()):
                    if channel_id in self._supervisors:
                        self._idle_refresh_at.pop(channel_id, None)
                    elif now >= refresh_at:
                        self._refresh_idle_timeline(channel_id)
        except asyncio.CancelledError:
            raise

    async def _deactivate_if_idle(
        self,
        channel_id: int,
        supervisor: ChannelSupervisor,
    ) -> None:
        lock = self._channel_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            if self._supervisors.get(channel_id) is not supervisor:
                return
            if (
                self.listeners.count(channel_id) > 0
                or supervisor.audio_listener_count > 0
            ):
                return
            self._supervisors.pop(channel_id, None)
            await supervisor.stop(preserve_timeline=True)
            self._demand_at.pop(channel_id, None)
            self._refresh_idle_timeline(channel_id)

    def _refresh_idle_timeline(self, channel_id: int) -> None:
        if channel_id in self._supervisors:
            self._idle_refresh_at.pop(channel_id, None)
            return
        with self.database.session_factory() as db:
            channel = db.get(Channel, channel_id)
            if channel is None or not channel.enabled:
                self._idle_refresh_at.pop(channel_id, None)
                return
            selection = recover_timeline(db, channel_id)
            state = channel.playback_state
            if state is None:
                state = PlaybackState(channel=channel)
                db.add(state)
            state.status = "idle" if selection is not None else "offline"
            state.ffmpeg_pid = None
            if selection is not None:
                state.last_error = None
            state.updated_at = utcnow()
            db.commit()
        self.publish_persisted_snapshot(channel_id, ffmpeg_running=False)
        snapshot = self.snapshot(channel_id)
        duration = snapshot.get("duration_seconds") if snapshot else None
        position = snapshot.get("position_seconds", 0) if snapshot else 0
        if isinstance(duration, (int, float)) and isinstance(position, (int, float)):
            self._idle_refresh_at[channel_id] = time.monotonic() + max(
                1.0,
                float(duration) - float(position),
            )
        else:
            self._idle_refresh_at.pop(channel_id, None)

    def reconcile(self, channel_id: int) -> None:
        self._schedule(lambda: self._reconcile(channel_id))

    def set_channel_route(self, channel_id: int, slug: str, *, enabled: bool) -> None:
        """Update the tiny in-memory routing index after a committed channel change."""
        with self._channel_routes_lock:
            previous_slug = self._channel_slugs.pop(channel_id, None)
            if (
                previous_slug is not None
                and self._enabled_channels_by_slug.get(previous_slug) == channel_id
            ):
                self._enabled_channels_by_slug.pop(previous_slug, None)
            if enabled:
                self._channel_slugs[channel_id] = slug
                self._enabled_channels_by_slug[slug] = channel_id

    def resolve_enabled_channel(self, slug: str) -> int | None:
        with self._channel_routes_lock:
            return self._enabled_channels_by_slug.get(slug)

    async def _reconcile(self, channel_id: int, *, force_active: bool = False) -> None:
        lock = self._channel_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            if self._stopping:
                return
            with self.database.session_factory() as db:
                channel = db.get(Channel, channel_id)
                enabled = bool(channel and channel.enabled)
                slug = channel.slug if channel else ""
            self.set_channel_route(channel_id, slug, enabled=enabled)
            current = self._supervisors.get(channel_id)
            if not enabled:
                self._idle_refresh_at.pop(channel_id, None)
                self.listeners.remove_channel(channel_id)
                self._demand_at.pop(channel_id, None)
                if current:
                    self._supervisors.pop(channel_id, None)
                    await current.stop()
                self.update_snapshot(
                    channel_id,
                    {
                        "channel_id": channel_id,
                        "status": "stopped",
                        "current_item_id": None,
                        "current_track": None,
                        "position_seconds": 0,
                        "server_time": iso(utcnow()),
                    },
                )
                return
            if current and current.slug != slug:
                self._supervisors.pop(channel_id, None)
                await current.stop(preserve_timeline=True)
                current = None
                if self._stopping:
                    return
            should_run = (
                self.settings.streaming.always_on
                or force_active
                or current is not None
                or self.listeners.count(channel_id) > 0
            )
            if not should_run:
                self._refresh_idle_timeline(channel_id)
                return
            if current is None:
                self._idle_refresh_at.pop(channel_id, None)
                self._demand_at.setdefault(channel_id, time.monotonic())
                current = ChannelSupervisor(self, channel_id, slug)
                self._supervisors[channel_id] = current
                current.start()
            elif not force_active:
                current.command(ChannelCommand("refresh"))

    def remove(self, channel_id: int) -> None:
        self.set_channel_route(channel_id, "", enabled=False)
        self._schedule(lambda: self._remove(channel_id))

    async def _remove(self, channel_id: int) -> None:
        lock = self._channel_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            self.listeners.remove_channel(channel_id)
            self._idle_refresh_at.pop(channel_id, None)
            supervisor = self._supervisors.pop(channel_id, None)
            if supervisor:
                await supervisor.stop()
            self._snapshots.pop(channel_id, None)
            self._snapshot_updated_at.pop(channel_id, None)

    def send_command(self, channel_id: int, command: ChannelCommand) -> None:
        self._schedule(lambda: self._send_command(channel_id, command))

    async def _send_command(self, channel_id: int, command: ChannelCommand) -> None:
        await self._reconcile(channel_id)
        supervisor = self._supervisors.get(channel_id)
        if supervisor:
            supervisor.command(command)
            return
        self._apply_idle_command(channel_id, command)

    def _apply_idle_command(self, channel_id: int, command: ChannelCommand) -> None:
        with self.database.session_factory() as db:
            selection = recover_timeline(db, channel_id)
            if command.kind == "play_now" and command.item_id is not None:
                with contextlib.suppress(ValueError):
                    selection = force_timeline_item(db, channel_id, command.item_id)
            elif command.kind == "skip" and selection is not None:
                channel = db.get(Channel, channel_id)
                if channel is not None:
                    state = channel.playback_state or PlaybackState(channel=channel)
                    db.add(state)
                    next_item = select_next(
                        state,
                        channel,
                        playable_items(db, channel_id),
                        selection.item_id,
                    )
                    if next_item is not None:
                        selection = force_timeline_item(db, channel_id, next_item.id)
            state = db.scalar(
                select(PlaybackState).where(PlaybackState.channel_id == channel_id)
            )
            if state is not None:
                state.status = "idle" if selection is not None else "offline"
                state.ffmpeg_pid = None
                state.updated_at = utcnow()
                db.commit()
        self.publish_persisted_snapshot(channel_id, ffmpeg_running=False)

    def update_snapshot(self, channel_id: int, snapshot: dict[str, object]) -> None:
        snapshot = {**snapshot, "listener_count": self.listeners.count(channel_id)}
        self._snapshots[channel_id] = snapshot
        self._snapshot_updated_at[channel_id] = time.monotonic()
        self.events.publish(
            channel_id,
            {"type": "playback", "playback": snapshot, **snapshot},
        )

    def update_runtime_snapshot(
        self,
        channel_id: int,
        changes: dict[str, object],
    ) -> None:
        snapshot = self.snapshot(channel_id) or {
            "channel_id": channel_id,
            "status": "starting",
            "current_item_id": None,
            "current_track": None,
            "position_seconds": 0.0,
            "duration_seconds": None,
            "last_error": None,
            "restart_count": 0,
        }
        snapshot.pop("listener_count", None)
        snapshot.update(changes)
        snapshot.setdefault("server_time", iso(utcnow()))
        self.update_snapshot(channel_id, snapshot)

    def publish_playlist_change(self, channel_id: int) -> None:
        if self._loop is None:
            return

        def publish() -> None:
            self.events.publish(channel_id, {"type": "playlist", "channel_id": channel_id})

        self._loop.call_soon_threadsafe(publish)

    def publish_channel_change(self, channel_id: int, channel: dict[str, object]) -> None:
        self.set_channel_route(
            channel_id,
            str(channel["slug"]),
            enabled=bool(channel["enabled"]),
        )
        if self._loop is None:
            return

        def publish() -> None:
            self.events.publish(
                channel_id,
                {"type": "channel", "channel_id": channel_id, "channel": channel},
            )

        self._loop.call_soon_threadsafe(publish)

    def supervisor_finished(
        self,
        supervisor: ChannelSupervisor,
        task: asyncio.Task[None],
    ) -> None:
        if self._supervisors.get(supervisor.channel_id) is not supervisor:
            return
        self._supervisors.pop(supervisor.channel_id, None)
        if not task.cancelled():
            with contextlib.suppress(Exception):
                error = task.exception()
                if error:
                    logger.error(
                        "channel %s supervisor crashed",
                        supervisor.channel_id,
                        exc_info=(type(error), error, error.__traceback__),
                    )
        if not self._stopping:
            self.reconcile(supervisor.channel_id)

    def publish_persisted_snapshot(
        self,
        channel_id: int,
        *,
        ffmpeg_running: bool,
    ) -> None:
        with self.database.session_factory() as db:
            channel = db.get(Channel, channel_id)
            if channel is None:
                return
            state = channel.playback_state
            item = None
            if state and state.current_item_id:
                item = db.scalar(
                    select(PlaylistItem)
                    .options(joinedload(PlaylistItem.track))
                    .where(PlaylistItem.id == state.current_item_id)
                )
            now = utcnow()
            position = state.position_seconds if state else 0
            if (
                state
                and state.status in {"live", "idle"}
                and state.anchor_at
            ):
                position += max(
                    0,
                    (now - aware_utc(state.anchor_at)).total_seconds(),
                )
            snapshot: dict[str, object] = {
                "channel_id": channel_id,
                "status": state.status if state else "starting",
                "current_item_id": item.id if item else None,
                "current_track": track_dict(item.track) if item else None,
                "position_seconds": position,
                "duration_seconds": item.track.duration_seconds if item else None,
                "server_time": iso(now),
                "started_at": iso(state.anchor_at) if state else None,
                "last_error": state.last_error if state else None,
                "ffmpeg_running": ffmpeg_running,
                "restart_count": state.restart_count if state else 0,
                "last_started_at": iso(state.last_started_at) if state else None,
            }
        self.update_snapshot(channel_id, snapshot)

    def snapshot(self, channel_id: int) -> dict[str, object] | None:
        value = self._snapshots.get(channel_id)
        if value is None:
            return None
        result = {**value, "listener_count": self.listeners.count(channel_id)}
        updated_at = self._snapshot_updated_at.get(channel_id)
        position = result.get("position_seconds")
        duration = result.get("duration_seconds")
        if (
            updated_at is not None
            and result.get("status") in {"live", "idle"}
            and isinstance(position, (int, float))
            and isinstance(duration, (int, float))
        ):
            result["position_seconds"] = min(
                float(duration),
                float(position) + max(0.0, time.monotonic() - updated_at),
            )
            result["server_time"] = iso(utcnow())
        return result


class ChannelSupervisor:
    PCM_SAMPLE_BYTES = 4
    PCM_CHUNK_FRAMES = 960  # 20 ms at 48 kHz

    def __init__(self, manager: PlaybackManager, channel_id: int, slug: str) -> None:
        self.manager = manager
        self.settings = manager.settings
        self.database = manager.database
        self.storage = manager.storage
        self.channel_id = channel_id
        self.slug = slug
        self.commands: asyncio.Queue[ChannelCommand] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._encoder: asyncio.subprocess.Process | None = None
        self._lossless_encoder: asyncio.subprocess.Process | None = None
        self._lossless_demand_at: float | None = None
        self._lossless_lock = asyncio.Lock()
        self._decoder: asyncio.subprocess.Process | None = None
        self._stderr_tasks: set[asyncio.Task[None]] = set()
        self._output_tasks: set[asyncio.Task[None]] = set()
        self._encoder_ready = asyncio.Event()
        aac_buffer_bytes = int(
            _bitrate_bytes_per_second(self.settings.streaming.output.bitrate)
            * self.settings.player_api.queue_seconds
        )
        lossless = self.settings.player_api.lossless
        lossless_buffer_bytes = int(
            lossless.sample_rate
            * lossless.channels
            * (lossless.sample_bits / 8)
            * self.settings.player_api.queue_seconds
        )
        self._aac_fanout = AudioFanout(
            aac_buffer_bytes,
            self.settings.player_api.queue_seconds,
        )
        self._lossless_fanout = FlacAudioFanout(
            lossless_buffer_bytes,
            self.settings.player_api.queue_seconds,
        )
        self._recent_errors: deque[str] = deque(maxlen=40)
        self._history_id: int | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"radio-channel-{self.channel_id}")
        self._task.add_done_callback(lambda task: self.manager.supervisor_finished(self, task))

    def command(self, command: ChannelCommand) -> None:
        self.commands.put_nowait(command)

    @property
    def audio_listener_count(self) -> int:
        return self._aac_fanout.listener_count + self._lossless_fanout.listener_count

    async def wait_hls_ready(self, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        manifest = self._channel_hls_dir() / "index.m3u8"
        while not manifest.is_file():
            if self._stopping.is_set() or (
                self._task is not None and self._task.done()
            ):
                raise StreamUnavailable
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.sleep(min(0.1, remaining))

    async def open_audio_stream(
        self,
        stream_format: str,
        timeout_seconds: float,
    ) -> AudioSubscription:
        await asyncio.wait_for(
            self._encoder_ready.wait(),
            timeout=timeout_seconds,
        )
        if self._stopping.is_set():
            raise StreamUnavailable
        if stream_format == "aac":
            return self._aac_fanout.subscribe()
        if stream_format != "flac":
            raise StreamUnavailable
        await self._ensure_lossless_encoder()
        self._lossless_demand_at = time.monotonic()
        return self._lossless_fanout.subscribe()

    async def stop_lossless_if_idle(
        self,
        now: float,
        idle_seconds: float,
    ) -> None:
        async with self._lossless_lock:
            if self._lossless_fanout.listener_count > 0:
                self._lossless_demand_at = now
                return
            if self._lossless_encoder is None:
                self._lossless_demand_at = None
                return
            last_demand = self._lossless_demand_at
            if last_demand is None:
                self._lossless_demand_at = now
                return
            if now - last_demand >= idle_seconds:
                await self._stop_lossless_encoder_locked()

    async def stop(self, preserve_timeline: bool = False) -> None:
        self._stopping.set()
        task, self._task = self._task, None
        if task:
            task.cancel()
            task_grace = self.settings.streaming.process_control.shutdown_timeout_seconds + 5
            done, _pending = await asyncio.wait({task}, timeout=task_grace)
            if not done:
                logger.warning(
                    "channel %s did not stop promptly; terminating media processes",
                    self.channel_id,
                )
        await self._stop_decoder()
        await self._stop_lossless_encoder()
        await self._stop_encoder()
        await self._finish_stderr_tasks()
        await self._finish_output_tasks()
        if task and not task.done():
            task.cancel()
            done, _pending = await asyncio.wait({task}, timeout=2)
            if not done:
                logger.error("channel %s supervisor task could not be reaped", self.channel_id)
        self._set_status("idle" if preserve_timeline else "stopped")
        if self.settings.streaming.process_control.stale_output_cleanup:
            self._remove_hls_output()

    async def _run(self) -> None:
        failures = 0
        selection: TimelineSelection | None = None
        try:
            while not self._stopping.is_set():
                try:
                    if selection is None:
                        with self.database.session_factory() as db:
                            selection = recover_timeline(db, self.channel_id)
                    if selection is None:
                        await self._stop_lossless_encoder()
                        await self._stop_encoder()
                        self._remove_hls_output()
                        self._set_status("offline", "频道歌单为空或没有可用歌曲")
                        command = await self._wait_for_command(30)
                        selection = self._apply_waiting_command(command)
                        continue
                    await self._ensure_encoder()
                    command = await self._play(selection)
                    failures = 0
                    selection = self._selection_after(command, selection.item_id)
                except FileNotFoundError as exc:
                    self._set_status("offline", str(exc))
                    await self._stop_lossless_encoder()
                    await self._stop_encoder()
                    command = await self._wait_for_command(30)
                    selection = self._apply_waiting_command(command)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failures += 1
                    message = self._recent_errors[-1] if self._recent_errors else str(exc)
                    status = (
                        "offline"
                        if failures >= self.settings.ffmpeg.restart.max_failures_before_offline
                        else "degraded"
                    )
                    self._set_status(status, message or "FFmpeg 播放进程异常")
                    await self._stop_decoder()
                    await self._stop_lossless_encoder()
                    await self._stop_encoder()
                    selection = None
                    delay = min(
                        self.settings.ffmpeg.restart.max_delay_seconds,
                        self.settings.ffmpeg.restart.initial_delay_seconds * 2 ** (failures - 1),
                    )
                    command = await self._wait_for_command(delay)
                    selection = self._apply_waiting_command(command)
        finally:
            await self._stop_decoder()
            await self._stop_lossless_encoder()
            await self._stop_encoder()

    async def _wait_for_command(self, wait_seconds: float) -> ChannelCommand | None:
        try:
            async with asyncio.timeout(wait_seconds):
                return await self.commands.get()
        except TimeoutError:
            return None

    def _apply_waiting_command(self, command: ChannelCommand | None) -> TimelineSelection | None:
        if command and command.kind == "play_now" and command.item_id is not None:
            with self.database.session_factory() as db, contextlib.suppress(ValueError):
                return force_timeline_item(db, self.channel_id, command.item_id)
        with self.database.session_factory() as db:
            return recover_timeline(db, self.channel_id)

    def _selection_after(
        self,
        command: ChannelCommand | None,
        current_item_id: int,
    ) -> TimelineSelection | None:
        with self.database.session_factory() as db:
            if command and command.kind == "play_now" and command.item_id is not None:
                try:
                    return force_timeline_item(db, self.channel_id, command.item_id)
                except ValueError:
                    return recover_timeline(db, self.channel_id)
            channel = db.get(Channel, self.channel_id)
            if channel is None:
                return None
            state = channel.playback_state or PlaybackState(channel=channel)
            db.add(state)
            items = playable_items(db, self.channel_id)
            next_item = select_next(state, channel, items, current_item_id)
            if next_item is None:
                state.current_item_id = None
                state.position_seconds = 0
                state.anchor_at = utcnow()
                db.commit()
                return None
            return force_timeline_item(db, self.channel_id, next_item.id)

    async def _ensure_encoder(self) -> None:
        if self._encoder and self._encoder.returncode is None:
            return
        if self._encoder is not None:
            await self._stop_encoder()
        binary = self.settings.ffmpeg.binary
        if not binary.is_file():
            raise FileNotFoundError(f"找不到 FFmpeg：{binary}")
        self._remove_hls_output()
        channel_dir = self._channel_hls_dir()
        channel_dir.mkdir(parents=True, exist_ok=True)
        with self.database.session_factory.begin() as db:
            state = db.scalar(
                select(PlaybackState).where(PlaybackState.channel_id == self.channel_id)
            )
            if state is None:
                state = PlaybackState(channel_id=self.channel_id)
                db.add(state)
                db.flush()
            state.generation += 1
            generation = state.generation
            start_number = max(state.next_media_sequence, int(time.time() * 1000))
            state.next_media_sequence = start_number
            state.restart_count += 1
            state.status = "starting"
            state.last_started_at = utcnow()
            state.updated_at = utcnow()
            restart_count = state.restart_count
            last_started_at = state.last_started_at
        output = self.settings.streaming.output
        hls = self.settings.streaming.hls
        # Keep the durable counter for recovery, and add a per-launch nonce so even
        # a lost final transaction or a wall-clock rollback cannot reuse cached URIs.
        stream_epoch = f"{generation}{secrets.randbelow(1_000_000_000_000):012d}"
        segment_pattern = channel_dir / f"g{stream_epoch}-seg-%019d.ts"
        manifest = channel_dir / "index.m3u8"
        hls_flags = "omit_endlist+temp_file+discont_start"
        if hls.delete_old_segments:
            hls_flags = f"delete_segments+{hls_flags}"
        hls_options = ":".join(
            (
                "f=hls",
                f"hls_segment_type={hls.segment_container}",
                f"hls_time={hls.segment_duration_seconds}",
                f"hls_list_size={hls.playlist_segments}",
                f"hls_delete_threshold={hls.delete_threshold}",
                f"start_number={start_number}",
                f"hls_flags={hls_flags}",
                f"hls_segment_filename={segment_pattern}",
            )
        )
        tee_target = f"[{hls_options}]{manifest}|[f=adts]pipe:1"
        command = [
            str(binary),
            "-hide_banner",
            "-loglevel",
            self.settings.ffmpeg.log_level,
            "-nostats",
            "-nostdin",
            "-y",
            "-re",
            "-f",
            "f32le",
            "-ar",
            str(output.sample_rate),
            "-ac",
            str(output.channels),
            "-blocksize",
            str(self._pcm_chunk_size),
            "-i",
            "pipe:0",
            "-map",
            "0:a:0",
            "-c:a",
            output.codec,
            "-profile:a",
            output.profile,
            "-b:a",
            output.bitrate,
            "-ar",
            str(output.sample_rate),
            "-ac",
            str(output.channels),
            "-threads",
            "1",
            "-f",
            "tee",
            tee_target,
        ]
        self._encoder = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._track_stderr(self._encoder, "encoder")
        self._track_output(self._encoder, self._aac_fanout, "aac")
        self._encoder_ready.set()
        with self.database.session_factory.begin() as db:
            state = db.scalar(
                select(PlaybackState).where(PlaybackState.channel_id == self.channel_id)
            )
            if state:
                state.ffmpeg_pid = self._encoder.pid
                state.updated_at = utcnow()
        self._publish_snapshot(
            status="starting",
            last_error=None,
            restart_count=restart_count,
            last_started_at=iso(last_started_at),
        )

    @property
    def _pcm_chunk_size(self) -> int:
        return (
            self.PCM_CHUNK_FRAMES * self.settings.streaming.output.channels * self.PCM_SAMPLE_BYTES
        )

    async def _play(self, selection: TimelineSelection) -> ChannelCommand | None:
        with self.database.session_factory() as db:
            item = db.scalar(
                select(PlaylistItem)
                .options(joinedload(PlaylistItem.track))
                .where(
                    PlaylistItem.channel_id == self.channel_id,
                    PlaylistItem.id == selection.item_id,
                )
            )
            if item is None or not item.track.available:
                return ChannelCommand("skip")
            track = item.track
            track_data = {
                "id": track.id,
                "storage_id": track.storage_id,
                "storage_name": track.storage_name,
                "stream_index": track.audio_stream_index,
                "duration": track.duration_seconds,
            }
        try:
            media_path = self.storage.resolve(
                str(track_data["storage_id"]),
                str(track_data["storage_name"]),
            )
        except StorageUnavailable:
            self._mark_track_missing(int(track_data["id"]))
            return ChannelCommand("skip")
        if not media_path.is_file():
            self._mark_track_missing(int(track_data["id"]))
            return ChannelCommand("skip")
        await self._start_decoder(
            media_path,
            int(track_data["stream_index"]),
            selection.offset_seconds,
        )
        assert self._decoder and self._decoder.stdout
        history_reason = "error"
        try:
            first_chunk = await asyncio.wait_for(
                self._decoder.stdout.read(self._pcm_chunk_size), timeout=15
            )
            if not first_chunk:
                await self._record_decoder_failure(int(track_data["id"]))
                return ChannelCommand("skip")
            self._history_id = self._begin_track(selection)
            bytes_written = 0
            last_checkpoint = time.monotonic()
            last_pcm_at = time.monotonic()
            stall_limit = max(10, self.settings.streaming.hls.segment_duration_seconds * 3)
            chunk: bytes | None = first_chunk
            while not self._stopping.is_set():
                command = self._next_control_command()
                if command:
                    history_reason = command.kind
                    return command
                if chunk == b"":
                    history_reason = "completed"
                    break
                if chunk is None:
                    try:
                        chunk = await asyncio.wait_for(
                            self._decoder.stdout.read(self._pcm_chunk_size),
                            timeout=0.5,
                        )
                    except TimeoutError as exc:
                        if time.monotonic() - last_pcm_at >= stall_limit:
                            raise RuntimeError(
                                "decoder produced no audio within the stall limit"
                            ) from exc
                        continue
                    if chunk:
                        last_pcm_at = time.monotonic()
                    continue
                if (
                    not self._encoder
                    or self._encoder.returncode is not None
                    or not self._encoder.stdin
                ):
                    raise RuntimeError("HLS encoder stopped unexpectedly")
                await self._write_pcm(
                    self._encoder,
                    chunk,
                    stall_limit,
                    "HLS encoder",
                )
                if (
                    self._lossless_encoder is not None
                    and self._lossless_encoder.returncode is None
                ):
                    try:
                        await self._write_pcm(
                            self._lossless_encoder,
                            chunk,
                            stall_limit,
                            "FLAC encoder",
                        )
                    except RuntimeError as exc:
                        self._recent_errors.append(str(exc)[-2000:])
                        await self._stop_lossless_encoder()
                bytes_written += len(chunk)
                last_pcm_at = time.monotonic()
                position = selection.offset_seconds + bytes_written / self._pcm_bytes_per_second
                checkpoint_interval = self.settings.streaming.playback.state_checkpoint_seconds
                if time.monotonic() - last_checkpoint >= checkpoint_interval:
                    await self._checkpoint(position)
                    last_checkpoint = time.monotonic()
                try:
                    chunk = await asyncio.wait_for(
                        self._decoder.stdout.read(self._pcm_chunk_size),
                        timeout=0.5,
                    )
                except TimeoutError:
                    chunk = None
            command = self._next_control_command()
            if command:
                history_reason = command.kind
            elif self._stopping.is_set():
                history_reason = "stopped"
            return command
        finally:
            if self._stopping.is_set():
                history_reason = "stopped"
            await self._stop_decoder()
            await asyncio.to_thread(self._finish_history, history_reason)

    @staticmethod
    async def _write_pcm(
        process: asyncio.subprocess.Process,
        chunk: bytes,
        timeout_seconds: float,
        label: str,
    ) -> None:
        if process.returncode is not None or process.stdin is None:
            raise RuntimeError(f"{label} stopped unexpectedly")
        process.stdin.write(chunk)
        try:
            await asyncio.wait_for(
                process.stdin.drain(),
                timeout=timeout_seconds,
            )
        except (BrokenPipeError, ConnectionResetError, TimeoutError) as exc:
            raise RuntimeError(f"{label} stopped accepting audio") from exc

    @property
    def _pcm_bytes_per_second(self) -> int:
        return (
            self.settings.streaming.output.sample_rate
            * self.settings.streaming.output.channels
            * self.PCM_SAMPLE_BYTES
        )

    def _next_control_command(self) -> ChannelCommand | None:
        while True:
            try:
                command = self.commands.get_nowait()
            except asyncio.QueueEmpty:
                return None
            if command.kind == "refresh":
                self.manager.publish_playlist_change(self.channel_id)
                continue
            return command

    async def _start_decoder(self, path: Path, stream_index: int, offset: float) -> None:
        await self._stop_decoder()
        command = [
            str(self.settings.ffmpeg.binary),
            "-hide_banner",
            "-loglevel",
            self.settings.ffmpeg.log_level,
            "-nostats",
            "-nostdin",
            "-protocol_whitelist",
            "file,pipe",
        ]
        if offset > 0:
            command.extend(["-ss", f"{offset:.6f}"])
        command.extend(
            [
                "-i",
                str(path),
                "-map",
                f"0:{stream_index}",
                "-vn",
                "-sn",
                "-dn",
                "-c:a",
                "pcm_f32le",
                "-ar",
                str(self.settings.streaming.output.sample_rate),
                "-ac",
                str(self.settings.streaming.output.channels),
                "-f",
                "f32le",
                "pipe:1",
            ]
        )
        self._decoder = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._track_stderr(self._decoder, "decoder")

    async def _ensure_lossless_encoder(self) -> None:
        async with self._lossless_lock:
            await self._ensure_lossless_encoder_locked()

    async def _ensure_lossless_encoder_locked(self) -> None:
        if self._lossless_encoder and self._lossless_encoder.returncode is None:
            return
        if self._lossless_encoder is not None:
            await self._stop_lossless_encoder_locked()
        binary = self.settings.ffmpeg.binary
        if not binary.is_file():
            raise FileNotFoundError(f"找不到 FFmpeg：{binary}")
        output = self.settings.streaming.output
        lossless = self.settings.player_api.lossless
        command = [
            str(binary),
            "-hide_banner",
            "-loglevel",
            self.settings.ffmpeg.log_level,
            "-nostats",
            "-nostdin",
            "-f",
            "f32le",
            "-ar",
            str(output.sample_rate),
            "-ac",
            str(output.channels),
            "-blocksize",
            str(self._pcm_chunk_size),
            "-i",
            "pipe:0",
            "-map",
            "0:a:0",
            "-c:a",
            lossless.codec,
            "-compression_level",
            str(lossless.compression_level),
            "-sample_fmt",
            f"s{lossless.sample_bits}",
            "-ar",
            str(lossless.sample_rate),
            "-ac",
            str(lossless.channels),
            "-threads",
            "1",
            "-f",
            "flac",
            "pipe:1",
        ]
        self._lossless_encoder = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._track_stderr(self._lossless_encoder, "lossless-encoder")
        self._track_output(
            self._lossless_encoder,
            self._lossless_fanout,
            "flac",
        )

    def _track_stderr(self, process: asyncio.subprocess.Process, label: str) -> None:
        async def drain() -> None:
            if not process.stderr:
                return
            while line := await process.stderr.readline():
                message = line.decode(errors="replace").strip()
                if message:
                    self._recent_errors.append(f"{label}: {message}"[-2000:])

        task = asyncio.create_task(drain())
        self._stderr_tasks.add(task)
        task.add_done_callback(self._stderr_tasks.discard)

    def _track_output(
        self,
        process: asyncio.subprocess.Process,
        fanout: AudioFanout | FlacAudioFanout,
        label: str,
    ) -> None:
        async def drain() -> None:
            try:
                if not process.stdout:
                    return
                while chunk := await process.stdout.read(4096):
                    fanout.publish(chunk)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._recent_errors.append(f"{label}: {exc}"[-2000:])
            finally:
                fanout.close()

        task = asyncio.create_task(
            drain(),
            name=f"radio-{label}-output-{self.channel_id}",
        )
        self._output_tasks.add(task)
        task.add_done_callback(self._output_tasks.discard)

    async def _stop_decoder(self) -> None:
        process, self._decoder = self._decoder, None
        if process is None:
            return
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=2)
        else:
            await process.wait()
        self._close_process_transport(process)

    async def _stop_lossless_encoder(self) -> None:
        async with self._lossless_lock:
            await self._stop_lossless_encoder_locked()

    async def _stop_lossless_encoder_locked(self) -> None:
        process, self._lossless_encoder = self._lossless_encoder, None
        self._lossless_demand_at = None
        if process is None:
            return
        if process.stdin:
            process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self.settings.streaming.process_control.shutdown_timeout_seconds,
                )
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=2)
        else:
            await process.wait()
        if process.stdin:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, TimeoutError):
                await asyncio.wait_for(process.stdin.wait_closed(), timeout=1)
        self._close_process_transport(process)
        self._lossless_fanout.close()

    async def _stop_encoder(self) -> None:
        process, self._encoder = self._encoder, None
        self._encoder_ready.clear()
        if process is None:
            return
        if process.stdin:
            process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self.settings.streaming.process_control.shutdown_timeout_seconds,
                )
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=2)
        else:
            await process.wait()
        if process.stdin:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, TimeoutError):
                await asyncio.wait_for(process.stdin.wait_closed(), timeout=1)
        self._close_process_transport(process)
        self._aac_fanout.close()
        with self.database.session_factory.begin() as db:
            state = db.scalar(
                select(PlaybackState).where(PlaybackState.channel_id == self.channel_id)
            )
            if state:
                state.ffmpeg_pid = None
                state.updated_at = utcnow()

    async def _finish_stderr_tasks(self) -> None:
        tasks = set(self._stderr_tasks)
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=2)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._stderr_tasks.difference_update(done | pending)

    async def _finish_output_tasks(self) -> None:
        tasks = set(self._output_tasks)
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=2)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._output_tasks.difference_update(done | pending)

    @staticmethod
    def _close_process_transport(process: asyncio.subprocess.Process) -> None:
        # asyncio Process exposes no public close method; closing its transport after wait()
        # releases any unread pipe transports before the application event loop is destroyed.
        transport = getattr(process, "_transport", None)
        if transport is not None:
            transport.close()

    def _remove_hls_output(self) -> None:
        channel_dir = self._channel_hls_dir()
        if channel_dir.is_dir():
            shutil.rmtree(channel_dir, ignore_errors=True)

    def _channel_hls_dir(self) -> Path:
        root = self.settings.paths.hls_dir.resolve()
        candidate = root / self.slug
        if candidate.is_symlink() or candidate.resolve().parent != root:
            raise RuntimeError("channel HLS path escapes the configured HLS directory")
        return candidate

    def _begin_track(self, selection: TimelineSelection) -> int:
        now = utcnow()
        with self.database.session_factory.begin() as db:
            state = db.scalar(
                select(PlaybackState).where(PlaybackState.channel_id == self.channel_id)
            )
            if state is None:
                state = PlaybackState(channel_id=self.channel_id)
                db.add(state)
            state.current_item_id = selection.item_id
            state.position_seconds = selection.offset_seconds
            state.anchor_at = now
            state.status = "live"
            state.last_error = None
            state.updated_at = now
            history = PlaybackHistory(
                channel_id=self.channel_id,
                playlist_item_id=selection.item_id,
                track_id=db.scalar(
                    select(PlaylistItem.track_id).where(PlaylistItem.id == selection.item_id)
                ),
                started_at=now - timedelta(seconds=selection.offset_seconds),
            )
            db.add(history)
            track = db.get(Track, history.track_id) if history.track_id else None
            if track:
                track.decode_failures = 0
                track.available = True
                track.unavailable_reason = None
                track.updated_at = now
                serialized_track = track_dict(track)
                duration_seconds: float | None = track.duration_seconds
            else:
                serialized_track = None
                duration_seconds = None
            db.flush()
            history_id = history.id
            prune_playback_history(db, self.channel_id)
        self._publish_snapshot(
            status="live",
            current_item_id=selection.item_id,
            current_track=serialized_track,
            position_seconds=selection.offset_seconds,
            duration_seconds=duration_seconds,
            server_time=iso(now),
            started_at=iso(now),
            last_error=None,
        )
        return history_id

    async def _checkpoint(self, position: float) -> None:
        now = utcnow()
        position = max(0, position)
        await asyncio.to_thread(self._persist_checkpoint, position, now)
        self._publish_snapshot(
            status="live",
            position_seconds=position,
            server_time=iso(now),
            started_at=iso(now),
        )

    def _persist_checkpoint(self, position: float, now: datetime) -> None:
        with self.database.session_factory.begin() as db:
            db.execute(
                update(PlaybackState)
                .where(PlaybackState.channel_id == self.channel_id)
                .values(
                    position_seconds=position,
                    anchor_at=now,
                    status="live",
                    updated_at=now,
                )
            )

    def _finish_history(self, reason: str) -> None:
        history_id, self._history_id = self._history_id, None
        if history_id is None:
            return
        with self.database.session_factory.begin() as db:
            history = db.get(PlaybackHistory, history_id)
            if history and history.ended_at is None:
                history.ended_at = utcnow()
                history.end_reason = reason

    async def _record_decoder_failure(self, track_id: int) -> None:
        with self.database.session_factory.begin() as db:
            track = db.get(Track, track_id)
            if track:
                track.decode_failures += 1
                if track.decode_failures >= 3:
                    track.available = False
                    track.unavailable_reason = "连续三次解码失败"
                track.updated_at = utcnow()

    def _mark_track_missing(self, track_id: int) -> None:
        with self.database.session_factory.begin() as db:
            track = db.get(Track, track_id)
            if track:
                track.available = False
                track.unavailable_reason = "媒体文件不存在"
                track.updated_at = utcnow()

    def _set_status(self, status: str, error: str | None = None) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as db:
            state = db.scalar(
                select(PlaybackState).where(PlaybackState.channel_id == self.channel_id)
            )
            if state is None:
                if db.get(Channel, self.channel_id) is None:
                    return
                state = PlaybackState(channel_id=self.channel_id)
                db.add(state)
            if (
                status == "idle"
                and state.anchor_at is not None
                and state.status in {"live", "starting", "degraded"}
            ):
                state.position_seconds = max(
                    0,
                    state.position_seconds
                    + (now - aware_utc(state.anchor_at)).total_seconds(),
                )
            state.status = status
            state.last_error = error
            state.ffmpeg_pid = self._encoder.pid if self._encoder else None
            state.anchor_at = now
            state.updated_at = now
            position_seconds = state.position_seconds
            restart_count = state.restart_count
            last_started_at = state.last_started_at
        self._publish_snapshot(
            status=status,
            last_error=error,
            position_seconds=position_seconds,
            server_time=iso(now),
            started_at=iso(now),
            restart_count=restart_count,
            last_started_at=iso(last_started_at),
        )

    def _publish_snapshot(self, **changes: object) -> None:
        self.manager.update_runtime_snapshot(
            self.channel_id,
            {
                **changes,
                "ffmpeg_running": bool(
                    self._encoder and self._encoder.returncode is None
                ),
            },
        )
