<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
const addTrackId = ref('')
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const dragIndex = ref<number | null>(null)
let playlistRequest = 0
let eventSource: EventSource | null = null

const currentChannel = computed(() =>
  channels.value.find((channel) => String(channel.id) === selectedChannelId.value) || null,
)
const currentItemId = computed(() =>
  currentChannel.value?.playback_state?.current_item_id ?? currentChannel.value?.playback?.current_item_id,
)
const availableTracks = computed(() => tracks.value.filter((track) => track.available !== false))
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

async function loadPlaylist() {
  const channel = currentChannel.value
  if (!channel) {
    playlist.value = []
    loading.value = false
    return
  }
  const requestId = ++playlistRequest
  loading.value = true
  error.value = ''
  try {
    const result = await api.admin.playlist(channel.id)
    if (requestId === playlistRequest) playlist.value = result
  } catch (cause) {
    if (requestId === playlistRequest) error.value = userFacingError(cause, '无法读取频道播放列表')
  } finally {
    if (requestId === playlistRequest) loading.value = false
  }
}

async function loadInitial() {
  loading.value = true
  error.value = ''
  try {
    const [channelList, trackList] = await Promise.all([api.admin.channels(), api.admin.tracks()])
    channels.value = channelList.sort((a, b) => (a.display_order ?? 0) - (b.display_order ?? 0))
    tracks.value = trackList
    if (!currentChannel.value && channels.value[0]) selectedChannelId.value = String(channels.value[0].id)
    else await loadPlaylist()
  } catch (cause) {
    error.value = userFacingError(cause, '无法读取播放列表管理数据')
    loading.value = false
  }
}

async function refreshContext() {
  const selected = selectedChannelId.value
  const [channelList, trackList] = await Promise.all([api.admin.channels(), api.admin.tracks()])
  channels.value = channelList.sort((a, b) => (a.display_order ?? 0) - (b.display_order ?? 0))
  tracks.value = trackList
  if (!channels.value.some((item) => String(item.id) === selected) && channels.value[0]) {
    selectedChannelId.value = String(channels.value[0].id)
  } else {
    await loadPlaylist()
  }
}

function closeEvents() {
  eventSource?.close()
  eventSource = null
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
    void loadPlaylist()
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

async function addTrack() {
  const channel = currentChannel.value
  const track = tracks.value.find((item) => String(item.id) === addTrackId.value)
  if (!channel || !track || busy.value) return
  clearMessages()
  busy.value = true
  try {
    await api.admin.addPlaylistItem(channel.id, track.id)
    addTrackId.value = ''
    await loadPlaylist()
    notice.value = `已将“${track.title}”加入播放列表。`
  } catch (cause) {
    error.value = userFacingError(cause, '曲目添加失败')
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
    await refreshContext()
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
    await refreshContext()
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
    await refreshContext()
    notice.value = '立即播放命令已发送，频道正在建立新时间线。'
  } catch (cause) {
    error.value = userFacingError(cause, '立即播放命令失败')
  } finally {
    busy.value = false
  }
}

watch(selectedChannelId, () => {
  addTrackId.value = ''
  const channel = currentChannel.value
  if (channel) openEvents(channel.id)
  else closeEvents()
  void loadPlaylist()
})

onMounted(() => void loadInitial())
onBeforeUnmount(closeEvents)
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
      <div><span class="eyebrow">Queue input</span><strong id="add-track-title">添加音乐库曲目</strong></div>
      <select v-model="addTrackId" aria-label="选择要添加的曲目" :disabled="busy || !availableTracks.length">
        <option value="">{{ availableTracks.length ? '选择曲目…' : '没有可添加的曲目' }}</option>
        <option v-for="track in availableTracks" :key="track.id" :value="String(track.id)">{{ track.title }} — {{ track.artist || '未知艺人' }}</option>
      </select>
      <button class="button button--primary" type="button" :disabled="busy || !addTrackId" @click="addTrack">加入列表</button>
    </section>

    <div class="playlist-admin-meta">
      <span>{{ playlist.length }} ITEMS</span>
      <span>TOTAL {{ formatDuration(totalDuration) }}</span>
      <span>拖放或使用上下按钮排序</span>
      <button class="text-button" type="button" :disabled="loading || busy" @click="loadPlaylist">刷新</button>
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
