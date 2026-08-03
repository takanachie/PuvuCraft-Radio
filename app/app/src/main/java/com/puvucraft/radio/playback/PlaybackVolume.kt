package com.puvucraft.radio.playback

import android.content.Context
import kotlin.math.ln
import kotlin.math.pow

internal object LogarithmicVolume {
    const val DEFAULT_LEVEL = 0.78f

    // A 100:1 amplitude range gives the useful part of the control a 40 dB taper.
    private const val GAIN_RANGE = 100.0
    private val logGainRange = ln(GAIN_RANGE)

    fun levelToPlayerGain(level: Float): Float {
        val safeLevel = normalizeLevel(level)
        if (safeLevel == 0f) return 0f

        return (
            (GAIN_RANGE.pow(safeLevel.toDouble()) - 1.0) /
                (GAIN_RANGE - 1.0)
            ).toFloat()
    }

    fun playerGainToLevel(gain: Float): Float {
        val safeGain = when {
            !gain.isFinite() -> 0f
            else -> gain.coerceIn(0f, 1f)
        }
        if (safeGain == 0f) return 0f

        return (
            ln(1.0 + safeGain * (GAIN_RANGE - 1.0)) /
                logGainRange
            ).toFloat()
    }

    fun normalizeLevel(level: Float): Float = when {
        !level.isFinite() -> DEFAULT_LEVEL
        else -> level.coerceIn(0f, 1f)
    }
}

internal class PlaybackVolumeStore(context: Context) {
    private val preferences = context.applicationContext.getSharedPreferences(
        PREFERENCES_NAME,
        Context.MODE_PRIVATE,
    )

    fun readLevel(): Float = readNormalized(
        key = VOLUME_LEVEL_KEY,
        defaultValue = LogarithmicVolume.DEFAULT_LEVEL,
    )

    fun readLastAudibleLevel(): Float = readNormalized(
        key = LAST_AUDIBLE_LEVEL_KEY,
        defaultValue = LogarithmicVolume.DEFAULT_LEVEL,
    ).takeIf { it > 0f } ?: LogarithmicVolume.DEFAULT_LEVEL

    fun saveLevel(level: Float) {
        val safeLevel = LogarithmicVolume.normalizeLevel(level)
        preferences.edit().apply {
            putFloat(VOLUME_LEVEL_KEY, safeLevel)
            if (safeLevel > 0f) {
                putFloat(LAST_AUDIBLE_LEVEL_KEY, safeLevel)
            }
        }.apply()
    }

    private fun readNormalized(key: String, defaultValue: Float): Float {
        val stored = preferences.getFloat(key, defaultValue)
        return if (stored.isFinite()) {
            stored.coerceIn(0f, 1f)
        } else {
            defaultValue
        }
    }

    private companion object {
        const val PREFERENCES_NAME = "puvucraft_playback_preferences"
        const val VOLUME_LEVEL_KEY = "volume_level"
        const val LAST_AUDIBLE_LEVEL_KEY = "last_audible_volume_level"
    }
}
