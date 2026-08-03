import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('external player API client', () => {
  it('reads only the hidden credential state', async () => {
    const state = {
      configured: true,
      created_at: '2026-07-29T00:00:00Z',
      connect_before: '2026-08-28T00:00:00Z',
      valid_for_new_connections: true,
      lossless_available: false,
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(state), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.auth.playerKey()).resolves.toEqual(state)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/auth/player-key')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'GET' })
  })

  it('requests a player URL only when the copy action is invoked', async () => {
    vi.stubGlobal('document', { cookie: 'radio_csrf=copy-token' })
    const response = {
      url: 'https://radio.example.com/listen/aac/opaque/default',
      stream_format: 'aac',
      channel_id: 1,
      created_at: '2026-07-29T00:00:00Z',
      connect_before: '2026-08-28T00:00:00Z',
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(response), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.auth.playerUrl({
      channel_id: 1,
      stream_format: 'aac',
    })).resolves.toEqual(response)

    const [path, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/auth/player-key/url')
    expect(options.method).toBe('POST')
    expect(options.body).toBe('{"channel_id":1,"stream_format":"aac"}')
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('copy-token')
  })
})

describe('admin track API client', () => {
  it('requests all matching IDs for cross-page selection', async () => {
    const response = {
      items: [],
      matching_ids: [31, 30, 29],
      page: 2,
      page_size: 10,
      total: 3,
      total_pages: 1,
      library_group: 'archive',
      library_groups: ['default', 'archive'],
      available_count: 3,
      unavailable_count: 0,
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(response), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.admin.tracks({
      page: 2,
      libraryGroup: 'archive',
      search: 'needle',
      availableOnly: true,
      excludeChannelId: 7,
      includeMatchingIds: true,
    })).resolves.toEqual(response)

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/admin/tracks?page=2&library_group=archive&search=needle&available_only=true&exclude_channel_id=7&include_matching_ids=true',
    )
  })
})
