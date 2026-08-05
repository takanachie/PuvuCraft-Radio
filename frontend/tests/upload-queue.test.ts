import { describe, expect, it } from 'vitest'
import type { UploadJob, UploadJobStatus } from '../src/api/types'
import {
  isAdditionalUploadTargetLocked,
  isRetryableUploadFailure,
  isUploadRetryBlocked,
  unhandledUploadFailures,
} from '../src/utils/uploadQueue'

function uploadJob(
  id: string,
  status: UploadJobStatus,
  overrides: Partial<UploadJob> = {},
): UploadJob {
  return {
    id,
    owner: { id: 1, username: 'admin' },
    client_id: 'client-a',
    original_filename: `${id}.flac`,
    target_library: 'default',
    declared_size_bytes: 1024,
    bytes_received: 1024,
    status,
    queue_position: null,
    storage_id: null,
    storage_name: null,
    sha256: null,
    track_id: null,
    duplicate: false,
    error_code: null,
    error_message: null,
    ready_at: null,
    lease_expires_at: null,
    started_at: null,
    completed_at: null,
    created_at: '2026-08-04T00:00:00Z',
    updated_at: '2026-08-04T00:01:00Z',
    ...overrides,
  }
}

describe('upload failure queue', () => {
  it('keeps unhandled failures, rejections, and expirations visible', () => {
    const jobs = [
      uploadJob('failed', 'failed'),
      uploadJob('rejected', 'rejected'),
      uploadJob('expired', 'expired'),
      uploadJob('completed', 'completed'),
      uploadJob('cancelled', 'cancelled'),
      uploadJob('active', 'uploading'),
    ]

    expect(
      unhandledUploadFailures(jobs, new Set(['rejected'])).map((job) => job.id),
    ).toEqual(['failed', 'expired'])
  })

  it('locks additional target selection while the local submitted queue has work', () => {
    expect(isAdditionalUploadTargetLocked(0)).toBe(false)
    expect(isAdditionalUploadTargetLocked(1)).toBe(true)
    expect(isAdditionalUploadTargetLocked(100)).toBe(true)
  })

  it('blocks both single and batch retry until the local queues are idle', () => {
    expect(isUploadRetryBlocked(0, 0, false)).toBe(false)
    expect(isUploadRetryBlocked(1, 0, false)).toBe(true)
    expect(isUploadRetryBlocked(0, 1, false)).toBe(true)
    expect(isUploadRetryBlocked(0, 0, true)).toBe(true)
  })

  it('only retries owned failures that still have a local file and target library', () => {
    const failed = uploadJob('failed', 'failed')
    expect(isRetryableUploadFailure(failed, true, true)).toBe(true)
    expect(isRetryableUploadFailure(failed, false, true)).toBe(false)
    expect(isRetryableUploadFailure(failed, true, false)).toBe(false)
    expect(isRetryableUploadFailure(uploadJob('expired', 'expired'), true, true)).toBe(true)
    expect(isRetryableUploadFailure(uploadJob('rejected', 'rejected'), true, true)).toBe(false)
    expect(isRetryableUploadFailure(
      uploadJob('duplicate-flag', 'failed', { duplicate: true }),
      true,
      true,
    )).toBe(false)
    expect(isRetryableUploadFailure(
      uploadJob('duplicate-code', 'failed', { error_code: 'duplicate_content' }),
      true,
      true,
    )).toBe(false)
    expect(isRetryableUploadFailure(
      uploadJob('missing-target', 'failed', { target_library: null }),
      true,
      true,
    )).toBe(false)
  })
})
