package com.puvucraft.radio.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.puvucraft.radio.PlayerStreamFormat
import com.puvucraft.radio.data.RadioChannel
import com.puvucraft.radio.data.RadioUser
import com.puvucraft.radio.data.TrackSummary
import com.puvucraft.radio.playback.PlaybackUiState
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

@Composable
fun PuvuRadioApp(
    uiState: RadioUiState,
    playbackState: PlaybackUiState,
    onUsernameChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onLogin: () -> Unit,
    onLogout: () -> Unit,
    onRefresh: () -> Unit,
    onSelectChannel: (Long) -> Unit,
    onStreamFormatChange: (PlayerStreamFormat) -> Unit,
    onListen: (Long) -> Unit,
    onTogglePlayback: () -> Unit,
    onVolumeChange: (Float) -> Unit,
    onToggleMute: () -> Unit,
    onDismissNotice: () -> Unit,
    onRenewPlayerKey: () -> Unit,
    onCancelPlayerKeyRenewal: () -> Unit,
) {
    PuvuCraftRadioTheme {
        ConsoleBackdrop {
            if (uiState.user == null) {
                LoginScreen(
                    state = uiState,
                    onUsernameChange = onUsernameChange,
                    onPasswordChange = onPasswordChange,
                    onLogin = onLogin,
                )
            } else {
                ListenerScreen(
                    state = uiState,
                    playbackState = playbackState,
                    onLogout = onLogout,
                    onRefresh = onRefresh,
                    onSelectChannel = onSelectChannel,
                    onStreamFormatChange = onStreamFormatChange,
                    onListen = onListen,
                    onTogglePlayback = onTogglePlayback,
                    onVolumeChange = onVolumeChange,
                    onToggleMute = onToggleMute,
                    onDismissNotice = onDismissNotice,
                )
            }

            if (uiState.renewalChannelId != null) {
                AlertDialog(
                    onDismissRequest = onCancelPlayerKeyRenewal,
                    containerColor = Ink2,
                    titleContentColor = Paper,
                    textContentColor = Muted,
                    title = { Text("播放凭据需要刷新") },
                    text = {
                        Text(
                            "当前播放凭据已过期。刷新后可以继续收听，但会断开同一账号正在使用的外部播放器连接。",
                        )
                    },
                    confirmButton = {
                        Button(
                            onClick = onRenewPlayerKey,
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Phosphor,
                                contentColor = Ink0,
                            ),
                        ) {
                            Text("刷新并收听")
                        }
                    },
                    dismissButton = {
                        TextButton(onClick = onCancelPlayerKeyRenewal) {
                            Text("取消")
                        }
                    },
                )
            }
        }
    }
}

@Composable
private fun ConsoleBackdrop(content: BoxScopeContent) {
    val gridColor = Color.White.copy(alpha = 0.018f)
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.linearGradient(
                    colors = listOf(Color(0xFF090C0D), Ink0, Color(0xFF0A0D0E)),
                ),
            )
            .background(
                Brush.radialGradient(
                    colors = listOf(
                        Phosphor.copy(alpha = 0.055f),
                        Ink0.copy(alpha = 0f),
                    ),
                    radius = 900f,
                ),
            )
            .drawBehind {
                val step = 32.dp.toPx()
                var x = 0f
                while (x <= size.width) {
                    drawLine(gridColor, start = androidx.compose.ui.geometry.Offset(x, 0f), end = androidx.compose.ui.geometry.Offset(x, size.height))
                    x += step
                }
                var y = 0f
                while (y <= size.height) {
                    drawLine(gridColor, start = androidx.compose.ui.geometry.Offset(0f, y), end = androidx.compose.ui.geometry.Offset(size.width, y))
                    y += step
                }
            },
        content = content,
    )
}

private typealias BoxScopeContent = @Composable androidx.compose.foundation.layout.BoxScope.() -> Unit

@Composable
private fun LoginScreen(
    state: RadioUiState,
    onUsernameChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onLogin: () -> Unit,
) {
    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .windowInsetsPadding(WindowInsets.safeDrawing)
            .imePadding(),
    ) {
        val wide = maxWidth >= 760.dp && maxHeight >= 560.dp
        val pagePadding = responsivePadding(maxWidth)
        val scroll = rememberScrollState()

        if (wide) {
            Row(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(pagePadding),
                horizontalArrangement = Arrangement.spacedBy(40.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SignalIntroduction(
                    modifier = Modifier.weight(1f),
                    expanded = true,
                )
                LoginPanel(
                    state = state,
                    onUsernameChange = onUsernameChange,
                    onPasswordChange = onPasswordChange,
                    onLogin = onLogin,
                    modifier = Modifier
                        .weight(0.82f)
                        .widthIn(max = 520.dp)
                        .verticalScroll(scroll),
                )
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(scroll)
                    .padding(pagePadding),
                verticalArrangement = Arrangement.Center,
            ) {
                SignalIntroduction(expanded = false)
                Spacer(Modifier.height(24.dp))
                LoginPanel(
                    state = state,
                    onUsernameChange = onUsernameChange,
                    onPasswordChange = onPasswordChange,
                    onLogin = onLogin,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@Composable
private fun SignalIntroduction(
    modifier: Modifier = Modifier,
    expanded: Boolean,
) {
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(if (expanded) 28.dp else 14.dp),
    ) {
        BrandMark(compact = !expanded)
        if (expanded) {
            RadioSignalIllustration(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(220.dp),
            )
        }
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Eyebrow("LIVE TRANSMISSION / PUVU FM")
            Text(
                text = if (expanded) "保持同步。\n接收正在发生的声音。" else "接入正在发生的声音。",
                style = if (expanded) {
                    MaterialTheme.typography.displayMedium
                } else {
                    MaterialTheme.typography.headlineLarge
                },
                color = Paper,
            )
            Text(
                text = "使用已获批准的账号连接你的 PuvuCraft Radio 服务器。",
                style = MaterialTheme.typography.bodyMedium,
                color = Muted,
            )
        }
    }
}

@Composable
private fun LoginPanel(
    state: RadioUiState,
    onUsernameChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onLogin: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val focusManager = LocalFocusManager.current
    var passwordVisible by remember { mutableStateOf(false) }

    ConsolePanel(
        modifier = modifier,
        accent = Amber,
    ) {
        Column(
            modifier = Modifier.padding(22.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Eyebrow("OPERATOR ACCESS / 01")
                Text(
                    text = "接入直播信号",
                    style = MaterialTheme.typography.headlineMedium,
                    color = Paper,
                )
                Text(
                    text = "密码不会保存；会话经系统密钥加密，最长保留 30 天。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Muted,
                )
            }

            ConsoleTextField(
                value = state.username,
                onValueChange = onUsernameChange,
                label = "IDENTITY / 用户名或邮箱",
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Text,
                    imeAction = ImeAction.Next,
                ),
                keyboardActions = KeyboardActions(
                    onNext = { focusManager.moveFocus(FocusDirection.Down) },
                ),
                enabled = !state.isRestoringSession && !state.isLoggingIn,
            )
            ConsoleTextField(
                value = state.password,
                onValueChange = onPasswordChange,
                label = "PASSWORD / 密码",
                visualTransformation = if (passwordVisible) {
                    VisualTransformation.None
                } else {
                    PasswordVisualTransformation()
                },
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Password,
                    imeAction = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(
                    onDone = {
                        focusManager.clearFocus()
                        onLogin()
                    },
                ),
                trailingContent = {
                    TextButton(onClick = { passwordVisible = !passwordVisible }) {
                        Text(
                            if (passwordVisible) "隐藏" else "显示",
                            style = MaterialTheme.typography.labelMedium,
                        )
                    }
                },
                enabled = !state.isRestoringSession && !state.isLoggingIn,
            )

            state.error?.let {
                InlineNotice(text = it, tone = NoticeTone.Danger)
            }
            state.notice?.let {
                InlineNotice(text = it, tone = NoticeTone.Status)
            }

            Button(
                onClick = onLogin,
                enabled = !state.isRestoringSession &&
                    !state.isLoggingIn &&
                    state.username.isNotBlank() &&
                    state.password.isNotEmpty(),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = Phosphor,
                    contentColor = Ink0,
                    disabledContainerColor = Metal,
                    disabledContentColor = Dim,
                ),
            ) {
                if (state.isLoggingIn || state.isRestoringSession) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        color = Ink0,
                        strokeWidth = 2.dp,
                    )
                } else {
                    SignalLamp(active = true, darkCenter = true)
                }
                Spacer(Modifier.width(10.dp))
                Text(
                    when {
                        state.isRestoringSession -> "正在恢复加密会话…"
                        state.isLoggingIn -> "正在校验信号…"
                        else -> "登录并开始收听"
                    },
                )
            }

            Text(
                text = "RELEASE: HTTPS ONLY  ·  DEBUG: LOCAL HTTP ENABLED",
                style = MaterialTheme.typography.labelSmall,
                color = Dim,
            )
        }
    }
}

@Composable
private fun ListenerScreen(
    state: RadioUiState,
    playbackState: PlaybackUiState,
    onLogout: () -> Unit,
    onRefresh: () -> Unit,
    onSelectChannel: (Long) -> Unit,
    onStreamFormatChange: (PlayerStreamFormat) -> Unit,
    onListen: (Long) -> Unit,
    onTogglePlayback: () -> Unit,
    onVolumeChange: (Float) -> Unit,
    onToggleMute: () -> Unit,
    onDismissNotice: () -> Unit,
) {
    val selectedChannel = state.channels.firstOrNull { it.id == state.selectedChannelId }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .windowInsetsPadding(WindowInsets.safeDrawing),
    ) {
        ConsoleHeader(
            user = requireNotNull(state.user),
            onLogout = onLogout,
        )

        BoxWithConstraints(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            val expanded = maxWidth >= 840.dp
            val padding = responsivePadding(maxWidth)
            val scrollState = rememberScrollState()

            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(scrollState)
                    .padding(padding),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                TunerStrip(
                    selectedChannel = selectedChannel,
                    playbackState = playbackState,
                    isLoading = state.isLoadingChannels,
                )

                AnimatedVisibility(state.error != null) {
                    state.error?.let {
                        InlineNotice(
                            text = it,
                            tone = NoticeTone.Danger,
                            actionLabel = "重新读取",
                            onAction = onRefresh,
                        )
                    }
                }
                AnimatedVisibility(state.notice != null) {
                    state.notice?.let {
                        InlineNotice(
                            text = it,
                            tone = NoticeTone.Status,
                            actionLabel = "关闭",
                            onAction = onDismissNotice,
                        )
                    }
                }

                if (state.channels.isEmpty() && !state.isLoadingChannels) {
                    EmptyConsole(onRefresh = onRefresh)
                } else if (expanded) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(14.dp),
                        verticalAlignment = Alignment.Top,
                    ) {
                        ChannelRail(
                            channels = state.channels,
                            selectedChannelId = state.selectedChannelId,
                            onSelectChannel = onSelectChannel,
                            modifier = Modifier.width(286.dp),
                        )
                        PlayerConsole(
                            channel = selectedChannel,
                            playbackState = playbackState,
                            isPreparing = state.isPreparingStream,
                            streamFormat = state.streamFormat,
                            losslessAvailable = state.losslessAvailable,
                            onStreamFormatChange = onStreamFormatChange,
                            onListen = onListen,
                            onTogglePlayback = onTogglePlayback,
                            onVolumeChange = onVolumeChange,
                            onToggleMute = onToggleMute,
                            modifier = Modifier.weight(1f),
                        )
                    }
                } else {
                    CompactChannelPicker(
                        channels = state.channels,
                        selectedChannelId = state.selectedChannelId,
                        onSelectChannel = onSelectChannel,
                    )
                    PlayerConsole(
                        channel = selectedChannel,
                        playbackState = playbackState,
                        isPreparing = state.isPreparingStream,
                        streamFormat = state.streamFormat,
                        losslessAvailable = state.losslessAvailable,
                        onStreamFormatChange = onStreamFormatChange,
                        onListen = onListen,
                        onTogglePlayback = onTogglePlayback,
                        onVolumeChange = onVolumeChange,
                        onToggleMute = onToggleMute,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "${state.streamFormat.wireValue.uppercase()} / LIVE STREAM",
                        style = MaterialTheme.typography.labelSmall,
                        color = Dim,
                    )
                    TextButton(
                        onClick = onRefresh,
                        enabled = !state.isLoadingChannels,
                    ) {
                        Text(if (state.isLoadingChannels) "读取中…" else "↻ 刷新频道")
                    }
                }
            }
        }
    }
}

@Composable
private fun ConsoleHeader(
    user: RadioUser,
    onLogout: () -> Unit,
) {
    Surface(
        color = Color(0xFF111615),
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, ConsoleLine),
    ) {
        BoxWithConstraints {
            val compact = maxWidth < 520.dp
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = if (compact) 12.dp else 20.dp, vertical = 10.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    modifier = Modifier.weight(1f),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    BrandMark(compact = true)
                    if (!compact) {
                        Column {
                            Text(
                                "LISTENER / LIVE",
                                style = MaterialTheme.typography.labelMedium,
                                color = Paper,
                            )
                            Text(
                                "MOBILE RECEIVER",
                                style = MaterialTheme.typography.labelSmall,
                                color = Dim,
                            )
                        }
                    }
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    if (!compact) {
                        Column(horizontalAlignment = Alignment.End) {
                            Text(
                                user.username,
                                style = MaterialTheme.typography.labelMedium,
                                color = Paper,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                user.role.uppercase(),
                                style = MaterialTheme.typography.labelSmall,
                                color = Dim,
                            )
                        }
                    }
                    OutlinedButton(onClick = onLogout) {
                        Text(if (compact) "退出" else "断开会话")
                    }
                }
            }
        }
    }
}

@Composable
private fun TunerStrip(
    selectedChannel: RadioChannel?,
    playbackState: PlaybackUiState,
    isLoading: Boolean,
) {
    ConsolePanel(accent = Phosphor) {
        BoxWithConstraints {
            val compact = maxWidth < 560.dp
            Column(
                modifier = Modifier.padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    listOf("88", "92", "96", "100", "104", "108").forEach {
                        Text(it, style = MaterialTheme.typography.labelSmall, color = Dim)
                    }
                }
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(2.dp)
                        .background(ConsoleLine),
                ) {
                    val dialPosition by animateFloatAsState(
                        targetValue = (
                            selectedChannel?.displayOrder
                                ?.coerceIn(1, 6)
                                ?.minus(1)
                                ?: 0
                            ) / 5f,
                        label = "dial",
                    )
                    Box(
                        modifier = Modifier
                            .fillMaxWidth(dialPosition.coerceAtLeast(0.015f))
                            .height(2.dp)
                            .background(Phosphor),
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            "CHANNEL SELECT",
                            style = MaterialTheme.typography.labelSmall,
                            color = Amber,
                        )
                        Text(
                            selectedChannel?.name ?: if (isLoading) "正在调谐…" else "没有可用频道",
                            style = MaterialTheme.typography.titleMedium,
                            color = Paper,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    if (!compact) {
                        SignalBars(
                            active = playbackState.isPlaying,
                            label = transportLabel(playbackState),
                        )
                    } else {
                        SignalLamp(active = playbackState.isPlaying)
                    }
                }
            }
        }
    }
}

@Composable
private fun ChannelRail(
    channels: List<RadioChannel>,
    selectedChannelId: Long?,
    onSelectChannel: (Long) -> Unit,
    modifier: Modifier = Modifier,
) {
    ConsolePanel(modifier = modifier, accent = Amber) {
        Column(modifier = Modifier.padding(10.dp)) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 6.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Eyebrow("FREQUENCY BANK")
                Text(
                    "${channels.size} CH",
                    style = MaterialTheme.typography.labelSmall,
                    color = Dim,
                )
            }
            channels.forEach { channel ->
                ChannelRow(
                    channel = channel,
                    selected = channel.id == selectedChannelId,
                    onClick = { onSelectChannel(channel.id) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@Composable
private fun CompactChannelPicker(
    channels: List<RadioChannel>,
    selectedChannelId: Long?,
    onSelectChannel: (Long) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        channels.forEach { channel ->
            ChannelRow(
                channel = channel,
                selected = channel.id == selectedChannelId,
                onClick = { onSelectChannel(channel.id) },
                modifier = Modifier.widthIn(min = 180.dp, max = 250.dp),
            )
        }
    }
}

@Composable
private fun ChannelRow(
    channel: RadioChannel,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val border = if (selected) PhosphorDeep else ConsoleLine
    val background = if (selected) Color(0xFF172015) else Ink1
    Row(
        modifier = modifier
            .padding(vertical = 3.dp)
            .border(1.dp, border, MaterialTheme.shapes.small)
            .background(background)
            .clickable(role = Role.RadioButton, onClick = onClick)
            .padding(12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = channel.displayOrder.coerceAtLeast(1).toString().padStart(2, '0'),
            style = MaterialTheme.typography.labelLarge,
            color = if (selected) Phosphor else Dim,
        )
        Column(modifier = Modifier.weight(1f)) {
            Text(
                channel.name,
                style = MaterialTheme.typography.labelLarge,
                color = Paper,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                channel.currentTrack?.title ?: channel.status.uppercase(),
                style = MaterialTheme.typography.labelSmall,
                color = Muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        SignalLamp(active = channel.status == "live")
    }
}

@Composable
private fun PlayerConsole(
    channel: RadioChannel?,
    playbackState: PlaybackUiState,
    isPreparing: Boolean,
    streamFormat: PlayerStreamFormat,
    losslessAvailable: Boolean,
    onStreamFormatChange: (PlayerStreamFormat) -> Unit,
    onListen: (Long) -> Unit,
    onTogglePlayback: () -> Unit,
    onVolumeChange: (Float) -> Unit,
    onToggleMute: () -> Unit,
    modifier: Modifier = Modifier,
) {
    ConsolePanel(modifier = modifier, accent = Phosphor) {
        BoxWithConstraints {
            val split = maxWidth >= 660.dp
            val currentChannel = channel?.id == playbackState.channelId
            val receiving = currentChannel &&
                (playbackState.isPlaying || playbackState.isBuffering)

            if (split) {
                Row(
                    modifier = Modifier.padding(18.dp),
                    horizontalArrangement = Arrangement.spacedBy(22.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    ArtworkDial(
                        channel = channel,
                        spinning = receiving,
                        modifier = Modifier
                            .widthIn(min = 180.dp, max = 250.dp)
                            .weight(0.78f),
                    )
                    TrackReadout(
                        channel = channel,
                        modifier = Modifier.weight(1.2f),
                    )
                    TransportDeck(
                        channel = channel,
                        playbackState = playbackState,
                        isPreparing = isPreparing,
                        streamFormat = streamFormat,
                        losslessAvailable = losslessAvailable,
                        onStreamFormatChange = onStreamFormatChange,
                        onListen = onListen,
                        onTogglePlayback = onTogglePlayback,
                        onVolumeChange = onVolumeChange,
                        onToggleMute = onToggleMute,
                        modifier = Modifier
                            .widthIn(min = 210.dp, max = 280.dp)
                            .weight(0.95f),
                    )
                }
            } else {
                Column(
                    modifier = Modifier.padding(14.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        ArtworkDial(
                            channel = channel,
                            spinning = receiving,
                            modifier = Modifier
                                .widthIn(max = 132.dp)
                                .weight(0.42f),
                        )
                        TrackReadout(
                            channel = channel,
                            modifier = Modifier.weight(0.58f),
                        )
                    }
                    TransportDeck(
                        channel = channel,
                        playbackState = playbackState,
                        isPreparing = isPreparing,
                        streamFormat = streamFormat,
                        losslessAvailable = losslessAvailable,
                        onStreamFormatChange = onStreamFormatChange,
                        onListen = onListen,
                        onTogglePlayback = onTogglePlayback,
                        onVolumeChange = onVolumeChange,
                        onToggleMute = onToggleMute,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }
    }
}

@Composable
private fun ArtworkDial(
    channel: RadioChannel?,
    spinning: Boolean,
    modifier: Modifier = Modifier,
) {
    val glow by animateFloatAsState(
        targetValue = if (spinning) 0.34f else 0.08f,
        label = "artworkGlow",
    )
    Box(
        modifier = modifier
            .aspectRatio(1f)
            .border(1.dp, Color(0xFF434E4A))
            .background(Ink0)
            .padding(9.dp),
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.fillMaxSize()) {
            val radius = min(size.width, size.height) * 0.42f
            drawCircle(
                brush = Brush.radialGradient(
                    colors = listOf(
                        Phosphor.copy(alpha = glow),
                        Color(0xFF111816),
                        Color(0xFF080A0A),
                    ),
                ),
                radius = radius,
            )
            drawCircle(ConsoleLine, radius = radius, style = Stroke(width = 1.dp.toPx()))
            drawCircle(Color(0xFF242D29), radius = radius * 0.68f, style = Stroke(width = 1.dp.toPx()))
            drawCircle(PhosphorDeep, radius = radius * 0.22f)
            drawCircle(Ink0, radius = radius * 0.075f)
            repeat(18) { index ->
                val angle = Math.toRadians(index * 20.0)
                val startRadius = radius * 0.76f
                val endRadius = radius * 0.92f
                drawLine(
                    color = if (index % 3 == 0) Amber.copy(alpha = 0.8f) else ConsoleLine,
                    start = androidx.compose.ui.geometry.Offset(
                        center.x + cos(angle).toFloat() * startRadius,
                        center.y + sin(angle).toFloat() * startRadius,
                    ),
                    end = androidx.compose.ui.geometry.Offset(
                        center.x + cos(angle).toFloat() * endRadius,
                        center.y + sin(angle).toFloat() * endRadius,
                    ),
                    strokeWidth = 1.dp.toPx(),
                )
            }
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                "RADIO",
                style = MaterialTheme.typography.labelLarge,
                color = Paper,
            )
            Text(
                "CH ${channel?.displayOrder?.coerceAtLeast(1)?.toString()?.padStart(2, '0') ?: "--"}",
                style = MaterialTheme.typography.labelSmall,
                color = Phosphor,
            )
        }
    }
}

@Composable
private fun TrackReadout(
    channel: RadioChannel?,
    modifier: Modifier = Modifier,
) {
    val track = channel?.currentTrack
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            StatusPill(channel?.status ?: "idle")
            channel?.listenerCount?.let {
                Text(
                    "$it LISTENERS",
                    style = MaterialTheme.typography.labelSmall,
                    color = Dim,
                )
            }
        }
        Eyebrow("NOW TRANSMITTING")
        Text(
            text = track?.title ?: "等待节目",
            style = MaterialTheme.typography.headlineMedium,
            color = Paper,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            text = track?.artist ?: channel?.description ?: "未知艺人",
            style = MaterialTheme.typography.bodyLarge,
            color = Muted,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        track?.album?.let {
            Text(
                it,
                style = MaterialTheme.typography.labelMedium,
                color = Dim,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun TransportDeck(
    channel: RadioChannel?,
    playbackState: PlaybackUiState,
    isPreparing: Boolean,
    streamFormat: PlayerStreamFormat,
    losslessAvailable: Boolean,
    onStreamFormatChange: (PlayerStreamFormat) -> Unit,
    onListen: (Long) -> Unit,
    onTogglePlayback: () -> Unit,
    onVolumeChange: (Float) -> Unit,
    onToggleMute: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val currentChannel = channel?.id == playbackState.channelId
    val selectedStreamActive = currentChannel &&
        playbackState.streamFormat == streamFormat
    val receiving = selectedStreamActive &&
        (playbackState.isPlaying || playbackState.isBuffering)
    val canResume = selectedStreamActive && playbackState.hasMedia
    val needsStreamSwitch = currentChannel &&
        playbackState.hasMedia &&
        !selectedStreamActive

    Column(
        modifier = modifier
            .border(1.dp, ConsoleLine)
            .background(Ink1)
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SignalLamp(active = receiving)
                Text(
                    transportLabel(
                        if (currentChannel) playbackState else PlaybackUiState(),
                    ),
                    style = MaterialTheme.typography.labelSmall,
                    color = if (receiving) Phosphor else Muted,
                )
            }
            if (playbackState.channelId != null && !currentChannel) {
                Text(
                    "CH ${playbackState.channelName.orEmpty()}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Amber,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }

        if (losslessAvailable) {
            StreamFormatSelector(
                selected = streamFormat,
                enabled = !isPreparing,
                onSelect = onStreamFormatChange,
            )
        }

        Button(
            onClick = {
                when {
                    receiving || canResume -> onTogglePlayback()
                    channel != null -> onListen(channel.id)
                }
            },
            enabled = channel != null && !isPreparing,
            modifier = Modifier
                .fillMaxWidth()
                .height(54.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = Color(0xFF273019),
                contentColor = Phosphor,
                disabledContainerColor = Metal,
                disabledContentColor = Dim,
            ),
        ) {
            if (isPreparing || (currentChannel && playbackState.isBuffering)) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    color = Phosphor,
                    strokeWidth = 2.dp,
                )
            } else {
                Text(
                    if (receiving) "Ⅱ" else "▶",
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Black,
                )
            }
            Spacer(Modifier.width(10.dp))
            Text(
                when {
                    isPreparing -> "正在连接"
                    receiving -> "暂停接收"
                    canResume -> "继续接收"
                    needsStreamSwitch -> "切换至 ${streamFormat.shortLabel()}"
                    else -> "开始收听"
                },
            )
        }

        OutlinedButton(
            onClick = onToggleMute,
            enabled = playbackState.controllerReady,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (playbackState.volumeLevel <= 0f) "取消静音" else "静音")
        }

        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text("VOLUME", style = MaterialTheme.typography.labelSmall, color = Dim)
                Text(
                    "${(playbackState.volumeLevel * 100).roundToInt()}%",
                    style = MaterialTheme.typography.labelSmall,
                    color = Paper,
                )
            }
            Slider(
                value = playbackState.volumeLevel,
                onValueChange = onVolumeChange,
                enabled = playbackState.controllerReady,
                colors = SliderDefaults.colors(
                    thumbColor = Phosphor,
                    activeTrackColor = PhosphorDeep,
                    inactiveTrackColor = ConsoleLine,
                ),
            )
        }

        playbackState.error?.let {
            Text(
                it,
                style = MaterialTheme.typography.labelSmall,
                color = RadioRed,
            )
        }
    }
}

@Composable
private fun StreamFormatSelector(
    selected: PlayerStreamFormat,
    enabled: Boolean,
    onSelect: (PlayerStreamFormat) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "STREAM FORMAT",
                style = MaterialTheme.typography.labelSmall,
                color = Dim,
            )
            Text(
                "ADMIN",
                style = MaterialTheme.typography.labelSmall,
                color = Amber,
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            PlayerStreamFormat.entries.forEach { format ->
                val active = format == selected
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .border(
                            width = 1.dp,
                            color = if (active) Phosphor else ConsoleLine,
                            shape = MaterialTheme.shapes.small,
                        )
                        .background(
                            color = if (active) {
                                Color(0xFF1A2417)
                            } else {
                                Ink0
                            },
                            shape = MaterialTheme.shapes.small,
                        )
                        .selectable(
                            selected = active,
                            enabled = enabled,
                            role = Role.RadioButton,
                            onClick = { onSelect(format) },
                        )
                        .padding(horizontal = 8.dp, vertical = 9.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    Text(
                        format.shortLabel(),
                        style = MaterialTheme.typography.labelMedium,
                        color = if (active) Phosphor else Paper,
                        maxLines = 1,
                    )
                    Text(
                        when (format) {
                            PlayerStreamFormat.AAC -> "320 kbps"
                            PlayerStreamFormat.FLAC -> "44.1k / 16bit"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = if (enabled) Muted else Dim,
                        maxLines = 1,
                    )
                }
            }
        }
        Text(
            "选择会保存在本机；切换后点击收听按钮建立新流。",
            style = MaterialTheme.typography.labelSmall,
            color = Dim,
        )
    }
}

@Composable
private fun EmptyConsole(onRefresh: () -> Unit) {
    ConsolePanel(accent = RadioRed) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "NO CARRIER",
                style = MaterialTheme.typography.labelLarge,
                color = RadioRed,
            )
            Text(
                "当前没有可收听的频道",
                style = MaterialTheme.typography.headlineMedium,
                color = Paper,
            )
            Text(
                "频道可能尚未启用，或管理员正在维护播放服务。",
                style = MaterialTheme.typography.bodyMedium,
                color = Muted,
            )
            OutlinedButton(onClick = onRefresh) {
                Text("重新读取")
            }
        }
    }
}

@Composable
private fun BrandMark(compact: Boolean) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(if (compact) 9.dp else 13.dp),
    ) {
        RadioDialIcon(if (compact) 34.dp else 48.dp)
        Column {
            Text(
                "PuvuFM",
                style = if (compact) {
                    MaterialTheme.typography.titleLarge
                } else {
                    MaterialTheme.typography.headlineMedium
                },
                color = Paper,
                maxLines = 1,
            )
            if (!compact) {
                Text(
                    "PUVUCRAFT / LIVE TRANSMISSION",
                    style = MaterialTheme.typography.labelSmall,
                    color = Amber,
                )
            }
        }
    }
}

@Composable
private fun RadioDialIcon(size: Dp) {
    Canvas(Modifier.requiredSize(size)) {
        val stroke = 1.5.dp.toPx()
        drawCircle(Ink2)
        drawCircle(ConsoleLine, style = Stroke(stroke))
        drawCircle(PhosphorDeep, radius = this.size.minDimension * 0.27f)
        drawCircle(Ink0, radius = this.size.minDimension * 0.09f)
        repeat(3) { index ->
            val angle = Math.toRadians(index * 120.0 - 90.0)
            val inner = this.size.minDimension * 0.16f
            val outer = this.size.minDimension * 0.39f
            drawLine(
                color = Phosphor,
                start = androidx.compose.ui.geometry.Offset(
                    center.x + cos(angle).toFloat() * inner,
                    center.y + sin(angle).toFloat() * inner,
                ),
                end = androidx.compose.ui.geometry.Offset(
                    center.x + cos(angle).toFloat() * outer,
                    center.y + sin(angle).toFloat() * outer,
                ),
                strokeWidth = stroke,
                cap = StrokeCap.Round,
            )
        }
    }
}

@Composable
private fun RadioSignalIllustration(modifier: Modifier = Modifier) {
    Canvas(modifier) {
        val centerY = size.height * 0.55f
        val path = Path().apply {
            moveTo(0f, centerY)
            val segments = 80
            repeat(segments + 1) { index ->
                val x = size.width * index / segments
                val envelope = sin(Math.PI * index / segments).toFloat()
                val y = centerY +
                    sin(index * 0.72f) * size.height * 0.2f * envelope
                lineTo(x, y)
            }
        }
        drawPath(
            path,
            color = Phosphor.copy(alpha = 0.78f),
            style = Stroke(width = 2.dp.toPx()),
        )
        drawLine(
            color = ConsoleLine,
            start = androidx.compose.ui.geometry.Offset(0f, centerY),
            end = androidx.compose.ui.geometry.Offset(size.width, centerY),
            strokeWidth = 1.dp.toPx(),
        )
    }
}

@Composable
private fun SignalBars(active: Boolean, label: String) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(2.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            repeat(4) { index ->
                Box(
                    Modifier
                        .width(3.dp)
                        .height((6 + index * 3).dp)
                        .background(
                            if (active) Phosphor.copy(alpha = 0.45f + index * 0.16f) else Dim,
                        ),
                )
            }
        }
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = if (active) Phosphor else Dim,
        )
    }
}

@Composable
private fun SignalLamp(
    active: Boolean,
    darkCenter: Boolean = false,
) {
    Box(
        modifier = Modifier
            .size(9.dp)
            .clip(CircleShape)
            .border(
                1.dp,
                if (active) Color(0xFFDFF89A) else Color(0xFF4D544C),
                CircleShape,
            )
            .background(
                if (active) {
                    if (darkCenter) PhosphorDeep else Phosphor
                } else {
                    Color(0xFF303630)
                },
            ),
    )
}

@Composable
private fun StatusPill(status: String) {
    val live = status == "live"
    Row(
        modifier = Modifier
            .border(1.dp, if (live) PhosphorDeep else ConsoleLine)
            .background(if (live) Color(0xFF1A2213) else Ink1)
            .padding(horizontal = 8.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SignalLamp(active = live)
        Text(
            status.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = if (live) Phosphor else Muted,
        )
    }
}

@Composable
private fun ConsolePanel(
    modifier: Modifier = Modifier,
    accent: Color = ConsoleLine,
    content: @Composable () -> Unit,
) {
    Surface(
        modifier = modifier
            .border(1.dp, ConsoleLine, MaterialTheme.shapes.medium)
            .drawBehind {
                drawRect(
                    color = accent.copy(alpha = 0.8f),
                    size = androidx.compose.ui.geometry.Size(size.width, 2.dp.toPx()),
                )
            },
        color = Ink2.copy(alpha = 0.97f),
        shape = MaterialTheme.shapes.medium,
        tonalElevation = 0.dp,
        shadowElevation = 8.dp,
        content = content,
    )
}

@Composable
private fun ConsoleTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    placeholder: String? = null,
    visualTransformation: VisualTransformation = VisualTransformation.None,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
    trailingContent: (@Composable () -> Unit)? = null,
    enabled: Boolean = true,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier.fillMaxWidth(),
        label = {
            Text(label, style = MaterialTheme.typography.labelSmall)
        },
        placeholder = placeholder?.let {
            {
                Text(
                    it,
                    style = MaterialTheme.typography.bodyMedium,
                    color = Dim,
                )
            }
        },
        trailingIcon = trailingContent,
        singleLine = true,
        visualTransformation = visualTransformation,
        keyboardOptions = keyboardOptions,
        keyboardActions = keyboardActions,
        enabled = enabled,
    )
}

private enum class NoticeTone {
    Danger,
    Status,
}

@Composable
private fun InlineNotice(
    text: String,
    tone: NoticeTone,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    val color = if (tone == NoticeTone.Danger) RadioRed else Cyan
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, color.copy(alpha = 0.65f))
            .background(color.copy(alpha = 0.07f))
            .padding(horizontal = 12.dp, vertical = 9.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SignalLamp(active = tone == NoticeTone.Status)
        Text(
            text,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodyMedium,
            color = if (tone == NoticeTone.Danger) Color(0xFFFFC4B9) else Paper,
        )
        if (actionLabel != null && onAction != null) {
            TextButton(onClick = onAction) {
                Text(actionLabel)
            }
        }
    }
}

@Composable
private fun Eyebrow(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = Amber,
    )
}

private fun transportLabel(state: PlaybackUiState): String = when {
    state.error != null -> "NO CARRIER"
    state.isBuffering -> "TUNING"
    state.isPlaying -> "DATA LOCK"
    state.hasMedia -> "RECEPTION PAUSED"
    state.controllerReady -> "STANDBY"
    else -> "PLAYER LINK"
}

private fun PlayerStreamFormat.shortLabel(): String = when (this) {
    PlayerStreamFormat.AAC -> "AAC"
    PlayerStreamFormat.FLAC -> "FLAC"
}

private fun responsivePadding(width: Dp): Dp = when {
    width < 360.dp -> 10.dp
    width < 600.dp -> 16.dp
    width < 840.dp -> 22.dp
    else -> 32.dp
}

@Preview(name = "Login · Android 9 phone", widthDp = 360, heightDp = 740)
@Composable
private fun LoginPhonePreview() {
    PuvuRadioApp(
        uiState = RadioUiState(),
        playbackState = PlaybackUiState(controllerReady = true),
        onUsernameChange = {},
        onPasswordChange = {},
        onLogin = {},
        onLogout = {},
        onRefresh = {},
        onSelectChannel = {},
        onStreamFormatChange = {},
        onListen = {},
        onTogglePlayback = {},
        onVolumeChange = {},
        onToggleMute = {},
        onDismissNotice = {},
        onRenewPlayerKey = {},
        onCancelPlayerKeyRenewal = {},
    )
}

@Preview(name = "Listener · tablet", widthDp = 1000, heightDp = 720)
@Composable
private fun ListenerTabletPreview() {
    val channels = listOf(
        RadioChannel(
            id = 1,
            name = "Puvu FM",
            slug = "puvu-fm",
            description = "全天候同步广播",
            displayOrder = 1,
            status = "live",
            listenerCount = 12,
            currentTrack = TrackSummary("Summer Signal", "PuvuCraft", "Night Console"),
        ),
        RadioChannel(
            id = 2,
            name = "After Hours",
            slug = "after-hours",
            description = "夜间频道",
            displayOrder = 2,
            status = "live",
            listenerCount = 4,
            currentTrack = TrackSummary("Static Bloom", "Unknown Artist", null),
        ),
    )
    PuvuRadioApp(
        uiState = RadioUiState(
            user = RadioUser(1, "listener", "listener@example.com", "listener"),
            channels = channels,
            selectedChannelId = 1,
            streamFormat = PlayerStreamFormat.FLAC,
            losslessAvailable = true,
        ),
        playbackState = PlaybackUiState(
            controllerReady = true,
            hasMedia = true,
            channelId = 1,
            channelName = "Puvu FM",
            streamFormat = PlayerStreamFormat.FLAC,
            isPlaying = true,
            volumeLevel = 0.78f,
        ),
        onUsernameChange = {},
        onPasswordChange = {},
        onLogin = {},
        onLogout = {},
        onRefresh = {},
        onSelectChannel = {},
        onStreamFormatChange = {},
        onListen = {},
        onTogglePlayback = {},
        onVolumeChange = {},
        onToggleMute = {},
        onDismissNotice = {},
        onRenewPlayerKey = {},
        onCancelPlayerKeyRenewal = {},
    )
}
