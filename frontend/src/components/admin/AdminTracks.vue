<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../../api'
import { getCookie, userFacingError } from '../../api/client'
import type {
  EntityId,
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
const CLIENT_BOUND_UPLOADS = new Set<UploadJobStatus>(['queued', 'ready', 'uploading'])
const TERMINAL_UPLOADS = new Set<UploadJobStatus>(['completed', 'failed', 'cancelled', 'expired'])
const UPLOAD_LABELS: Record<UploadJobStatus, string> = {
  queued: '排队中',
  ready: '等待传输',
  uploading: '上传中',
  verifying: '校验中',
  normalizing: '规范化',
  placing: '迁移中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  expired: '已过期',
}

function createUploadClientId(): string {
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}

const uploadClientId = createUploadClientId()
const tracks = ref<Track[]>([])
const loading = ref(true)
const scanning = ref(false)
const reserving = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const search = ref('')
const selectedFiles = ref<File[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const editingId = ref<EntityId | null>(null)
const coverFile = ref<File | null>(null)
const editForm = reactive<TrackInput>({ title: '', artist: '', album: '', cover_url: '' })
const uploadQueue = ref<UploadQueueSnapshot>({
  jobs: [],
  queue_limit: 10,
  max_concurrent: 3,
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
let leaving = false

const editingTrack = computed(() => tracks.value.find((track) => String(track.id) === String(editingId.value)) || null)
const hasOversizedFile = computed(() => selectedFiles.value.some((file) => file.size > MAX_UPLOAD_BYTES))
const filteredTracks = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase()
  if (!needle) return tracks.value
  return tracks.value.filter((track) =>
    [track.title, track.artist, track.album, track.original_filename]
      .some((value) => value?.toLocaleLowerCase().includes(needle)),
  )
})
const availableCount = computed(() => tracks.value.filter((track) => track.available !== false).length)

function clearMessages() {
  error.value = ''
  notice.value = ''
}

async function load(preserveSelection = true) {
  loading.value = true
  error.value = ''
  try {
    tracks.value = await api.admin.tracks()
    if (preserveSelection && editingId.value !== null) {
      const updated = tracks.value.find((track) => String(track.id) === String(editingId.value))
      if (updated) beginEdit(updated)
      else editingId.value = null
    }
  } catch (cause) {
    error.value = userFacingError(cause, '无法读取音乐库')
  } finally {
    loading.value = false
  }
}

function chooseFiles(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFiles.value = Array.from(input.files || [])
  error.value = ''
  const oversized = selectedFiles.value.find((file) => file.size > MAX_UPLOAD_BYTES)
  if (oversized) error.value = `${oversized.name} 超过 500 MiB 上传上限。`
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
          notice.value = job.duplicate
            ? `${job.original_filename} 已完成；内容重复，沿用现有曲目。`
            : `${job.original_filename} 已完成并写入 ${job.storage_id || '可用存储'}。`
        } else if (job.status === 'failed') {
          error.value = `${job.original_filename}：${job.error_message || '服务器处理失败'}`
        }
      }
      filesByJob.delete(job.id)
      delete localUploadBytes[job.id]
    }
  }
  if (refreshLibrary) void load(false)
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

async function reserveSelectedFiles() {
  if (!selectedFiles.value.length || reserving.value || hasOversizedFile.value) return
  clearMessages()
  reserving.value = true
  const accepted = new Set<File>()
  try {
    for (const file of selectedFiles.value) {
      const job = await api.admin.reserveUpload(uploadClientId, file)
      filesByJob.set(job.id, file)
      accepted.add(file)
    }
    notice.value = `已申请 ${accepted.size} 个上传任务；服务器将在并行位置空闲时自动开始传输。`
  } catch (cause) {
    const prefix = accepted.size ? `已成功申请 ${accepted.size} 个任务；` : ''
    error.value = `${prefix}${userFacingError(cause, '无法申请上传队列位置')}`
  } finally {
    selectedFiles.value = selectedFiles.value.filter((file) => !accepted.has(file))
    if (!selectedFiles.value.length && fileInput.value) fileInput.value.value = ''
    reserving.value = false
    void api.admin.heartbeatUploads(uploadClientId).catch(() => undefined)
    await refreshUploadQueue()
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

async function scan() {
  if (scanning.value || !window.confirm('扫描服务器配置的导入目录？扫描可能需要一段时间，请勿重复提交。')) return
  clearMessages()
  scanning.value = true
  try {
    const result = await api.admin.scanTracks()
    await load()
    const imported = result?.imported ?? result?.tracks?.length ?? 0
    const skipped = result?.skipped ?? result?.duplicates?.length ?? 0
    notice.value = `目录扫描完成：导入 ${imported}，跳过 ${skipped}${result?.unavailable !== undefined ? `，标记不可用 ${result.unavailable}` : ''}。`
  } catch (cause) {
    error.value = userFacingError(cause, '服务器目录扫描失败')
  } finally {
    scanning.value = false
  }
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
    await load()
    notice.value = '曲目已从音乐库删除。'
  } catch (cause) {
    error.value = userFacingError(cause, '无法删除曲目；请先确认它未被任何频道引用')
  } finally {
    saving.value = false
  }
}

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
})
</script>

<template>
  <div class="workspace-stack">
    <header class="workspace-header">
      <div>
        <span class="eyebrow">Media ingest</span>
        <h2>音乐库</h2>
        <p>上传经过探测的音频文件，或扫描服务器允许的导入目录。</p>
      </div>
      <div class="metric-pair">
        <div><strong>{{ availableCount }}</strong><span>AVAILABLE</span></div>
        <div><strong>{{ tracks.length - availableCount }}</strong><span>UNAVAILABLE</span></div>
      </div>
    </header>

    <InlineNotice v-if="error" tone="danger">{{ error }}</InlineNotice>
    <InlineNotice v-else-if="notice" tone="success">{{ notice }}</InlineNotice>

    <section class="ingest-rack" aria-labelledby="ingest-title">
      <div class="ingest-rack__upload">
        <span class="eyebrow" id="ingest-title">Upload bus</span>
        <label class="file-drop" :class="{ populated: selectedFiles.length }" for="track-files">
          <span aria-hidden="true">＋</span>
          <strong>{{ selectedFiles.length ? `已选择 ${selectedFiles.length} 个文件` : '选择音频文件' }}</strong>
          <small>MP3 / FLAC / M4A / AAC / WAV / OGG · 每个最大 500 MiB</small>
        </label>
        <input id="track-files" ref="fileInput" class="visually-hidden" type="file" multiple accept=".mp3,.flac,.m4a,.aac,.wav,.ogg,audio/*" @change="chooseFiles" />
        <div v-if="selectedFiles.length" class="file-queue">
          <span v-for="file in selectedFiles" :key="`${file.name}-${file.lastModified}`">{{ file.name }} <small>{{ formatFileSize(file.size) }}</small></span>
        </div>
        <button
          class="button button--primary"
          type="button"
          :disabled="reserving || !selectedFiles.length || hasOversizedFile || (!queueLoading && uploadQueue.available_slots === 0)"
          @click="reserveSelectedFiles"
        >
          {{ reserving ? '正在申请…' : `申请上传队列 · ${uploadQueue.available_slots}/${uploadQueue.queue_limit} 空位` }}
        </button>
        <small class="ingest-note">页面保持开启时服务器才会安排传输；关闭页面会取消排队及上传中的任务并清理临时文件。</small>
      </div>
      <div class="ingest-rack__scan">
        <span class="eyebrow">Server import</span>
        <strong>扫描受信任目录</strong>
        <p>服务器会验证音频流、提取标签与封面、检测 SHA-256 重复；超出推流限制的文件将先规范化为 FLAC。</p>
        <button class="button button--quiet" type="button" :disabled="scanning" @click="scan">{{ scanning ? '扫描进行中…' : '开始目录扫描' }}</button>
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
            <tr v-else-if="!uploadQueue.jobs.length"><td colspan="6" class="table-message">上传队列为空。</td></tr>
            <template v-else>
              <tr v-for="job in uploadQueue.jobs" :key="job.id">
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
                  <small>{{ job.duplicate ? '重复内容复用' : job.status === 'completed' ? '已落盘' : '自动选择' }}</small>
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

    <div class="library-toolbar">
      <div class="field field--search">
        <label for="track-search">搜索音乐库</label>
        <input id="track-search" v-model="search" type="search" placeholder="标题 / 艺人 / 专辑 / 文件名" />
      </div>
      <button class="button button--quiet button--small" type="button" :disabled="loading" @click="load()">刷新库</button>
    </div>

    <div class="data-frame">
      <table class="console-table track-table">
        <thead><tr><th>曲目</th><th>专辑</th><th>时长</th><th>文件</th><th>状态</th><th class="align-right">操作</th></tr></thead>
        <tbody>
          <tr v-if="loading"><td colspan="6" class="table-message">正在索引音乐库…</td></tr>
          <tr v-else-if="!filteredTracks.length"><td colspan="6" class="table-message">音乐库中没有匹配的曲目。</td></tr>
          <template v-else>
            <tr v-for="track in filteredTracks" :key="track.id" :class="{ unavailable: track.available === false }">
              <td data-label="曲目" class="track-cell">
                <span class="mini-cover"><img v-if="track.cover_url" :src="track.cover_url" alt="" /><i v-else aria-hidden="true">♪</i></span>
                <span><strong>{{ track.title }}</strong><small>{{ track.artist || '未知艺人' }}</small></span>
              </td>
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
