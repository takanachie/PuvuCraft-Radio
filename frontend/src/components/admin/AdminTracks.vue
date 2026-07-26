<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../../api'
import { getCookie, isApiError, userFacingError } from '../../api/client'
import type {
  EntityId,
  SimilarTrackCandidate,
  Track,
  TrackInput,
  UploadJob,
  UploadJobStatus,
  UploadQueueSnapshot,
} from '../../api/types'
import { formatDuration, formatFileSize } from '../../utils/format'
import InlineNotice from '../InlineNotice.vue'
import StatusBadge from '../StatusBadge.vue'

const MAX_UPLOAD_BYTES = 500 * 1024 * 1024
const MAX_LOCAL_UPLOADS = 1000
const AUDIO_EXTENSIONS = new Set(['.mp3', '.flac', '.m4a', '.aac', '.wav', '.ogg'])
const CLIENT_BOUND_UPLOADS = new Set<UploadJobStatus>(['queued', 'ready', 'uploading'])
const VISIBLE_UPLOADS = new Set<UploadJobStatus>([
  'queued',
  'ready',
  'uploading',
  'verifying',
  'normalizing',
  'placing',
])
const TERMINAL_UPLOADS = new Set<UploadJobStatus>([
  'completed',
  'failed',
  'rejected',
  'cancelled',
  'expired',
])
const UPLOAD_LABELS: Record<UploadJobStatus, string> = {
  queued: '排队中',
  ready: '等待传输',
  uploading: '上传中',
  verifying: '校验中',
  normalizing: '规范化',
  placing: '迁移中',
  completed: '已完成',
  failed: '失败',
  rejected: '已驳回',
  cancelled: '已取消',
  expired: '已过期',
}

interface SubmittedUploadTask {
  id: string
  file: File
  similarities: SimilarTrackCandidate[]
}

function createUploadClientId(): string {
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}

const uploadClientId = createUploadClientId()
const tracks = ref<Track[]>([])
const loading = ref(true)
const committing = ref(false)
const preflighting = ref(false)
const feedingSubmitted = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const search = ref('')
const libraryGroup = ref('default')
const libraryGroups = ref<string[]>(['default'])
const trackPage = ref(1)
const trackPageSize = ref(10)
const trackTotal = ref(0)
const trackTotalPages = ref(1)
const availableCount = ref(0)
const unavailableCount = ref(0)
const selectedTrackIds = ref<Set<string>>(new Set())
const moveTargetLibrary = ref('')
const movingTracks = ref(false)
const newLibraryName = ref('')
const renamedLibraryName = ref('default')
const librarySaving = ref(false)
const selectedFiles = ref<File[]>([])
const submittedUploads = ref<SubmittedUploadTask[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const directoryInput = ref<HTMLInputElement | null>(null)
const uploadReviewOpen = ref(false)
const uploadReviewQuery = ref('')
const uploadReviewError = ref('')
const uploadReviewNotice = ref('')
const submittedQueueError = ref('')
const uploadReviewSearchInput = ref<HTMLInputElement | null>(null)
const similaritiesByFile = ref<Record<string, SimilarTrackCandidate[]>>({})
const confirmedSimilarFiles = ref<Set<string>>(new Set())
const editingId = ref<EntityId | null>(null)
const coverFile = ref<File | null>(null)
const editForm = reactive<TrackInput>({ title: '', artist: '', album: '', cover_url: '' })
const uploadQueue = ref<UploadQueueSnapshot>({
  jobs: [],
  queue_limit: 20,
  max_concurrent: 5,
  active_count: 0,
  available_slots: 0,
  heartbeat_interval_seconds: 5,
})
const queueLoading = ref(true)
const localUploadBytes = reactive<Record<string, number>>({})
const filesByJob = new Map<string, File>()
const requestsByJob = new Map<string, XMLHttpRequest>()
const handledTerminalJobs = new Set<string>()
let eventSource: EventSource | null = null
let heartbeatTimer: number | undefined
let heartbeatSeconds = 0
let trackRequest = 0
let appliedTrackRequest = 0
let visibleTrackRequest = 0
let libraryRefreshPending = false
let libraryRefreshRunning = false
let preflightRequest = 0
let submittedRetryTimer: number | undefined
let submittedRetryAttempt = 0
let trackSearchTimer: number | undefined
let leaving = false

const editingTrack = computed(() => tracks.value.find((track) => String(track.id) === String(editingId.value)) || null)
const allPageTracksSelected = computed(() =>
  tracks.value.length > 0
  && tracks.value.every((track) => selectedTrackIds.value.has(String(track.id))),
)
const targetLibraryGroups = computed(() =>
  libraryGroups.value.filter((group) => group !== libraryGroup.value),
)
const currentLibraryTrackCount = computed(() =>
  availableCount.value + unavailableCount.value,
)
const visibleUploadJobs = computed(() =>
  uploadQueue.value.jobs.filter((job) => VISIBLE_UPLOADS.has(job.status)),
)
const pendingUploadBytes = computed(() =>
  selectedFiles.value.reduce((total, file) => total + file.size, 0),
)
const submittedUploadBytes = computed(() =>
  submittedUploads.value.reduce((total, task) => total + task.file.size, 0),
)
const invalidPendingCount = computed(() =>
  selectedFiles.value.filter((file) => Boolean(pendingFileIssue(file))).length,
)
const filteredPendingFiles = computed(() => {
  const needle = uploadReviewQuery.value.trim().toLocaleLowerCase()
  if (!needle) return selectedFiles.value
  return selectedFiles.value.filter((file) => {
    const candidates = pendingFileSimilarities(file)
    return [
      pendingFilePath(file),
      file.name,
      ...candidates.flatMap((candidate) => [
        candidate.title,
        candidate.artist,
        candidate.album,
        candidate.original_filename,
      ]),
    ].some((value) => value?.toLocaleLowerCase().includes(needle))
  })
})
const similarPendingCount = computed(() =>
  selectedFiles.value.filter((file) => pendingFileSimilarities(file).length > 0).length,
)
const unconfirmedSimilarCount = computed(() =>
  selectedFiles.value.filter((file) => {
    const key = pendingFileKey(file)
    return pendingFileSimilarities(file).length > 0 && !confirmedSimilarFiles.value.has(key)
  }).length,
)

function clearMessages() {
  error.value = ''
  notice.value = ''
}

async function load(preserveSelection = true, background = false, page = trackPage.value) {
  const requestId = ++trackRequest
  const requestedLibrary = libraryGroup.value
  const requestedSearch = search.value.trim()
  if (!background) {
    visibleTrackRequest = requestId
    loading.value = true
    error.value = ''
  }
  try {
    const result = await api.admin.tracks({
      page,
      libraryGroup: requestedLibrary,
      search: requestedSearch,
    })
    if (
      requestId < appliedTrackRequest
      || libraryGroup.value !== requestedLibrary
      || search.value.trim() !== requestedSearch
    ) return
    appliedTrackRequest = requestId
    tracks.value = result.items
    trackPage.value = result.page
    trackPageSize.value = result.page_size
    trackTotal.value = result.total
    trackTotalPages.value = result.total_pages
    libraryGroups.value = result.library_groups
    availableCount.value = result.available_count
    unavailableCount.value = result.unavailable_count
    if (preserveSelection && editingId.value !== null) {
      const updated = tracks.value.find((track) => String(track.id) === String(editingId.value))
      if (updated) beginEdit(updated)
      else editingId.value = null
    }
  } catch (cause) {
    if (
      !background
      && requestId >= appliedTrackRequest
      && libraryGroup.value === requestedLibrary
      && search.value.trim() === requestedSearch
    ) {
      error.value = userFacingError(cause, '无法读取音乐库')
    }
  } finally {
    if (!background && requestId === visibleTrackRequest) {
      loading.value = false
      void runPendingLibraryRefresh()
    }
  }
}

function changeLibraryGroup() {
  cancelTrackSearch()
  cancelEdit()
  selectedTrackIds.value = new Set()
  moveTargetLibrary.value = ''
  trackPage.value = 1
  void load(false, false, 1)
}

function cancelTrackSearch() {
  if (trackSearchTimer === undefined) return
  window.clearTimeout(trackSearchTimer)
  trackSearchTimer = undefined
}

function normalizeLibraryGroups(groups: string[]): string[] {
  const unique = [...new Set(groups)]
  unique.sort((left, right) => {
    if (left === 'default') return -1
    if (right === 'default') return 1
    return left.localeCompare(right)
  })
  return unique
}

async function createLibrary() {
  const name = newLibraryName.value.trim()
  if (!name || librarySaving.value) return
  clearMessages()
  librarySaving.value = true
  try {
    const created = await api.admin.createTrackLibrary(name)
    const resolvedName = created?.name || name
    libraryGroups.value = normalizeLibraryGroups([...libraryGroups.value, resolvedName])
    libraryGroup.value = resolvedName
    renamedLibraryName.value = resolvedName
    newLibraryName.value = ''
    selectedTrackIds.value = new Set()
    moveTargetLibrary.value = ''
    cancelEdit()
    trackPage.value = 1
    await load(false, false, 1)
    notice.value = `音乐库“${resolvedName}”已创建。`
  } catch (cause) {
    error.value = userFacingError(cause, '无法创建音乐库')
  } finally {
    librarySaving.value = false
  }
}

async function renameLibrary() {
  const currentName = libraryGroup.value
  const name = renamedLibraryName.value.trim()
  if (
    currentName === 'default'
    || !name
    || name === currentName
    || librarySaving.value
    || loading.value
  ) return
  clearMessages()
  librarySaving.value = true
  try {
    const renamed = await api.admin.renameTrackLibrary(currentName, name)
    const resolvedName = renamed?.name || name
    libraryGroups.value = normalizeLibraryGroups(
      libraryGroups.value.map((group) => group === currentName ? resolvedName : group),
    )
    libraryGroup.value = resolvedName
    renamedLibraryName.value = resolvedName
    if (moveTargetLibrary.value === currentName) moveTargetLibrary.value = resolvedName
    selectedTrackIds.value = new Set()
    cancelEdit()
    trackPage.value = 1
    await load(false, false, 1)
    notice.value = `音乐库“${currentName}”已重命名为“${resolvedName}”。`
  } catch (cause) {
    error.value = userFacingError(cause, '无法重命名音乐库')
  } finally {
    librarySaving.value = false
  }
}

async function deleteLibrary() {
  const name = libraryGroup.value
  if (
    name === 'default'
    || currentLibraryTrackCount.value > 0
    || librarySaving.value
    || loading.value
  ) return
  if (!window.confirm(`删除空音乐库“${name}”？`)) return
  clearMessages()
  librarySaving.value = true
  try {
    await api.admin.deleteTrackLibrary(name)
    libraryGroups.value = normalizeLibraryGroups(
      libraryGroups.value.filter((group) => group !== name),
    )
    libraryGroup.value = 'default'
    renamedLibraryName.value = 'default'
    selectedTrackIds.value = new Set()
    moveTargetLibrary.value = ''
    cancelEdit()
    trackPage.value = 1
    await load(false, false, 1)
    notice.value = `空音乐库“${name}”已删除。`
  } catch (cause) {
    error.value = userFacingError(cause, '无法删除音乐库')
  } finally {
    librarySaving.value = false
  }
}

function goToTrackPage(page: number) {
  const target = Math.min(Math.max(1, page), trackTotalPages.value)
  if (target === trackPage.value || loading.value) return
  cancelEdit()
  void load(false, false, target)
}

function toggleTrackSelection(track: Track, event: Event) {
  const selected = new Set(selectedTrackIds.value)
  const trackId = String(track.id)
  if ((event.target as HTMLInputElement).checked) selected.add(trackId)
  else selected.delete(trackId)
  selectedTrackIds.value = selected
}

function togglePageTracks(event: Event) {
  const selected = new Set(selectedTrackIds.value)
  const checked = (event.target as HTMLInputElement).checked
  for (const track of tracks.value) {
    if (checked) selected.add(String(track.id))
    else selected.delete(String(track.id))
  }
  selectedTrackIds.value = selected
}

function clearSelectedTracks() {
  selectedTrackIds.value = new Set()
}

async function moveSelectedTracks() {
  const target = moveTargetLibrary.value.trim()
  if (!selectedTrackIds.value.size || movingTracks.value) return
  if (!target) {
    error.value = '请选择目标音乐库。'
    return
  }
  if (target === libraryGroup.value) {
    error.value = '目标音乐库不能与当前音乐库相同。'
    return
  }
  if (!window.confirm(
    `将所选 ${selectedTrackIds.value.size} 首曲目从“${libraryGroup.value}”迁入“${target}”？`,
  )) return

  clearMessages()
  movingTracks.value = true
  const movedCount = selectedTrackIds.value.size
  try {
    const result = await api.admin.moveTracksToLibrary(
      libraryGroup.value,
      target,
      [...selectedTrackIds.value].map(Number),
    )
    libraryGroups.value = result.library_groups
    selectedTrackIds.value = new Set()
    moveTargetLibrary.value = ''
    cancelEdit()
    await load(false)
    notice.value = `已将 ${movedCount} 首曲目迁入“${target}”。`
  } catch (cause) {
    error.value = userFacingError(cause, '无法迁移所选曲目')
  } finally {
    movingTracks.value = false
  }
}

function scheduleLibraryRefresh() {
  libraryRefreshPending = true
  void runPendingLibraryRefresh()
}

async function runPendingLibraryRefresh() {
  if (libraryRefreshRunning || loading.value || leaving || !libraryRefreshPending) return
  libraryRefreshPending = false
  libraryRefreshRunning = true
  try {
    await load(false, true)
  } finally {
    libraryRefreshRunning = false
    if (libraryRefreshPending) void runPendingLibraryRefresh()
  }
}

function pendingFilePath(file: File): string {
  return file.webkitRelativePath || file.name
}

function pendingFileKey(file: File): string {
  return `${pendingFilePath(file)}\u0000${file.size}\u0000${file.lastModified}`
}

function pendingFileIssue(file: File): string {
  if (file.size <= 0) return '空文件，无法上传'
  if (file.size > MAX_UPLOAD_BYTES) return '超过 500 MiB 上传上限'
  return ''
}

function pendingFileSimilarities(file: File): SimilarTrackCandidate[] {
  return similaritiesByFile.value[pendingFileKey(file)] || []
}

function candidateSignature(candidates: SimilarTrackCandidate[]): string {
  return candidates
    .map((candidate) => [
      candidate.id,
      candidate.similarity,
      candidate.title,
      candidate.artist,
      candidate.album,
      candidate.original_filename,
    ].join(':'))
    .join('|')
}

function isSupportedAudioFile(file: File): boolean {
  const dot = file.name.lastIndexOf('.')
  if (dot < 0) return false
  return AUDIO_EXTENSIONS.has(file.name.slice(dot).toLocaleLowerCase())
}

function stageFiles(files: File[], source: 'files' | 'directory') {
  if (!files.length) return
  clearMessages()
  uploadReviewError.value = ''
  const existingKeys = new Set([
    ...selectedFiles.value.map(pendingFileKey),
    ...submittedUploads.value.map((task) => pendingFileKey(task.file)),
    ...[...filesByJob.values()].map(pendingFileKey),
  ])
  const additions: File[] = []
  let duplicateCount = 0
  let unsupportedCount = 0
  let overflowCount = 0

  for (const file of files) {
    if (!isSupportedAudioFile(file)) {
      unsupportedCount += 1
      continue
    }
    const key = pendingFileKey(file)
    if (existingKeys.has(key)) {
      duplicateCount += 1
      continue
    }
    if (
      selectedFiles.value.length
      + submittedUploads.value.length
      + additions.length
      >= MAX_LOCAL_UPLOADS
    ) {
      overflowCount += 1
      continue
    }
    existingKeys.add(key)
    additions.push(file)
  }

  if (additions.length) selectedFiles.value = [...selectedFiles.value, ...additions]
  const messages: string[] = []
  if (additions.length) {
    messages.push(
      source === 'directory'
        ? `已从本地目录加入 ${additions.length} 个音频文件。`
        : `已加入 ${additions.length} 个音频文件。`,
    )
  }
  if (unsupportedCount) messages.push(`已忽略 ${unsupportedCount} 个不支持的文件。`)
  if (duplicateCount) messages.push(`已忽略 ${duplicateCount} 个重复选择。`)
  if (overflowCount) {
    messages.push(`本地待确认与已提交队列合计最多保留 ${MAX_LOCAL_UPLOADS} 个文件。`)
  }
  uploadReviewNotice.value = messages.join(' ')

  if (selectedFiles.value.length) {
    openUploadReview()
  } else {
    error.value = source === 'directory'
      ? '所选目录中没有支持的音频文件。'
      : '没有加入可上传的音频文件。'
  }
}

function chooseFiles(event: Event) {
  const input = event.target as HTMLInputElement
  if (committing.value) {
    input.value = ''
    return
  }
  stageFiles(Array.from(input.files || []), 'files')
  input.value = ''
}

function chooseDirectory(event: Event) {
  const input = event.target as HTMLInputElement
  if (committing.value) {
    input.value = ''
    return
  }
  stageFiles(Array.from(input.files || []), 'directory')
  input.value = ''
}

function openUploadReview() {
  if (!selectedFiles.value.length) return
  if (!uploadReviewOpen.value) uploadReviewQuery.value = ''
  uploadReviewOpen.value = true
  void nextTick(() => uploadReviewSearchInput.value?.focus())
  void preflightPendingFiles()
}

function closeUploadReview() {
  if (!committing.value) uploadReviewOpen.value = false
}

function clearPendingFiles() {
  if (committing.value) return
  preflightRequest += 1
  preflighting.value = false
  selectedFiles.value = []
  similaritiesByFile.value = {}
  confirmedSimilarFiles.value = new Set()
  uploadReviewError.value = ''
  uploadReviewNotice.value = ''
  uploadReviewQuery.value = ''
  if (fileInput.value) fileInput.value.value = ''
  if (directoryInput.value) directoryInput.value.value = ''
  uploadReviewOpen.value = false
}

function removePendingFile(file: File) {
  if (committing.value) return
  const key = pendingFileKey(file)
  selectedFiles.value = selectedFiles.value.filter((candidate) => candidate !== file)
  const nextSimilarities = { ...similaritiesByFile.value }
  delete nextSimilarities[key]
  similaritiesByFile.value = nextSimilarities
  const confirmed = new Set(confirmedSimilarFiles.value)
  confirmed.delete(key)
  confirmedSimilarFiles.value = confirmed
  uploadReviewError.value = ''
  if (!selectedFiles.value.length) clearPendingFiles()
}

function setSimilarityConfirmation(file: File, confirmed: boolean) {
  const key = pendingFileKey(file)
  const next = new Set(confirmedSimilarFiles.value)
  if (confirmed) next.add(key)
  else next.delete(key)
  confirmedSimilarFiles.value = next
  uploadReviewError.value = ''
}

function changeSimilarityConfirmation(file: File, event: Event) {
  setSimilarityConfirmation(file, (event.target as HTMLInputElement).checked)
}

function updateFileSimilarities(file: File, candidates: SimilarTrackCandidate[]) {
  const key = pendingFileKey(file)
  similaritiesByFile.value = {
    ...similaritiesByFile.value,
    [key]: candidates,
  }
  const confirmed = new Set(confirmedSimilarFiles.value)
  confirmed.delete(key)
  confirmedSimilarFiles.value = confirmed
}

async function preflightPendingFiles(): Promise<boolean> {
  const files = [...selectedFiles.value]
  if (!files.length) return true
  const requestId = ++preflightRequest
  preflighting.value = true
  uploadReviewError.value = ''
  try {
    const filenames = [...new Set(files.map((file) => file.name))]
    const result = await api.admin.preflightUploads(filenames)
    if (requestId !== preflightRequest) return false
    if (
      files.length !== selectedFiles.value.length
      || files.some(
        (file, index) =>
          pendingFileKey(file) !== pendingFileKey(selectedFiles.value[index]),
      )
    ) {
      uploadReviewError.value = '待上传清单在检查期间发生变化，请重新确认后提交。'
      return false
    }
    const byFilename = new Map(
      result.files.map((checked) => [checked.filename, checked.candidates] as const),
    )
    const nextSimilarities: Record<string, SimilarTrackCandidate[]> = {}
    const nextConfirmed = new Set<string>()
    for (const file of selectedFiles.value) {
      const key = pendingFileKey(file)
      const candidates = byFilename.get(file.name) || []
      nextSimilarities[key] = candidates
      if (
        candidates.length
        && confirmedSimilarFiles.value.has(key)
        && candidateSignature(candidates) === candidateSignature(similaritiesByFile.value[key] || [])
      ) {
        nextConfirmed.add(key)
      }
    }
    similaritiesByFile.value = nextSimilarities
    confirmedSimilarFiles.value = nextConfirmed
    return true
  } catch (cause) {
    if (requestId === preflightRequest) {
      uploadReviewError.value = userFacingError(cause, '无法检查待上传文件的相似曲目')
    }
    return false
  } finally {
    if (requestId === preflightRequest) preflighting.value = false
  }
}

function isOwnedJob(job: UploadJob): boolean {
  return job.client_id === uploadClientId
}

function uploadStatus(job: UploadJob): string {
  if (job.status === 'queued' && job.queue_position) {
    return `${UPLOAD_LABELS[job.status]} · 第 ${job.queue_position} 位`
  }
  return UPLOAD_LABELS[job.status]
}

function uploadBytes(job: UploadJob): number {
  return localUploadBytes[job.id] ?? job.bytes_received
}

function uploadPercent(job: UploadJob): number {
  if (job.status === 'completed' || ['verifying', 'normalizing', 'placing'].includes(job.status)) {
    return 100
  }
  if (!job.declared_size_bytes) return 0
  return Math.min(100, Math.round(uploadBytes(job) / job.declared_size_bytes * 100))
}

function trackAudioDetail(track: Track): string {
  const parts: string[] = []
  if (track.sample_rate) parts.push(`${(track.sample_rate / 1000).toFixed(track.sample_rate % 1000 ? 1 : 0)} kHz`)
  if (track.channels) parts.push(`${track.channels} ch`)
  if (track.bits_per_sample) parts.push(`${track.bits_per_sample} bit`)
  if (track.normalized) parts.push('FLAC 规范化')
  return parts.join(' · ')
}

function xhrError(xhr: XMLHttpRequest): string {
  try {
    const payload = JSON.parse(xhr.responseText) as { message?: string; detail?: string | { message?: string } }
    if (payload.message) return payload.message
    if (typeof payload.detail === 'string') return payload.detail
    if (payload.detail?.message) return payload.detail.message
  } catch {
    // A proxy or network failure may return a non-JSON response.
  }
  return xhr.status ? `上传请求失败 (${xhr.status})` : '上传连接已中断'
}

function similarTrackCandidates(cause: unknown): SimilarTrackCandidate[] {
  if (!isApiError(cause) || cause.code !== 'similar_tracks_found') return []
  if (!cause.body || typeof cause.body !== 'object') return []
  const details = (cause.body as { details?: unknown }).details
  if (!details || typeof details !== 'object') return []
  const candidates = (details as { candidates?: unknown }).candidates
  return Array.isArray(candidates) ? candidates as SimilarTrackCandidate[] : []
}

function startTransfer(job: UploadJob) {
  const file = filesByJob.get(job.id)
  if (!file || requestsByJob.has(job.id) || job.status !== 'ready' || !isOwnedJob(job)) return
  if (file.name !== job.original_filename || file.size !== job.declared_size_bytes) {
    error.value = `${job.original_filename} 与预约文件不一致，任务将被取消。`
    void cancelUpload(job)
    return
  }

  const xhr = new XMLHttpRequest()
  requestsByJob.set(job.id, xhr)
  xhr.open('PUT', `/api/admin/uploads/${encodeURIComponent(job.id)}/content`)
  xhr.withCredentials = true
  xhr.setRequestHeader('Accept', 'application/json')
  xhr.setRequestHeader('Content-Type', 'application/octet-stream')
  xhr.setRequestHeader('X-Upload-Client-ID', uploadClientId)
  const csrfToken = getCookie('radio_csrf')
  if (csrfToken) xhr.setRequestHeader('X-CSRF-Token', csrfToken)
  xhr.upload.onprogress = (event) => {
    localUploadBytes[job.id] = event.loaded
  }
  xhr.onload = () => {
    requestsByJob.delete(job.id)
    if (xhr.status >= 200 && xhr.status < 300) {
      localUploadBytes[job.id] = file.size
      notice.value = `${file.name} 已传输完成，服务器正在校验并按需规范化。`
    } else if (!leaving) {
      error.value = `${file.name}：${xhrError(xhr)}`
    }
    void refreshUploadQueue()
  }
  xhr.onerror = () => {
    requestsByJob.delete(job.id)
    if (!leaving) error.value = `${file.name}：上传连接已中断。`
    void refreshUploadQueue()
  }
  xhr.onabort = () => {
    requestsByJob.delete(job.id)
    delete localUploadBytes[job.id]
  }
  xhr.send(file)
}

function applyUploadSnapshot(snapshot: UploadQueueSnapshot) {
  uploadQueue.value = snapshot
  queueLoading.value = false
  startHeartbeat(snapshot.heartbeat_interval_seconds)
  let refreshLibrary = false
  for (const job of snapshot.jobs) {
    if (job.status === 'ready') startTransfer(job)
    if (TERMINAL_UPLOADS.has(job.status) && !handledTerminalJobs.has(job.id)) {
      handledTerminalJobs.add(job.id)
      if (job.status === 'completed') refreshLibrary = true
      if (isOwnedJob(job)) {
        if (job.status === 'completed') {
          notice.value = `${job.original_filename} 已完成并写入 ${job.storage_id || '可用存储'}。`
        } else if (job.status === 'rejected') {
          error.value = `${job.original_filename}：${job.error_message || 'SHA-256 与已有曲目相同，已自动驳回'}`
        } else if (job.status === 'failed') {
          error.value = `${job.original_filename}：${job.error_message || '服务器处理失败'}`
        }
      }
      filesByJob.delete(job.id)
      delete localUploadBytes[job.id]
    }
  }
  if (refreshLibrary) scheduleLibraryRefresh()
  scheduleSubmittedUploads()
}

async function refreshUploadQueue() {
  try {
    applyUploadSnapshot(await api.admin.uploadQueue())
  } catch (cause) {
    if (!leaving) error.value = userFacingError(cause, '无法读取公共上传队列')
  } finally {
    queueLoading.value = false
  }
}

function parseQueueEvent(event: Event) {
  try {
    applyUploadSnapshot(JSON.parse((event as MessageEvent<string>).data) as UploadQueueSnapshot)
  } catch {
    if (!leaving) error.value = '公共上传队列返回了无效状态。'
  }
}

function connectUploadEvents() {
  eventSource = new EventSource('/api/admin/uploads/events', { withCredentials: true })
  eventSource.addEventListener('upload_queue', parseQueueEvent)
  eventSource.onerror = () => {
    if (!leaving) void refreshUploadQueue()
  }
}

function startHeartbeat(seconds: number) {
  const nextSeconds = Math.max(1, seconds)
  if (heartbeatTimer !== undefined && heartbeatSeconds === nextSeconds) return
  if (heartbeatTimer !== undefined) window.clearInterval(heartbeatTimer)
  heartbeatSeconds = nextSeconds
  heartbeatTimer = window.setInterval(() => {
    void api.admin.heartbeatUploads(uploadClientId).catch(() => undefined)
  }, nextSeconds * 1000)
}

function clearSubmittedRetry() {
  if (submittedRetryTimer !== undefined) window.clearTimeout(submittedRetryTimer)
  submittedRetryTimer = undefined
}

function scheduleSubmittedRetry() {
  if (leaving || submittedRetryTimer !== undefined || !submittedUploads.value.length) return
  const delay = Math.min(30_000, 1500 * 2 ** submittedRetryAttempt)
  submittedRetryAttempt += 1
  submittedRetryTimer = window.setTimeout(() => {
    submittedRetryTimer = undefined
    void retrySubmittedUploads()
  }, delay)
}

function scheduleSubmittedUploads() {
  if (
    leaving
    || feedingSubmitted.value
    || submittedRetryTimer !== undefined
    || queueLoading.value
    || uploadQueue.value.available_slots <= 0
    || !submittedUploads.value.length
  ) return
  void pushSubmittedUploads()
}

function returnSubmittedTaskForReview(
  task: SubmittedUploadTask,
  candidates: SimilarTrackCandidate[],
) {
  submittedUploads.value = submittedUploads.value.filter((item) => item.id !== task.id)
  if (
    !selectedFiles.value.some(
      (file) => pendingFileKey(file) === pendingFileKey(task.file),
    )
  ) {
    selectedFiles.value = [...selectedFiles.value, task.file]
  }
  updateFileSimilarities(task.file, candidates)
  uploadReviewNotice.value = ''
  uploadReviewError.value = `“${task.file.name}”的相似度结果在等待期间发生变化，请重新确认。`
  uploadReviewOpen.value = true
  void nextTick(() => uploadReviewSearchInput.value?.focus())
}

async function pushSubmittedUploads() {
  if (
    feedingSubmitted.value
    || leaving
    || queueLoading.value
    || uploadQueue.value.available_slots <= 0
    || !submittedUploads.value.length
  ) return

  feedingSubmitted.value = true
  clearSubmittedRetry()
  let availableSlots = uploadQueue.value.available_slots
  let pushed = 0
  let shouldContinue = false
  let pushFailed = false
  try {
    const batch = submittedUploads.value.slice(0, availableSlots)
    const checked = await api.admin.preflightUploads([
      ...new Set(batch.map((task) => task.file.name)),
    ])
    if (leaving) return
    const latestByFilename = new Map(
      checked.files.map((file) => [file.filename, file.candidates] as const),
    )

    for (const task of batch) {
      if (leaving || availableSlots <= 0) break
      if (!submittedUploads.value.some((item) => item.id === task.id)) continue
      const latestCandidates = latestByFilename.get(task.file.name) || []
      if (
        candidateSignature(latestCandidates)
        !== candidateSignature(task.similarities)
      ) {
        returnSubmittedTaskForReview(task, latestCandidates)
        shouldContinue = true
        continue
      }

      let job: UploadJob
      try {
        job = await api.admin.reserveUpload(
          uploadClientId,
          task.file,
          latestCandidates.length > 0,
        )
      } catch (cause) {
        const candidates = similarTrackCandidates(cause)
        if (candidates.length) {
          returnSubmittedTaskForReview(task, candidates)
          shouldContinue = true
          continue
        }
        if (isApiError(cause) && cause.code === 'upload_queue_full') {
          uploadQueue.value = { ...uploadQueue.value, available_slots: 0 }
          break
        }
        throw cause
      }

      if (leaving) {
        void api.admin.cancelUpload(job.id).catch(() => undefined)
        break
      }
      filesByJob.set(job.id, task.file)
      submittedUploads.value = submittedUploads.value.filter(
        (item) => item.id !== task.id,
      )
      availableSlots -= 1
      pushed += 1
    }
  } catch (cause) {
    if (!leaving) {
      pushFailed = true
      submittedQueueError.value = userFacingError(
        cause,
        '本地已提交任务暂时无法推送至公共队列',
      )
      scheduleSubmittedRetry()
    }
  } finally {
    feedingSubmitted.value = false
  }

  if (leaving) return
  if (pushed) {
    if (!pushFailed) {
      submittedRetryAttempt = 0
      submittedQueueError.value = ''
    }
    notice.value = `已将 ${pushed} 个本地任务推送至公共上传队列。`
    void api.admin.heartbeatUploads(uploadClientId).catch(() => undefined)
    await refreshUploadQueue()
  } else if (shouldContinue && !pushFailed) {
    scheduleSubmittedUploads()
  }
  if (!submittedUploads.value.length) {
    clearSubmittedRetry()
    submittedRetryAttempt = 0
    submittedQueueError.value = ''
  }
}

async function retrySubmittedUploads() {
  if (leaving || feedingSubmitted.value || !submittedUploads.value.length) return
  clearSubmittedRetry()
  submittedQueueError.value = ''
  await refreshUploadQueue()
  scheduleSubmittedUploads()
}

function removeSubmittedUpload(task: SubmittedUploadTask) {
  if (feedingSubmitted.value) return
  submittedUploads.value = submittedUploads.value.filter((item) => item.id !== task.id)
  if (!submittedUploads.value.length) {
    clearSubmittedRetry()
    submittedRetryAttempt = 0
    submittedQueueError.value = ''
  } else {
    scheduleSubmittedUploads()
  }
}

async function commitSelectedFiles() {
  if (!selectedFiles.value.length || committing.value || preflighting.value) return
  committing.value = true
  uploadReviewError.value = ''
  let committedCount = 0
  try {
    const preflightComplete = await preflightPendingFiles()
    if (!preflightComplete) return
    if (invalidPendingCount.value) {
      uploadReviewError.value = '请先移除空文件或超过 500 MiB 上限的文件。'
      return
    }
    if (unconfirmedSimilarCount.value) {
      uploadReviewError.value = `仍有 ${unconfirmedSimilarCount.value} 个相似文件未完成二次确认。`
      return
    }

    clearMessages()
    const files = [...selectedFiles.value]
    const tasks = files.map((file) => ({
      id: createUploadClientId(),
      file,
      similarities: [...pendingFileSimilarities(file)],
    }))
    submittedUploads.value = [...submittedUploads.value, ...tasks]
    committedCount = tasks.length
    selectedFiles.value = []
    similaritiesByFile.value = {}
    confirmedSimilarFiles.value = new Set()
    uploadReviewOpen.value = false
    uploadReviewQuery.value = ''
    uploadReviewError.value = ''
    uploadReviewNotice.value = ''
    if (fileInput.value) fileInput.value.value = ''
    if (directoryInput.value) directoryInput.value.value = ''
  } finally {
    committing.value = false
  }
  if (committedCount) {
    notice.value = `已将 ${committedCount} 个文件提交到本地已提交队列；公共队列出现空位后会自动推送。`
    scheduleSubmittedUploads()
  }
}

async function cancelUpload(job: UploadJob) {
  if (!isOwnedJob(job) || !CLIENT_BOUND_UPLOADS.has(job.status)) return
  requestsByJob.get(job.id)?.abort()
  try {
    await api.admin.cancelUpload(job.id)
  } catch (cause) {
    error.value = userFacingError(cause, '无法取消上传任务')
  } finally {
    await refreshUploadQueue()
  }
}

function expireOwnedUploads() {
  leaving = true
  for (const xhr of [...requestsByJob.values()]) xhr.abort()
  const csrfToken = getCookie('radio_csrf')
  const headers = new Headers({
    Accept: 'application/json',
    'Content-Type': 'application/json',
  })
  if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
  void fetch('/api/admin/uploads/expire', {
    method: 'POST',
    headers,
    body: JSON.stringify({ client_id: uploadClientId }),
    credentials: 'include',
    keepalive: true,
  }).catch(() => undefined)
}

function beginEdit(track: Track) {
  clearMessages()
  editingId.value = track.id
  coverFile.value = null
  Object.assign(editForm, {
    title: track.title,
    artist: track.artist || '',
    album: track.album || '',
    cover_url: track.cover_url || '',
  })
}

function cancelEdit() {
  editingId.value = null
  coverFile.value = null
}

function chooseCover(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null
  if (file && file.size > 10 * 1024 * 1024) {
    error.value = '封面文件超过 10 MiB 上限。'
    coverFile.value = null
    return
  }
  coverFile.value = file
  error.value = ''
}

async function saveTrack() {
  if (!editingTrack.value || saving.value) return
  if (!editForm.title.trim()) {
    error.value = '曲目标题不能为空。'
    return
  }
  clearMessages()
  saving.value = true
  const trackId = editingTrack.value.id
  const hadCoverUpload = Boolean(coverFile.value)
  let metadataSaved = false
  let coverSaved = !hadCoverUpload
  try {
    await api.admin.updateTrack(trackId, {
      title: editForm.title.trim(),
      artist: editForm.artist.trim(),
      album: editForm.album.trim(),
      cover_url: editForm.cover_url?.trim() || null,
    })
    metadataSaved = true
    if (coverFile.value) {
      await api.admin.uploadTrackCover(trackId, coverFile.value)
      coverSaved = true
    }
    coverFile.value = null
    await load()
    notice.value = '曲目元数据已保存。'
  } catch (cause) {
    await load().catch(() => undefined)
    const detail = userFacingError(cause, '曲目更新失败')
    if (metadataSaved && !coverSaved) error.value = `元数据已保存，但封面上传失败：${detail}`
    else if (metadataSaved) error.value = `更改已保存，但界面刷新失败：${detail}`
    else error.value = detail
  } finally {
    saving.value = false
  }
}

async function remove(track: Track) {
  if (!window.confirm(`永久删除“${track.title}”及其媒体文件？若曲目仍被播放列表引用，服务器会拒绝此操作。`)) return
  clearMessages()
  saving.value = true
  try {
    await api.admin.deleteTrack(track.id)
    if (String(editingId.value) === String(track.id)) editingId.value = null
    const selected = new Set(selectedTrackIds.value)
    selected.delete(String(track.id))
    selectedTrackIds.value = selected
    await load()
    notice.value = '曲目已从音乐库删除。'
  } catch (cause) {
    error.value = userFacingError(cause, '无法删除曲目；请先确认它未被任何频道引用')
  } finally {
    saving.value = false
  }
}

watch(search, () => {
  cancelTrackSearch()
  trackSearchTimer = window.setTimeout(() => {
    trackSearchTimer = undefined
    cancelEdit()
    trackPage.value = 1
    void load(false, false, 1)
  }, 250)
})

watch(libraryGroup, (value) => {
  renamedLibraryName.value = value
})

onMounted(() => {
  void load()
  void refreshUploadQueue()
  connectUploadEvents()
  startHeartbeat(5)
  window.addEventListener('beforeunload', expireOwnedUploads)
})

onBeforeUnmount(() => {
  expireOwnedUploads()
  window.removeEventListener('beforeunload', expireOwnedUploads)
  eventSource?.close()
  if (heartbeatTimer !== undefined) window.clearInterval(heartbeatTimer)
  cancelTrackSearch()
  clearSubmittedRetry()
})
</script>

<template>
  <div class="workspace-stack">
    <header class="workspace-header">
      <div>
        <span class="eyebrow">Media ingest</span>
        <h2>音乐库</h2>
        <p>从本地选择音频文件或整个目录，确认后先进入本地已提交队列，再按空位推送至公共队列。</p>
      </div>
      <div class="metric-pair">
        <div><strong>{{ availableCount }}</strong><span>AVAILABLE</span></div>
        <div><strong>{{ unavailableCount }}</strong><span>UNAVAILABLE</span></div>
      </div>
    </header>

    <InlineNotice v-if="error" tone="danger">{{ error }}</InlineNotice>
    <InlineNotice v-else-if="notice" tone="success">{{ notice }}</InlineNotice>

    <section class="ingest-rack" aria-labelledby="ingest-title">
      <div class="ingest-rack__upload">
        <span class="eyebrow" id="ingest-title">Upload bus</span>
        <label class="file-drop" :class="{ populated: selectedFiles.length }" for="track-files">
          <span aria-hidden="true">＋</span>
          <strong>{{ selectedFiles.length ? '继续添加音频文件' : '选择音频文件' }}</strong>
          <small>MP3 / FLAC / M4A / AAC / WAV / OGG · 每个最大 500 MiB</small>
        </label>
        <input
          id="track-files"
          ref="fileInput"
          class="visually-hidden"
          type="file"
          multiple
          accept=".mp3,.flac,.m4a,.aac,.wav,.ogg,audio/*"
          :disabled="committing"
          @change="chooseFiles"
        />
        <div v-if="selectedFiles.length" class="pending-upload-summary">
          <span><strong>{{ selectedFiles.length }}</strong> 个待上传文件</span>
          <span>{{ formatFileSize(pendingUploadBytes) }}</span>
          <span v-if="similarPendingCount" class="pending-upload-summary__warning">{{ similarPendingCount }} 个名称相似</span>
        </div>
        <button
          class="button button--primary"
          type="button"
          :disabled="committing || !selectedFiles.length"
          @click="openUploadReview"
        >
          管理并确认待上传清单
        </button>
        <small class="ingest-note">名称相似时需二次确认，SHA-256 完全相同会自动驳回。确认后的全部文件会在本地等待，并持续按公共队列空位自动推送；关闭页面会清空本地等待任务、取消远端任务并清理临时文件。</small>
      </div>
      <div class="ingest-rack__scan">
        <span class="eyebrow">Local directory</span>
        <strong>扫描本地音频目录</strong>
        <p>由操作者选择本机目录；浏览器会递归读取其中支持的音频文件，并先加入可编辑的待上传清单，不会立即占用公共队列。</p>
        <label
          class="button button--quiet upload-picker-button"
          :class="{ disabled: committing }"
          for="track-directory"
        >
          选择本地目录
        </label>
        <input
          id="track-directory"
          ref="directoryInput"
          class="visually-hidden"
          type="file"
          multiple
          webkitdirectory
          accept=".mp3,.flac,.m4a,.aac,.wav,.ogg,audio/*"
          :disabled="committing"
          @change="chooseDirectory"
        />
      </div>
    </section>

    <Teleport to="body">
      <div
        v-if="uploadReviewOpen"
        class="batch-add-overlay"
        @pointerdown.self="closeUploadReview"
        @keydown.esc="closeUploadReview"
      >
        <section
          class="batch-add-tab upload-review-tab"
          role="dialog"
          aria-modal="true"
          aria-labelledby="upload-review-title"
          aria-describedby="upload-review-description"
        >
          <header class="batch-add-tab__header">
            <div>
              <span class="eyebrow">Upload staging</span>
              <h3 id="upload-review-title">确认待上传清单</h3>
              <p id="upload-review-description">
                提交前可移除文件；名称相似项会高亮显示，并要求逐项二次确认。
              </p>
            </div>
            <button
              class="icon-close"
              type="button"
              aria-label="关闭待上传清单"
              :disabled="committing"
              @click="closeUploadReview"
            >
              ×
            </button>
          </header>

          <div class="batch-add-tab__toolbar">
            <InlineNotice v-if="uploadReviewError" class="batch-add-tab__notice" tone="danger">
              {{ uploadReviewError }}
            </InlineNotice>
            <InlineNotice v-else-if="uploadReviewNotice" class="batch-add-tab__notice" tone="success">
              {{ uploadReviewNotice }}
            </InlineNotice>
            <div class="field field--search">
              <label for="upload-review-search">筛选待上传文件</label>
              <input
                id="upload-review-search"
                ref="uploadReviewSearchInput"
                v-model="uploadReviewQuery"
                type="search"
                placeholder="输入文件名、目录或相似曲目信息"
                autocomplete="off"
              />
            </div>
            <div class="batch-add-tab__selection-tools">
              <span>
                {{ selectedFiles.length }} 个文件 · {{ formatFileSize(pendingUploadBytes) }}
                <template v-if="preflighting"> · 正在检查相似度</template>
                <template v-if="invalidPendingCount"> · {{ invalidPendingCount }} 个文件不可上传</template>
                <template v-if="similarPendingCount">
                  · {{ similarPendingCount }} 个名称相似 / {{ unconfirmedSimilarCount }} 个未确认
                </template>
              </span>
              <label
                class="button button--quiet button--small upload-picker-button"
                :class="{ disabled: committing }"
                for="track-files"
              >
                添加文件
              </label>
              <label
                class="button button--quiet button--small upload-picker-button"
                :class="{ disabled: committing }"
                for="track-directory"
              >
                添加目录
              </label>
              <button class="text-button" type="button" :disabled="committing" @click="clearPendingFiles">清空</button>
            </div>
          </div>

          <div class="batch-add-tab__list upload-review-list">
            <article
              v-for="file in filteredPendingFiles"
              :key="pendingFileKey(file)"
              class="upload-review-file"
              :class="{
                'upload-review-file--invalid': pendingFileIssue(file),
                'upload-review-file--similar': pendingFileSimilarities(file).length,
                'upload-review-file--confirmed': confirmedSimilarFiles.has(pendingFileKey(file)),
              }"
            >
              <div class="upload-review-file__identity">
                <strong>{{ file.name }}</strong>
                <small v-if="pendingFilePath(file) !== file.name">{{ pendingFilePath(file) }}</small>
                <small>{{ formatFileSize(file.size) }}</small>
              </div>
              <span v-if="pendingFileIssue(file)" class="upload-review-file__issue">
                {{ pendingFileIssue(file) }}
              </span>
              <span v-else-if="pendingFileSimilarities(file).length" class="upload-review-file__issue">
                {{
                  confirmedSimilarFiles.has(pendingFileKey(file))
                    ? '已完成二次确认'
                    : '需要二次确认'
                }}
              </span>
              <button
                class="button button--danger button--small"
                type="button"
                :disabled="committing"
                :aria-label="`从待上传清单移除 ${file.name}`"
                @click="removePendingFile(file)"
              >
                移除
              </button>

              <div
                v-if="pendingFileSimilarities(file).length"
                class="upload-similarity-review"
              >
                <div class="upload-similarity-review__heading">
                  <strong>检测到 {{ pendingFileSimilarities(file).length }} 条名称相似记录</strong>
                  <span>这可能是不同编码、现场版或重制版，请核对后继续。</span>
                </div>
                <ul>
                  <li
                    v-for="candidate in pendingFileSimilarities(file)"
                    :key="candidate.id"
                  >
                    <span>
                      <strong>{{ candidate.title }} — {{ candidate.artist || '未知艺人' }}</strong>
                      <small>
                        {{ candidate.album || '未标注专辑' }} · {{ candidate.original_filename }}
                      </small>
                    </span>
                    <b>{{ Math.round(candidate.similarity * 100) }}%</b>
                  </li>
                </ul>
                <label class="upload-similarity-confirm">
                  <input
                    type="checkbox"
                    :checked="confirmedSimilarFiles.has(pendingFileKey(file))"
                    :disabled="committing || preflighting"
                    @change="changeSimilarityConfirmation(file, $event)"
                  />
                  <span>我已核对相似记录，仍确认上传此文件；SHA-256 完全重复仍会自动驳回</span>
                </label>
              </div>
            </article>
            <div v-if="!filteredPendingFiles.length" class="batch-add-tab__empty">
              没有匹配的待上传文件。
            </div>
          </div>

          <footer class="batch-add-tab__footer">
            <span>
              确认后，全部 {{ selectedFiles.length }} 个文件都会进入本地已提交队列；即使公共队列已满，也会继续等待并在空位出现时自动推送。
            </span>
            <div>
              <button class="button button--quiet" type="button" :disabled="committing" @click="closeUploadReview">
                稍后处理
              </button>
              <button
                class="button button--primary"
                type="button"
                :disabled="
                  committing
                  || preflighting
                  || !selectedFiles.length
                  || invalidPendingCount > 0
                  || unconfirmedSimilarCount > 0
                "
                @click="commitSelectedFiles"
              >
                {{
                  committing
                    ? '正在提交…'
                    : preflighting
                      ? '正在检查相似度…'
                      : `确认提交 ${selectedFiles.length} 个本地任务`
                }}
              </button>
            </div>
          </footer>
        </section>
      </div>
    </Teleport>

    <section class="upload-queue-panel local-upload-queue" aria-labelledby="local-upload-queue-title">
      <header class="upload-queue-panel__header">
        <div>
          <span class="eyebrow">Local submitted queue</span>
          <h3 id="local-upload-queue-title">本地已提交队列</h3>
          <p>保存所有已确认但尚未取得公共队列位置的文件；只要本页面保持开启，就会持续按远端空位自动推送。</p>
        </div>
        <div class="queue-metrics">
          <span><strong>{{ submittedUploads.length }}</strong>等待推送</span>
          <span><strong>{{ formatFileSize(submittedUploadBytes) }}</strong>本地文件</span>
          <span><strong>{{ uploadQueue.available_slots }}</strong>远端空位</span>
        </div>
      </header>
      <InlineNotice v-if="submittedQueueError" tone="danger">
        {{ submittedQueueError }}
        <button
          class="text-button"
          type="button"
          :disabled="feedingSubmitted"
          @click="retrySubmittedUploads"
        >
          立即重试
        </button>
      </InlineNotice>
      <div class="data-frame local-upload-queue__frame">
        <table class="console-table local-upload-queue-table">
          <thead><tr><th>顺序</th><th>文件</th><th>大小</th><th>本地阶段</th><th class="align-right">操作</th></tr></thead>
          <tbody>
            <tr v-if="!submittedUploads.length">
              <td colspan="5" class="table-message">没有等待推送至公共队列的本地任务。</td>
            </tr>
            <template v-else>
              <tr v-for="(task, index) in submittedUploads" :key="task.id">
                <td data-label="顺序"><span class="playlist-position">{{ String(index + 1).padStart(3, '0') }}</span></td>
                <td data-label="文件">
                  <strong>{{ task.file.name }}</strong>
                  <small v-if="pendingFilePath(task.file) !== task.file.name">{{ pendingFilePath(task.file) }}</small>
                </td>
                <td data-label="大小">{{ formatFileSize(task.file.size) }}</td>
                <td data-label="本地阶段">
                  <StatusBadge
                    :status="feedingSubmitted && index === 0 ? 'ready' : 'queued'"
                    :label="feedingSubmitted && index === 0 ? '正在申请公共队列位置' : '等待公共队列空位'"
                  />
                </td>
                <td data-label="操作" class="table-actions">
                  <button
                    class="button button--danger button--small"
                    type="button"
                    :disabled="feedingSubmitted"
                    @click="removeSubmittedUpload(task)"
                  >
                    移除
                  </button>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>

    <section class="upload-queue-panel" aria-labelledby="upload-queue-title">
      <header class="upload-queue-panel__header">
        <div>
          <span class="eyebrow">Shared upload queue</span>
          <h3 id="upload-queue-title">公共上传队列</h3>
          <p>所有管理员共享 {{ uploadQueue.queue_limit }} 个任务位置；服务器最多并行处理 {{ uploadQueue.max_concurrent }} 个任务。</p>
        </div>
        <div class="queue-metrics">
          <span><strong>{{ uploadQueue.active_count }}</strong>处理中</span>
          <span><strong>{{ uploadQueue.available_slots }}</strong>空位</span>
        </div>
      </header>
      <div class="data-frame">
        <table class="console-table upload-queue-table">
          <thead><tr><th>文件</th><th>申请人</th><th>进度</th><th>阶段</th><th>存储</th><th class="align-right">操作</th></tr></thead>
          <tbody>
            <tr v-if="queueLoading"><td colspan="6" class="table-message">正在连接公共上传队列…</td></tr>
            <tr v-else-if="!visibleUploadJobs.length"><td colspan="6" class="table-message">上传队列为空。</td></tr>
            <template v-else>
              <tr v-for="job in visibleUploadJobs" :key="job.id">
                <td data-label="文件">
                  <strong>{{ job.original_filename }}</strong>
                  <small>{{ formatFileSize(job.declared_size_bytes) }} · {{ job.id.slice(0, 8) }}</small>
                </td>
                <td data-label="申请人">
                  <strong>{{ job.owner.username }}</strong>
                  <small>{{ isOwnedJob(job) ? '本页面任务' : '其他管理员' }}</small>
                </td>
                <td data-label="进度">
                  <div class="upload-progress">
                    <span><i :style="{ width: `${uploadPercent(job)}%` }"></i></span>
                    <small>{{ uploadPercent(job) }}% · {{ formatFileSize(uploadBytes(job)) }}</small>
                  </div>
                </td>
                <td data-label="阶段">
                  <StatusBadge :status="job.status" :label="uploadStatus(job)" />
                  <small v-if="job.error_message" class="queue-error">{{ job.error_message }}</small>
                </td>
                <td data-label="存储">
                  <strong>{{ job.storage_id || '—' }}</strong>
                  <small>{{ job.duplicate ? 'SHA-256 重复，已驳回' : job.status === 'completed' ? '已落盘' : '自动选择' }}</small>
                </td>
                <td data-label="操作" class="table-actions">
                  <button
                    v-if="isOwnedJob(job) && CLIENT_BOUND_UPLOADS.has(job.status)"
                    class="button button--danger button--small"
                    type="button"
                    @click="cancelUpload(job)"
                  >
                    取消
                  </button>
                  <span v-else class="queue-action-placeholder">—</span>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>

    <section class="library-management" aria-labelledby="library-management-title">
      <header>
        <div>
          <span class="eyebrow">Library registry</span>
          <h3 id="library-management-title">音乐库管理</h3>
          <p>音乐库是独立数据库记录；新建空库后，才能将曲目迁入其中。</p>
        </div>
      </header>
      <form class="library-management__form" @submit.prevent="createLibrary">
        <div class="field">
          <label for="new-track-library">添加音乐库</label>
          <input
            id="new-track-library"
            v-model="newLibraryName"
            type="text"
            maxlength="80"
            placeholder="输入新音乐库名称"
            :disabled="librarySaving"
          />
        </div>
        <button
          class="button button--primary"
          type="submit"
          :disabled="librarySaving || !newLibraryName.trim()"
        >
          添加音乐库
        </button>
      </form>
      <div class="library-management__current">
        <div class="field">
          <label for="rename-track-library">当前音乐库名称</label>
          <input
            id="rename-track-library"
            v-model="renamedLibraryName"
            type="text"
            maxlength="80"
            :disabled="librarySaving || loading || libraryGroup === 'default'"
          />
        </div>
        <div class="library-management__actions">
          <button
            class="button button--quiet"
            type="button"
            :disabled="
              librarySaving
              || loading
              || libraryGroup === 'default'
              || !renamedLibraryName.trim()
              || renamedLibraryName.trim() === libraryGroup
            "
            @click="renameLibrary"
          >
            重命名当前库
          </button>
          <button
            class="button button--danger"
            type="button"
            :disabled="
              librarySaving
              || loading
              || libraryGroup === 'default'
              || currentLibraryTrackCount > 0
            "
            @click="deleteLibrary"
          >
            删除当前空库
          </button>
        </div>
        <small v-if="libraryGroup === 'default'">default 是系统音乐库，不能重命名或删除。</small>
        <small v-else-if="currentLibraryTrackCount > 0">
          当前库还有 {{ currentLibraryTrackCount }} 首曲目；全部迁走后才能删除。
        </small>
        <small v-else>当前音乐库为空，可以安全删除。</small>
      </div>
    </section>

    <div class="library-toolbar">
      <div class="field field--library">
        <label for="track-library">所属音乐库</label>
        <select
          id="track-library"
          v-model="libraryGroup"
          :disabled="loading || movingTracks || librarySaving"
          @change="changeLibraryGroup"
        >
          <option v-for="group in libraryGroups" :key="group" :value="group">{{ group }}</option>
        </select>
      </div>
      <div class="field field--search">
        <label for="track-search">搜索音乐库</label>
        <input id="track-search" v-model="search" type="search" placeholder="标题 / 艺人 / 专辑 / 文件名" />
      </div>
      <button class="button button--quiet button--small" type="button" :disabled="loading" @click="load()">刷新库</button>
    </div>

    <div class="data-frame">
      <table class="console-table track-table">
        <thead>
          <tr>
            <th class="track-select-cell">
              <input
                type="checkbox"
                aria-label="选择当前页全部曲目"
                :checked="allPageTracksSelected"
                :disabled="!tracks.length || movingTracks"
                @change="togglePageTracks"
              />
            </th>
            <th>曲目</th><th>音乐库</th><th>专辑</th><th>时长</th><th>文件</th><th>状态</th><th class="align-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="8" class="table-message">正在查询音乐库…</td></tr>
          <tr v-else-if="!tracks.length"><td colspan="8" class="table-message">音乐库中没有匹配的曲目。</td></tr>
          <template v-else>
            <tr
              v-for="track in tracks"
              :key="track.id"
              :class="{
                unavailable: track.available === false,
                selected: selectedTrackIds.has(String(track.id)),
              }"
            >
              <td data-label="选择" class="track-select-cell">
                <input
                  type="checkbox"
                  :aria-label="`选择 ${track.title}`"
                  :checked="selectedTrackIds.has(String(track.id))"
                  :disabled="movingTracks"
                  @change="toggleTrackSelection(track, $event)"
                />
              </td>
              <td data-label="曲目" class="track-cell">
                <span class="mini-cover"><img v-if="track.cover_url" :src="track.cover_url" alt="" /><i v-else aria-hidden="true">♪</i></span>
                <span><strong>{{ track.title }}</strong><small>{{ track.artist || '未知艺人' }}</small></span>
              </td>
              <td data-label="音乐库"><span class="mono-label">{{ track.library_group || 'default' }}</span></td>
              <td data-label="专辑">{{ track.album || '—' }}</td>
              <td data-label="时长" class="mono-label">{{ formatDuration(track.duration_seconds) }}</td>
              <td data-label="文件">
                <span class="file-detail">
                  {{ track.original_filename || '服务器媒体' }}
                  <small>{{ formatFileSize(track.file_size_bytes) }}<template v-if="trackAudioDetail(track)"> · {{ trackAudioDetail(track) }}</template></small>
                </span>
              </td>
              <td data-label="状态"><StatusBadge :status="track.available === false ? 'unavailable' : 'available'" /></td>
              <td data-label="操作" class="table-actions">
                <button class="button button--quiet button--small" type="button" @click="beginEdit(track)">编辑</button>
                <button class="button button--danger button--small" type="button" :disabled="saving" @click="remove(track)">删除</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <div class="library-pagination">
      <span>
        第 {{ trackPage }} / {{ trackTotalPages }} 页 · 当前 {{ tracks.length }} 条 ·
        共 {{ trackTotal }} 条匹配结果 · 每页固定 {{ trackPageSize }} 条
      </span>
      <div>
        <button
          class="button button--quiet button--small"
          type="button"
          :disabled="loading || trackPage <= 1"
          @click="goToTrackPage(trackPage - 1)"
        >
          上一页
        </button>
        <button
          class="button button--quiet button--small"
          type="button"
          :disabled="loading || trackPage >= trackTotalPages"
          @click="goToTrackPage(trackPage + 1)"
        >
          下一页
        </button>
      </div>
    </div>

    <section class="library-batch-move" aria-labelledby="library-batch-move-title">
      <div>
        <span class="eyebrow">Library transfer</span>
        <strong id="library-batch-move-title">批量迁移所属音乐库</strong>
        <small>当前已从“{{ libraryGroup }}”跨页选择 {{ selectedTrackIds.size }} 首曲目。</small>
      </div>
      <div class="field">
        <label for="target-track-library">目标音乐库</label>
        <select
          id="target-track-library"
          v-model="moveTargetLibrary"
          :disabled="movingTracks"
        >
          <option value="">选择已创建的目标音乐库</option>
          <option
            v-for="group in targetLibraryGroups"
            :key="group"
            :value="group"
          >
            {{ group }}
          </option>
        </select>
      </div>
      <div class="library-batch-move__actions">
        <button
          class="button button--quiet button--small"
          type="button"
          :disabled="movingTracks || !selectedTrackIds.size"
          @click="clearSelectedTracks"
        >
          清空选择
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="movingTracks || !selectedTrackIds.size || !moveTargetLibrary.trim()"
          @click="moveSelectedTracks"
        >
          {{ movingTracks ? '正在迁移…' : `迁移所选 ${selectedTrackIds.size} 首` }}
        </button>
      </div>
    </section>

    <section v-if="editingTrack" class="drawer-editor" aria-labelledby="track-editor-title">
      <header>
        <div><span class="eyebrow">Metadata editor</span><h3 id="track-editor-title">编辑曲目信息</h3></div>
        <button class="icon-close" type="button" aria-label="关闭曲目编辑器" @click="cancelEdit">×</button>
      </header>
      <form class="console-form compact-form" @submit.prevent="saveTrack">
        <div class="field-grid field-grid--three">
          <div class="field"><label for="track-title">标题</label><input id="track-title" v-model="editForm.title" required /></div>
          <div class="field"><label for="track-artist">艺人</label><input id="track-artist" v-model="editForm.artist" /></div>
          <div class="field"><label for="track-album">专辑</label><input id="track-album" v-model="editForm.album" /></div>
        </div>
        <div class="field">
          <label for="track-cover">封面 URL</label>
          <input id="track-cover" v-model="editForm.cover_url" type="text" inputmode="url" placeholder="/api/covers/… 或 https://…" />
          <small>只接受 HTTPS 或本站封面路径；留空可清除 URL 覆盖。</small>
        </div>
        <div class="field">
          <label for="track-cover-file">上传替换封面</label>
          <input id="track-cover-file" type="file" accept="image/jpeg,image/png,image/webp" @change="chooseCover" />
          <small>{{ coverFile ? `${coverFile.name} · ${formatFileSize(coverFile.size)}` : 'JPEG / PNG / WebP，最大 10 MiB；上传文件优先于 URL。' }}</small>
        </div>
        <div class="form-actions">
          <button class="button button--primary" type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存元数据' }}</button>
          <button class="button button--quiet" type="button" @click="cancelEdit">取消</button>
        </div>
      </form>
    </section>
  </div>
</template>
