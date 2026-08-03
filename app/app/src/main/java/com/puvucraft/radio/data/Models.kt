package com.puvucraft.radio.data

import com.puvucraft.radio.PlayerStreamFormat
import org.json.JSONObject

data class RadioUser(
    val id: Long,
    val username: String,
    val email: String,
    val role: String,
)

data class TrackSummary(
    val title: String,
    val artist: String?,
    val album: String?,
)

data class RadioChannel(
    val id: Long,
    val name: String,
    val slug: String,
    val description: String?,
    val displayOrder: Int,
    val status: String,
    val listenerCount: Int?,
    val currentTrack: TrackSummary?,
)

data class PlayerKeyState(
    val configured: Boolean,
    val validForNewConnections: Boolean,
    val connectBefore: String?,
    val losslessAvailable: Boolean,
)

data class StreamTicket(
    val url: String,
    val channelId: Long,
    val streamFormat: PlayerStreamFormat,
)

internal fun parseUser(payload: JSONObject): RadioUser {
    val user = payload.optJSONObject("user") ?: payload
    return RadioUser(
        id = user.getLong("id"),
        username = user.getString("username"),
        email = user.optNullableString("email").orEmpty(),
        role = user.optNullableString("role").orEmpty(),
    )
}

internal fun parseChannel(payload: JSONObject): RadioChannel {
    val playback = payload.optJSONObject("playback_state")
        ?: payload.optJSONObject("playback")
    val track = playback?.optJSONObject("current_track")
        ?: payload.optJSONObject("current_track")

    return RadioChannel(
        id = payload.getLong("id"),
        name = payload.getString("name"),
        slug = payload.getString("slug"),
        description = payload.optNullableString("description"),
        displayOrder = payload.optInt("display_order", 0),
        status = playback?.optNullableString("status")
            ?: payload.optNullableString("status")
            ?: "idle",
        listenerCount = playback?.optNullableInt("listener_count")
            ?: payload.optNullableInt("listener_count"),
        currentTrack = track?.let {
            TrackSummary(
                title = it.optNullableString("title") ?: "等待节目",
                artist = it.optNullableString("artist"),
                album = it.optNullableString("album"),
            )
        },
    )
}

internal fun parsePlayerKeyState(payload: JSONObject): PlayerKeyState = PlayerKeyState(
    configured = payload.optBoolean("configured", false),
    validForNewConnections = payload.optBoolean("valid_for_new_connections", false),
    connectBefore = payload.optNullableString("connect_before"),
    losslessAvailable = payload.optBoolean("lossless_available", false),
)

private fun JSONObject.optNullableString(name: String): String? {
    if (!has(name) || isNull(name)) return null
    return optString(name).takeIf { it.isNotBlank() }
}

private fun JSONObject.optNullableInt(name: String): Int? {
    if (!has(name) || isNull(name)) return null
    return optInt(name)
}
