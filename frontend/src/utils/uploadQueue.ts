import type { UploadJob, UploadJobStatus } from '../api/types'

const FAILURE_STATUSES: ReadonlySet<UploadJobStatus> = new Set([
  'failed',
  'rejected',
  'expired',
])

export function isUploadFailureStatus(status: UploadJobStatus): boolean {
  return FAILURE_STATUSES.has(status)
}

export function unhandledUploadFailures(
  jobs: UploadJob[],
  handledJobIds: ReadonlySet<string>,
): UploadJob[] {
  return jobs.filter(
    (job) => isUploadFailureStatus(job.status) && !handledJobIds.has(job.id),
  )
}

export function isAdditionalUploadTargetLocked(localSubmittedCount: number): boolean {
  return localSubmittedCount > 0
}

export function isUploadRetryBlocked(
  localSubmittedCount: number,
  pendingReviewCount: number,
  busy: boolean,
): boolean {
  return localSubmittedCount > 0 || pendingReviewCount > 0 || busy
}

export function isRetryableUploadFailure(
  job: UploadJob,
  ownedByCurrentClient: boolean,
  hasLocalFile: boolean,
): boolean {
  return (
    ownedByCurrentClient
    && hasLocalFile
    && Boolean(job.target_library)
    && !job.duplicate
    && job.error_code !== 'duplicate_content'
    && (job.status === 'failed' || job.status === 'expired')
  )
}
