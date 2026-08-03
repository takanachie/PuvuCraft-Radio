package com.puvucraft.radio.data

import java.net.URI

object ServerUrl {
    fun normalize(rawValue: String, allowCleartext: Boolean): String {
        val raw = rawValue.trim()
        require(raw.isNotEmpty()) { "请输入电台服务器地址" }

        val withScheme = if ("://" in raw) raw else "https://$raw"
        val uri = runCatching { URI(withScheme).normalize() }
            .getOrElse { throw IllegalArgumentException("服务器地址格式不正确") }
        val scheme = uri.scheme?.lowercase()

        require(scheme == "https" || (allowCleartext && scheme == "http")) {
            if (allowCleartext) {
                "服务器地址必须使用 http:// 或 https://"
            } else {
                "正式版仅允许连接 HTTPS 服务器"
            }
        }
        require(!uri.host.isNullOrBlank()) { "服务器地址缺少有效域名或 IP" }
        require(uri.userInfo == null) { "服务器地址不能包含用户名或密码" }
        require(uri.query == null && uri.fragment == null) {
            "服务器地址不能包含查询参数或片段"
        }

        val normalizedPath = uri.path
            .orEmpty()
            .trimEnd('/')
            .takeUnless { it == "/" }
            .orEmpty()

        return URI(
            scheme,
            null,
            uri.host,
            uri.port,
            normalizedPath,
            null,
            null,
        ).toASCIIString()
    }
}
