package com.puvucraft.radio.data

import com.puvucraft.radio.PlayerStreamFormat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.CookieManager
import java.net.CookiePolicy
import java.net.HttpURLConnection
import java.net.URI
import java.nio.charset.StandardCharsets

class ApiException(
    val status: Int,
    val code: String,
    override val message: String,
    cause: Throwable? = null,
) : IOException(message, cause)

class RadioApiClient private constructor(
    val baseUrl: String,
    private val sessionStore: EncryptedSessionStore?,
) {
    private val cookies = CookieManager(null, CookiePolicy.ACCEPT_ORIGINAL_SERVER)

    suspend fun login(username: String, password: String): RadioUser {
        val body = JSONObject()
            .put("username", username)
            .put("password", password)
        return parseUser(requestObject("/api/auth/login", "POST", body))
    }

    suspend fun me(): RadioUser =
        parseUser(requestObject("/api/auth/me"))

    suspend fun channels(): List<RadioChannel> {
        val response = requestArray("/api/channels")
        return buildList(response.length()) {
            for (index in 0 until response.length()) {
                add(parseChannel(response.getJSONObject(index)))
            }
        }
    }

    suspend fun playerKey(): PlayerKeyState =
        parsePlayerKeyState(requestObject("/api/auth/player-key"))

    suspend fun regeneratePlayerKey(): PlayerKeyState =
        parsePlayerKeyState(
            requestObject(
                path = "/api/auth/player-key/regenerate",
                method = "POST",
                requireCsrf = true,
            ),
        )

    suspend fun createStreamTicket(
        channelId: Long,
        streamFormat: PlayerStreamFormat,
    ): StreamTicket {
        val body = JSONObject()
            .put("channel_id", channelId)
            .put("stream_format", streamFormat.wireValue)
        val response = requestObject(
            path = "/api/auth/player-key/url",
            method = "POST",
            body = body,
            requireCsrf = true,
        )
        return StreamTicket(
            url = TlsCompatibility.rewriteStreamUrl(response.getString("url")),
            channelId = response.getLong("channel_id"),
            streamFormat = PlayerStreamFormat.fromWireValue(
                response.optString("stream_format"),
            ) ?: streamFormat,
        )
    }

    suspend fun logout() {
        requestText(
            path = "/api/auth/logout",
            method = "POST",
            requireCsrf = true,
        )
        cookies.cookieStore.removeAll()
    }

    fun forgetSession() {
        cookies.cookieStore.removeAll()
        sessionStore?.clear()
    }

    fun hasRestoredSession(): Boolean = cookies.cookieStore.cookies.any {
        !it.hasExpired()
    }

    private suspend fun requestObject(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        requireCsrf: Boolean = false,
    ): JSONObject {
        val text = requestText(path, method, body, requireCsrf)
        return try {
            JSONObject(text ?: "{}")
        } catch (error: JSONException) {
            throw ApiException(0, "invalid_response", "服务器返回了无法识别的数据", error)
        }
    }

    private suspend fun requestArray(path: String): JSONArray {
        val text = requestText(path)
        return try {
            JSONArray(text ?: "[]")
        } catch (error: JSONException) {
            throw ApiException(0, "invalid_response", "服务器返回了无法识别的数据", error)
        }
    }

    private suspend fun requestText(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        requireCsrf: Boolean = false,
    ): String? = withContext(Dispatchers.IO) {
        val uri = TlsCompatibility.networkUri(URI.create("$baseUrl$path"))
        val connection = try {
            (uri.toURL().openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = API_TIMEOUT_MILLIS
                readTimeout = API_TIMEOUT_MILLIS
                useCaches = false
                setRequestProperty("Accept", "application/json")
                setRequestProperty("User-Agent", USER_AGENT)

                cookies.get(uri, emptyMap()).forEach { (name, values) ->
                    setRequestProperty(name, values.joinToString("; "))
                }

                if (requireCsrf) {
                    val csrf = cookies.cookieStore.cookies
                        .lastOrNull { it.name == CSRF_COOKIE && !it.hasExpired() }
                        ?.value
                        ?: throw ApiException(
                            401,
                            "missing_session",
                            "登录会话已失效，请重新登录",
                        )
                    setRequestProperty(CSRF_HEADER, csrf)
                }

                if (body != null) {
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json; charset=utf-8")
                    outputStream.writer(StandardCharsets.UTF_8).use {
                        it.write(body.toString())
                    }
                }
            }
        } catch (error: ApiException) {
            throw error
        } catch (error: IOException) {
            throw ApiException(0, "network_error", "无法连接电台服务器，请检查地址和网络", error)
        }

        try {
            val status = connection.responseCode
            storeResponseCookies(uri, connection)
            val responseStream = if (status in 200..299) {
                connection.inputStream
            } else {
                connection.errorStream
            }
            val responseText = responseStream
                ?.bufferedReader(StandardCharsets.UTF_8)
                ?.use { it.readText() }
                ?.takeIf { it.isNotBlank() }

            if (status !in 200..299) {
                throw errorFromResponse(status, responseText)
            }
            responseText
        } catch (error: ApiException) {
            throw error
        } catch (error: IOException) {
            throw ApiException(0, "network_error", "与电台服务器的连接已中断", error)
        } finally {
            connection.disconnect()
        }
    }

    private fun storeResponseCookies(uri: URI, connection: HttpURLConnection) {
        val headers = buildMap<String, List<String>> {
            connection.headerFields.forEach { (name, values) ->
                if (name != null && values != null) {
                    put(name, values.filterNotNull())
                }
            }
        }
        cookies.put(uri, headers)
        sessionStore?.save(baseUrl, cookies.cookieStore.cookies)
    }

    private fun errorFromResponse(status: Int, body: String?): ApiException {
        val payload = body?.let { runCatching { JSONObject(it) }.getOrNull() }
        val code = payload?.optString("code")?.takeIf { it.isNotBlank() } ?: "http_$status"
        val message = payload?.optString("message")?.takeIf { it.isNotBlank() }
            ?: when (status) {
                401 -> "登录会话已失效，请重新登录"
                403 -> "当前账号无权执行此操作"
                404 -> "请求的电台资源不存在"
                429 -> "操作过于频繁，请稍后重试"
                in 500..599 -> "电台服务器暂时不可用"
                else -> "请求失败（$status）"
            }
        return ApiException(status, code, message)
    }

    companion object {
        private const val API_TIMEOUT_MILLIS = 20_000
        private const val CSRF_COOKIE = "radio_csrf"
        private const val CSRF_HEADER = "X-CSRF-Token"
        private const val USER_AGENT = "PuvuCraftRadio-Android/1.0"

        fun create(
            rawBaseUrl: String,
            allowCleartext: Boolean,
            sessionStore: EncryptedSessionStore? = null,
            restoreSession: Boolean = false,
            persistSessionChanges: Boolean = true,
        ): RadioApiClient {
            val baseUrl = ServerUrl.normalize(rawBaseUrl, allowCleartext)
            return RadioApiClient(
                baseUrl = baseUrl,
                sessionStore = sessionStore.takeIf { persistSessionChanges },
            ).also { client ->
                if (restoreSession) {
                    val cookieUri = TlsCompatibility.networkUri(URI.create(baseUrl))
                    sessionStore?.restore(baseUrl)?.forEach {
                        client.cookies.cookieStore.add(cookieUri, it)
                    }
                }
            }
        }
    }
}
