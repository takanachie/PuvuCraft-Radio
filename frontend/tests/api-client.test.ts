import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getCookie, request, unwrapEntity, unwrapList } from '../src/api/client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('API client', () => {
  it('reads and decodes an exact cookie name', () => {
    const source = 'radio_csrf_extra=wrong; radio_csrf=token%2Fwith%20space; theme=dark'
    expect(getCookie('radio_csrf', source)).toBe('token/with space')
    expect(getCookie('missing', source)).toBeNull()
  })

  it('sends credentials and the readable CSRF cookie on mutations', async () => {
    vi.stubGlobal('document', { cookie: 'radio_csrf=csrf-123' })
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await request('/api/admin/test', { method: 'PATCH', json: { enabled: true } })

    expect(fetchMock).toHaveBeenCalledOnce()
    const [path, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(options.headers)
    expect(path).toBe('/api/admin/test')
    expect(options.credentials).toBe('include')
    expect(headers.get('X-CSRF-Token')).toBe('csrf-123')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(options.body).toBe('{"enabled":true}')
  })

  it('does not add a CSRF header to read-only requests', async () => {
    vi.stubGlobal('document', { cookie: 'radio_csrf=csrf-123' })
    const fetchMock = vi.fn().mockResolvedValue(new Response('[]', {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await request('/api/channels')

    const options = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(options.headers).has('X-CSRF-Token')).toBe(false)
    expect(options.credentials).toBe('include')
  })

  it('normalizes machine-readable API errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'slug_conflict',
      message: 'Slug already exists',
    }), {
      status: 409,
      statusText: 'Conflict',
      headers: { 'content-type': 'application/json' },
    })))

    const error = await request('/api/admin/channels', { method: 'POST', json: {} })
      .catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 409,
      code: 'slug_conflict',
      message: 'Slug already exists',
    })
  })

  it('accepts direct and wrapped entity/list response shapes', () => {
    expect(unwrapEntity<{ id: number }>({ data: { user: { id: 7 } } }, ['user'])).toEqual({ id: 7 })
    expect(unwrapList<number>({ data: { tracks: [1, 2, 3] } }, ['tracks'])).toEqual([1, 2, 3])
    expect(unwrapList<number>({ playlist: { items: [6, 7] } }, ['playlist'])).toEqual([6, 7])
    expect(unwrapList<number>([4, 5], ['items'])).toEqual([4, 5])
  })
})
