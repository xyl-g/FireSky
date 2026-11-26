package com.firesky.mobile.store

import kotlinx.coroutines.test.runTest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.nio.file.Files
import java.security.MessageDigest
import kotlin.concurrent.thread

class AppUpdaterTest {

    private data class HttpReply(
        val status: Int,
        val body: ByteArray = ByteArray(0),
        val headers: Map<String, String> = emptyMap(),
    )

    private fun withMockServer(
        handler: (path: String) -> HttpReply,
        block: suspend (baseUrl: String) -> Unit,
    ) = runTest {
        val socket = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        val worker = thread(isDaemon = true) {
            while (!socket.isClosed) {
                val client = try {
                    socket.accept()
                } catch (_: Exception) {
                    break
                }
                client.use { conn ->
                    val reader = BufferedReader(InputStreamReader(conn.getInputStream(), Charsets.UTF_8))
                    val requestLine = reader.readLine() ?: return@use
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) break
                    }
                    val path = requestLine.split(' ').getOrNull(1) ?: "/"
                    val reply = handler(path)
                    val headerText = buildString {
                        append("HTTP/1.1 ").append(reply.status).append(" Status\r\n")
                        append("Connection: close\r\n")
                        append("Content-Length: ").append(reply.body.size).append("\r\n")
                        reply.headers.forEach { (k, v) ->
                            append(k).append(": ").append(v).append("\r\n")
                        }
                        append("\r\n")
                    }
                    val out = conn.getOutputStream()
                    out.write(headerText.toByteArray(Charsets.US_ASCII))
                    if (reply.body.isNotEmpty()) out.write(reply.body)
                    out.flush()
                }
            }
        }
        try {
            block("http://127.0.0.1:${socket.localPort}")
        } finally {
            socket.close()
            worker.join(2_000)
        }
    }

    @Test
    fun normalizeAndCompareSemanticVersions() {
        assertEquals("2.0.3", AppUpdater.normalizeTag("v2.0.3"))
        assertEquals("2.0.3", AppUpdater.normalizeTag("V2.0.3"))
        assertEquals("2.0.3", AppUpdater.normalizeTag("2.0.3"))

        assertTrue(AppUpdater.compareVersions("2.1.0", "2.0.3") > 0)
        assertTrue(AppUpdater.compareVersions("2.10.0", "2.2.0") > 0)
        assertEquals(0, AppUpdater.compareVersions("v2.0.0", "2.0"))
        assertTrue(AppUpdater.compareVersions("2.0.2", "2.0.3") < 0)
    }

    @Test
    fun parseReleaseMatchesAndroidApkAndRejectsDraftOrPrerelease() {
        val release = JSONObject()
            .put("tag_name", "v2.1.0")
            .put("body", "Release notes")
            .put("html_url", "https://github.com/xyl-g/FireSky/releases/tag/v2.1.0")
            .put(
                "assets",
                JSONArray()
                    .put(
                        JSONObject()
                            .put("name", "FireSky-2.1.0-android-arm64.apk")
                            .put("browser_download_url", "https://github.com/xyl-g/FireSky/releases/download/v2.1.0/FireSky-2.1.0-android-arm64.apk")
                            .put("size", 12345L),
                    )
                    .put(
                        JSONObject()
                            .put("name", "SHA256SUMS.txt")
                            .put("browser_download_url", "https://github.com/xyl-g/FireSky/releases/download/v2.1.0/SHA256SUMS.txt"),
                    ),
            )

        val info = AppUpdater.parseRelease(release)
        assertNotNull(info)
        assertEquals("2.1.0", info!!.version)
        assertEquals("FireSky-2.1.0-android-arm64.apk", info.assetName)
        assertEquals(12345L, info.assetBytes)
        assertNotNull(info.checksumUrl)

        assertNull(AppUpdater.parseRelease(JSONObject(release.toString()).put("draft", true)))
        assertNull(AppUpdater.parseRelease(JSONObject(release.toString()).put("prerelease", true)))
        assertNull(
            AppUpdater.parseRelease(
                JSONObject(release.toString()).put(
                    "assets",
                    JSONArray().put(
                        JSONObject()
                            .put("name", "FireSky-2.1.0-macos-arm64.dmg")
                            .put("browser_download_url", "https://github.com/xyl-g/FireSky/releases/download/v2.1.0/FireSky-2.1.0-macos-arm64.dmg"),
                    ),
                ),
            ),
        )
    }

    @Test
    fun parseSha256SumsHandlesMultipleFormats() {
        val digest = "a".repeat(64)
        val text = """
            ${"b".repeat(64)}  FireSky-2.1.0-macos-arm64.dmg
            $digest *./FireSky-2.1.0-android-arm64.apk
        """.trimIndent()
        assertEquals(digest, AppUpdater.parseSha256Sums(text, "FireSky-2.1.0-android-arm64.apk"))
        assertNull(AppUpdater.parseSha256Sums(text, "missing.apk"))
    }

    @Test
    fun checkLatestReleaseReturnsAvailableUpToDateAndRateLimitFallback() {
        var apiStatus = 200
        var apiTag = "v2.1.0"
        var capturedBase = ""

        withMockServer(handler = { path ->
            when (path) {
                "/repos/xyl-g/FireSky/releases/latest" -> {
                    val payload = JSONObject()
                        .put("tag_name", apiTag)
                        .put("body", "What's new")
                        .put(
                            "assets",
                            JSONArray().put(
                                JSONObject()
                                    .put("name", "FireSky-${AppUpdater.normalizeTag(apiTag)}-android-arm64.apk")
                                    .put("browser_download_url", "$capturedBase/xyl-g/FireSky/releases/download/$apiTag/FireSky-${AppUpdater.normalizeTag(apiTag)}-android-arm64.apk")
                                    .put("size", 256L),
                            ),
                        )
                        .toString()
                        .toByteArray()
                    HttpReply(apiStatus, payload)
                }
                "/xyl-g/FireSky/releases/latest" -> HttpReply(
                    status = 302,
                    headers = mapOf("Location" to "$capturedBase/xyl-g/FireSky/releases/tag/v2.2.0"),
                )
                "/xyl-g/FireSky/releases/tag/v2.2.0" -> HttpReply(200, "<html>ok</html>".toByteArray())
                else -> HttpReply(404)
            }
        }) { baseUrl ->
            capturedBase = baseUrl
            val avail = AppUpdater.checkLatestRelease("2.0.3", apiBaseUrl = baseUrl, webBaseUrl = baseUrl)
            assertTrue(avail is UpdateCheckResult.Available)
            assertEquals("2.1.0", (avail as UpdateCheckResult.Available).info.version)

            apiTag = "v2.0.3"
            val current = AppUpdater.checkLatestRelease("2.0.3", apiBaseUrl = baseUrl, webBaseUrl = baseUrl)
            assertTrue(current is UpdateCheckResult.UpToDate)

            apiStatus = 403
            val fallback = AppUpdater.checkLatestRelease("2.0.3", apiBaseUrl = baseUrl, webBaseUrl = baseUrl)
            assertTrue(fallback is UpdateCheckResult.Available)
            val fbInfo = (fallback as UpdateCheckResult.Available).info
            assertEquals("2.2.0", fbInfo.version)
            assertEquals("$baseUrl/xyl-g/FireSky/releases/download/v2.2.0/FireSky-2.2.0-android-arm64.apk", fbInfo.assetUrl)
            assertEquals("$baseUrl/xyl-g/FireSky/releases/download/v2.2.0/SHA256SUMS.txt", fbInfo.checksumUrl)
        }
    }

    @Test
    fun downloadAndVerifyApkValidatesChecksumAndCleansUpOnMismatch() {
        val apkBytes = "fake-apk-binary-payload-v2.1.0".toByteArray()
        val goodDigest = MessageDigest.getInstance("SHA-256").digest(apkBytes)
            .joinToString("") { "%02x".format(it) }
        var sumContent = "$goodDigest  FireSky-2.1.0-android-arm64.apk\n"
        val tempDir = Files.createTempDirectory("firesky-updater-test").toFile()

        try {
            withMockServer(handler = { path ->
                when (path) {
                    "/apk" -> HttpReply(200, apkBytes)
                    "/sums" -> HttpReply(200, sumContent.toByteArray())
                    else -> HttpReply(404)
                }
            }) { baseUrl ->
                val info = UpdateInfo(
                    version = "2.1.0",
                    notes = "",
                    assetName = "FireSky-2.1.0-android-arm64.apk",
                    assetUrl = "$baseUrl/apk",
                    assetBytes = apkBytes.size.toLong(),
                    checksumUrl = "$baseUrl/sums",
                    releaseUrl = "$baseUrl/release",
                )
                val downloaded = AppUpdater.downloadAndVerifyApk(
                    info = info,
                    targetDir = tempDir,
                    currentVersion = "2.0.3",
                    onProgress = { _, _ -> },
                    onVerifying = {},
                )
                assertTrue(downloaded.isFile)
                assertEquals(apkBytes.size.toLong(), downloaded.length())

                sumContent = "${"0".repeat(64)}  FireSky-2.1.0-android-arm64.apk\n"
                try {
                    AppUpdater.downloadAndVerifyApk(
                        info = info,
                        targetDir = tempDir,
                        currentVersion = "2.0.3",
                        onProgress = { _, _ -> },
                        onVerifying = {},
                    )
                    fail("Expected SHA-256 mismatch failure")
                } catch (expected: Exception) {
                    assertTrue(expected.message?.contains("SHA-256") == true)
                    val leftovers = tempDir.listFiles()?.filter { it.name.endsWith(".part") }.orEmpty()
                    assertTrue(leftovers.isEmpty())
                }
            }
        } finally {
            tempDir.deleteRecursively()
        }
    }
}
