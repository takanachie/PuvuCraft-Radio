package com.puvucraft.radio.data

import java.net.URI
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TlsCompatibilityTest {
    @Test
    fun `maps only the production https hostname`() {
        assertEquals(
            "puvu.phi-s.tech",
            TlsCompatibility.transportHostFor(
                URI("https://www.phi-s.tech/api/auth/me"),
            ),
        )
        assertEquals(
            "puvu.phi-s.tech",
            TlsCompatibility.transportHostFor(
                URI("https://WWW.PHI-S.TECH:443/api/auth/me"),
            ),
        )
    }

    @Test
    fun `does not relax unrelated or cleartext hosts`() {
        assertNull(
            TlsCompatibility.transportHostFor(
                URI("https://radio.example.com/api/auth/me"),
            ),
        )
        assertNull(
            TlsCompatibility.transportHostFor(
                URI("http://www.phi-s.tech/api/auth/me"),
            ),
        )
        assertNull(
            TlsCompatibility.transportHostFor(
                URI("https://www.phi-s.tech:8443/api/auth/me"),
            ),
        )
    }

    @Test
    fun `rewrites only production stream links to the certificate host`() {
        assertEquals(
            "https://puvu.phi-s.tech/listen/aac/token/channel",
            TlsCompatibility.rewriteStreamUrl(
                "https://www.phi-s.tech/listen/aac/token/channel",
            ),
        )
        assertEquals(
            "https://radio.example.com/listen/aac/token/channel",
            TlsCompatibility.rewriteStreamUrl(
                "https://radio.example.com/listen/aac/token/channel",
            ),
        )
    }
}
