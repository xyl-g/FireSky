package com.firesky.mobile.store

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import java.security.MessageDigest
import javax.net.ssl.SSLException

enum class UpdatePhase {
    IDLE,
    CHECKING,
    CURRENT,
    AVAILABLE,
    DOWNLOADING,
    VERIFYING,
    READY_TO_INSTALL,
    FAILED,
}

data class UpdateInfo(
    val version: String,
    val notes: String,
    val assetName: String,
    val assetUrl: String,
    val assetBytes: Long,
    val checksumUrl: String?,
    val releaseUrl: String,
)

sealed class UpdateCheckResult {
    data object UpToDate : UpdateCheckResult()
    data class Available(val info: UpdateInfo) : UpdateCheckResult()
}

internal class UpdateNetworkException(message: String) : IOException(message)

/**
 * FireSky Android 应用内更新器（对齐 macOS Updater.swift 与 Windows Updater.cs）：
 * - 查询 GitHub Releases latest API，403/429 限流时自动回退至 `/releases/latest` 重定向解析；
 * - 匹配 `FireSky-<version>-android-arm64.apk` 并下载到私有缓存；
 * - 若存在 `SHA256SUMS.txt` 则先校验 SHA-256 摘要，校验失败自动删除临时文件。
 */
object AppUpdater {
    const val REPOSITORY = "xyl-g/FireSky"
    const val DEFAULT_API_BASE_URL = "https://api.github.com"
    const val DEFAULT_WEB_BASE_URL = "https://github.com"
    private const val MAX_REDIRECTS = 5
    private val VERSION_PATTERN = Regex("^[A-Za-z0-9.]+$")

    /** `"v2.0.3"` / `"V2.0.3"` → `"2.0.3"`；其余原样返回。 */
    fun normalizeTag(tag: String): String {
        val trimmed = tag.trim()
        return if (trimmed.startsWith("v", ignoreCase = true)) trimmed.substring(1) else trimmed
    }

    /** 资产名约定：与 `.github/workflows/native-desktop.yml` 保持一致。 */
    fun assetName(version: String): String = "FireSky-$version-android-arm64.apk"

    /**
     * 语义化版本比较（逐段整数，缺失段按 0 补齐，支持 `v` 前缀与 `-prerelease` 后缀）。
     * 返回值：< 0 表示 `left < right`；0 表示相等；> 0 表示 `left > right`。
     */
    fun compareVersions(left: String, right: String): Int {
        val leftParts = versionSegments(left)
        val rightParts = versionSegments(right)
        val count = maxOf(leftParts.size, rightParts.size)
        for (index in 0 until count) {
            val l = leftParts.getOrElse(index) { 0 }
            val r = rightParts.getOrElse(index) { 0 }
            if (l != r) return l.compareTo(r)
        }
        return 0
    }

    private fun versionSegments(raw: String): List<Int> {
        val clean = normalizeTag(raw).substringBefore('-')
        if (clean.isEmpty()) return emptyList()
        return clean.split('.').map { it.toIntOrNull() ?: 0 }
    }

    /** 从 GitHub Release JSON 解析 Android APK 更新信息。 */
    fun parseRelease(release: JSONObject, webBaseUrl: String = DEFAULT_WEB_BASE_URL): UpdateInfo? {
        if (release.optBoolean("draft", false) || release.optBoolean("prerelease", false)) {
            return null
        }
        val tag = release.optString("tag_name", "").trim()
        val version = normalizeTag(tag)
        if (version.isEmpty() || version.length > 32 || !VERSION_PATTERN.matches(version)) {
            return null
        }
        val assets = release.optJSONArray("assets") ?: JSONArray()
        val wantedName = assetName(version)
        val assetList = (0 until assets.length()).mapNotNull { assets.optJSONObject(it) }

        val exactAsset = assetList.firstOrNull { item ->
            item.optString("name", "") == wantedName &&
                item.optString("browser_download_url", "").contains("/releases/download/")
        }
        val fallbackApk = assetList.firstOrNull { item ->
            val name = item.optString("name", "")
            val url = item.optString("browser_download_url", "")
            name.endsWith(".apk", ignoreCase = true) && url.contains("/releases/download/")
        }
        val selected = exactAsset ?: fallbackApk ?: return null
        val selectedName = selected.optString("name", wantedName)
        val assetUrl = selected.optString("browser_download_url", "")
        if (assetUrl.isEmpty()) return null

        val checksumUrl = assetList.firstOrNull { it.optString("name", "") == "SHA256SUMS.txt" }
            ?.optString("browser_download_url", "")
            ?.takeIf { it.isNotEmpty() }

        val webBase = webBaseUrl.trimEnd('/')
        val releaseUrl = release.optString("html_url", "")
            .ifEmpty { "$webBase/$REPOSITORY/releases/tag/$tag" }
        val notes = release.optString("body", "").take(1200)
        return UpdateInfo(
            version = version,
            notes = notes,
            assetName = selectedName,
            assetUrl = assetUrl,
            assetBytes = selected.optLong("size", 0L).coerceAtLeast(0L),
            checksumUrl = checksumUrl,
            releaseUrl = releaseUrl,
        )
    }

    /** 从 `SHA256SUMS.txt` 提取目标文件的小写 64 位 SHA-256 摘要。 */
    fun parseSha256Sums(content: String, targetFileName: String): String? {
        for (rawLine in content.lineSequence()) {
            val line = rawLine.trim()
            if (line.isEmpty()) continue
            val parts = line.split(Regex("\\s+"), limit = 2)
            if (parts.size < 2) continue
            val hash = parts[0].lowercase()
            if (hash.length != 64 || !hash.all { it in '0'..'9' || it in 'a'..'f' }) continue
            val namePart = parts[1].trim().removePrefix("*").substringAfterLast('/')
            if (namePart == targetFileName) return hash
        }
        return null
    }

    fun sha256Hex(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val buffer = ByteArray(64 * 1024)
        file.inputStream().use { input ->
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    suspend fun checkLatestRelease(
        currentVersion: String,
        apiBaseUrl: String = DEFAULT_API_BASE_URL,
        webBaseUrl: String = DEFAULT_WEB_BASE_URL,
    ): UpdateCheckResult = withContext(Dispatchers.IO) {
        val apiBase = apiBaseUrl.trimEnd('/')
        val webBase = webBaseUrl.trimEnd('/')
        val ua = userAgent(currentVersion)
        val url = "$apiBase/repos/$REPOSITORY/releases/latest"

        val (status, _, bodyBytes) = executeGet(
            urlString = url,
            userAgent = ua,
            accept = "application/vnd.github+json",
            connectTimeoutMs = 15_000,
            readTimeoutMs = 20_000,
        )
        if (status == 403 || status == 429) {
            return@withContext fetchLatestReleaseFallback(currentVersion, webBase, ua)
        }
        if (status != 200) {
            throw UpdateNetworkException("GitHub 返回状态 $status")
        }
        val release = runCatching { JSONObject(bodyBytes.toString(Charsets.UTF_8)) }.getOrNull()
            ?: throw UpdateNetworkException("GitHub 响应无法解析")
        val parsed = parseRelease(release, webBaseUrl = webBase)
            ?: throw UpdateNetworkException("最新 Release 未包含适用的 Android APK")
        if (compareVersions(parsed.version, currentVersion) <= 0) {
            return@withContext UpdateCheckResult.UpToDate
        }
        UpdateCheckResult.Available(parsed)
    }

    private fun fetchLatestReleaseFallback(
        currentVersion: String,
        webBase: String,
        ua: String,
    ): UpdateCheckResult {
        val (status, finalUrl, _) = executeGet(
            urlString = "$webBase/$REPOSITORY/releases/latest",
            userAgent = ua,
            accept = null,
            connectTimeoutMs = 15_000,
            readTimeoutMs = 20_000,
        )
        if (status != 200) {
            throw UpdateNetworkException("GitHub Releases 页面返回状态 $status")
        }
        val path = runCatching { URL(finalUrl).path }.getOrDefault("")
        val marker = "/releases/tag/"
        val idx = path.indexOf(marker)
        if (idx < 0) {
            throw UpdateNetworkException("未在 GitHub Releases 页面找到有效版本标签")
        }
        val tag = path.substring(idx + marker.length).trim('/')
        val version = normalizeTag(tag)
        if (version.isEmpty() || version.length > 32 || !VERSION_PATTERN.matches(version)) {
            throw UpdateNetworkException("GitHub Releases 标签格式无效：$tag")
        }
        if (compareVersions(version, currentVersion) <= 0) {
            return UpdateCheckResult.UpToDate
        }
        val name = assetName(version)
        val notes = "由于 GitHub API 请求受限（403/429），本次通过 Releases 页面重定向检测到 v$version，未获取详细更新日志；请前往 GitHub Releases 查看完整说明。"
        return UpdateCheckResult.Available(
            UpdateInfo(
                version = version,
                notes = notes,
                assetName = name,
                assetUrl = "$webBase/$REPOSITORY/releases/download/$tag/$name",
                assetBytes = 0L,
                checksumUrl = "$webBase/$REPOSITORY/releases/download/$tag/SHA256SUMS.txt",
                releaseUrl = finalUrl,
            ),
        )
    }

    suspend fun downloadAndVerifyApk(
        info: UpdateInfo,
        targetDir: File,
        currentVersion: String,
        onProgress: (received: Long, total: Long) -> Unit,
        onVerifying: () -> Unit,
    ): File = withContext(Dispatchers.IO) {
        if (!targetDir.exists()) targetDir.mkdirs()
        targetDir.listFiles()?.forEach { old -> old.delete() }

        val partFile = File(targetDir, "FireSky-${info.version}.apk.part")
        val finalFile = File(targetDir, "FireSky-${info.version}.apk")
        val ua = userAgent(currentVersion)

        try {
            val conn = openFollowingRedirects(
                urlString = info.assetUrl,
                userAgent = ua,
                accept = "application/octet-stream",
                connectTimeoutMs = 20_000,
                readTimeoutMs = 120_000,
            ).first
            try {
                val status = conn.responseCode
                if (status !in 200..299) {
                    throw UpdateNetworkException("安装包下载失败（HTTP $status）")
                }
                val expectedTotal = conn.contentLengthLong.takeIf { it > 0 } ?: info.assetBytes
                var received = 0L
                var lastReportNs = 0L
                val buffer = ByteArray(64 * 1024)
                conn.inputStream.use { input ->
                    FileOutputStream(partFile).use { output ->
                        while (true) {
                            ensureActive()
                            val count = input.read(buffer)
                            if (count < 0) break
                            output.write(buffer, 0, count)
                            received += count
                            val now = System.nanoTime()
                            if (now - lastReportNs >= 150_000_000L) {
                                lastReportNs = now
                                onProgress(received, maxOf(expectedTotal, received))
                            }
                        }
                    }
                }
                onProgress(received, maxOf(expectedTotal, received))
            } finally {
                conn.disconnect()
            }

            ensureActive()
            onVerifying()

            if (!info.checksumUrl.isNullOrEmpty()) {
                val (sumStatus, _, sumBytes) = executeGet(
                    urlString = info.checksumUrl,
                    userAgent = ua,
                    accept = null,
                    connectTimeoutMs = 15_000,
                    readTimeoutMs = 20_000,
                )
                if (sumStatus != 200) {
                    throw UpdateNetworkException("无法获取 SHA256SUMS 校验文件（HTTP $sumStatus）")
                }
                val expectedHash = parseSha256Sums(sumBytes.toString(Charsets.UTF_8), info.assetName)
                    ?: throw UpdateNetworkException("校验文件中未找到 ${info.assetName} 的 SHA-256 记录")
                val actualHash = sha256Hex(partFile)
                if (!actualHash.equals(expectedHash, ignoreCase = true)) {
                    throw UpdateNetworkException("安装包 SHA-256 校验未通过，已放弃安装")
                }
            }

            if (finalFile.exists()) finalFile.delete()
            if (!partFile.renameTo(finalFile)) {
                throw UpdateNetworkException("无法保存下载的 APK 文件")
            }
            finalFile
        } catch (t: Throwable) {
            partFile.delete()
            throw t
        }
    }

    fun friendlyError(error: Throwable): String = when (error) {
        is UpdateNetworkException -> error.message ?: "更新检查失败"
        is SocketTimeoutException -> "连接 GitHub 超时，请检查网络或代理后重试"
        is UnknownHostException -> "无法解析 GitHub 域名，请检查网络连接"
        is SSLException -> "与 GitHub 建立安全连接失败（TLS）"
        else -> error.message?.takeIf { it.isNotBlank() } ?: "更新检查失败"
    }

    private fun userAgent(currentVersion: String): String =
        "FireSky/${currentVersion.ifBlank { "dev" }} (Android)"

    private fun executeGet(
        urlString: String,
        userAgent: String,
        accept: String?,
        connectTimeoutMs: Int,
        readTimeoutMs: Int,
    ): Triple<Int, String, ByteArray> {
        val (conn, finalUrl) = openFollowingRedirects(
            urlString = urlString,
            userAgent = userAgent,
            accept = accept,
            connectTimeoutMs = connectTimeoutMs,
            readTimeoutMs = readTimeoutMs,
        )
        try {
            val status = conn.responseCode
            val stream = if (status in 200..299) conn.inputStream else conn.errorStream
            val bytes = stream?.use { it.readBytes() } ?: ByteArray(0)
            return Triple(status, finalUrl, bytes)
        } finally {
            conn.disconnect()
        }
    }

    private fun openFollowingRedirects(
        urlString: String,
        userAgent: String,
        accept: String?,
        connectTimeoutMs: Int,
        readTimeoutMs: Int,
    ): Pair<HttpURLConnection, String> {
        var current = urlString
        for (hop in 0..MAX_REDIRECTS) {
            val url = URL(current)
            val conn = (url.openConnection() as HttpURLConnection).apply {
                instanceFollowRedirects = false
                requestMethod = "GET"
                this.connectTimeout = connectTimeoutMs
                this.readTimeout = readTimeoutMs
                setRequestProperty("User-Agent", userAgent)
                if (!accept.isNullOrEmpty()) setRequestProperty("Accept", accept)
            }
            val code = conn.responseCode
            if (code in setOf(301, 302, 303, 307, 308)) {
                val location = conn.getHeaderField("Location")
                conn.disconnect()
                if (location.isNullOrBlank()) {
                    throw UpdateNetworkException("重定向响应缺少目标地址")
                }
                current = URL(url, location).toString()
                continue
            }
            return conn to current
        }
        throw UpdateNetworkException("重定向次数过多")
    }
}
