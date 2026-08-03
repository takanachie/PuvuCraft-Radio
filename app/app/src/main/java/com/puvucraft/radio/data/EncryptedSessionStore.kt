package com.puvucraft.radio.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpCookie
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class EncryptedSessionStore(context: Context) {
    private val preferences = context.getSharedPreferences(
        PREFERENCES_NAME,
        Context.MODE_PRIVATE,
    )
    private val knownRecords = mutableMapOf<String, CookieRecord>()

    @Synchronized
    fun restore(baseUrl: String): List<HttpCookie> {
        val payload = decryptPayload() ?: return emptyList()
        if (payload.optString("base_url") != baseUrl) {
            clear()
            return emptyList()
        }

        val now = System.currentTimeMillis()
        val storedCookies = payload.optJSONArray("cookies") ?: JSONArray()
        val restored = buildList {
            for (index in 0 until storedCookies.length()) {
                val record = runCatching {
                    CookieRecord.fromJson(storedCookies.getJSONObject(index))
                }.getOrNull() ?: continue
                if (record.expiresAtMillis <= now) continue

                val remainingSeconds = (record.expiresAtMillis - now) / 1_000
                if (remainingSeconds <= 0) continue
                knownRecords[record.key] = record
                add(
                    HttpCookie(record.name, record.value).apply {
                        record.domain?.let { domain = it }
                        path = record.path
                        secure = record.secure
                        isHttpOnly = record.httpOnly
                        maxAge = remainingSeconds
                    },
                )
            }
        }

        if (restored.isEmpty()) {
            clear()
        } else if (restored.size != storedCookies.length()) {
            save(baseUrl, restored)
        }
        return restored
    }

    @Synchronized
    fun save(baseUrl: String, cookies: List<HttpCookie>) {
        val now = System.currentTimeMillis()
        val active = cookies
            .filterNot { it.hasExpired() || it.maxAge == 0L }
            .map { cookie ->
                val key = cookie.recordKey()
                val existing = knownRecords[key]
                    ?.takeIf { it.value == cookie.value && it.expiresAtMillis > now }
                existing ?: CookieRecord(
                    name = cookie.name,
                    value = cookie.value,
                    domain = cookie.domain,
                    path = cookie.path ?: "/",
                    secure = cookie.secure,
                    httpOnly = cookie.isHttpOnly,
                    expiresAtMillis = now + cookieLifetimeMillis(cookie),
                )
            }
            .filter { it.expiresAtMillis > now }

        if (active.isEmpty()) {
            clear()
            return
        }

        knownRecords.clear()
        active.associateByTo(knownRecords) { it.key }
        val payload = JSONObject()
            .put("version", 1)
            .put("base_url", baseUrl)
            .put(
                "cookies",
                JSONArray().apply {
                    active.forEach { put(it.toJson()) }
                },
            )
        encryptPayload(payload.toString())
    }

    @Synchronized
    fun clear() {
        knownRecords.clear()
        preferences.edit().clear().apply()
    }

    private fun cookieLifetimeMillis(cookie: HttpCookie): Long {
        val serverLifetime = cookie.maxAge
            .takeIf { it >= 0 }
            ?.coerceAtMost(MAX_SESSION_SECONDS)
            ?: MAX_SESSION_SECONDS
        return serverLifetime.coerceAtLeast(0) * 1_000
    }

    private fun encryptPayload(plaintext: String) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        val ciphertext = cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8))
        preferences.edit()
            .putString(IV_KEY, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString(DATA_KEY, Base64.encodeToString(ciphertext, Base64.NO_WRAP))
            .apply()
    }

    private fun decryptPayload(): JSONObject? {
        val encodedIv = preferences.getString(IV_KEY, null) ?: return null
        val encodedData = preferences.getString(DATA_KEY, null) ?: return null
        return runCatching {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                getOrCreateKey(),
                GCMParameterSpec(128, Base64.decode(encodedIv, Base64.NO_WRAP)),
            )
            val plaintext = cipher.doFinal(
                Base64.decode(encodedData, Base64.NO_WRAP),
            )
            JSONObject(String(plaintext, Charsets.UTF_8))
        }.getOrElse {
            clear()
            null
        }
    }

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEY_STORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }

        return KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            ANDROID_KEY_STORE,
        ).run {
            init(
                KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build(),
            )
            generateKey()
        }
    }

    private data class CookieRecord(
        val name: String,
        val value: String,
        val domain: String?,
        val path: String,
        val secure: Boolean,
        val httpOnly: Boolean,
        val expiresAtMillis: Long,
    ) {
        val key: String
            get() = listOf(name, domain.orEmpty(), path).joinToString("\u0000")

        fun toJson(): JSONObject = JSONObject()
            .put("name", name)
            .put("value", value)
            .put("domain", domain)
            .put("path", path)
            .put("secure", secure)
            .put("http_only", httpOnly)
            .put("expires_at", expiresAtMillis)

        companion object {
            fun fromJson(payload: JSONObject): CookieRecord = CookieRecord(
                name = payload.getString("name"),
                value = payload.getString("value"),
                domain = payload.optString("domain").takeIf {
                    payload.has("domain") && !payload.isNull("domain") && it.isNotBlank()
                },
                path = payload.optString("path", "/"),
                secure = payload.optBoolean("secure", true),
                httpOnly = payload.optBoolean("http_only", false),
                expiresAtMillis = payload.getLong("expires_at"),
            )
        }
    }

    private fun HttpCookie.recordKey(): String =
        listOf(name, domain.orEmpty(), path ?: "/").joinToString("\u0000")

    companion object {
        private const val PREFERENCES_NAME = "puvucraft_encrypted_session"
        private const val IV_KEY = "iv"
        private const val DATA_KEY = "data"
        private const val KEY_ALIAS = "puvucraft_radio_session_v1"
        private const val ANDROID_KEY_STORE = "AndroidKeyStore"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val MAX_SESSION_SECONDS = 30L * 24 * 60 * 60
    }
}
