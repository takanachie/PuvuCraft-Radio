<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { userFacingError } from '../api/client'
import type {
  Channel,
  PlaybackState,
  PlayerKeyState,
  PlayerStreamFormat,
  TrackSummary,
} from '../api/types'
import ConsoleHeader from '../components/ConsoleHeader.vue'
import InlineNotice from '../components/InlineNotice.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useLiveAudio } from '../composables/useLiveAudio'
import { session } from '../session'
import { formatDuration } from '../utils/format'
import { interpolatedPosition, parsePlaybackEvent, playbackFromEvent, playbackPercent } from '../utils/playback'

const route = useRoute()
const router = useRouter()
const audio = ref<HTMLAudioElement | null>(null)
const player = useLiveAudio(audio)

const channels = ref<Channel[]>([])
const selectedId = ref('')
const channel = ref<Channel | null>(null)
const loading = ref(true)
const pageError = ref('')
const eventsState = ref<'connecting' | 'connected' | 'reconnecting'>('connecting')
const playback = ref<PlaybackState>({ status: 'starting', position_seconds: 0 })
const receivedAt = ref(Date.now())
const clock = ref(Date.now())
const playerKey = ref<PlayerKeyState | null>(null)
const playerKeyLoading = ref(true)
const playerKeyBusy = ref(false)
const playerKeyError = ref('')
const playerKeyNotice = ref('')
const playerStreamFormat = ref<PlayerStreamFormat>('aac')

let eventSource: EventSource | null = null
let clockTimer: ReturnType<typeof setInterval> | null = null
let loadSequence = 0
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
const transportLabel = computed(() => {
  const labels: Record<string, string> = {
    idle: '未接收',
    connecting: '正在建立接收',
    ready: '信号就绪',
    playing: '正在收听',
    paused: '已暂停接收',
    buffering: '接收缓冲中',
    reconnecting: '接收重连中',
    blocked: '等待继续接收',
    unsupported: '浏览器不支持',
    error: '接收错误',
  }
  return labels[player.state.value] || player.state.value
})
const canCopyPlayerUrl = computed(() =>
  Boolean(
    selectedChannel.value
    && playerKey.value?.configured
    && playerKey.value.valid_for_new_connections,
  ),
)

function formatPlayerDate(value: string | null | undefined): string {
  if (!value) return '尚未生成'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间不可用'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function hlsUrl(slug: string): string {
  return `/hls/${encodeURIComponent(slug)}/index.m3u8`
}

function closeEvents() {
  eventSource?.close()
  eventSource = null
}

function handleEvent(message: MessageEvent<string>) {
  const event = parsePlaybackEvent(message.data)
  if (!event) return

  const type = (event.type || event.event || message.type || '').toLowerCase()
  if (type === 'heartbeat' || type === 'ping') return
  if (type.includes('playlist')) return

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
      const shouldResume = player.isReceiving.value
      player.disconnect()
      if (shouldResume) hasConnected = false
      void loadChannels()
      return
    }
    if (event.channel.slug && event.channel.slug !== previousSlug) {
      const shouldReceive = player.isReceiving.value
      player.connect(hlsUrl(event.channel.slug), shouldReceive)
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
  for (const name of ['playback', 'state', 'track', 'status', 'channel']) {
    eventSource.addEventListener(name, handleEvent as EventListener)
  }
}

async function loadChannel(channelId: string) {
  const target = channels.value.find((item) => String(item.id) === channelId)
  if (!target) return
  const sequence = ++loadSequence
  const shouldReceive = !hasConnected || player.isReceiving.value
  pageError.value = ''
  loading.value = true
  closeEvents()
  player.disconnect()

  try {
    const detail = await api.channels.get(target.id)
    if (sequence !== loadSequence) return

    channel.value = detail
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
    player.connect(hlsUrl(detail.slug), shouldReceive)
    void router.replace({ query: { ...route.query, channel: detail.slug } })
  } catch (cause) {
    if (sequence !== loadSequence) return
    pageError.value = userFacingError(cause, '频道暂时无法连接')
    if (shouldReceive) hasConnected = false
    player.disconnect()
  } finally {
    if (sequence === loadSequence) {
      loading.value = false
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

async function loadPlayerKey() {
  playerKeyLoading.value = true
  playerKeyError.value = ''
  try {
    playerKey.value = await api.auth.playerKey()
    if (!playerKey.value.lossless_available) playerStreamFormat.value = 'aac'
  } catch (cause) {
    playerKeyError.value = userFacingError(cause, '无法读取播放器连接状态')
  } finally {
    playerKeyLoading.value = false
  }
}

async function refreshPlayerKey() {
  if (playerKeyBusy.value) return
  playerKeyBusy.value = true
  playerKeyError.value = ''
  playerKeyNotice.value = ''
  try {
    playerKey.value = await api.auth.regeneratePlayerKey()
    playerKeyNotice.value = '有效日期已刷新，之前复制的播放器连接已立即失效。'
  } catch (cause) {
    playerKeyError.value = userFacingError(cause, '无法刷新播放器连接有效日期')
  } finally {
    playerKeyBusy.value = false
  }
}

async function copyPlayerUrl() {
  const target = selectedChannel.value
  if (!target || !canCopyPlayerUrl.value || playerKeyBusy.value) return
  playerKeyBusy.value = true
  playerKeyError.value = ''
  playerKeyNotice.value = ''
  try {
    const result = await api.auth.playerUrl({
      channel_id: target.id,
      stream_format: playerStreamFormat.value,
    })
    if (!navigator.clipboard?.writeText) {
      throw new Error('当前浏览器不允许安全复制')
    }
    await navigator.clipboard.writeText(result.url)
    playerKeyNotice.value = `已复制“${target.name}”的播放器连接。`
  } catch (cause) {
    playerKeyError.value = userFacingError(cause, '无法复制播放器连接')
  } finally {
    playerKeyBusy.value = false
  }
}

function changeVolume(event: Event) {
  player.setVolume(Number((event.target as HTMLInputElement).value))
}

watch(selectedId, (value, previous) => {
  if (value && value !== previous) {
    playerKeyNotice.value = ''
    void loadChannel(value)
  }
})

onMounted(() => {
  clockTimer = setInterval(() => {
    clock.value = Date.now()
  }, 250)
  void loadChannels()
  void loadPlayerKey()
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

          <div class="transport-deck" aria-label="直播接收控制">
            <div class="transport-deck__state">
              <span class="status-lamp" :class="{ 'status-lamp--active': player.isReceiving.value }" aria-hidden="true"></span>
              <span>{{ transportLabel }}</span>
            </div>
            <button
              class="transport-button transport-button--play"
              type="button"
              :aria-label="player.isReceiving.value ? '暂停接收直播流' : '继续接收直播流'"
              :disabled="loading || !selectedChannel || player.state.value === 'idle'"
              @click="player.toggleReception"
            >
              <span aria-hidden="true">{{ player.isReceiving.value ? 'Ⅱ' : '▶' }}</span>
              {{ player.isReceiving.value ? '暂停接收' : '继续接收' }}
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
              <p id="autoplay-copy">自动接收已被阻止。点击后将从频道当前直播点继续接收，而不是从缓存位置继续。</p>
            </div>
            <button class="button button--primary" type="button" @click="player.continueReception">
              继续接收直播
            </button>
          </div>
        </section>
      </template>

      <section class="player-link-panel" aria-labelledby="player-link-title" :aria-busy="playerKeyLoading || playerKeyBusy">
        <div class="player-link-panel__heading">
          <div>
            <span class="eyebrow">External player</span>
            <h2 id="player-link-title">FM / 播放器连接</h2>
          </div>
          <StatusBadge
            :status="playerKey?.valid_for_new_connections ? 'approved' : 'expired'"
            :label="playerKeyLoading ? '读取中' : playerKey?.valid_for_new_connections ? '可建立连接' : '需刷新日期'"
          />
        </div>

        <p class="player-link-panel__copy">
          复制当前频道的持续音频连接，可用于支持网络音频流的 FM 或媒体播放器。连接中不提供曲目、歌手或版权元数据。
        </p>

        <dl class="player-link-dates">
          <div>
            <dt>创建时间</dt>
            <dd>{{ formatPlayerDate(playerKey?.created_at) }}</dd>
          </div>
          <div>
            <dt>最晚建立连接时间</dt>
            <dd>{{ formatPlayerDate(playerKey?.connect_before) }}</dd>
          </div>
        </dl>

        <div class="player-link-controls">
          <div class="player-link-channel">
            <span>当前频道</span>
            <strong>{{ selectedChannel?.name || '尚未选择频道' }}</strong>
          </div>
          <label v-if="playerKey?.lossless_available" for="player-stream-format">
            音频格式
            <select id="player-stream-format" v-model="playerStreamFormat" :disabled="playerKeyBusy">
              <option value="aac">AAC · 320 kbps</option>
              <option value="flac">FLAC · 44.1 kHz / 16 bit</option>
            </select>
          </label>
          <div v-else class="player-link-format">
            <span>音频格式</span>
            <strong>AAC · 320 kbps</strong>
          </div>
          <button
            class="button button--primary"
            type="button"
            :disabled="playerKeyLoading || playerKeyBusy || !canCopyPlayerUrl"
            @click="copyPlayerUrl"
          >
            复制当前频道连接
          </button>
          <button
            class="button button--quiet"
            type="button"
            :disabled="playerKeyLoading || playerKeyBusy"
            @click="refreshPlayerKey"
          >
            刷新有效日期
          </button>
        </div>

        <InlineNotice v-if="playerKeyError" tone="danger" title="播放器连接未更新">
          {{ playerKeyError }}
        </InlineNotice>
        <p v-if="playerKeyNotice" class="player-link-panel__notice" role="status">
          {{ playerKeyNotice }}
        </p>
        <p class="player-link-panel__warning">
          刷新有效日期会重新生成内部凭据，正在使用及此前复制的连接将失效。播放凭据不会显示在页面中。
        </p>
      </section>
    </main>
    <audio ref="audio" preload="none" playsinline aria-hidden="true"></audio>
  </div>
</template>
