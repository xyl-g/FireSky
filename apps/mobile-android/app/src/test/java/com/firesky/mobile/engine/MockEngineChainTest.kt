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
                    client.call(
                        "forecast",
                        JSONObject().put("session", session).put("confirmed", true).put("cycle_age_confirmed", true),
                    )
                }
                val models = forecast.optJSONObject("model_results")
                val gfs = models?.optJSONObject("gfs")
                val ec = models?.optJSONObject("ec")
                assertNotNull(gfs)
                assertNotNull(ec)
                assertEquals("cirrus", gfs?.optJSONObject("cloud_genus_proxy")?.optString("label"))
                assertNotNull(gfs?.optJSONObject("cross_section")?.optJSONArray("distances_km"))

                val inventory = withTimeout(10_000) { client.call("inventory") }
                val categories = inventory.optJSONObject("categories")?.optJSONArray("categories")
                assertNotNull(categories)
                assertEquals(6, categories?.length())

                val savedRetention = withTimeout(10_000) {
                    client.call(
                        "save_retention_settings",
                        JSONObject().put("settings", JSONObject().put("gfs_cycles", 4)),
                    )
                }
                assertTrue(savedRetention.optBoolean("configured"))
                val applied = withTimeout(10_000) { client.call("apply_retention_settings") }
                assertTrue(applied.optBoolean("configured"))
                assertTrue(applied.optJSONObject("result")!!.optInt("removed_count") > 0)

                val syncRun = withTimeout(10_000) {
                    client.call(
                        "data_sync_run",
                        JSONObject().put("sources", org.json.JSONArray(listOf("gfs", "ec"))).put("force", true),
                    )
                }
                assertTrue(syncRun.optBoolean("forced"))
                assertEquals("synced", syncRun.optJSONObject("sources")?.optJSONObject("gfs")?.optString("status"))
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
    fun mockNetworkProbeStreamsIOSRowsAndSharedResultShape() {
        val client = EngineClient(MockTransport())
        client.start(JSONObject())
        try {
            runBlocking {
                val hello = withTimeout(10_000) { client.call("hello") }
                val capabilities = hello.optJSONArray("capabilities")
                assertTrue((0 until (capabilities?.length() ?: 0)).any { capabilities?.optString(it) == "network_probe" })

                val progress = java.util.Collections.synchronizedList(mutableListOf<JSONObject>())
                val response = withTimeout(10_000) {
                    client.call(
                        "network_probe",
                        JSONObject().put("ecmwf_source", "aws").put("aerosol_source", "geos-fp"),
                    ) { event -> progress += event }
                }
                assertTrue(response.optBoolean("all_passed"))
                assertTrue(response.optString("checked_at").isNotEmpty())
                val rows = response.optJSONArray("datasets")
                assertEquals(3, rows?.length())
                assertEquals(listOf("gfs", "ec", "cams"), (0 until (rows?.length() ?: 0)).map { rows!!.getJSONObject(it).optString("source") })
                assertEquals("GEOS-FP · NASA OPeNDAP", rows?.getJSONObject(2)?.optString("title"))
                for (index in 0 until (rows?.length() ?: 0)) {
                    val row = rows!!.getJSONObject(index)
                    assertEquals("passed", row.optString("state"))
                    assertTrue(row.optString("host").isNotEmpty())
                    assertTrue(row.optInt("bytes_received") > 0)
                    assertTrue(row.optInt("elapsed_ms") > 0)
                }
                assertEquals(6, progress.size)
                assertEquals(3, progress.count { it.optJSONObject("probe")?.optString("state") == "testing" })
                assertEquals(3, progress.count { it.optJSONObject("probe")?.optString("state") == "passed" })
            }
        } finally {
            client.stop()
        }
    }

    @Test
    fun mockRegionalAndNowcastChains() {
        val client = EngineClient(MockTransport())
        client.start(JSONObject())
        try {
            runBlocking {
                val hello = withTimeout(10_000) { client.call("hello") }
                assertTrue((hello.optJSONArray("regions")?.length() ?: 0) >= 1)

                val regionalCheck = withTimeout(10_000) {
                    client.call(
                        "check",
                        JSONObject().put("kind", "regional").put("payload", payload().put("model_source", "gfs").put("regions", org.json.JSONArray(listOf("china")))),
                    )
                }
                val regSession = regionalCheck.optString("session")
                withTimeout(20_000) { client.call("prepare", JSONObject().put("session", regSession)) }
                val regForecast = withTimeout(20_000) {
                    client.call("forecast", JSONObject().put("session", regSession).put("confirmed", true))
                }
                val gfsReg = regForecast.optJSONObject("model_results")?.optJSONObject("gfs")
                assertNotNull(gfsReg)
                assertTrue((gfsReg?.optJSONArray("points")?.length() ?: 0) > 0)
                assertEquals("available", gfsReg?.optJSONObject("city_ranking")?.optString("status"))

                val nowcastCheck = withTimeout(10_000) {
                    client.call(
                        "check",
                        JSONObject().put("kind", "nowcast").put("payload", payload().put("forecast_mode", "nowcast").put("model_source", "satellite")),
                    )
                }
                val nowSession = nowcastCheck.optString("session")
                withTimeout(20_000) { client.call("prepare", JSONObject().put("session", nowSession)) }
                val nowForecast = withTimeout(20_000) {
                    client.call("forecast", JSONObject().put("session", nowSession).put("confirmed", true))
                }
                val nowcastObj = nowForecast.optJSONObject("nowcast")
                assertNotNull(nowcastObj)
                assertTrue(nowcastObj!!.has("lagrangian_b13_trend_k_per_hour"))
                assertTrue(nowcastObj.has("horizon_gap_score"))
                assertTrue(nowcastObj.has("overcast_shadow_factor"))
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
