package com.puvucraft.radio.data

import java.net.URI

/**
 * Temporary compatibility for the production two-hop reverse proxy.
 *
 * www.phi-s.tech currently presents the certificate for puvu.phi-s.tech.
 * Preserve www as the fixed public identity while routing network transport
 * through the certificate-valid proxy hostname.
 */
internal object TlsCompatibility {
    private const val PUBLIC_HOST = "www.phi-s.tech"
    private const val TRANSPORT_HOST = "puvu.phi-s.tech"

    fun networkUri(uri: URI): URI {
        val transportHost = transportHostFor(uri) ?: return uri
        return URI(
            uri.scheme,
            uri.userInfo,
            transportHost,
            uri.port,
            uri.path,
            uri.query,
            uri.fragment,
        )
    }

    fun rewriteStreamUrl(rawUrl: String): String {
        val uri = runCatching { URI(rawUrl) }.getOrNull() ?: return rawUrl
        return networkUri(uri).toASCIIString()
    }

    internal fun transportHostFor(uri: URI): String? {
        val standardHttpsPort = uri.port == -1 || uri.port == 443
        return TRANSPORT_HOST.takeIf {
            uri.scheme.equals("https", ignoreCase = true) &&
                uri.host.equals(PUBLIC_HOST, ignoreCase = true) &&
                standardHttpsPort
        }
    }
}
