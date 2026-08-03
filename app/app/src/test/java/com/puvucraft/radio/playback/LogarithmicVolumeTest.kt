package com.puvucraft.radio.playback

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LogarithmicVolumeTest {
    @Test
    fun `endpoints map to silence and unity gain`() {
        assertEquals(0f, LogarithmicVolume.levelToPlayerGain(0f), TOLERANCE)
        assertEquals(1f, LogarithmicVolume.levelToPlayerGain(1f), TOLERANCE)
    }

    @Test
    fun `half level uses logarithmic rather than linear gain`() {
        val gain = LogarithmicVolume.levelToPlayerGain(0.5f)

        assertEquals(9f / 99f, gain, TOLERANCE)
        assertTrue(gain < 0.5f)
    }

    @Test
    fun `level and gain conversions round trip`() {
        listOf(0f, 0.1f, 0.25f, 0.5f, 0.78f, 1f).forEach { level ->
            val restored = LogarithmicVolume.playerGainToLevel(
                LogarithmicVolume.levelToPlayerGain(level),
            )

            assertEquals(level, restored, TOLERANCE)
        }
    }

    @Test
    fun `inputs are kept in the supported range`() {
        assertEquals(0f, LogarithmicVolume.levelToPlayerGain(-1f), TOLERANCE)
        assertEquals(1f, LogarithmicVolume.levelToPlayerGain(2f), TOLERANCE)
        assertEquals(0f, LogarithmicVolume.playerGainToLevel(-1f), TOLERANCE)
        assertEquals(1f, LogarithmicVolume.playerGainToLevel(2f), TOLERANCE)
    }

    private companion object {
        const val TOLERANCE = 0.000_01f
    }
}
