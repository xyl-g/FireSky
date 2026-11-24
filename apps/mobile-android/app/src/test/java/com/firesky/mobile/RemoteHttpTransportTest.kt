package com.firesky.mobile

import com.firesky.mobile.engine.EngineClient
import com.firesky.mobile.engine.EngineError
import com.firesky.mobile.engine.FrameListener
import com.firesky.mobile.engine.RemoteHttpTransport
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.InputStream
import java.io.OutputStream
import java.lang.reflect.Proxy
import java.net.InetSocketAddress
import java.net.URI
import java.util.Collections
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * RemoteHttpTransport 离线 JVM 单测：使用 JDK 内置 `com.sun.net.httpserver.HttpServer`
 * 在 `127.0.0.1:0` 上启动真实 HTTP 服务器，覆盖：
 * 1) `normalizeBaseUrl`（尾部斜杠、`/api/v1/firesky` 后缀、非法 scheme 拒绝）
 * 2) 完整 RPC 链路：`GET /desktop/status` → `POST /desktop/jobs`（Bearer Token 且无 Origin）
 *    → `GET /desktop/jobs/{id}`（`onProgress` 增量回调与终态 `result`）→ `POST /release`
 * 3) `401 Unauthorized` 友好鉴权提示且不泄露原始 Token
 * 4) `403 untrusted_host` 提示开启 Mac 远程访问
 * 5) `404` for `save_cams_key` 提示仅限 Mac 本地配置
 * 6) `409 engine_exited` 自动刷新 `instance_id` 并重试一次成功
 * 7) `cancel(requestId)` 命中 `/desktop/jobs/{id}/cancel`
 */
class RemoteHttpTransportTest {

    private class Exchange(private val raw: Any) {
        private val cls = Class.forName("com.sun.net.httpserver.HttpExchange")
        private val headersCls = Class.forName("com.sun.net.httpserver.Headers")

        val requestMethod: String
            get() = cls.getMethod("getRequestMethod").invoke(raw) as String

        val requestURI: URI
            get() = cls.getMethod("getRequestURI").invoke(raw) as URI

        fun header(name: String): String? {
            val headers = cls.getMethod("getRequestHeaders").invoke(raw)
            return headersCls.getMethod("getFirst", String::class.java).invoke(headers, name) as String?
        }

        fun readBody(): String {
            val stream = cls.getMethod("getRequestBody").invoke(raw) as InputStream
            return stream.bufferedReader(Charsets.UTF_8).use { it.readText() }
        }

        fun replyJson(status: Int, body: JSONObject) {
            val bytes = body.toString().toByteArray(Charsets.UTF_8)
            val respHeaders = cls.getMethod("getResponseHeaders").invoke(raw)
            headersCls.getMethod("add", String::class.java, String::class.java)
                .invoke(respHeaders, "Content-Type", "application/json")
            cls.getMethod("sendResponseHeaders", Int::class.javaPrimitiveType, Long::class.javaPrimitiveType)
                .invoke(raw, status, bytes.size.toLong())
            val out = cls.getMethod("getResponseBody").invoke(raw) as OutputStream
            out.use { it.write(bytes) }
        }

        fun close() {
            cls.getMethod("close").invoke(raw)
        }
    }

    private fun withServer(
        handler: (Exchange) -> Unit,
        block: (baseUrl: String) -> Unit,
    ) {
        val serverCls = Class.forName("com.sun.net.httpserver.HttpServer")
        val handlerCls = Class.forName("com.sun.net.httpserver.HttpHandler")
        val server = serverCls
            .getMethod("create", InetSocketAddress::class.java, Int::class.javaPrimitiveType)
            .invoke(null, InetSocketAddress("127.0.0.1", 0), 0)
        val proxyHandler = Proxy.newProxyInstance(handlerCls.classLoader, arrayOf(handlerCls)) { _, method, args ->
            if (method.name == "handle" && args != null && args.isNotEmpty()) {
                val exchange = Exchange(args[0])
                try {
                    handler(exchange)
                } finally {
                    exchange.close()
                }
            }
            null
        }
        serverCls.getMethod("createContext", String::class.java, handlerCls).invoke(server, "/", proxyHandler)
        serverCls.getMethod("start").invoke(server)
        try {
            val addr = serverCls.getMethod("getAddress").invoke(server) as InetSocketAddress
            block("http://127.0.0.1:${addr.port}")
        } finally {
            serverCls.getMethod("stop", Int::class.javaPrimitiveType).invoke(server, 0)
        }
    }

    @Test
    fun normalizeBaseUrlHandlesSuffixesTrailingSlashesAndInvalidSchemes() {
        assertEquals(
            "https://firesky.example.com",
            RemoteHttpTransport.normalizeBaseUrl("  https://firesky.example.com/  "),
        )
        assertEquals(
            "https://firesky.example.com",
            RemoteHttpTransport.normalizeBaseUrl("https://firesky.example.com/api/v1/firesky"),
        )
        assertEquals(
            "https://firesky.example.com",
            RemoteHttpTransport.normalizeBaseUrl("https://firesky.example.com/api/v1/firesky/"),
        )
        assertEquals(
            "http://192.168.1.50:8765",
            RemoteHttpTransport.normalizeBaseUrl("http://192.168.1.50:8765///"),
        )
        for (invalid in listOf("", "   ", "192.168.1.50:8765", "ftp://firesky.example.com", "https://")) {
            val err = runCatching { RemoteHttpTransport.normalizeBaseUrl(invalid) }.exceptionOrNull()
            assertTrue("无效地址应抛 EngineError：$invalid", err is EngineError)
        }
    }

    @Test
    fun fullRpcChainAuthProgressResultAndRelease() {
        val token = "secret-bearer-token-0123456789abcdef"
        val requests = CopyOnWriteArrayList<String>()
        val authHeaders = CopyOnWriteArrayList<String?>()
        val originHeaders = CopyOnWriteArrayList<String?>()
        val pollCount = AtomicInteger(0)
        var capturedRequestId = ""

        withServer({ exchange ->
            val path = exchange.requestURI.path + (exchange.requestURI.rawQuery?.let { "?$it" } ?: "")
            requests.add("${exchange.requestMethod} $path")
            authHeaders.add(exchange.header("Authorization"))
            originHeaders.add(exchange.header("Origin"))

            when {
                exchange.requestMethod == "GET" && exchange.requestURI.path == "/api/v1/firesky/desktop/status" -> {
                    exchange.replyJson(
                        200,
                        JSONObject()
                            .put("instance_id", "inst-1")
                            .put("running", true)
                            .put("workspace", "/Users/mac/FireSky")
                            .put("protocol", 1)
                            .put("engine", "firesky-rust"),
                    )
                }
                exchange.requestMethod == "POST" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs" -> {
                    val req = JSONObject(exchange.readBody())
                    capturedRequestId = req.getString("request_id")
                    assertEquals("inst-1", req.getString("instance_id"))
                    assertEquals("forecast", req.getString("method"))
                    exchange.replyJson(
                        202,
                        JSONObject()
                            .put("request_id", capturedRequestId)
                            .put("instance_id", "inst-1")
                            .put("status", "running"),
                    )
                }
                exchange.requestMethod == "GET" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs/$capturedRequestId" -> {
                    assertEquals("instance_id=inst-1", exchange.requestURI.rawQuery)
                    when (pollCount.incrementAndGet()) {
                        1 -> exchange.replyJson(
                            200,
                            JSONObject()
                                .put("request_id", capturedRequestId)
                                .put("instance_id", "inst-1")
                                .put("status", "running")
                                .put("progress_seq", 1)
                                .put("progress", JSONObject().put("percent", 42.0).put("source", "gfs")),
                        )
                        else -> exchange.replyJson(
                            200,
                            JSONObject()
                                .put("request_id", capturedRequestId)
                                .put("instance_id", "inst-1")
                                .put("status", "done")
                                .put("progress_seq", 2)
                                .put("progress", JSONObject().put("percent", 100.0).put("source", "ec"))
                                .put("result", JSONObject().put("score", 2.1).put("ok", true)),
                        )
                    }
                }
                exchange.requestMethod == "POST" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs/$capturedRequestId/release" -> {
                    val req = JSONObject(exchange.readBody())
                    assertEquals("inst-1", req.getString("instance_id"))
                    exchange.replyJson(200, JSONObject().put("released", true))
                }
                else -> exchange.replyJson(404, JSONObject())
            }
        }) { baseUrl ->
            val transport = RemoteHttpTransport(
                baseUrl = "$baseUrl/api/v1/firesky/",
                token = token,
                pollDelayProvider = { 5L },
            )
            val client = EngineClient(transport)
            client.start(JSONObject())
            try {
                val progressEvents = Collections.synchronizedList(mutableListOf<JSONObject>())
                val result = runBlocking {
                    withTimeout(10_000) {
                        client.call("forecast", JSONObject().put("city", "hangzhou")) { event ->
                            progressEvents.add(event)
                        }
                    }
                }
                assertTrue(result.optBoolean("ok"))
                assertEquals(2.1, result.optDouble("score"), 1e-6)
                assertEquals(2, progressEvents.size)
                assertEquals(42.0, progressEvents[0].optDouble("percent"), 1e-6)
                assertEquals(100.0, progressEvents[1].optDouble("percent"), 1e-6)

                assertTrue(requests.any { it == "GET /api/v1/firesky/desktop/status" })
                assertTrue(requests.any { it == "POST /api/v1/firesky/desktop/jobs" })
                assertTrue(requests.any { it.startsWith("GET /api/v1/firesky/desktop/jobs/$capturedRequestId") })
                assertTrue(requests.any { it == "POST /api/v1/firesky/desktop/jobs/$capturedRequestId/release" })
                assertTrue(authHeaders.all { it == "Bearer $token" })
                for (origin in originHeaders) {
                    assertNull("远程请求绝不能携带 Origin 头", origin)
                }
            } finally {
                client.stop()
            }
        }
    }

    @Test
    fun unauthorizedMapsToFriendlyMessageWithoutLeakingToken() {
        val secretToken = "super-secret-token-should-never-leak-987654"
        withServer({ exchange ->
            exchange.replyJson(
                401,
                JSONObject().put(
                    "error",
                    JSONObject()
                        .put("code", "authentication_required")
                        .put("message", "Rejected Bearer $secretToken"),
                ),
            )
        }) { baseUrl ->
            val client = EngineClient(RemoteHttpTransport(baseUrl, secretToken, pollDelayProvider = { 5L }))
            client.start(JSONObject())
            try {
                val error = runCatching {
                    runBlocking { withTimeout(10_000) { client.call("hello") } }
                }.exceptionOrNull()
                assertTrue(error is EngineError)
                val msg = error!!.message.orEmpty()
                assertTrue(msg.contains("鉴权失败"))
                assertFalse("异常文案绝不能泄露原始令牌", msg.contains(secretToken))
            } finally {
                client.stop()
            }
        }
    }

    @Test
    fun forbiddenUntrustedHostMapsToMacRemoteAccessHint() {
        withServer({ exchange ->
            exchange.replyJson(
                403,
                JSONObject().put(
                    "error",
                    JSONObject()
                        .put("code", "untrusted_host")
                        .put("message", "local HTTP requires a loopback Host"),
                ),
            )
        }) { baseUrl ->
            val err = runCatching {
                RemoteHttpTransport.testConnection(baseUrl, "some-token")
            }.exceptionOrNull()
            assertTrue(err is EngineError)
            assertTrue(err!!.message.orEmpty().contains("Mac「设置 → 高级 → 局域网与隧道服务」"))
        }
    }

    @Test
    fun saveCamsKeyNotFoundMapsToLocalOnlyHint() {
        withServer({ exchange ->
            when {
                exchange.requestURI.path == "/api/v1/firesky/desktop/status" -> exchange.replyJson(
                    200,
                    JSONObject()
                        .put("instance_id", "inst-cams")
                        .put("running", true)
                        .put("workspace", "/Users/mac/FireSky")
                        .put("protocol", 1)
                        .put("engine", "firesky-rust"),
                )
                exchange.requestURI.path == "/api/v1/firesky/desktop/jobs" -> exchange.replyJson(
                    404,
                    JSONObject().put(
                        "error",
                        JSONObject()
                            .put("code", "invalid_request")
                            .put("message", "desktop workflow method is not available over HTTP"),
                    ),
                )
                else -> exchange.replyJson(404, JSONObject())
            }
        }) { baseUrl ->
            val client = EngineClient(RemoteHttpTransport(baseUrl, "tok", pollDelayProvider = { 5L }))
            client.start(JSONObject())
            try {
                val err = runCatching {
                    runBlocking { withTimeout(10_000) { client.call("save_cams_key", JSONObject().put("key", "abc")) } }
                }.exceptionOrNull()
                assertTrue(err is EngineError)
                assertTrue(err!!.message.orEmpty().contains("不支持通过网络修改 Mac 的 CAMS Key"))
            } finally {
                client.stop()
            }
        }
    }

    @Test
    fun conflictEngineExitedRefreshesInstanceIdAndRetriesOnce() {
        val statusCalls = AtomicInteger(0)
        val submitInstances = CopyOnWriteArrayList<String>()
        var capturedRequestId = ""

        withServer({ exchange ->
            when {
                exchange.requestMethod == "GET" && exchange.requestURI.path == "/api/v1/firesky/desktop/status" -> {
                    val seq = statusCalls.incrementAndGet()
                    val inst = if (seq == 1) "inst-old" else "inst-new"
                    exchange.replyJson(
                        200,
                        JSONObject()
                            .put("instance_id", inst)
                            .put("running", true)
                            .put("workspace", "/Users/mac/FireSky")
                            .put("protocol", 1)
                            .put("engine", "firesky-rust"),
                    )
                }
                exchange.requestMethod == "POST" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs" -> {
                    val req = JSONObject(exchange.readBody())
                    capturedRequestId = req.getString("request_id")
                    val inst = req.getString("instance_id")
                    submitInstances.add(inst)
                    if (inst == "inst-old") {
                        exchange.replyJson(
                            409,
                            JSONObject().put(
                                "error",
                                JSONObject()
                                    .put("code", "engine_exited")
                                    .put("message", "desktop runtime changed"),
                            ),
                        )
                    } else {
                        exchange.replyJson(
                            202,
                            JSONObject()
                                .put("request_id", capturedRequestId)
                                .put("instance_id", inst)
                                .put("status", "running"),
                        )
                    }
                }
                exchange.requestMethod == "GET" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs/$capturedRequestId" -> {
                    exchange.replyJson(
                        200,
                        JSONObject()
                            .put("request_id", capturedRequestId)
                            .put("instance_id", "inst-new")
                            .put("status", "done")
                            .put("progress_seq", 0)
                            .put("result", JSONObject().put("recovered", true)),
                    )
                }
                exchange.requestMethod == "POST" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs/$capturedRequestId/release" -> {
                    exchange.replyJson(200, JSONObject().put("released", true))
                }
                else -> exchange.replyJson(404, JSONObject())
            }
        }) { baseUrl ->
            val client = EngineClient(RemoteHttpTransport(baseUrl, "tok", pollDelayProvider = { 5L }))
            client.start(JSONObject())
            try {
                val result = runBlocking {
                    withTimeout(10_000) { client.call("hello") }
                }
                assertTrue(result.optBoolean("recovered"))
                assertEquals(2, statusCalls.get())
                assertEquals(listOf("inst-old", "inst-new"), submitInstances)
            } finally {
                client.stop()
            }
        }
    }

    @Test
    fun cancelHitsCancelEndpointAndStopsJob() {
        val jobStartedLatch = CountDownLatch(1)
        val cancelHitLatch = CountDownLatch(1)
        var capturedRequestId = ""
        val cancelled = AtomicBoolean(false)

        withServer({ exchange ->
            when {
                exchange.requestMethod == "GET" && exchange.requestURI.path == "/api/v1/firesky/desktop/status" -> {
                    exchange.replyJson(
                        200,
                        JSONObject()
                            .put("instance_id", "inst-cancel")
                            .put("running", true)
                            .put("workspace", "/Users/mac/FireSky")
                            .put("protocol", 1)
                            .put("engine", "firesky-rust"),
                    )
                }
                exchange.requestMethod == "POST" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs" -> {
                    val req = JSONObject(exchange.readBody())
                    capturedRequestId = req.getString("request_id")
                    exchange.replyJson(
                        202,
                        JSONObject()
                            .put("request_id", capturedRequestId)
                            .put("instance_id", "inst-cancel")
                            .put("status", "running"),
                    )
                    jobStartedLatch.countDown()
                }
                exchange.requestMethod == "GET" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs/$capturedRequestId" -> {
                    if (cancelled.get()) {
                        exchange.replyJson(
                            200,
                            JSONObject()
                                .put("request_id", capturedRequestId)
                                .put("instance_id", "inst-cancel")
                                .put("status", "cancelled")
                                .put("progress_seq", 0)
                                .put(
                                    "error",
                                    JSONObject()
                                        .put("code", "cancelled")
                                        .put("message", "desktop operation cancelled"),
                                ),
                        )
                    } else {
                        exchange.replyJson(
                            200,
                            JSONObject()
                                .put("request_id", capturedRequestId)
                                .put("instance_id", "inst-cancel")
                                .put("status", "running")
                                .put("progress_seq", 0),
                        )
                    }
                }
                exchange.requestMethod == "POST" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs/$capturedRequestId/cancel" -> {
                    val req = JSONObject(exchange.readBody())
                    assertEquals("inst-cancel", req.getString("instance_id"))
                    cancelled.set(true)
                    cancelHitLatch.countDown()
                    exchange.replyJson(200, JSONObject().put("requested", true))
                }
                exchange.requestMethod == "POST" && exchange.requestURI.path == "/api/v1/firesky/desktop/jobs/$capturedRequestId/release" -> {
                    exchange.replyJson(200, JSONObject().put("released", true))
                }
                else -> exchange.replyJson(404, JSONObject())
            }
        }) { baseUrl ->
            val transport = RemoteHttpTransport(baseUrl, "tok", pollDelayProvider = { 10L })
            val frames = Collections.synchronizedList(mutableListOf<JSONObject>())
            val terminalLatch = CountDownLatch(1)
            transport.onFrame = FrameListener { raw ->
                val obj = JSONObject(raw)
                frames.add(obj)
                if (obj.optString("type") == "error" || obj.optString("type") == "result") {
                    terminalLatch.countDown()
                }
            }
            transport.start(JSONObject())
            try {
                val reqId = "11111111-2222-4333-8444-555555555555"
                transport.send(
                    JSONObject()
                        .put("id", reqId)
                        .put("method", "prepare")
                        .put("params", JSONObject())
                        .toString(),
                )
                assertTrue("作业应启动", jobStartedLatch.await(5, TimeUnit.SECONDS))
                transport.cancel(reqId)
                assertTrue("cancel 端点应被命中", cancelHitLatch.await(5, TimeUnit.SECONDS))
                assertTrue("应收到取消终态帧", terminalLatch.await(5, TimeUnit.SECONDS))
                val last = frames.last()
                assertEquals("error", last.optString("type"))
                assertEquals("cancelled", last.optJSONObject("error")?.optString("code"))
            } finally {
                transport.stop()
            }
        }
    }
}
