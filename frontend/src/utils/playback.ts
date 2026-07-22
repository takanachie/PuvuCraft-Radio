import type { PlaybackEvent, PlaybackState, TrackSummary } from '../api/types'
import { clamp } from './format'

export interface TimedPlayback {
  state: PlaybackState
  receivedAt: number
}

function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

export function parsePlaybackEvent(raw: string): PlaybackEvent | null {
  try {
    const parsed = JSON.parse(raw) as unknown
    const root = object(parsed)
    if (!root) return null
    const nested = object(root.data)
    return (nested ? { ...root, ...nested } : root) as PlaybackEvent
  } catch {
    return null
  }
}

export function playbackFromEvent(
  event: PlaybackEvent,
  previous: PlaybackState,
): { playback: PlaybackState; track?: TrackSummary | null } {
  const eventRecord = event as unknown as Record<string, unknown>
  const nested = object(event.playback)
    ?? object(event.playback_state)
    ?? object(event.state)
    ?? object(event.channel?.playback)
    ?? object(event.channel?.playback_state)
  const source = { ...eventRecord, ...(nested || {}) } as Partial<PlaybackState>
  const status = source.status ?? event.channel?.status ?? previous.status
  const track = event.current_track !== undefined
    ? event.current_track
    : event.track !== undefined
      ? event.track
      : event.channel?.current_track !== undefined
        ? event.channel.current_track
        : source.current_track
  const trackChanged = track !== undefined
    && String(track?.id ?? '') !== String(previous.current_track?.id ?? '')
  const reportedPosition = source.position_seconds
    ?? source.elapsed_seconds
    ?? source.server_position_seconds
    ?? source.offset_seconds
  const position = reportedPosition ?? (trackChanged ? 0 : previous.position_seconds)
  const duration = source.duration_seconds
    ?? (trackChanged ? track?.duration_seconds : previous.duration_seconds)

  return {
    playback: {
      ...previous,
      ...source,
      status,
      current_track: track !== undefined ? track : previous.current_track,
      position_seconds: position,
      elapsed_seconds: position ?? previous.elapsed_seconds,
      duration_seconds: duration,
    },
    track,
  }
}

export function interpolatedPosition(snapshot: TimedPlayback, now = Date.now()): number {
  const state = snapshot.state
  const anchor = state.position_seconds ?? state.elapsed_seconds ?? 0
  const advances = state.status === 'live'
  const elapsed = advances ? Math.max(0, (now - snapshot.receivedAt) / 1000) : 0
  const duration = state.duration_seconds ?? state.current_track?.duration_seconds
  return duration && duration > 0 ? clamp(anchor + elapsed, 0, duration) : Math.max(0, anchor + elapsed)
}

export function playbackPercent(position: number, duration?: number | null): number {
  if (!duration || duration <= 0) return 0
  return clamp((position / duration) * 100, 0, 100)
}
