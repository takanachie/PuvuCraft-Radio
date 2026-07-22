<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { userFacingError } from '../api/client'
import type { Channel, PlaybackState, PlaylistItem, TrackSummary } from '../api/types'
import ConsoleHeader from '../components/ConsoleHeader.vue'
import InlineNotice from '../components/InlineNotice.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useLiveAudio } from '../composables/useLiveAudio'
import { session } from '../session'
import { formatDuration, itemId, trackFromItem } from '../utils/format'
import { interpolatedPosition, parsePlaybackEvent, playbackFromEvent, playbackPercent } from '../utils/playback'

const route = useRoute()
const router = useRouter()
const audio = ref<HTMLAudioElement | null>(null)
const player = useLiveAudio(audio)

const channels = ref<Channel[]>([])
const selectedId = ref('')
const channel = ref<Channel | null>(null)
const playlist = ref<PlaylistItem[]>([])
const loading = ref(true)
const playlistLoading = ref(false)
const pageError = ref('')
const eventsState = ref<'connecting' | 'connected' | 'reconnecting'>('connecting')
const playback = ref<PlaybackState>({ status: 'starting', position_seconds: 0 })
const receivedAt = ref(Date.now())
const clock = ref(Date.now())

let eventSource: EventSource | null = null
let clockTimer: ReturnType<typeof setInterval> | null = null
let loadSequence = 0
let playlistSequence = 0
let hasConnected = false
let lastSessionCheck = 0

const availableChannels = computed(() => channels.value.filter((item) => item.enabled !== false))
const selectedChannel = computed(() =>
  channels.value.find((item) => String(item.id) === selectedId.value) || channel.value,
)
const currentTrack = computed<TrackSummary | null>(() =>
  playback.value.current_track !== undefined
    ? playback.value.current_track
    : channel.value?.current_track ?? null,
)
const duration = computed(() => playback.value.duration_seconds ?? currentTrack.value?.duration_seconds ?? null)
const serverPosition = computed(() => interpolatedPosition({ state: playback.value, receivedAt: receivedAt.value }, clock.value))
const progress = computed(() => playbackPercent(serverPosition.value, duration.value))
const currentItemId = computed(() => playback.value.current_item_id)
const transportActive = computed(() =>
  player.isPlaying.value || (player.wantsPlayback.value && !player.autoplayBlocked.value),
)

const transportLabel = computed(() => {
  const labels: Record<string, string> = {
    idle: '未连接',
    connecting: '正在调谐',
    ready: '信号就绪',
    playing: '正在收听',
    paused: '本地已暂停',
    buffering: '正在缓冲',
    reconnecting: '信号重连中',
    blocked: '等待开始',
    unsupported: '浏览器不支持',
    error: '播放错误',
  }
  return labels[player.state.value] || player.state.value
})

function hlsUrl(slug: string): string {
  return `/hls/${encodeURIComponent(slug)}/index.m3u8`
}

function closeEvents() {
  eventSource?.close()
  eventSource = null
}

async function refreshPlaylist(channelId: Channel['id']) {
  const requestId = ++playlistSequence
  playlistLoading.value = true
  try {
    const result = await api.channels.playlist(channelId)
    if (requestId === playlistSequence && String(channel.value?.id) === String(channelId)) {
      playlist.value = result
    }
  } catch (cause) {
    if (requestId === playlistSequence && String(channel.value?.id) === String(channelId)) {
      pageError.value = userFacingError(cause, '无法读取频道歌单')
    }
  } finally {
    if (requestId === playlistSequence) playlistLoading.value = false
  }
}

function handleEvent(message: MessageEvent<string>) {
  const event = parsePlaybackEvent(message.data)
  if (!event) return

  const type = (event.type || event.event || message.type || '').toLowerCase()
  if (type === 'heartbeat' || type === 'ping') return
  if (type.includes('playlist')) {
    if (channel.value) void refreshPlaylist(channel.value.id)
    return
  }

  const next = playbackFromEvent(event, playback.value)
  playback.value = next.playback
  if (next.track !== undefined) playback.value.current_track = next.track
  receivedAt.value = Date.now()

  if (event.channel && channel.value) {
    const previousSlug = channel.value.slug
    channel.value = { ...channel.value, ...event.channel }
    channels.value = channels.value.map((item) =>
      String(item.id) === String(channel.value?.id) ? { ...item, ...event.channel } : item,
    )
    if (event.channel.enabled === false) {
      player.disconnect()
      void loadChannels()
      return
    }
    if (event.channel.slug && event.channel.slug !== previousSlug) {
      const shouldPlay = player.wantsPlayback.value || player.isPlaying.value
      player.connect(hlsUrl(event.channel.slug), shouldPlay)
      void router.replace({ query: { ...route.query, channel: event.channel.slug } })
    }
  }
}

function openEvents(channelId: Channel['id']) {
  closeEvents()
  eventsState.value = 'connecting'
  eventSource = new EventSource(`/api/channels/${encodeURIComponent(String(channelId))}/events`, {
    withCredentials: true,
  })
  eventSource.onopen = () => {
    eventsState.value = 'connected'
  }
  eventSource.onerror = () => {
    eventsState.value = 'reconnecting'
    const now = Date.now()
    if (navigator.onLine && now - lastSessionCheck > 15_000) {
      lastSessionCheck = now
      void session.loadUser(true).catch(() => null)
    }
  }
  eventSource.onmessage = handleEvent
  for (const name of ['playback', 'state', 'track', 'status', 'playlist', 'channel']) {
    eventSource.addEventListener(name, handleEvent as EventListener)
  }
}

async function loadChannel(channelId: string) {
  const target = channels.value.find((item) => String(item.id) === channelId)
  if (!target) return
  const sequence = ++loadSequence
  playlistSequence += 1
  const shouldPlay = !hasConnected || player.wantsPlayback.value || player.isPlaying.value
  pageError.value = ''
  loading.value = true
  closeEvents()
  player.disconnect()

  try {
    const [detailResult, playlistResult] = await Promise.allSettled([
      api.channels.get(target.id),
      api.channels.playlist(target.id),
    ])
    if (sequence !== loadSequence) return
    if (detailResult.status === 'rejected') throw detailResult.reason

    const detail = detailResult.value
    channel.value = detail
    if (playlistResult.status === 'fulfilled') playlist.value = playlistResult.value
    else {
      playlist.value = []
      pageError.value = userFacingError(playlistResult.reason, '直播已连接，但播放列表暂时无法读取')
    }
    const initialPlayback = detail.playback_state ?? detail.playback
    playback.value = initialPlayback
      ? { ...initialPlayback, status: initialPlayback.status || detail.status || 'starting' }
      : {
          status: detail.status || 'starting',
          current_track: detail.current_track,
          position_seconds: 0,
        }
    receivedAt.value = Date.now()
    openEvents(detail.id)

    hasConnected = true
    player.connect(hlsUrl(detail.slug), shouldPlay)
    void router.replace({ query: { ...route.query, channel: detail.slug } })
  } catch (cause) {
    if (sequence !== loadSequence) return
    pageError.value = userFacingError(cause, '频道暂时无法连接')
    if (shouldPlay) hasConnected = false
    player.disconnect()
  } finally {
    if (sequence === loadSequence) {
      loading.value = false
      playlistLoading.value = false
    }
  }
}

async function loadChannels() {
  loading.value = true
  pageError.value = ''
  try {
    channels.value = await api.channels.list()
    const requested = typeof route.query.channel === 'string' ? route.query.channel : ''
    const initial = availableChannels.value.find(
      (item) => String(item.id) === requested || item.slug === requested,
    ) ?? availableChannels.value[0]
    if (initial) {
      const nextId = String(initial.id)
      if (selectedId.value === nextId) void loadChannel(nextId)
      else selectedId.value = nextId
    }
    else loading.value = false
  } catch (cause) {
    pageError.value = userFacingError(cause, '无法读取可用频道')
    loading.value = false
  }
}

function isCurrent(item: PlaylistItem): boolean {
  if (currentItemId.value !== null && currentItemId.value !== undefined) {
    return String(itemId(item)) === String(currentItemId.value)
  }
  return Boolean(item.is_current)
}

function changeVolume(event: Event) {
  player.setVolume(Number((event.target as HTMLInputElement).value))
}

watch(selectedId, (value, previous) => {
  if (value && value !== previous) void loadChannel(value)
})

onMounted(() => {
  clockTimer = setInterval(() => {
    clock.value = Date.now()
  }, 250)
  void loadChannels()
})

onBeforeUnmount(() => {
  closeEvents()
  if (clockTimer) clearInterval(clockTimer)
})
</script>

<template>
  <div class="console-page radio-page">
    <ConsoleHeader section="LISTENER / LIVE" />
    <main id="main-content" class="radio-console">
      <section class="tuner-strip" aria-label="频道选择">
        <div class="tuner-strip__scale" aria-hidden="true">
          <span v-for="mark in ['88', '92', '96', '100', '104', '108']" :key="mark">{{ mark }}</span>
        </div>
        <div class="tuner-strip__control">
          <label for="channel-select">CHANNEL SELECT</label>
          <select id="channel-select" v-model="selectedId" :disabled="loading || !availableChannels.length">
            <option v-if="!availableChannels.length" value="">没有可用频道</option>
            <option v-for="item in availableChannels" :key="item.id" :value="String(item.id)">
              {{ item.name }}
            </option>
          </select>
        </div>
        <div class="tuner-strip__signal">
          <span class="signal-bars" :class="{ active: eventsState === 'connected' }" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
          <span>{{ eventsState === 'connected' ? 'DATA LOCK' : eventsState === 'reconnecting' ? 'RE-SYNC' : 'TUNING' }}</span>
        </div>
      </section>

      <InlineNotice v-if="pageError" tone="danger" title="信号不可用">
        {{ pageError }}
        <button class="text-button" type="button" @click="loadChannels">重新连接</button>
      </InlineNotice>

      <div v-if="!availableChannels.length && !loading" class="empty-console">
        <span class="empty-console__code">NO CARRIER</span>
        <h1>当前没有可收听的频道</h1>
        <p>频道可能尚未启用，或管理员正在维护播放服务。</p>
      </div>

      <template v-else>
        <section class="now-playing" :aria-busy="loading">
          <div class="now-playing__cover">
            <div class="cover-frame" :class="{ 'cover-frame--spinning': player.isPlaying.value }">
              <img v-if="currentTrack?.cover_url" :src="currentTrack.cover_url" :alt="`${currentTrack.title} 封面`" />
              <div v-else class="cover-placeholder" aria-label="暂无封面">
                <span>RADIO</span><i></i><small>NO ARTWORK</small>
              </div>
            </div>
            <span class="cover-index">CH {{ String(selectedChannel?.display_order ?? '01').padStart(2, '0') }}</span>
          </div>

          <div class="now-playing__readout">
            <div class="readout-status">
              <StatusBadge :status="playback.status || selectedChannel?.status" />
              <span>{{ selectedChannel?.name || '正在读取频道' }}</span>
              <span v-if="playback.listener_count !== undefined">{{ playback.listener_count }} LISTENERS</span>
            </div>
            <div class="track-readout" aria-live="polite">
              <span class="eyebrow">Now transmitting</span>
              <h1>{{ currentTrack?.title || (loading ? '正在调谐…' : '等待节目') }}</h1>
              <p>{{ currentTrack?.artist || '未知艺人' }}</p>
              <small v-if="currentTrack?.album">{{ currentTrack.album }}</small>
            </div>

            <div class="server-progress">
              <div class="server-progress__labels">
                <span>SERVER TIMELINE</span>
                <output>{{ formatDuration(serverPosition) }} / {{ formatDuration(duration) }}</output>
              </div>
              <div
                class="server-progress__track"
                role="progressbar"
                aria-label="服务器播放进度（只读）"
                :aria-valuenow="Math.round(serverPosition)"
                aria-valuemin="0"
                :aria-valuemax="Math.max(1, Math.round(duration || serverPosition || 1))"
                :aria-valuetext="`${formatDuration(serverPosition)} / ${formatDuration(duration)}`"
              >
                <span :style="{ width: `${progress}%` }"></span>
              </div>
              <p>LIVE SERVER POSITION / 此进度由服务器同步，不能拖动</p>
            </div>

            <div v-if="playback.last_error" class="readout-error" role="status">{{ playback.last_error }}</div>
            <div v-if="player.error.value" class="readout-error" role="status">{{ player.error.value }}</div>
          </div>

          <div class="transport-deck" aria-label="本地播放控制">
            <div class="transport-deck__state">
              <span class="status-lamp" :class="{ 'status-lamp--active': player.isPlaying.value }" aria-hidden="true"></span>
              <span>{{ transportLabel }}</span>
            </div>
            <button
              class="transport-button transport-button--play"
              type="button"
              :aria-label="transportActive ? '暂停本地播放' : '从直播点开始播放'"
              @click="player.togglePlayback"
            >
              <span aria-hidden="true">{{ transportActive ? 'Ⅱ' : '▶' }}</span>
              {{ transportActive ? '暂停' : '播放' }}
            </button>
            <button
              class="transport-button"
              type="button"
              :aria-pressed="player.muted.value"
              :aria-label="player.muted.value ? '取消静音' : '静音'"
              @click="player.toggleMute"
            >
              <span aria-hidden="true">{{ player.muted.value ? '×' : '◖' }}</span>
              {{ player.muted.value ? '静音中' : '静音' }}
            </button>
            <div class="volume-control">
              <label for="radio-volume">VOLUME <output>{{ Math.round(player.volume.value * 100) }}</output></label>
              <input
                id="radio-volume"
                :value="player.volume.value"
                type="range"
                min="0"
                max="1"
                step="0.01"
                aria-label="本地音量"
                @input="changeVolume"
              />
            </div>
          </div>

          <div v-if="player.autoplayBlocked.value" class="autoplay-gate" role="dialog" aria-labelledby="autoplay-title" aria-describedby="autoplay-copy">
            <span class="autoplay-gate__pulse" aria-hidden="true"></span>
            <div>
              <strong id="autoplay-title">浏览器正在等待你的操作</strong>
              <p id="autoplay-copy">自动播放已被阻止。点击后将从频道当前直播点开始，而不是从缓存位置继续。</p>
            </div>
            <button class="button button--primary" type="button" @click="player.resumeLive">开始收听直播</button>
          </div>
        </section>

        <section class="playlist-console" aria-labelledby="playlist-title">
          <header class="section-header">
            <div>
              <span class="eyebrow">Program memory</span>
              <h2 id="playlist-title">完整播放列表</h2>
            </div>
            <div class="section-header__meta">
              <span>{{ playlist.length }} TRACKS</span>
              <span>{{ selectedChannel?.playback_mode === 'shuffle' ? 'SHUFFLE' : 'SEQUENTIAL' }}</span>
            </div>
          </header>

          <div v-if="playlistLoading" class="loading-line" role="status">正在读取播放列表…</div>
          <ol v-else-if="playlist.length" class="listener-playlist">
            <li v-for="(item, index) in playlist" :key="itemId(item)" :class="{ current: isCurrent(item), unavailable: trackFromItem(item).available === false }">
              <span class="playlist-position">{{ String(index + 1).padStart(2, '0') }}</span>
              <span v-if="isCurrent(item)" class="playing-bars" aria-label="正在播放"><i></i><i></i><i></i></span>
              <span v-else class="playlist-dot" aria-hidden="true"></span>
              <span class="playlist-track">
                <strong>{{ trackFromItem(item).title }}</strong>
                <small>{{ trackFromItem(item).artist || '未知艺人' }}<template v-if="trackFromItem(item).album"> / {{ trackFromItem(item).album }}</template></small>
              </span>
              <span v-if="trackFromItem(item).available === false" class="playlist-unavailable">不可用</span>
              <time>{{ formatDuration(trackFromItem(item).duration_seconds) }}</time>
            </li>
          </ol>
          <div v-else class="playlist-empty">该频道的播放列表为空，直播暂时无法启动。</div>
        </section>
      </template>
    </main>
    <audio ref="audio" preload="none" playsinline aria-hidden="true"></audio>
  </div>
</template>
