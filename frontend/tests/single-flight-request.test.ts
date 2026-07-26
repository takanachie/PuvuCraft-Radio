import { afterEach, describe, expect, it, vi } from 'vitest'
import { SingleFlightRequest } from '../src/utils/singleFlightRequest'

afterEach(() => {
  vi.useRealTimers()
})

describe('SingleFlightRequest', () => {
  it('prevents overlap, aborts a timed-out request, and permits a later retry', async () => {
    vi.useFakeTimers()
    const request = new SingleFlightRequest()
    const task = vi.fn((signal: AbortSignal) => new Promise<void>((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true })
    }))

    const firstResult = request.run(task, 1000).catch((error: unknown) => error)
    await expect(request.run(task, 1000)).resolves.toBe(false)
    expect(task).toHaveBeenCalledOnce()
    expect(request.active).toBe(true)

    await vi.advanceTimersByTimeAsync(1000)
    expect(await firstResult).toEqual(new Error('aborted'))
    expect(request.active).toBe(false)

    const retry = vi.fn().mockResolvedValue(undefined)
    await expect(request.run(retry, 1000)).resolves.toBe(true)
    expect(retry).toHaveBeenCalledOnce()
  })
})
