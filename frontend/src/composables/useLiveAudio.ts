import type Hls from 'hls.js'
import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

export type AudioTransportState =
  | 'idle'
  | 'connecting'
  | 'ready'
  | 'playing'
  | 'paused'
  | 'buffering'
  | 'reconnecting'
  | 'blocked'
  | 'unsupported'
  | 'error'

function savedVolume(): number {
  try {
    const value = Number(localStorage.getItem('radio_volume'))
    return Number.isFinite(value) && value >= 0 && value <= 1 ? value : 0.78
  } catch {
    return 0.78
  }
}

export function useLiveAudio(audioElement: Ref<HTMLAudioElement | null>) {
  const state = ref<AudioTransportState>('idle')
  const isPlaying = ref(false)
  const wantsPlayback = ref(false)
  const autoplayBlocked = ref(false)
  const muted = ref(false)
  const volume = ref(savedVolume())
  const error = ref('')

  let hls: Hls | null = null
  let currentUrl = ''
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let retryCount = 0
  let sourceGeneration = 0

  function clearRetry() {
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = null
  }

  function destroyEngine(clearMedia = true, invalidateLoad = true) {
    if (invalidateLoad) sourceGeneration += 1
    clearRetry()
    if (hls) {
      hls.destroy()
      hls = null
    }
    if (clearMedia && audioElement.value) {
      audioElement.value.pause()
      audioElement.value.removeAttribute('src')
      audioElement.value.load()
    }
  }

  async function attemptPlay() {
    const audio = audioElement.value
    if (!audio || !currentUrl || !wantsPlayback.value) return
    try {
      await audio.play()
      autoplayBlocked.value = false
      error.value = ''
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'NotAllowedError') {
        autoplayBlocked.value = true
        state.value = 'blocked'
        return
      }
      if (state.value !== 'connecting') {
        state.value = 'error'
        error.value = '浏览器无法开始播放此直播流'
      }
    }
  }

  function scheduleReload() {
    if (!wantsPlayback.value || !currentUrl || retryTimer) return
    state.value = 'reconnecting'
    const delay = Math.min(8000, 750 * 2 ** retryCount)
    retryCount += 1
    retryTimer = setTimeout(() => {
      retryTimer = null
      void loadSource()
    }, delay)
  }

  async function loadSource() {
    const audio = audioElement.value
    if (!audio || !currentUrl) return

    const generation = ++sourceGeneration
    destroyEngine(false, false)
    state.value = 'connecting'
    error.value = ''

    const nativeHls = audio.canPlayType('application/vnd.apple.mpegurl')
    if (nativeHls) {
      audio.src = currentUrl
      audio.load()
      if (wantsPlayback.value) void attemptPlay()
      return
    }

    let HlsClass: typeof import('hls.js').default
    try {
      HlsClass = (await import('hls.js')).default
    } catch {
      if (generation !== sourceGeneration) return
      state.value = navigator.onLine ? 'error' : 'reconnecting'
      error.value = '播放器组件加载失败，请检查网络后重试'
      scheduleReload()
      return
    }
    if (generation !== sourceGeneration || audio !== audioElement.value || !currentUrl) return
    if (!HlsClass.isSupported()) {
      state.value = 'unsupported'
      error.value = '此浏览器不支持 HLS 直播播放'
      return
    }

    hls = new HlsClass({
      enableWorker: true,
      lowLatencyMode: false,
      liveSyncDurationCount: 2,
      liveMaxLatencyDurationCount: 5,
      backBufferLength: 0,
      xhrSetup(xhr) {
        xhr.withCredentials = true
      },
    })
    hls.attachMedia(audio)
    hls.on(HlsClass.Events.MEDIA_ATTACHED, () => hls?.loadSource(currentUrl))
    hls.on(HlsClass.Events.MANIFEST_PARSED, () => {
      retryCount = 0
      state.value = 'ready'
      if (wantsPlayback.value) void attemptPlay()
    })
    hls.on(HlsClass.Events.ERROR, (_event, data) => {
      if (!data.fatal) return
      const responseCode = 'response' in data ? data.response?.code : undefined
      if (responseCode === 401) {
        wantsPlayback.value = false
        state.value = 'error'
        error.value = '登录会话已失效，请重新登录'
        hls?.stopLoad()
        window.dispatchEvent(new CustomEvent('radio:unauthorized'))
        return
      }
      if (data.type === HlsClass.ErrorTypes.MEDIA_ERROR && hls) {
        state.value = 'buffering'
        hls.recoverMediaError()
        return
      }
      error.value = data.type === HlsClass.ErrorTypes.NETWORK_ERROR
        ? '直播信号暂时中断，正在重连'
        : '播放器遇到无法恢复的流错误'
      scheduleReload()
    })
  }

  function connect(url: string, autoplay = false) {
    currentUrl = url
    wantsPlayback.value = autoplay
    autoplayBlocked.value = false
    retryCount = 0
    void loadSource()
  }

  function disconnect() {
    wantsPlayback.value = false
    currentUrl = ''
    destroyEngine()
    isPlaying.value = false
    state.value = 'idle'
  }

  function resumeLive() {
    if (!currentUrl) return
    wantsPlayback.value = true
    autoplayBlocked.value = false
    error.value = ''

    const audio = audioElement.value
    if (hls && audio) {
      state.value = 'connecting'
      hls.stopLoad()
      hls.loadSource(currentUrl)
      hls.startLoad(-1)
      void attemptPlay()
      return
    }

    void loadSource()
  }

  function pause() {
    wantsPlayback.value = false
    autoplayBlocked.value = false
    clearRetry()
    audioElement.value?.pause()
    if (hls) hls.stopLoad()
    else if (audioElement.value) {
      audioElement.value.removeAttribute('src')
      audioElement.value.load()
    }
    state.value = 'paused'
  }

  function togglePlayback() {
    if (isPlaying.value || (wantsPlayback.value && !autoplayBlocked.value)) pause()
    else resumeLive()
  }

  function toggleMute() {
    const audio = audioElement.value
    muted.value = !muted.value
    if (audio) audio.muted = muted.value
  }

  function setVolume(value: number) {
    volume.value = Math.min(1, Math.max(0, value))
    if (audioElement.value) audioElement.value.volume = volume.value
    try {
      localStorage.setItem('radio_volume', String(volume.value))
    } catch {
      // Volume persistence is optional in restricted browsing modes.
    }
  }

  function handlePlaying() {
    isPlaying.value = true
    retryCount = 0
    state.value = 'playing'
    autoplayBlocked.value = false
  }

  function handlePause() {
    isPlaying.value = false
    if (!wantsPlayback.value) state.value = 'paused'
  }

  function handleWaiting() {
    if (wantsPlayback.value) state.value = 'buffering'
  }

  function handleCanPlay() {
    if (state.value !== 'playing') state.value = 'ready'
    if (wantsPlayback.value) void attemptPlay()
  }

  function handleError() {
    if (wantsPlayback.value) scheduleReload()
  }

  function bind(audio: HTMLAudioElement) {
    audio.volume = volume.value
    audio.muted = muted.value
    audio.addEventListener('playing', handlePlaying)
    audio.addEventListener('pause', handlePause)
    audio.addEventListener('waiting', handleWaiting)
    audio.addEventListener('stalled', handleWaiting)
    audio.addEventListener('canplay', handleCanPlay)
    audio.addEventListener('error', handleError)
    if (currentUrl) void loadSource()
  }

  function unbind(audio: HTMLAudioElement) {
    audio.removeEventListener('playing', handlePlaying)
    audio.removeEventListener('pause', handlePause)
    audio.removeEventListener('waiting', handleWaiting)
    audio.removeEventListener('stalled', handleWaiting)
    audio.removeEventListener('canplay', handleCanPlay)
    audio.removeEventListener('error', handleError)
  }

  function handleOnline() {
    if (wantsPlayback.value && currentUrl) void loadSource()
  }

  function handleOffline() {
    if (wantsPlayback.value) state.value = 'reconnecting'
  }

  watch(audioElement, (next, previous) => {
    if (previous) unbind(previous)
    if (next) bind(next)
  }, { immediate: true })

  if (typeof window !== 'undefined') {
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
  }

  onBeforeUnmount(() => {
    if (audioElement.value) unbind(audioElement.value)
    destroyEngine()
    if (typeof window !== 'undefined') {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  })

  return {
    state,
    isPlaying,
    wantsPlayback,
    autoplayBlocked,
    muted,
    volume,
    error,
    connect,
    disconnect,
    resumeLive,
    pause,
    togglePlayback,
    toggleMute,
    setVolume,
  }
}
