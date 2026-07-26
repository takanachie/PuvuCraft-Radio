export interface ApiErrorBody {
  code?: string
  message?: string
  detail?: unknown
  details?: unknown
  errors?: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly body: unknown

  constructor(status: number, code: string, message: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.body = body
  }
}

export interface ApiRequestOptions extends Omit<RequestInit, 'body'> {
  body?: BodyInit | null
  json?: unknown
}

export function getCookie(name: string, source?: string): string | null {
  const cookies = source ?? (typeof document === 'undefined' ? '' : document.cookie)
  const prefix = `${encodeURIComponent(name)}=`

  for (const part of cookies.split(';')) {
    const cookie = part.trim()
    if (cookie.startsWith(prefix)) {
      try {
        return decodeURIComponent(cookie.slice(prefix.length))
      } catch {
        return cookie.slice(prefix.length)
      }
    }
  }

  return null
}

function errorMessage(status: number, payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload.trim()) return payload
  if (!payload || typeof payload !== 'object') return fallback

  const body = payload as ApiErrorBody
  if (typeof body.message === 'string' && body.message.trim()) return body.message
  if (typeof body.detail === 'string' && body.detail.trim()) return body.detail

  if (body.detail && typeof body.detail === 'object' && !Array.isArray(body.detail)) {
    const detail = body.detail as Record<string, unknown>
    if (typeof detail.message === 'string' && detail.message.trim()) return detail.message
  }

  if (Array.isArray(body.detail)) {
    const messages = body.detail
      .map((item) => {
        if (!item || typeof item !== 'object') return ''
        const record = item as Record<string, unknown>
        return typeof record.msg === 'string' ? record.msg : ''
      })
      .filter(Boolean)
    if (messages.length) return messages.join('；')
  }

  return fallback || `请求失败 (${status})`
}

function errorCode(payload: unknown, status: number): string {
  if (payload && typeof payload === 'object') {
    const body = payload as ApiErrorBody
    if (typeof body.code === 'string') return body.code
    if (body.detail && typeof body.detail === 'object') {
      const detail = body.detail as Record<string, unknown>
      if (typeof detail.code === 'string') return detail.code
    }
  }
  return `http_${status}`
}

async function readResponse(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined
  const text = await response.text()
  if (!text) return undefined

  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('json')) {
    try {
      return JSON.parse(text) as unknown
    } catch {
      return text
    }
  }

  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

export async function request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const { json, ...fetchOptions } = options
  const method = (options.method || 'GET').toUpperCase()
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')

  let body = options.body
  if (json !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(json)
  }

  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrfToken = getCookie('radio_csrf')
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
  }

  let response: Response
  try {
    response = await fetch(path, {
      ...fetchOptions,
      method,
      headers,
      body,
      credentials: 'include',
    })
  } catch (error) {
    throw new ApiError(0, 'network_error', '无法连接到电台服务器', error)
  }

  const payload = await readResponse(response)
  if (!response.ok) {
    const fallback = response.statusText || `请求失败 (${response.status})`
    const apiError = new ApiError(
      response.status,
      errorCode(payload, response.status),
      errorMessage(response.status, payload, fallback),
      payload,
    )
    if (response.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('radio:unauthorized'))
    }
    throw apiError
  }

  return payload as T
}

export function unwrapEntity<T>(payload: unknown, keys: string[] = []): T {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return payload as T
  const record = payload as Record<string, unknown>
  if ('data' in record && record.data !== undefined) return unwrapEntity<T>(record.data, keys)
  for (const key of keys) {
    if (record[key] !== undefined) return record[key] as T
  }
  return payload as T
}

export function unwrapList<T>(payload: unknown, keys: string[] = []): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (!payload || typeof payload !== 'object') return []
  const record = payload as Record<string, unknown>
  if ('data' in record) return unwrapList<T>(record.data, keys)
  for (const key of [...keys, 'items', 'results']) {
    if (Array.isArray(record[key])) return record[key] as T[]
    if (record[key] && typeof record[key] === 'object') return unwrapList<T>(record[key], [])
  }
  return []
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

export function userFacingError(error: unknown, fallback = '操作未完成，请稍后重试'): string {
  if (error instanceof ApiError) {
    if (error.code === 'network_error') return '无法连接服务器，请检查网络后重试'
    if (error.code === 'admin_required') return '当前账号没有执行此操作的权限'
    if (error.code === 'csrf_failed') return '请求安全令牌已失效，请刷新页面后重试'
    if (error.code === 'account_not_approved') return '账号尚未获准登录'
    if (error.status === 429) return '操作过于频繁，请稍后重试'
    return error.message || fallback
  }
  return error instanceof Error && error.message ? error.message : fallback
}
