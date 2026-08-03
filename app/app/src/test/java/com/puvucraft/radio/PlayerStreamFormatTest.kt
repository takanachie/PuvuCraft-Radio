package com.puvucraft.radio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PlayerStreamFormatTest {
    @Test
    fun `restores supported stream preferences case insensitively`() {
        assertEquals(
            PlayerStreamFormat.AAC,
            PlayerStreamFormat.fromWireValue("AAC"),
        )
        assertEquals(
            PlayerStreamFormat.FLAC,
            PlayerStreamFormat.fromWireValue("flac"),
        )
    }

    @Test
    fun `rejects missing or unknown stream preferences`() {
        assertNull(PlayerStreamFormat.fromWireValue(null))
        assertNull(PlayerStreamFormat.fromWireValue("mp3"))
    }
}
