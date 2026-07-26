from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from backend.app.database import Database
from backend.app.models import (
    PLAYBACK_HISTORY_LIMIT,
    Channel,
    PlaybackHistory,
    PlaybackState,
    PlaylistItem,
    Track,
    utcnow,
)
from backend.app.serializers import channel_dict
from backend.app.services.playback import prune_playback_history
from backend.app.services.timeline import recover_timeline


def _track(index: int, duration: float) -> Track:
    return Track(
        storage_name=f"{index}.mp3",
        original_filename=f"{index}.mp3",
        sha256=f"{index:064x}",
        file_size_bytes=100,
        mime_type="audio/mpeg",
        audio_stream_index=0,
        duration_seconds=duration,
        title=f"Track {index}",
        artist="Artist",
        album="",
        available=True,
    )


def test_playback_history_keeps_latest_ten_entries(settings) -> None:
    database = Database(settings)
    database.initialize()
    now = utcnow()
    with database.session_factory.begin() as db:
        channel = db.get(Channel, 1)
        assert channel is not None
        track = _track(100, 30)
        db.add(track)
        db.flush()
        history_rows = [
            PlaybackHistory(
                channel_id=channel.id,
                track_id=track.id,
                started_at=now + timedelta(seconds=index),
                ended_at=now + timedelta(seconds=index + 1),
                end_reason="completed",
            )
            for index in range(PLAYBACK_HISTORY_LIMIT + 3)
        ]
        db.add_all(history_rows)
        db.flush()
        expected_ids = [entry.id for entry in history_rows[-PLAYBACK_HISTORY_LIMIT:]]
        prune_playback_history(db, channel.id)

    with database.session_factory() as db:
        retained_ids = list(
            db.scalars(
                select(PlaybackHistory.id)
                .where(PlaybackHistory.channel_id == 1)
                .order_by(PlaybackHistory.id)
            ).all()
        )
        assert retained_ids == expected_ids
        channel = db.get(Channel, 1)
        assert channel is not None
        response = channel_dict(db, channel, None, include_health=True)
        recent_history = response["health"]["recent_history"]
        assert len(recent_history) == PLAYBACK_HISTORY_LIMIT
        assert [entry["id"] for entry in recent_history] == list(reversed(expected_ids))
    database.close()


def test_recovery_walks_across_tracks_and_loops(settings) -> None:
    database = Database(settings)
    database.initialize()
    now = utcnow()
    with database.session_factory.begin() as db:
        channel = db.get(Channel, 1)
        assert channel is not None
        tracks = [_track(1, 30), _track(2, 40), _track(3, 50)]
        db.add_all(tracks)
        db.flush()
        items = [
            PlaylistItem(channel_id=channel.id, track_id=track.id, position=index)
            for index, track in enumerate(tracks)
        ]
        db.add_all(items)
        db.flush()
        state = channel.playback_state or PlaybackState(channel=channel)
        state.current_item_id = items[0].id
        state.position_seconds = 10
        state.anchor_at = now - timedelta(seconds=145)
        state.status = "live"

    with database.session_factory() as db:
        selection = recover_timeline(db, 1, now=now)
        assert selection is not None
        # 10 + 145 seconds, minus one 120-second loop: 35 seconds.
        assert selection.item_id == 2
        assert selection.offset_seconds == 5
    database.close()


def test_stopped_channel_does_not_advance(settings) -> None:
    database = Database(settings)
    database.initialize()
    now = utcnow()
    with database.session_factory.begin() as db:
        channel = db.get(Channel, 1)
        assert channel is not None
        track = _track(9, 100)
        db.add(track)
        db.flush()
        item = PlaylistItem(channel_id=channel.id, track_id=track.id, position=0)
        db.add(item)
        db.flush()
        state = channel.playback_state or PlaybackState(channel=channel)
        state.current_item_id = item.id
        state.position_seconds = 20
        state.anchor_at = now - timedelta(hours=1)
        state.status = "stopped"

    with database.session_factory() as db:
        selection = recover_timeline(db, 1, now=now)
        assert selection is not None
        assert selection.offset_seconds == 20
    database.close()


def test_recovery_normalizes_offsets_between_one_and_two_cycles(settings) -> None:
    database = Database(settings)
    database.initialize()
    now = utcnow()
    with database.session_factory.begin() as db:
        channel = db.get(Channel, 1)
        assert channel is not None
        tracks = [_track(index, 10) for index in range(20, 23)]
        db.add_all(tracks)
        db.flush()
        items = [
            PlaylistItem(channel_id=channel.id, track_id=track.id, position=index)
            for index, track in enumerate(tracks)
        ]
        db.add_all(items)
        db.flush()
        state = channel.playback_state or PlaybackState(channel=channel)
        state.current_item_id = items[0].id
        state.position_seconds = 0
        state.anchor_at = now - timedelta(seconds=55)
        state.status = "live"

    with database.session_factory() as db:
        selection = recover_timeline(db, 1, now=now)
        assert selection is not None
        assert selection.item_id == items[2].id
        assert selection.offset_seconds == 5
    database.close()


def test_unavailable_current_track_advances_to_next_playlist_item(settings) -> None:
    database = Database(settings)
    database.initialize()
    with database.session_factory.begin() as db:
        channel = db.get(Channel, 1)
        assert channel is not None
        first, second = _track(30, 10), _track(31, 10)
        first.available = False
        db.add_all([first, second])
        db.flush()
        first_item = PlaylistItem(channel_id=channel.id, track_id=first.id, position=0)
        second_item = PlaylistItem(channel_id=channel.id, track_id=second.id, position=1)
        db.add_all([first_item, second_item])
        db.flush()
        state = channel.playback_state or PlaybackState(channel=channel)
        state.current_item_id = first_item.id
        state.position_seconds = 5
        state.anchor_at = utcnow()
        state.status = "live"
        expected_id = second_item.id

    with database.session_factory() as db:
        selection = recover_timeline(db, 1)
        assert selection is not None
        assert selection.item_id == expected_id
        assert selection.offset_seconds == 0
    database.close()
