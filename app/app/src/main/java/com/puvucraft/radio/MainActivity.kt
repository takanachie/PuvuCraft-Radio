package com.puvucraft.radio

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.activity.compose.setContent
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.puvucraft.radio.playback.PlaybackConnection
import com.puvucraft.radio.ui.PuvuRadioApp
import com.puvucraft.radio.ui.RadioEvent
import com.puvucraft.radio.ui.RadioViewModel
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {
    private lateinit var playback: PlaybackConnection

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        playback = PlaybackConnection(applicationContext)

        setContent {
            val radioViewModel: RadioViewModel = viewModel()
            val uiState by radioViewModel.state.collectAsStateWithLifecycle()
            val playbackState by playback.state.collectAsStateWithLifecycle()
            val notificationPermission = rememberLauncherForActivityResult(
                ActivityResultContracts.RequestPermission(),
            ) {
                // Playback remains available when notification permission is declined.
            }

            LaunchedEffect(radioViewModel) {
                radioViewModel.events.collect { event ->
                    when (event) {
                        is RadioEvent.Play -> {
                            if (
                                Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                                ContextCompat.checkSelfPermission(
                                    this@MainActivity,
                                    Manifest.permission.POST_NOTIFICATIONS,
                                ) != PackageManager.PERMISSION_GRANTED
                            ) {
                                notificationPermission.launch(
                                    Manifest.permission.POST_NOTIFICATIONS,
                                )
                            }
                            playback.play(event.request)
                        }

                        RadioEvent.StopPlayback -> playback.stopAndClear()
                    }
                }
            }

            LaunchedEffect(radioViewModel) {
                this@MainActivity.lifecycle.repeatOnLifecycle(
                    Lifecycle.State.RESUMED,
                ) {
                    while (true) {
                        radioViewModel.refreshChannelsInBackground()
                        delay(CHANNEL_REFRESH_INTERVAL_MILLIS)
                    }
                }
            }

            PuvuRadioApp(
                uiState = uiState,
                playbackState = playbackState,
                onUsernameChange = radioViewModel::updateUsername,
                onPasswordChange = radioViewModel::updatePassword,
                onLogin = radioViewModel::login,
                onLogout = radioViewModel::logout,
                onRefresh = radioViewModel::refreshChannels,
                onSelectChannel = radioViewModel::selectChannel,
                onStreamFormatChange = radioViewModel::selectStreamFormat,
                onListen = radioViewModel::requestPlayback,
                onTogglePlayback = playback::toggle,
                onVolumeChange = playback::setVolume,
                onToggleMute = playback::toggleMute,
                onDismissNotice = radioViewModel::dismissNotice,
                onRenewPlayerKey = radioViewModel::renewPlayerKeyAndPlay,
                onCancelPlayerKeyRenewal = radioViewModel::cancelPlayerKeyRenewal,
            )
        }
    }

    override fun onDestroy() {
        playback.release()
        super.onDestroy()
    }

    private companion object {
        const val CHANNEL_REFRESH_INTERVAL_MILLIS = 5_000L
    }
}
