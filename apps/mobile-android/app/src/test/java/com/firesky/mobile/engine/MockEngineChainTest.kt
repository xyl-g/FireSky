package com.firesky.mobile.engine

import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/**
 * mock 通道 JVM 回归（任务书 T1 §5.5「mock 通道仍可切换、原单测不回归」）：
 * MockTransport + EngineClient 的 hello → check → prepare → forecast 全链
 * 不依赖 .so / Android 框架，保证 MN2 默认真引擎后 mock 演示通道仍完整。
 */
class MockEngineChainTest {

    private fun payload(): JSONObject = JSONObject()
        .put("name", "杭州")
        .put("lat", 30.2741)
        .put("lon", 120.1551)
        .put("elevation_m", 8.0)
        .put("timezone_name", "Asia/Shanghai")
        .put("target_date", LocalDate.now().toString())
        .put("phenomenon", "dusk")
        .put("model_source", "both")
        .put("workers", 1)
        .put("forecast_mode", "model")
        .put("satellite_source", "off")
        .put("ecmwf_source", "ecmwf")

    @Test
    fun mockFullChain() {
        val client = EngineClient(MockTransport())
        client.start(JSONObject())
        try {
            runBlocking {
                val hello = withTimeout(10_000) { client.call("hello") }
                assertEquals(1, hello.optInt("protocol"))
                assertEquals("firesky-mock", hello.optString("engine"))

                val check = withTimeout(10_000) {
                    client.call("check", JSONObject().put("kind", "point").put("payload", payload()))
                }
                val session = check.optString("session")
                assertTrue("check 应返回会话令牌", session.isNotEmpty())

                val progressEvents = mutableListOf<JSONObject>()
                val prepared = withTimeout(30_000) {
                    client.call("prepare", JSONObject().put("session", session)) { event ->
                        progressEvents.add(event)
                    }
                }
                assertEquals(true, prepared.optJSONObject("report")?.optBoolean("ready"))
                assertTrue("prepare 应发进度帧", progressEvents.isNotEmpty())

                val forecast = withTimeout(30_000) {
                    client.call("forecast", JSONObject().put("session", session).put("confirmed", true))
                }
                val models = forecast.optJSONObject("model_results")
                assertNotNull(models?.optJSONObject("gfs"))
                assertNotNull(models?.optJSONObject("ec"))
            }
        } finally {
            client.stop()
        }
    }

    @Test
    fun mockSearchAndMetadata() {
        val client = EngineClient(MockTransport())
        client.start(JSONObject())
        try {
            runBlocking {
                val point = withTimeout(10_000) { client.call("search", JSONObject().put("query", "杭州")) }
                assertEquals("杭州", point.optString("name"))
                val metadata = withTimeout(10_000) {
                    client.call(
                        "metadata",
                        JSONObject().put("lat", point.optDouble("lat")).put("lon", point.optDouble("lon")),
                    )
                }
                assertEquals("Asia/Shanghai", metadata.optString("timezone_name"))
            }
        } finally {
            client.stop()
        }
    }

    @Test
    fun stopFailsPendingCalls() {
        val client = EngineClient(MockTransport())
        client.start(JSONObject())
        client.stop()
        val error = runCatching {
            runBlocking { client.call("hello") }
        }.exceptionOrNull()
        assertTrue("stop 后调用应抛 EngineError", error is EngineError)
    }
}