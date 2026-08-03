package com.puvucraft.radio.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ServerUrlTest {
    @Test
    fun `adds https and removes trailing slash`() {
        assertEquals(
            "https://radio.example.com",
            ServerUrl.normalize(" radio.example.com/ ", allowCleartext = false),
        )
    }

    @Test
    fun `keeps an explicit port and base path`() {
        assertEquals(
            "https://radio.example.com:8443/station",
            ServerUrl.normalize(
                "https://radio.example.com:8443/station/",
                allowCleartext = false,
            ),
        )
    }

    @Test
    fun `release mode rejects cleartext servers`() {
        assertThrows(IllegalArgumentException::class.java) {
            ServerUrl.normalize("http://192.168.1.20:8000", allowCleartext = false)
        }
    }

    @Test
    fun `debug mode accepts a local cleartext server`() {
        assertEquals(
            "http://192.168.1.20:8000",
            ServerUrl.normalize("http://192.168.1.20:8000/", allowCleartext = true),
        )
    }
}
