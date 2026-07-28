from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..models import Channel, PlaybackState, PlaylistItem, utcnow
from ..security import aware_utc


@dataclass(frozen=True)
class TimelineSelection:
    item_id: int
    offset_seconds: float


def playable_items(db: Session, channel_id: int) -> list[PlaylistItem]:
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


def _available(items: list[PlaylistItem]) -> list[PlaylistItem]:
    return [item for item in items if item.track.available and item.track.duration_seconds > 0]


def _new_shuffle(ids: list[int], previous: int | None = None) -> list[int]:
    result = list(ids)
    random.SystemRandom().shuffle(result)
    if previous is not None and len(result) > 1 and result[0] == previous:
        result[0], result[1] = result[1], result[0]
    return result


def _normalize_shuffle(
    state: PlaybackState,
    ids: list[int],
    current_id: int | None,
    playlist_version: int,
) -> None:
    existing = [item_id for item_id in state.shuffle_order if item_id in ids]
    if state.playlist_version == playlist_version and set(existing) == set(ids):
        state.shuffle_order = existing
        if existing:
            state.shuffle_cursor = min(state.shuffle_cursor, len(existing) - 1)
        return
    remaining = [item_id for item_id in ids if item_id != current_id]
    shuffled = _new_shuffle(remaining)
    if current_id in ids:
        state.shuffle_order = [current_id, *shuffled]
        state.shuffle_cursor = 0
    else:
        state.shuffle_order = shuffled
        state.shuffle_cursor = 0
    state.playlist_version = playlist_version


def select_next(
    state: PlaybackState,
    channel: Channel,
    items: list[PlaylistItem],
    current_id: int | None,
) -> PlaylistItem | None:
    available = _available(items)
    if not available:
        return None
    by_id = {item.id: item for item in available}
    ids = [item.id for item in available]

    if channel.playback_mode == "shuffle":
        _normalize_shuffle(state, ids, current_id, channel.playlist_version)
        if not state.shuffle_order:
            return None
        if current_id in state.shuffle_order:
            state.shuffle_cursor = state.shuffle_order.index(current_id)
        next_cursor = state.shuffle_cursor + 1
        if next_cursor >= len(state.shuffle_order):
            state.shuffle_order = _new_shuffle(ids, current_id)
            next_cursor = 0
        state.shuffle_cursor = next_cursor
        return by_id[state.shuffle_order[next_cursor]]

    if current_id in by_id:
        current_index = ids.index(current_id)
        return by_id[ids[(current_index + 1) % len(ids)]]
    return available[0]


def recover_timeline(
    db: Session,
    channel_id: int,
    now: datetime | None = None,
) -> TimelineSelection | None:
    now = now or utcnow()
    channel = db.get(Channel, channel_id)
    if channel is None:
        return None
    state = channel.playback_state
    if state is None:
        state = PlaybackState(channel=channel, status="starting")
        db.add(state)
        db.flush()
    items = playable_items(db, channel_id)
    available = _available(items)
    if not available:
        return None
    by_id = {item.id: item for item in available}

    current = by_id.get(state.current_item_id or -1)
    if current is None:
        if channel.playback_mode == "shuffle":
            stale_order = list(state.shuffle_order)
            stale_id = state.current_item_id
            if stale_id in stale_order:
                start = stale_order.index(stale_id)
                current = next(
                    (
                        by_id[stale_order[(start + step) % len(stale_order)]]
                        for step in range(1, len(stale_order) + 1)
                        if stale_order[(start + step) % len(stale_order)] in by_id
                    ),
                    None,
                )
            if current is None:
                _normalize_shuffle(state, list(by_id), None, channel.playlist_version)
                current = by_id[state.shuffle_order[0]]
                state.shuffle_cursor = 0
        else:
            all_ids = [item.id for item in items]
            stale_id = state.current_item_id
            if stale_id in all_ids:
                start = all_ids.index(stale_id)
                current = next(
                    (
                        by_id[all_ids[(start + step) % len(all_ids)]]
                        for step in range(1, len(all_ids) + 1)
                        if all_ids[(start + step) % len(all_ids)] in by_id
                    ),
                    available[0],
                )
            else:
                current = available[0]
        offset = 0.0
    else:
        offset = max(0.0, state.position_seconds)
        if state.status in {"live", "idle", "starting", "degraded", "offline"} and state.anchor_at:
            offset += max(0.0, (now - aware_utc(state.anchor_at)).total_seconds())

    cycle_duration = sum(item.track.duration_seconds for item in available)
    completed_cycles = int(offset // cycle_duration) if cycle_duration > 0 else 0
    if completed_cycles:
        offset %= cycle_duration
        if channel.playback_mode == "shuffle":
            ids = list(by_id)
            state.shuffle_order = _new_shuffle(ids, current.id)
            state.shuffle_cursor = state.shuffle_order.index(current.id)

    for _ in range(len(available)):
        duration = current.track.duration_seconds
        if offset < duration:
            break
        offset -= duration
        next_item = select_next(state, channel, items, current.id)
        if next_item is None:
            return None
        current = next_item
    if offset >= current.track.duration_seconds:
        raise RuntimeError("timeline recovery failed to normalize the track offset")

    state.current_item_id = current.id
    state.position_seconds = offset
    state.anchor_at = now
    state.playlist_version = channel.playlist_version
    state.updated_at = now
    db.commit()
    return TimelineSelection(current.id, offset)


def force_timeline_item(db: Session, channel_id: int, item_id: int) -> TimelineSelection:
    channel = db.get(Channel, channel_id)
    item = db.scalar(
        select(PlaylistItem)
        .options(joinedload(PlaylistItem.track))
        .where(PlaylistItem.channel_id == channel_id, PlaylistItem.id == item_id)
    )
    if channel is None or item is None or not item.track.available:
        raise ValueError("playlist item is unavailable")
    state = channel.playback_state or PlaybackState(channel=channel)
    db.add(state)
    if channel.playback_mode == "shuffle":
        ids = [candidate.id for candidate in _available(playable_items(db, channel_id))]
        _normalize_shuffle(state, ids, item.id, channel.playlist_version)
    state.current_item_id = item.id
    state.position_seconds = 0
    state.anchor_at = datetime.now(UTC)
    state.playlist_version = channel.playlist_version
    state.updated_at = utcnow()
    db.commit()
    return TimelineSelection(item.id, 0)
