package com.puvucraft.radio.playback

import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import com.puvucraft.radio.BuildConfig
import com.puvucraft.radio.data.DEFAULT_RADIO_SERVER_URL
import com.puvucraft.radio.data.EncryptedSessionStore
import com.puvucraft.radio.data.RadioApiClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class PlaybackService : MediaSessionService() {
    private var mediaSession: MediaSession? = null
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var activityHeartbeatJob: Job? = null

    @UnstableApi
    override fun onCreate() {
        super.onCreate()

        val hardwareFirstCodecSelector = MediaCodecSelector {
                mimeType,
                requiresSecureDecoder,
                requiresTunnelingDecoder,
            ->
            MediaCodecSelector.DEFAULT.getDecoderInfos(
                mimeType,
                requiresSecureDecoder,
                requiresTunnelingDecoder,
            ).sortedByDescending { it.hardwareAccelerated }
        }
        val renderersFactory = DefaultRenderersFactory(this)
            .setMediaCodecSelector(hardwareFirstCodecSelector)
            .setEnableDecoderFallback(true)

        val player = ExoPlayer.Builder(this, renderersFactory)
            .build()
            .apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(C.USAGE_MEDIA)
                        .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                        .build(),
                    true,
                )
                setHandleAudioBecomingNoisy(true)
                volume = LogarithmicVolume.levelToPlayerGain(
                    PlaybackVolumeStore(this@PlaybackService).readLevel(),
                )
            }

        mediaSession = MediaSession.Builder(this, player)
            .setCallback(SecureConnectionCallback())
            .build()
        startActivityHeartbeat()
    }

    override fun onGetSession(
        controllerInfo: MediaSession.ControllerInfo,
    ): MediaSession? = mediaSession

    override fun onDestroy() {
        activityHeartbeatJob?.cancel()
        activityHeartbeatJob = null
        serviceScope.cancel()
        mediaSession?.run {
            player.release()
            release()
        }
        mediaSession = null
        super.onDestroy()
    }

    private fun startActivityHeartbeat() {
        activityHeartbeatJob = serviceScope.launch {
            while (isActive) {
                delay(ACTIVITY_HEARTBEAT_INTERVAL_MILLIS)
                sendActivityHeartbeat()
            }
        }
    }

    private suspend fun sendActivityHeartbeat() {
        val sessionStore = EncryptedSessionStore(this)
        val client = runCatching {
            RadioApiClient.create(
                rawBaseUrl = DEFAULT_RADIO_SERVER_URL,
                allowCleartext = BuildConfig.DEBUG,
                sessionStore = sessionStore,
                restoreSession = true,
                persistSessionChanges = false,
            )
        }.getOrNull() ?: return
        if (!client.hasRestoredSession()) return

        // A heartbeat must never interrupt playback; foreground UI requests will
        // surface authentication and network errors when the app is reopened.
        runCatching { client.me() }
    }

    private inner class SecureConnectionCallback : MediaSession.Callback {
        @UnstableApi
        override fun onConnect(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
        ): MediaSession.ConnectionResult {
            if (controller.packageName != packageName && !controller.isTrusted) {
                return MediaSession.ConnectionResult.reject()
            }
            return super.onConnect(session, controller)
        }
    }

    private companion object {
        const val ACTIVITY_HEARTBEAT_INTERVAL_MILLIS = 60_000L
    }
}
