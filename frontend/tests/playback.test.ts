import { describe, expect, it } from 'vitest'
import { formatDuration, itemId, slugify, trackFromItem } from '../src/utils/format'
import {
  interpolatedPosition,
  parsePlaybackEvent,
  playbackFromEvent,
  playbackPercent,
} from '../src/utils/playback'

describe('playback utilities', () => {
  it('unwraps nested SSE data and preserves the prior state', () => {
    const event = parsePlaybackEvent(JSON.stringify({
      type: 'playback',
      data: {
        status: 'live',
        position_seconds: 42,
        duration_seconds: 120,
        current_track: { id: 9, title: 'Signal Test', artist: 'Station' },
      },
    }))

    expect(event).not.toBeNull()
    const result = playbackFromEvent(event!, {
      status: 'starting',
      current_item_id: 14,
      position_seconds: 0,
    })
    expect(result.playback).toMatchObject({
      status: 'live',
      current_item_id: 14,
      position_seconds: 42,
      duration_seconds: 120,
    })
    expect(result.track?.title).toBe('Signal Test')
  })

  it('interpolates from the server snapshot and clamps to duration', () => {
    const snapshot = {
      state: { status: 'live', position_seconds: 58, duration_seconds: 60 },
      receivedAt: 1_000,
    }
    expect(interpolatedPosition(snapshot, 2_000)).toBe(59)
    expect(interpolatedPosition(snapshot, 5_000)).toBe(60)
    expect(playbackPercent(30, 60)).toBe(50)
  })

  it('does not advance a non-live server snapshot', () => {
    expect(interpolatedPosition({
      state: { status: 'offline', position_seconds: 12, duration_seconds: 40 },
      receivedAt: 1_000,
    }, 20_000)).toBe(12)
  })

  it('resets stale position and duration when a track boundary omits an offset', () => {
    const result = playbackFromEvent({
      type: 'track',
      current_track: { id: 2, title: 'Next', duration_seconds: 90 },
      current_item_id: 22,
    }, {
      status: 'live',
      current_track: { id: 1, title: 'Previous', duration_seconds: 240 },
      current_item_id: 11,
      position_seconds: 239,
      duration_seconds: 240,
    })

    expect(result.playback.position_seconds).toBe(0)
    expect(result.playback.duration_seconds).toBe(90)
    expect(result.playback.current_track?.id).toBe(2)
  })
})

describe('display utilities', () => {
  it('formats durations without exposing invalid values', () => {
    expect(formatDuration(65.9)).toBe('1:05')
    expect(formatDuration(3_661)).toBe('1:01:01')
    expect(formatDuration(Number.NaN)).toBe('--:--')
  })

  it('normalizes slugs and flattened playlist records', () => {
    expect(slugify(' Night  Shift! ')).toBe('night-shift')
    const item = { id: 'item-1', track_id: 8, title: 'Night Shift', artist: 'Operator' }
    expect(itemId(item)).toBe('item-1')
    expect(trackFromItem(item)).toMatchObject({ id: 8, title: 'Night Shift', artist: 'Operator' })
  })
})
