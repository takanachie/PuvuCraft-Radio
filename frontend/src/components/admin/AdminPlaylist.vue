<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../../api'
import { userFacingError } from '../../api/client'
import type { Channel, PlaybackEvent, PlaybackMode, PlaylistItem, Track } from '../../api/types'
import { formatDuration, itemId, trackFromItem } from '../../utils/format'
import InlineNotice from '../InlineNotice.vue'
import StatusBadge from '../StatusBadge.vue'

const channels = ref<Channel[]>([])
const tracks = ref<Track[]>([])
const playlist = ref<PlaylistItem[]>([])
const selectedChannelId = ref('')
const libraryGroup = ref('default')
const libraryGroups = ref<string[]>(['default'])
const addTrackId = ref('')
const selectedAddTrack = ref<Track | null>(null)
const addTrackQuery = ref('')
const addTrackPickerOpen = ref(false)
const highlightedAddTrackIndex = ref(-1)
const batchAddOpen = ref(false)
const batchAddQuery = ref('')
const batchAddTrackIds = ref<Set<string>>(new Set())
const batchAddError = ref('')
const batchAddSearchInput = ref<HTMLInputElement | null>(null)
const candidateLoading = ref(false)
const candidatePage = ref(1)
const candidateTotalPages = ref(1)
const candidateTotal = ref(0)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const dragIndex = ref<number | null>(null)
let playlistRequest = 0
let appliedPlaylistRequest = 0
let visiblePlaylistRequest = 0
let eventSource: EventSource | null = null
let playlistRefreshTimer: number | undefined
let candidateSearchTimer: number | undefined
let candidateRequest = 0

const currentChannel = computed(() =>
  channels.value.find((channel) => String(channel.id) === selectedChannelId.value) || null,
)
const currentItemId = computed(() =>
  currentChannel.value?.playback_state?.current_item_id ?? currentChannel.value?.playback?.current_item_id,
)
const playlistTrackIds = computed(() =>
  new Set(playlist.value.map((item) => String(trackFromItem(item).id))),
)
const addableTracks = computed(() =>
  tracks.value.filter(
    (track) => track.available !== false && !playlistTrackIds.value.has(String(track.id)),
  ),
)
const filteredAddableTracks = computed(() => addableTracks.value)
const filteredBatchAddTracks = computed(() => addableTracks.value)
const allFilteredBatchTracksSelected = computed(() =>
  filteredBatchAddTracks.value.length > 0
  && filteredBatchAddTracks.value.every(
    (track) => batchAddTrackIds.value.has(String(track.id)),
  ),
)
const highlightedAddTrackOptionId = computed(() =>
  highlightedAddTrackIndex.value >= 0
    ? `playlist-track-option-${highlightedAddTrackIndex.value}`
    : undefined,
)
const totalDuration = computed(() =>
  playlist.value.reduce((sum, item) => sum + (trackFromItem(item).duration_seconds || 0), 0),
)

function clearMessages() {
  error.value = ''
  notice.value = ''
}

function isCurrent(item: PlaylistItem): boolean {
  if (currentItemId.value !== null && currentItemId.value !== undefined) {
    return String(itemId(item)) === String(currentItemId.value)
  }
  return Boolean(item.is_current)
}

async function loadPlaylist(background = false, reportError = true) {
  const channel = currentChannel.value
  if (!channel) {
    playlist.value = []
    if (!background) loading.value = false
    return
  }
  const requestId = ++playlistRequest
  const channelId = String(channel.id)
  if (!background) {
    visiblePlaylistRequest = requestId
    loading.value = true
    error.value = ''
  }
  try {
    const result = await api.admin.playlist(channel.id)
    if (
      requestId >= appliedPlaylistRequest
      && String(currentChannel.value?.id) === channelId
    ) {
      appliedPlaylistRequest = requestId
      playlist.value = result
    }
  } catch (cause) {
    if (
      reportError
      && requestId >= appliedPlaylistRequest
      && String(currentChannel.value?.id) === channelId
    ) {
      error.value = userFacingError(cause, '无法读取频道播放列表')
    }
  } finally {
    if (!background && requestId === visiblePlaylistRequest) loading.value = false
  }
}

function activeCandidateQuery(): string {
  if (batchAddOpen.value) return batchAddQuery.value
  return selectedAddTrack.value ? '' : addTrackQuery.value
}

async function loadTrackCandidates(
  page = candidatePage.value,
  query = activeCandidateQuery(),
  reportError = true,
) {
  const channel = currentChannel.value
  if (!channel) {
    tracks.value = []
    candidatePage.value = 1
    candidateTotalPages.value = 1
    candidateTotal.value = 0
    candidateLoading.value = false
    return
  }

  const requestId = ++candidateRequest
  const channelId = String(channel.id)
  const requestedLibrary = libraryGroup.value
  candidateLoading.value = true
  try {
    const result = await api.admin.tracks({
      page,
      libraryGroup: requestedLibrary,
      search: query,
      availableOnly: true,
      excludeChannelId: channel.id,
    })
    if (
      requestId !== candidateRequest
      || String(currentChannel.value?.id) !== channelId
      || libraryGroup.value !== requestedLibrary
    ) return
    tracks.value = result.items
    candidatePage.value = result.page
    candidateTotalPages.value = result.total_pages
    candidateTotal.value = result.total
    libraryGroups.value = result.library_groups
  } catch (cause) {
    if (requestId !== candidateRequest || !reportError) return
    if (batchAddOpen.value) {
      batchAddError.value = userFacingError(cause, '无法查询批量添加候选曲目')
    } else {
      error.value = userFacingError(cause, '无法查询音乐库候选曲目')
    }
  } finally {
    if (requestId === candidateRequest) candidateLoading.value = false
  }
}

async function loadInitial() {
  loading.value = true
  error.value = ''
  try {
    const channelList = await api.admin.channels()
    channels.value = channelList.sort((a, b) => (a.display_order ?? 0) - (b.display_order ?? 0))
    if (!currentChannel.value && channels.value[0]) selectedChannelId.value = String(channels.value[0].id)
    else await Promise.all([loadPlaylist(), loadTrackCandidates()])
  } catch (cause) {
    error.value = userFacingError(cause, '无法读取播放列表管理数据')
    loading.value = false
  }
}

async function refreshContext(background = false) {
  const selected = selectedChannelId.value
  const channelList = await api.admin.channels()
  channels.value = channelList.sort((a, b) => (a.display_order ?? 0) - (b.display_order ?? 0))
  if (!channels.value.some((item) => String(item.id) === selected) && channels.value[0]) {
    selectedChannelId.value = String(channels.value[0].id)
  } else {
    await Promise.all([
      loadPlaylist(background),
      loadTrackCandidates(candidatePage.value, activeCandidateQuery(), !background),
    ])
  }
}

function closeEvents() {
  eventSource?.close()
  eventSource = null
}

function schedulePlaylistRefresh() {
  if (playlistRefreshTimer !== undefined) window.clearTimeout(playlistRefreshTimer)
  playlistRefreshTimer = window.setTimeout(() => {
    playlistRefreshTimer = undefined
    void Promise.all([
      loadPlaylist(true, false),
      loadTrackCandidates(candidatePage.value, activeCandidateQuery(), false),
    ])
  }, 75)
}

function cancelPlaylistRefresh() {
  if (playlistRefreshTimer === undefined) return
  window.clearTimeout(playlistRefreshTimer)
  playlistRefreshTimer = undefined
}

function handlePlaybackEvent(message: MessageEvent<string>) {
  let event: PlaybackEvent
  try {
    event = JSON.parse(message.data) as PlaybackEvent
  } catch {
    return
  }
  const type = (event.type || event.event || message.type || '').toLowerCase()
  if (type.includes('playlist')) {
    schedulePlaylistRefresh()
    return
  }
  const selected = currentChannel.value
  if (!selected) return
  const state = event.playback || event.playback_state || event.state
  if (state) selected.playback_state = { ...(selected.playback_state || { status: 'starting' }), ...state }
  if (event.channel) Object.assign(selected, event.channel)
}

function openEvents(channelId: Channel['id']) {
  closeEvents()
  eventSource = new EventSource(`/api/channels/${encodeURIComponent(String(channelId))}/events`, {
    withCredentials: true,
  })
  eventSource.onmessage = handlePlaybackEvent
  for (const name of ['playback', 'state', 'track', 'status', 'playlist', 'channel']) {
    eventSource.addEventListener(name, handlePlaybackEvent as EventListener)
  }
}

function addTrackLabel(track: Track): string {
  return `${track.title} — ${track.artist || '未知艺人'}`
}

function scheduleCandidateSearch(query: string) {
  if (candidateSearchTimer !== undefined) window.clearTimeout(candidateSearchTimer)
  candidateSearchTimer = window.setTimeout(() => {
    candidateSearchTimer = undefined
    candidatePage.value = 1
    void loadTrackCandidates(1, query)
  }, 250)
}

function cancelCandidateSearch() {
  if (candidateSearchTimer === undefined) return
  window.clearTimeout(candidateSearchTimer)
  candidateSearchTimer = undefined
}

function changeCandidateLibrary() {
  cancelCandidateSearch()
  addTrackId.value = ''
  selectedAddTrack.value = null
  addTrackQuery.value = ''
  batchAddQuery.value = ''
  batchAddTrackIds.value = new Set()
  highlightedAddTrackIndex.value = -1
  tracks.value = []
  candidatePage.value = 1
  candidateTotalPages.value = 1
  candidateTotal.value = 0
  void loadTrackCandidates(1, '')
}

function goToCandidatePage(page: number) {
  const target = Math.min(Math.max(1, page), candidateTotalPages.value)
  if (target === candidatePage.value || candidateLoading.value) return
  highlightedAddTrackIndex.value = -1
  void loadTrackCandidates(target, activeCandidateQuery())
}

function resetAddTrackSelection() {
  cancelCandidateSearch()
  addTrackId.value = ''
  selectedAddTrack.value = null
  addTrackQuery.value = ''
  addTrackPickerOpen.value = false
  highlightedAddTrackIndex.value = -1
}

function openAddTrackPicker() {
  if (busy.value || loading.value || !currentChannel.value) return
  addTrackPickerOpen.value = true
  const selectedIndex = filteredAddableTracks.value.findIndex(
    (track) => String(track.id) === addTrackId.value,
  )
  if (selectedIndex >= 0) {
    highlightedAddTrackIndex.value = selectedIndex
    return
  }
  highlightedAddTrackIndex.value = filteredAddableTracks.value.length ? 0 : -1
}

function filterAddTracks() {
  addTrackId.value = ''
  selectedAddTrack.value = null
  addTrackPickerOpen.value = true
  highlightedAddTrackIndex.value = -1
  scheduleCandidateSearch(addTrackQuery.value)
}

function filterBatchTracks() {
  batchAddError.value = ''
  scheduleCandidateSearch(batchAddQuery.value)
}

function moveAddTrackHighlight(offset: number) {
  if (!addTrackPickerOpen.value) openAddTrackPicker()
  const count = filteredAddableTracks.value.length
  if (!count) {
    highlightedAddTrackIndex.value = -1
    return
  }
  const current = highlightedAddTrackIndex.value
  if (current < 0) {
    highlightedAddTrackIndex.value = offset > 0 ? 0 : count - 1
    return
  }
  highlightedAddTrackIndex.value = (current + offset + count) % count
}

function dismissAddTrackPicker() {
  addTrackPickerOpen.value = false
  highlightedAddTrackIndex.value = -1
}

function selectAddTrack(track: Track) {
  cancelCandidateSearch()
  addTrackId.value = String(track.id)
  selectedAddTrack.value = track
  addTrackQuery.value = addTrackLabel(track)
  dismissAddTrackPicker()
}

function selectHighlightedAddTrack() {
  if (!addTrackPickerOpen.value) {
    openAddTrackPicker()
    return
  }
  const track = filteredAddableTracks.value[highlightedAddTrackIndex.value]
  if (track) selectAddTrack(track)
}

function closeAddTrackPicker(event: FocusEvent) {
  const picker = event.currentTarget as HTMLElement
  const nextTarget = event.relatedTarget
  if (nextTarget instanceof Node && picker.contains(nextTarget)) return
  dismissAddTrackPicker()
}

function resetBatchAdd() {
  cancelCandidateSearch()
  batchAddOpen.value = false
  batchAddQuery.value = ''
  batchAddTrackIds.value = new Set()
  batchAddError.value = ''
}

function openBatchAdd() {
  if (busy.value || loading.value || !currentChannel.value) return
  resetAddTrackSelection()
  batchAddQuery.value = ''
  batchAddTrackIds.value = new Set()
  batchAddOpen.value = true
  tracks.value = []
  candidatePage.value = 1
  candidateTotalPages.value = 1
  candidateTotal.value = 0
  void loadTrackCandidates(1, '')
  void nextTick(() => batchAddSearchInput.value?.focus())
}

function closeBatchAdd() {
  if (busy.value) return
  resetBatchAdd()
  tracks.value = []
  candidatePage.value = 1
  candidateTotalPages.value = 1
  candidateTotal.value = 0
  void loadTrackCandidates(1, addTrackQuery.value)
}

function toggleBatchTrack(track: Track) {
  const trackId = String(track.id)
  const selected = new Set(batchAddTrackIds.value)
  if (selected.has(trackId)) selected.delete(trackId)
  else selected.add(trackId)
  batchAddTrackIds.value = selected
  batchAddError.value = ''
}

function toggleFilteredBatchTracks() {
  const selected = new Set(batchAddTrackIds.value)
  if (allFilteredBatchTracksSelected.value) {
    for (const track of filteredBatchAddTracks.value) selected.delete(String(track.id))
  } else {
    for (const track of filteredBatchAddTracks.value) selected.add(String(track.id))
  }
  batchAddTrackIds.value = selected
  batchAddError.value = ''
}

function clearBatchTracks() {
  batchAddTrackIds.value = new Set()
  batchAddError.value = ''
}

async function addTrack() {
  const channel = currentChannel.value
  const track = selectedAddTrack.value
  if (!channel || !track || busy.value) return
  clearMessages()
  busy.value = true
  try {
    await api.admin.addPlaylistItem(channel.id, track.id)
    resetAddTrackSelection()
    await Promise.all([loadPlaylist(true), loadTrackCandidates(1, '')])
    notice.value = `已将“${track.title}”加入播放列表。`
  } catch (cause) {
    error.value = userFacingError(cause, '曲目添加失败')
  } finally {
    busy.value = false
  }
}

async function addTracksBatch() {
  const channel = currentChannel.value
  const selectedTrackIds = [...batchAddTrackIds.value].map(Number)
  if (!channel || !selectedTrackIds.length || busy.value) return
  clearMessages()
  busy.value = true
  try {
    const added = await api.admin.addPlaylistItems(
      channel.id,
      selectedTrackIds,
    )
    resetBatchAdd()
    await Promise.all([loadPlaylist(true), loadTrackCandidates(1, '')])
    notice.value = added.length
      ? `已批量将 ${added.length} 首曲目加入播放列表。`
      : '所选曲目均已存在于播放列表中。'
  } catch (cause) {
    batchAddError.value = userFacingError(cause, '批量添加曲目失败')
  } finally {
    busy.value = false
  }
}

async function persistOrder(previous: PlaylistItem[]) {
  const channel = currentChannel.value
  if (!channel) return
  clearMessages()
  busy.value = true
  try {
    const result = await api.admin.reorderPlaylist(channel.id, playlist.value.map(itemId))
    if (result.length) playlist.value = result
    notice.value = '播放顺序已更新，将从下一次选曲开始生效。'
  } catch (cause) {
    playlist.value = previous
    error.value = userFacingError(cause, '无法保存播放顺序')
  } finally {
    busy.value = false
  }
}

function move(index: number, offset: number) {
  const target = index + offset
  if (busy.value || target < 0 || target >= playlist.value.length) return
  const previous = [...playlist.value]
  const next = [...playlist.value]
  const [moved] = next.splice(index, 1)
  next.splice(target, 0, moved)
  playlist.value = next
  void persistOrder(previous)
}

function beginDrag(index: number, event: DragEvent) {
  if (busy.value) return
  dragIndex.value = index
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(index))
  }
}

function dropAt(index: number) {
  const from = dragIndex.value
  dragIndex.value = null
  if (from === null || from === index || busy.value) return
  const previous = [...playlist.value]
  const next = [...playlist.value]
  const [moved] = next.splice(from, 1)
  next.splice(index, 0, moved)
  playlist.value = next
  void persistOrder(previous)
}

async function remove(item: PlaylistItem) {
  const channel = currentChannel.value
  if (!channel || busy.value) return
  const track = trackFromItem(item)
  const warning = isCurrent(item)
    ? `“${track.title}”正在播放。移除它会触发受控切换，继续吗？`
    : `从此频道移除“${track.title}”？媒体文件仍会保留在音乐库。`
  if (!window.confirm(warning)) return
  clearMessages()
  busy.value = true
  try {
    await api.admin.removePlaylistItem(channel.id, itemId(item))
    await refreshContext(true)
    notice.value = '曲目已从播放列表移除。'
  } catch (cause) {
    error.value = userFacingError(cause, '无法移除播放列表项目')
  } finally {
    busy.value = false
  }
}

async function changeMode(mode: PlaybackMode) {
  const channel = currentChannel.value
  if (!channel || mode === channel.playback_mode || busy.value) return
  clearMessages()
  busy.value = true
  try {
    const updated = await api.admin.updateChannel(channel.id, { playback_mode: mode })
    channel.playback_mode = updated?.playback_mode || mode
    notice.value = `播放模式已切换为${mode === 'shuffle' ? '随机播放' : '顺序循环'}，下次选曲生效。`
  } catch (cause) {
    error.value = userFacingError(cause, '无法切换播放模式')
  } finally {
    busy.value = false
  }
}

async function skip() {
  const channel = currentChannel.value
  if (!channel || busy.value || !window.confirm(`让“${channel.name}”立即跳到下一首？所有听众都会同步切换。`)) return
  clearMessages()
  busy.value = true
  try {
    await api.admin.skip(channel.id)
    await refreshContext(true)
    notice.value = '跳过命令已发送，频道正在同步切换。'
  } catch (cause) {
    error.value = userFacingError(cause, '跳过命令失败')
  } finally {
    busy.value = false
  }
}

async function playNow(item: PlaylistItem) {
  const channel = currentChannel.value
  if (!channel || busy.value) return
  const track = trackFromItem(item)
  if (!window.confirm(`立即在“${channel.name}”播放“${track.title}”？当前节目会中断，所有听众都会同步切换。`)) return
  clearMessages()
  busy.value = true
  try {
    await api.admin.playNow(channel.id, itemId(item))
    await refreshContext(true)
    notice.value = '立即播放命令已发送，频道正在建立新时间线。'
  } catch (cause) {
    error.value = userFacingError(cause, '立即播放命令失败')
  } finally {
    busy.value = false
  }
}

watch(selectedChannelId, () => {
  cancelPlaylistRefresh()
  resetAddTrackSelection()
  resetBatchAdd()
  tracks.value = []
  candidatePage.value = 1
  candidateTotalPages.value = 1
  candidateTotal.value = 0
  const channel = currentChannel.value
  if (channel) openEvents(channel.id)
  else closeEvents()
  void Promise.all([loadPlaylist(), loadTrackCandidates(1, '')])
})

watch(filteredAddableTracks, (items) => {
  if (!addTrackPickerOpen.value || !items.length) highlightedAddTrackIndex.value = -1
  else if (
    highlightedAddTrackIndex.value < 0
    || highlightedAddTrackIndex.value >= items.length
  ) {
    highlightedAddTrackIndex.value = 0
  }
})

onMounted(() => void loadInitial())
onBeforeUnmount(() => {
  cancelPlaylistRefresh()
  cancelCandidateSearch()
  candidateRequest += 1
  closeEvents()
})
</script>

<template>
  <div class="workspace-stack">
    <header class="workspace-header">
      <div>
        <span class="eyebrow">Program sequencer</span>
        <h2>播放列表与直播控制</h2>
        <p>调整队列、播放模式，以及向频道监督器发送受控切换命令。</p>
      </div>
      <div class="channel-picker">
        <label for="playlist-channel">操作频道</label>
        <select id="playlist-channel" v-model="selectedChannelId" :disabled="busy || !channels.length">
          <option v-if="!channels.length" value="">没有频道</option>
          <option v-for="channel in channels" :key="channel.id" :value="String(channel.id)">{{ channel.name }}</option>
        </select>
      </div>
    </header>

    <InlineNotice v-if="error" tone="danger">{{ error }}</InlineNotice>
    <InlineNotice v-else-if="notice" tone="success">{{ notice }}</InlineNotice>

    <div v-if="currentChannel" class="playback-command-rack">
      <div class="command-status">
        <span>CHANNEL STATE</span>
        <StatusBadge :status="currentChannel.enabled ? (currentChannel.status || currentChannel.health?.status) : 'stopped'" />
      </div>
      <div class="mode-switch">
        <span>SELECTION MODE</span>
        <div role="group" aria-label="播放模式">
          <button type="button" :class="{ active: currentChannel.playback_mode === 'sequential' }" :aria-pressed="currentChannel.playback_mode === 'sequential'" :disabled="busy" @click="changeMode('sequential')">顺序循环</button>
          <button type="button" :class="{ active: currentChannel.playback_mode === 'shuffle' }" :aria-pressed="currentChannel.playback_mode === 'shuffle'" :disabled="busy" @click="changeMode('shuffle')">随机播放</button>
        </div>
      </div>
      <button class="button button--danger command-skip" type="button" :disabled="busy || !playlist.length || currentChannel?.enabled === false" @click="skip">SKIP / 跳到下一首</button>
    </div>

    <section class="playlist-add-rack" aria-labelledby="add-track-title">
      <div class="playlist-add-rack__heading"><span class="eyebrow">Queue input</span><strong id="add-track-title">添加音乐库曲目</strong></div>
      <div class="field playlist-library-picker">
        <label for="playlist-track-library">音乐库</label>
        <select
          id="playlist-track-library"
          v-model="libraryGroup"
          :disabled="busy || loading || candidateLoading"
          @change="changeCandidateLibrary"
        >
          <option v-for="group in libraryGroups" :key="group" :value="group">{{ group }}</option>
        </select>
      </div>
      <div class="playlist-track-picker" @focusout="closeAddTrackPicker">
        <input
          v-model="addTrackQuery"
          type="text"
          role="combobox"
          aria-label="选择要添加的曲目"
          aria-autocomplete="list"
          aria-controls="playlist-track-options"
          :aria-expanded="addTrackPickerOpen"
          :aria-activedescendant="highlightedAddTrackOptionId"
          :placeholder="candidateLoading ? '正在查询…' : candidateTotal ? '选择曲目…' : '输入内容以搜索曲目'"
          :disabled="busy || loading || !currentChannel"
          autocomplete="off"
          @focus="openAddTrackPicker"
          @input="filterAddTracks"
          @keydown.down.prevent="moveAddTrackHighlight(1)"
          @keydown.up.prevent="moveAddTrackHighlight(-1)"
          @keydown.enter.prevent="selectHighlightedAddTrack"
          @keydown.esc="dismissAddTrackPicker"
        />
        <span class="playlist-track-picker__chevron" aria-hidden="true">⌄</span>
        <div class="playlist-track-picker__meta">
          <span>{{ candidateTotal }} 条候选 · 第 {{ candidatePage }} / {{ candidateTotalPages }} 页</span>
          <div>
            <button
              type="button"
              aria-label="候选曲目上一页"
              :disabled="candidateLoading || candidatePage <= 1"
              @click="goToCandidatePage(candidatePage - 1)"
            >
              ←
            </button>
            <button
              type="button"
              aria-label="候选曲目下一页"
              :disabled="candidateLoading || candidatePage >= candidateTotalPages"
              @click="goToCandidatePage(candidatePage + 1)"
            >
              →
            </button>
          </div>
        </div>
        <div
          v-if="addTrackPickerOpen"
          id="playlist-track-options"
          class="playlist-track-picker__options"
          role="listbox"
          aria-label="可添加曲目"
        >
          <span v-if="candidateLoading" class="playlist-track-picker__empty">正在查询数据库…</span>
          <template v-else>
            <button
              v-for="(track, index) in filteredAddableTracks"
              :id="`playlist-track-option-${index}`"
              :key="track.id"
              class="playlist-track-picker__option"
              :class="{ active: highlightedAddTrackIndex === index }"
              type="button"
              role="option"
              tabindex="-1"
              :aria-selected="String(track.id) === addTrackId"
              @pointerdown.prevent="selectAddTrack(track)"
              @click="selectAddTrack(track)"
            >
              <strong>{{ track.title }} — {{ track.artist || '未知艺人' }}</strong>
              <small>{{ track.album || '未标注专辑' }} · {{ formatDuration(track.duration_seconds) }}</small>
            </button>
          </template>
          <span v-if="!candidateLoading && !filteredAddableTracks.length" class="playlist-track-picker__empty">没有匹配的可添加曲目</span>
        </div>
      </div>
      <div class="playlist-add-rack__actions">
        <button class="button button--primary" type="button" :disabled="busy || loading || !addTrackId" @click="addTrack">加入列表</button>
        <button class="button button--quiet" type="button" :disabled="busy || loading || !currentChannel" @click="openBatchAdd">批量添加</button>
      </div>
    </section>

    <Teleport to="body">
      <div
        v-if="batchAddOpen"
        class="batch-add-overlay"
        @pointerdown.self="closeBatchAdd"
        @keydown.esc="closeBatchAdd"
      >
        <section
          class="batch-add-tab"
          role="dialog"
          aria-modal="true"
          aria-labelledby="batch-add-title"
        >
          <header class="batch-add-tab__header">
            <div>
              <span class="eyebrow">Batch queue input</span>
              <h3 id="batch-add-title">批量添加音乐库曲目</h3>
              <p>当前歌单已有曲目已自动从候选中排除。</p>
            </div>
            <button
              class="icon-close"
              type="button"
              aria-label="关闭批量添加"
              :disabled="busy"
              @click="closeBatchAdd"
            >
              ×
            </button>
          </header>

          <div class="batch-add-tab__toolbar">
            <InlineNotice v-if="batchAddError" class="batch-add-tab__notice" tone="danger">
              {{ batchAddError }}
            </InlineNotice>
            <div class="field">
              <label for="batch-track-library">音乐库</label>
              <select
                id="batch-track-library"
                v-model="libraryGroup"
                :disabled="busy || candidateLoading"
                @change="changeCandidateLibrary"
              >
                <option v-for="group in libraryGroups" :key="group" :value="group">{{ group }}</option>
              </select>
            </div>
            <div class="field field--search">
              <label for="batch-track-search">筛选音乐库</label>
              <input
                id="batch-track-search"
                ref="batchAddSearchInput"
                v-model="batchAddQuery"
                type="search"
                placeholder="输入标题、艺人、专辑或原文件名"
                autocomplete="off"
                @input="filterBatchTracks"
              />
            </div>
            <div class="batch-add-tab__selection-tools">
              <span>
                {{ candidateTotal }} 条候选 · 第 {{ candidatePage }} / {{ candidateTotalPages }} 页 /
                已选 {{ batchAddTrackIds.size }} 首
              </span>
              <button
                class="text-button"
                type="button"
                :disabled="!filteredBatchAddTracks.length"
                @click="toggleFilteredBatchTracks"
              >
                {{ allFilteredBatchTracksSelected ? '取消当前页' : '全选当前页' }}
              </button>
              <button
                class="text-button"
                type="button"
                :disabled="!batchAddTrackIds.size"
                @click="clearBatchTracks"
              >
                清空选择
              </button>
              <button
                class="text-button"
                type="button"
                :disabled="candidateLoading || candidatePage <= 1"
                @click="goToCandidatePage(candidatePage - 1)"
              >
                上一页
              </button>
              <button
                class="text-button"
                type="button"
                :disabled="candidateLoading || candidatePage >= candidateTotalPages"
                @click="goToCandidatePage(candidatePage + 1)"
              >
                下一页
              </button>
            </div>
          </div>

          <div class="batch-add-tab__list" role="group" aria-label="批量添加候选曲目">
            <div v-if="candidateLoading" class="batch-add-tab__empty">
              正在按音乐库和搜索条件查询数据库…
            </div>
            <template v-else>
              <label
                v-for="track in filteredBatchAddTracks"
                :key="track.id"
                class="batch-add-track"
                :class="{ selected: batchAddTrackIds.has(String(track.id)) }"
              >
                <input
                  type="checkbox"
                  :checked="batchAddTrackIds.has(String(track.id))"
                  @change="toggleBatchTrack(track)"
                />
                <span>
                  <strong>{{ track.title }}</strong>
                  <small>{{ track.artist || '未知艺人' }} · {{ track.album || '未标注专辑' }}</small>
                </span>
                <small>{{ formatDuration(track.duration_seconds) }}</small>
              </label>
            </template>
            <div v-if="!candidateLoading && !filteredBatchAddTracks.length" class="batch-add-tab__empty">
              没有匹配且尚未加入当前歌单的曲目。
            </div>
          </div>

          <footer class="batch-add-tab__footer">
            <span>从“{{ libraryGroup }}”中跨页选择；将按所选顺序追加到当前播放列表末尾。</span>
            <div>
              <button class="button button--quiet" type="button" :disabled="busy" @click="closeBatchAdd">取消</button>
              <button
                class="button button--primary"
                type="button"
                :disabled="busy || !batchAddTrackIds.size"
                @click="addTracksBatch"
              >
                {{ busy ? '正在添加…' : `添加所选 ${batchAddTrackIds.size} 首` }}
              </button>
            </div>
          </footer>
        </section>
      </div>
    </Teleport>

    <div class="playlist-admin-meta">
      <span>{{ playlist.length }} ITEMS</span>
      <span>TOTAL {{ formatDuration(totalDuration) }}</span>
      <span>拖放或使用上下按钮排序</span>
      <button class="text-button" type="button" :disabled="loading || busy" @click="loadPlaylist()">刷新</button>
    </div>

    <ol class="admin-playlist" :aria-busy="loading || busy">
      <li v-if="loading" class="admin-playlist__message">正在读取播放顺序…</li>
      <li v-else-if="!playlist.length" class="admin-playlist__message">播放列表为空。添加可用曲目后，已启用频道才会开始直播。</li>
      <template v-else>
        <li
          v-for="(item, index) in playlist"
          :key="itemId(item)"
          :class="{ current: isCurrent(item), dragging: dragIndex === index, unavailable: trackFromItem(item).available === false }"
          :draggable="!busy"
          @dragstart="beginDrag(index, $event)"
          @dragend="dragIndex = null"
          @dragover.prevent
          @drop="dropAt(index)"
        >
          <span class="drag-handle" aria-hidden="true">⠿</span>
          <span class="playlist-position">{{ String(index + 1).padStart(2, '0') }}</span>
          <span class="playlist-admin-track">
            <strong>{{ trackFromItem(item).title }}</strong>
            <small>{{ trackFromItem(item).artist || '未知艺人' }} · {{ formatDuration(trackFromItem(item).duration_seconds) }}</small>
          </span>
          <StatusBadge v-if="isCurrent(item)" status="live" label="正在播放" />
          <StatusBadge v-else-if="trackFromItem(item).available === false" status="unavailable" />
          <div class="playlist-order-actions">
            <button type="button" :disabled="busy || index === 0" :aria-label="`将 ${trackFromItem(item).title} 上移`" @click="move(index, -1)">↑</button>
            <button type="button" :disabled="busy || index === playlist.length - 1" :aria-label="`将 ${trackFromItem(item).title} 下移`" @click="move(index, 1)">↓</button>
          </div>
          <div class="playlist-item-actions">
            <button class="button button--quiet button--small" type="button" :disabled="busy || currentChannel?.enabled === false || trackFromItem(item).available === false || isCurrent(item)" @click="playNow(item)">立即播放</button>
            <button class="button button--danger button--small" type="button" :disabled="busy" @click="remove(item)">移除</button>
          </div>
        </li>
      </template>
    </ol>
  </div>
</template>
