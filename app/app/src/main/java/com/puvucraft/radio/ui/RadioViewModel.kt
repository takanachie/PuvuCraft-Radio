package com.puvucraft.radio.ui

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.puvucraft.radio.BuildConfig
import com.puvucraft.radio.PlayerStreamFormat
import com.puvucraft.radio.data.ApiException
import com.puvucraft.radio.data.EncryptedSessionStore
import com.puvucraft.radio.data.RadioApiClient
import com.puvucraft.radio.data.RadioChannel
import com.puvucraft.radio.data.RadioUser
import com.puvucraft.radio.playback.StreamPlaybackRequest
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class RadioUiState(
    val username: String = "",
    val password: String = "",
    val user: RadioUser? = null,
    val channels: List<RadioChannel> = emptyList(),
    val selectedChannelId: Long? = null,
    val streamFormat: PlayerStreamFormat = PlayerStreamFormat.AAC,
    val losslessAvailable: Boolean = false,
    val renewalChannelId: Long? = null,
    val isRestoringSession: Boolean = false,
    val isLoggingIn: Boolean = false,
    val isLoadingChannels: Boolean = false,
    val isPreparingStream: Boolean = false,
    val error: String? = null,
    val notice: String? = null,
)

sealed interface RadioEvent {
    data class Play(val request: StreamPlaybackRequest) : RadioEvent
    data object StopPlayback : RadioEvent
}

class RadioViewModel(application: Application) : AndroidViewModel(application) {
    private val preferences = application.getSharedPreferences(
        "puvucraft_radio_preferences",
        Context.MODE_PRIVATE,
    )
    private val sessionStore = EncryptedSessionStore(application)
    private val hasSavedSession = preferences.contains(SERVER_URL_KEY)
    private val _state = MutableStateFlow(
        RadioUiState(
            streamFormat = readPreferredStreamFormat(),
            isRestoringSession = hasSavedSession,
        ),
    )
    val state: StateFlow<RadioUiState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<RadioEvent>(extraBufferCapacity = 1)
    val events: SharedFlow<RadioEvent> = _events.asSharedFlow()

    private var api: RadioApiClient? = null
    private var channelRefreshInProgress = false

    init {
        restoreSavedSession()
    }

    fun updateUsername(value: String) {
        _state.update { it.copy(username = value, error = null) }
    }

    fun updatePassword(value: String) {
        _state.update { it.copy(password = value, error = null) }
    }

    fun dismissNotice() {
        _state.update { it.copy(notice = null) }
    }

    fun login() {
        val snapshot = _state.value
        if (snapshot.isLoggingIn || snapshot.isRestoringSession) return
        val username = snapshot.username.trim()
        if (username.isEmpty() || snapshot.password.isEmpty()) {
            _state.update { it.copy(error = "请输入用户名和密码") }
            return
        }

        viewModelScope.launch {
            _state.update {
                it.copy(
                    isLoggingIn = true,
                    error = null,
                    notice = null,
                )
            }

            val client = try {
                RadioApiClient.create(
                    rawBaseUrl = DEFAULT_SERVER_URL,
                    allowCleartext = BuildConfig.DEBUG,
                    sessionStore = sessionStore,
                )
            } catch (error: IllegalArgumentException) {
                _state.update {
                    it.copy(isLoggingIn = false, error = error.message)
                }
                return@launch
            }

            try {
                val user = client.login(username, snapshot.password)
                val channels = client.channels()
                val playerKey = client.playerKey()
                api = client
                preferences.edit()
                    .putString(SERVER_URL_KEY, client.baseUrl)
                    .apply()
                _state.update {
                    it.copy(
                        password = "",
                        user = user,
                        channels = channels,
                        selectedChannelId = channels.firstOrNull()?.id,
                        streamFormat = preferredStreamFormat(
                            losslessAvailable = playerKey.losslessAvailable,
                        ),
                        losslessAvailable = playerKey.losslessAvailable,
                        isRestoringSession = false,
                        isLoggingIn = false,
                        error = null,
                    )
                }
            } catch (error: Exception) {
                client.forgetSession()
                _state.update {
                    it.copy(
                        isLoggingIn = false,
                        error = error.userFacingMessage(),
                    )
                }
            }
        }
    }

    fun selectChannel(channelId: Long) {
        if (_state.value.channels.none { it.id == channelId }) return
        _state.update {
            it.copy(
                selectedChannelId = channelId,
                error = null,
                notice = null,
            )
        }
    }

    fun selectStreamFormat(streamFormat: PlayerStreamFormat) {
        if (
            streamFormat == PlayerStreamFormat.FLAC &&
            !_state.value.losslessAvailable
        ) {
            return
        }
        preferences.edit()
            .putString(STREAM_FORMAT_KEY, streamFormat.wireValue)
            .apply()
        _state.update {
            it.copy(
                streamFormat = streamFormat,
                error = null,
            )
        }
    }

    fun refreshChannels() {
        refreshChannels(showLoading = true)
    }

    fun refreshChannelsInBackground() {
        refreshChannels(showLoading = false)
    }

    private fun refreshChannels(showLoading: Boolean) {
        val client = api ?: return
        if (channelRefreshInProgress) return
        channelRefreshInProgress = true

        viewModelScope.launch {
            if (showLoading) {
                _state.update {
                    it.copy(isLoadingChannels = true, error = null)
                }
            }
            try {
                val channels = client.channels()
                _state.update { current ->
                    val selected = current.selectedChannelId
                        ?.takeIf { id -> channels.any { it.id == id } }
                        ?: channels.firstOrNull()?.id
                    current.copy(
                        channels = channels,
                        selectedChannelId = selected,
                        isLoadingChannels = if (showLoading) {
                            false
                        } else {
                            current.isLoadingChannels
                        },
                    )
                }
            } catch (error: Exception) {
                if (error.isUnauthorized()) {
                    expireSession()
                } else if (showLoading) {
                    _state.update {
                        it.copy(
                            isLoadingChannels = false,
                            error = error.userFacingMessage(),
                        )
                    }
                }
            } finally {
                channelRefreshInProgress = false
            }
        }
    }

    fun requestPlayback(channelId: Long) {
        val channel = _state.value.channels.firstOrNull { it.id == channelId } ?: return
        val client = api ?: return
        if (_state.value.isPreparingStream) return

        viewModelScope.launch {
            _state.update {
                it.copy(
                    isPreparingStream = true,
                    error = null,
                    notice = "正在建立直播连接…",
                )
            }
            try {
                val key = client.playerKey()
                val streamFormat = allowedStreamFormat(
                    requested = _state.value.streamFormat,
                    losslessAvailable = key.losslessAvailable,
                )
                _state.update {
                    it.copy(
                        streamFormat = streamFormat,
                        losslessAvailable = key.losslessAvailable,
                    )
                }
                if (!key.configured || !key.validForNewConnections) {
                    _state.update {
                        it.copy(
                            renewalChannelId = channel.id,
                            isPreparingStream = false,
                            notice = null,
                        )
                    }
                    return@launch
                }
                issuePlayback(client, channel, streamFormat)
            } catch (error: Exception) {
                when {
                    error.isUnauthorized() -> expireSession()
                    error is ApiException &&
                        error.code in PLAYER_KEY_RENEWAL_CODES -> {
                        _state.update {
                            it.copy(
                                renewalChannelId = channel.id,
                                isPreparingStream = false,
                                notice = null,
                            )
                        }
                    }
                    else -> {
                        _state.update {
                            it.copy(
                                isPreparingStream = false,
                                notice = null,
                                error = error.userFacingMessage(),
                            )
                        }
                    }
                }
            }
        }
    }

    fun renewPlayerKeyAndPlay() {
        val snapshot = _state.value
        val channel = snapshot.channels.firstOrNull {
            it.id == snapshot.renewalChannelId
        } ?: run {
            _state.update { it.copy(renewalChannelId = null) }
            return
        }
        val client = api ?: return
        if (snapshot.isPreparingStream) return

        viewModelScope.launch {
            _state.update {
                it.copy(
                    renewalChannelId = null,
                    isPreparingStream = true,
                    error = null,
                    notice = "正在刷新播放凭据…",
                )
            }
            try {
                val key = client.regeneratePlayerKey()
                val streamFormat = allowedStreamFormat(
                    requested = _state.value.streamFormat,
                    losslessAvailable = key.losslessAvailable,
                )
                _state.update {
                    it.copy(
                        streamFormat = streamFormat,
                        losslessAvailable = key.losslessAvailable,
                    )
                }
                issuePlayback(client, channel, streamFormat)
            } catch (error: Exception) {
                if (error.isUnauthorized()) {
                    expireSession()
                } else {
                    _state.update {
                        it.copy(
                            isPreparingStream = false,
                            notice = null,
                            error = error.userFacingMessage(),
                        )
                    }
                }
            }
        }
    }

    fun cancelPlayerKeyRenewal() {
        _state.update { it.copy(renewalChannelId = null) }
    }

    fun logout() {
        val client = api
        _state.update {
            it.copy(
                isLoadingChannels = false,
                isPreparingStream = false,
                notice = "正在断开会话…",
            )
        }
        viewModelScope.launch {
            runCatching { client?.logout() }
            clearLocalSession("已安全退出")
        }
    }

    private suspend fun issuePlayback(
        client: RadioApiClient,
        channel: RadioChannel,
        streamFormat: PlayerStreamFormat,
    ) {
        val ticket = client.createStreamTicket(channel.id, streamFormat)
        _events.emit(
            RadioEvent.Play(
                StreamPlaybackRequest(
                    url = ticket.url,
                    channelId = channel.id,
                    channelName = channel.name,
                    description = channel.description,
                    streamFormat = ticket.streamFormat,
                ),
            ),
        )
        _state.update {
            it.copy(
                isPreparingStream = false,
                notice = when (ticket.streamFormat) {
                    PlayerStreamFormat.AAC -> "AAC 320 kbps 直播连接已建立"
                    PlayerStreamFormat.FLAC -> "FLAC 无损直播连接已建立"
                },
            )
        }
    }

    private suspend fun expireSession() {
        clearLocalSession("登录会话已失效，请重新登录", asError = true)
    }

    private suspend fun clearLocalSession(message: String, asError: Boolean = false) {
        api?.forgetSession()
        sessionStore.clear()
        preferences.edit().remove(SERVER_URL_KEY).apply()
        api = null
        _events.emit(RadioEvent.StopPlayback)
        _state.update {
            it.copy(
                password = "",
                user = null,
                channels = emptyList(),
                selectedChannelId = null,
                streamFormat = readPreferredStreamFormat(),
                losslessAvailable = false,
                renewalChannelId = null,
                isRestoringSession = false,
                isLoggingIn = false,
                isLoadingChannels = false,
                isPreparingStream = false,
                error = message.takeIf { asError },
                notice = message.takeUnless { asError },
            )
        }
    }

    private fun restoreSavedSession() {
        if (!hasSavedSession) {
            _state.update { it.copy(isRestoringSession = false) }
            return
        }

        viewModelScope.launch {
            val client = try {
                RadioApiClient.create(
                    rawBaseUrl = DEFAULT_SERVER_URL,
                    allowCleartext = BuildConfig.DEBUG,
                    sessionStore = sessionStore,
                    restoreSession = true,
                )
            } catch (_: IllegalArgumentException) {
                sessionStore.clear()
                _state.update { it.copy(isRestoringSession = false) }
                return@launch
            }

            if (!client.hasRestoredSession()) {
                preferences.edit().remove(SERVER_URL_KEY).apply()
                _state.update { it.copy(isRestoringSession = false) }
                return@launch
            }

            try {
                val user = client.me()
                val channels = client.channels()
                val playerKey = client.playerKey()
                api = client
                _state.update {
                    it.copy(
                        username = user.username,
                        password = "",
                        user = user,
                        channels = channels,
                        selectedChannelId = channels.firstOrNull()?.id,
                        streamFormat = preferredStreamFormat(
                            losslessAvailable = playerKey.losslessAvailable,
                        ),
                        losslessAvailable = playerKey.losslessAvailable,
                        isRestoringSession = false,
                        error = null,
                        notice = "已恢复加密登录会话",
                    )
                }
            } catch (error: Exception) {
                if (error.isUnauthorized()) {
                    client.forgetSession()
                    preferences.edit().remove(SERVER_URL_KEY).apply()
                    _state.update {
                        it.copy(
                            isRestoringSession = false,
                            error = "登录会话已过期，请重新登录",
                        )
                    }
                } else {
                    _state.update {
                        it.copy(
                            isRestoringSession = false,
                            error = "暂时无法恢复登录：${error.userFacingMessage()}",
                        )
                    }
                }
            }
        }
    }

    private fun readPreferredStreamFormat(): PlayerStreamFormat =
        PlayerStreamFormat.fromWireValue(
            preferences.getString(STREAM_FORMAT_KEY, null),
        ) ?: PlayerStreamFormat.AAC

    private fun preferredStreamFormat(
        losslessAvailable: Boolean,
    ): PlayerStreamFormat = allowedStreamFormat(
        requested = readPreferredStreamFormat(),
        losslessAvailable = losslessAvailable,
    )

    private fun allowedStreamFormat(
        requested: PlayerStreamFormat,
        losslessAvailable: Boolean,
    ): PlayerStreamFormat = if (
        requested == PlayerStreamFormat.FLAC && !losslessAvailable
    ) {
        PlayerStreamFormat.AAC
    } else {
        requested
    }

    private fun Throwable.isUnauthorized(): Boolean =
        this is ApiException && status == 401

    private fun Throwable.userFacingMessage(): String = when {
        this is IllegalArgumentException -> message ?: "输入内容不正确"
        this is ApiException && code == "invalid_credentials" -> "用户名或密码不正确"
        this is ApiException && code == "account_not_approved" -> "账号尚未通过管理员审批"
        this is ApiException && code == "account_unavailable" -> "账号当前不可用"
        this is ApiException && status == 429 -> "操作过于频繁，请稍后重试"
        this is ApiException -> message
        else -> message ?: "操作失败，请稍后重试"
    }

    companion object {
        private const val DEFAULT_SERVER_URL = "https://www.phi-s.tech"
        private const val SERVER_URL_KEY = "server_url"
        private const val STREAM_FORMAT_KEY = "stream_format"
        private val PLAYER_KEY_RENEWAL_CODES = setOf(
            "player_key_missing",
            "player_key_expired",
        )
    }
}
