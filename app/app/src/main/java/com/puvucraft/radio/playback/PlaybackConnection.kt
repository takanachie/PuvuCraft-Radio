package com.puvucraft.radio.playback

import android.content.ComponentName
import android.content.Context
import android.os.Bundle
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.puvucraft.radio.PlayerStreamFormat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class StreamPlaybackRequest(
    val url: String,
    val channelId: Long,
    val channelName: String,
    val description: String?,
    val streamFormat: PlayerStreamFormat,
)

data class PlaybackUiState(
    val controllerReady: Boolean = false,
    val hasMedia: Boolean = false,
    val channelId: Long? = null,
    val channelName: String? = null,
    val streamFormat: PlayerStreamFormat? = null,
    val isPlaying: Boolean = false,
    val isBuffering: Boolean = false,
    val volumeLevel: Float = LogarithmicVolume.DEFAULT_LEVEL,
    val error: String? = null,
)

class PlaybackConnection(context: Context) {
    private val appContext = context.applicationContext
    private val volumeStore = PlaybackVolumeStore(appContext)
    private val mainExecutor = ContextCompat.getMainExecutor(appContext)
    private val sessionToken = SessionToken(
        appContext,
        ComponentName(appContext, PlaybackService::class.java),
    )
    private val controllerFuture = MediaController.Builder(appContext, sessionToken)
        .buildAsync()

    private val _state = MutableStateFlow(
        PlaybackUiState(volumeLevel = volumeStore.readLevel()),
    )
    val state: StateFlow<PlaybackUiState> = _state.asStateFlow()

    private var controller: MediaController? = null
    private var pendingPlayback: StreamPlaybackRequest? = null

    private val listener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) {
            updateState(player)
        }

        override fun onPlayerError(error: PlaybackException) {
            _state.value = _state.value.copy(
                isPlaying = false,
                isBuffering = false,
                error = "直播信号中断，可点击重新连接",
            )
        }
    }

    init {
        controllerFuture.addListener(
            {
                runCatching { controllerFuture.get() }
                    .onSuccess { connectedController ->
                        controller = connectedController
                        connectedController.addListener(listener)
                        updateState(connectedController)
                        pendingPlayback?.also {
                            pendingPlayback = null
                            playNow(connectedController, it)
                        }
                    }
                    .onFailure {
                        _state.value = _state.value.copy(
                            controllerReady = false,
                            error = "无法连接后台播放器",
                        )
                    }
            },
            mainExecutor,
        )
    }

    fun play(request: StreamPlaybackRequest) {
        mainExecutor.execute {
            val activeController = controller
            if (activeController == null) {
                pendingPlayback = request
                _state.value = _state.value.copy(
                    channelId = request.channelId,
                    channelName = request.channelName,
                    streamFormat = request.streamFormat,
                    isBuffering = true,
                    error = null,
                )
            } else {
                playNow(activeController, request)
            }
        }
    }

    fun toggle() {
        mainExecutor.execute {
            val activeController = controller ?: return@execute
            if (!activeController.hasMediaItem()) return@execute

            if (
                activeController.isPlaying ||
                activeController.playWhenReady ||
                activeController.playbackState == Player.STATE_BUFFERING
            ) {
                activeController.stop()
            } else {
                if (activeController.playbackState == Player.STATE_IDLE) {
                    activeController.prepare()
                }
                activeController.play()
            }
            updateState(activeController)
        }
    }

    fun setVolume(volumeLevel: Float) {
        mainExecutor.execute {
            val safeLevel = LogarithmicVolume.normalizeLevel(volumeLevel)
            volumeStore.saveLevel(safeLevel)
            controller?.volume = LogarithmicVolume.levelToPlayerGain(safeLevel)
            _state.value = _state.value.copy(volumeLevel = safeLevel)
        }
    }

    fun toggleMute() {
        val nextLevel = if (_state.value.volumeLevel > 0f) {
            0f
        } else {
            volumeStore.readLastAudibleLevel()
        }
        setVolume(nextLevel)
    }

    fun stopAndClear() {
        mainExecutor.execute {
            disconnectNow()
        }
    }

    fun forceDisconnect(onDisconnected: () -> Unit) {
        mainExecutor.execute {
            disconnectNow()
            onDisconnected()
        }
    }

    fun release() {
        controller?.removeListener(listener)
        controller = null
        MediaController.releaseFuture(controllerFuture)
    }

    private fun playNow(
        activeController: MediaController,
        request: StreamPlaybackRequest,
    ) {
        val metadata = MediaMetadata.Builder()
            .setTitle(request.channelName)
            .setArtist("PuvuCraft · PuvuFM")
            .setDescription(request.description)
            .setMediaType(MediaMetadata.MEDIA_TYPE_RADIO_STATION)
            .setIsPlayable(true)
            .setExtras(
                Bundle().apply {
                    putString(STREAM_FORMAT_EXTRA, request.streamFormat.wireValue)
                },
            )
            .build()
        val item = MediaItem.Builder()
            .setMediaId(request.channelId.toString())
            .setUri(request.url)
            .setMimeType(
                when (request.streamFormat) {
                    PlayerStreamFormat.AAC -> MimeTypes.AUDIO_AAC
                    PlayerStreamFormat.FLAC -> MimeTypes.AUDIO_FLAC
                },
            )
            .setMediaMetadata(metadata)
            .build()

        activeController.setMediaItem(item)
        activeController.prepare()
        activeController.play()
        updateState(activeController)
    }

    private fun disconnectNow() {
        pendingPlayback = null
        controller?.run {
            stop()
            clearMediaItems()
            updateState(this)
        }
        if (controller == null) {
            _state.value = PlaybackUiState(
                volumeLevel = volumeStore.readLevel(),
            )
            return
        }
        _state.value = _state.value.copy(
            hasMedia = false,
            channelId = null,
            channelName = null,
            streamFormat = null,
            isPlaying = false,
            isBuffering = false,
            error = null,
        )
    }

    private fun updateState(player: Player) {
        val mediaItem = player.currentMediaItem
        _state.value = PlaybackUiState(
            controllerReady = true,
            hasMedia = mediaItem != null,
            channelId = mediaItem?.mediaId?.toLongOrNull(),
            channelName = mediaItem?.mediaMetadata?.title?.toString(),
            streamFormat = PlayerStreamFormat.fromWireValue(
                mediaItem?.mediaMetadata?.extras?.getString(STREAM_FORMAT_EXTRA),
            ),
            isPlaying = player.isPlaying,
            isBuffering = player.playbackState == Player.STATE_BUFFERING,
            volumeLevel = LogarithmicVolume.playerGainToLevel(player.volume),
            error = player.playerError?.let { "直播信号中断，可点击重新连接" },
        )
    }

    private fun Player.hasMediaItem(): Boolean = currentMediaItem != null

    private companion object {
        const val STREAM_FORMAT_EXTRA = "com.puvucraft.puvufm.STREAM_FORMAT"
    }
}
