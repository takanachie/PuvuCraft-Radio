<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../api'
import { userFacingError } from '../../api/client'
import type { EntityId, Track, TrackInput } from '../../api/types'
import { formatDuration, formatFileSize } from '../../utils/format'
import InlineNotice from '../InlineNotice.vue'
import StatusBadge from '../StatusBadge.vue'

const tracks = ref<Track[]>([])
const loading = ref(true)
const scanning = ref(false)
const uploading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const search = ref('')
const selectedFiles = ref<File[]>([])
const uploadProgress = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const editingId = ref<EntityId | null>(null)
const coverFile = ref<File | null>(null)
const editForm = reactive<TrackInput>({ title: '', artist: '', album: '', cover_url: '' })

const editingTrack = computed(() => tracks.value.find((track) => String(track.id) === String(editingId.value)) || null)
const hasOversizedFile = computed(() => selectedFiles.value.some((file) => file.size > 500 * 1024 * 1024))
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
  const oversized = selectedFiles.value.find((file) => file.size > 500 * 1024 * 1024)
  if (oversized) error.value = `${oversized.name} 超过 500 MiB 上传上限。`
}

async function upload() {
  if (!selectedFiles.value.length || uploading.value) return
  if (selectedFiles.value.some((file) => file.size > 500 * 1024 * 1024)) return
  clearMessages()
  uploading.value = true
  let completed = 0
  try {
    for (const [index, file] of selectedFiles.value.entries()) {
      uploadProgress.value = `${index + 1} / ${selectedFiles.value.length} · ${file.name}`
      await api.admin.uploadTrack(file)
      completed += 1
    }
    selectedFiles.value = []
    if (fileInput.value) fileInput.value.value = ''
    await load()
    notice.value = `已处理 ${completed} 个上传文件；重复内容由服务器按哈希策略处理。`
  } catch (cause) {
    error.value = `${completed ? `已完成 ${completed} 个文件。` : ''}${userFacingError(cause, '音频上传失败')}`
  } finally {
    uploading.value = false
    uploadProgress.value = ''
  }
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

onMounted(() => void load())
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
        <button class="button button--primary" type="button" :disabled="uploading || !selectedFiles.length || hasOversizedFile" @click="upload">
          {{ uploading ? `正在上传 ${uploadProgress}` : '上传并导入' }}
        </button>
      </div>
      <div class="ingest-rack__scan">
        <span class="eyebrow">Server import</span>
        <strong>扫描受信任目录</strong>
        <p>服务器会验证音频流、提取标签与封面、检测 SHA-256 重复，并标记消失的文件。</p>
        <button class="button button--quiet" type="button" :disabled="scanning" @click="scan">{{ scanning ? '扫描进行中…' : '开始目录扫描' }}</button>
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
              <td data-label="文件"><span class="file-detail">{{ track.original_filename || '服务器媒体' }}<small>{{ formatFileSize(track.file_size_bytes) }}</small></span></td>
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
