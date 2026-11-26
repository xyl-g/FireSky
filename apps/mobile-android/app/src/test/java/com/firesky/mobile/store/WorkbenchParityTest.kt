package com.firesky.mobile.store

import com.firesky.mobile.ui.cloudGenusRawBrief
import com.firesky.mobile.ui.cloudLayerMetricValue
import com.firesky.mobile.ui.cycleLabel
import com.firesky.mobile.ui.friendlyEngineMessage
import com.firesky.mobile.ui.isBestTimeWithinFourHours
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/**
 * macOS v1.9.10 parity logic unit tests:
 * cloud_genus_proxy formatting, internal-code suppression, cycle-age confirmation gating,
 * per-page error isolation, and stale parameter banner scoping.
 */
class WorkbenchParityTest {

    @Test
    fun cloudGenusBriefFormatsLabelAndFallback() {
        val direct = JSONObject()
            .put("label", "cirrus")
            .put("confidence", 0.724)
        assertEquals("卷云（代理 72%）", cloudGenusRawBrief(direct))

        val unknownWithCompatible = JSONObject()
            .put("label", "unknown")
            .put("compatible_genera", JSONArray(listOf("altocumulus", "altostratus")))
        assertEquals("高积云等（代理）", cloudGenusRawBrief(unknownWithCompatible))

        val emptyUnknown = JSONObject().put("label", "unknown")
        assertNull(cloudGenusRawBrief(emptyUnknown))
    }

    @Test
    fun suppressesInternalNowcastMapCode() {
        assertEquals("", friendlyEngineMessage("native-nowcast-map-publication-not-requested"))
        assertEquals("", friendlyEngineMessage("  native-nowcast-map-publication-not-requested  "))
        assertTrue(
            friendlyEngineMessage(
                "data cycle is unverified or older than the latest release; explicit cycle-age confirmation is required",
            ).contains("使用当前缓存继续"),
        )
    }

    @Test
    fun cloudLayerAndCycleLabelFormatting() {
        val report = JSONObject()
            .put("dominant_burnable_cloud_base_agl_mean_m", 6500.0)
            .put("dominant_burnable_cloud_top_agl_mean_m", 9200.0)
            .put("cycle_time_utc", "2026-11-12T06:00:00Z")
        assertEquals("6.5–9.2 km", cloudLayerMetricValue(report))
        assertEquals("2026-11-12 06:00Z", cycleLabel("gfs", report))
    }

    @Test
    fun errorScopingIsolatesPages() {
        val scoped = WorkbenchState(error = "搜索超时", errorPage = "workbench")
        assertEquals("搜索超时", scoped.errorText("workbench"))
        assertEquals("", scoped.errorText("data"))
        assertEquals("", scoped.errorText("history"))
        assertEquals("", scoped.errorText("settings"))

        val global = WorkbenchState(error = "引擎未连接", errorPage = null)
        assertEquals("引擎未连接", global.errorText("workbench"))
        assertEquals("引擎未连接", global.errorText("data"))
    }

    @Test
    fun cycleAgeConfirmationDetectsUnverifiedOrOldDatasets() {
        val freshState = WorkbenchState(
            ready = true,
            rows = listOf(
                DataRow(source = "gfs", ready = true, old = false, verified = true, missing = 0, latest = true),
                DataRow(source = "ecmwf", ready = true, old = false, verified = true, missing = 0, latest = true),
            ),
        )
        assertFalse(freshState.needsCycleAgeConfirmation)
        assertTrue(freshState.flaggedCycleAgeRows.isEmpty())

        val staleState = WorkbenchState(
            ready = true,
            rows = listOf(
                DataRow(source = "gfs", ready = true, old = true, verified = true, missing = 0, latest = false),
                DataRow(source = "cams", ready = true, old = false, verified = false, missing = 0, latest = true),
            ),
        )
        assertTrue(staleState.needsCycleAgeConfirmation)
        assertEquals(2, staleState.flaggedCycleAgeRows.size)
    }

    @Test
    fun staleParameterBannerReflectsOriginalResultParams() {
        val today = LocalDate.of(2026, 11, 24)
        val params = JSONObject()
            .put("name", "杭州")
            .put("lat", 30.2741)
            .put("lon", 120.1551)
            .put("elevation_m", 8.0)
            .put("timezone_name", "Asia/Shanghai")
            .put("target_date", today.toString())
            .put("phenomenon", "dusk")
            .put("ecmwf_source", "ecmwf")

        val matchingState = WorkbenchState(
            location = LocationState(name = "杭州", lat = 30.2741, lon = 120.1551, elevation = 8.0, timezone = "Asia/Shanghai"),
            targetDate = today,
            phenomenon = "dusk",
            ecmwfSource = "ecmwf",
            result = JSONObject().put("model_results", JSONObject()),
            resultParams = params,
            resultKind = "point",
        )
        assertFalse(matchingState.resultParametersStale)
        assertEquals("杭州 · 2026-11-24", matchingState.resultTitle)

        val editedState = matchingState.copy(
            location = matchingState.location.copy(name = "北京", lat = 39.9042, lon = 116.4074),
            phenomenon = "dawn",
        )
        assertTrue(editedState.resultParametersStale)
        assertEquals("杭州 · 2026-11-24", editedState.resultTitle)
        assertEquals("dusk", editedState.resultPhenomenon)
    }

    @Test
    fun parsesRegionalModelAndCityRankingContract() {
        val report = JSONObject()
            .put("cycle_time_utc", "2026-11-24T00:00:00Z")
            .put("precision", JSONObject().put("point_spacing_deg", 0.25).put("model_grid_spacing_deg", 0.25))
            .put("regions", JSONArray(listOf("china")))
            .put("point_count", 2)
            .put("success_count", 2)
            .put("error_count", 0)
            .put(
                "points",
                JSONArray()
                    .put(
                        JSONObject()
                            .put("lat", 30.25)
                            .put("lon", 120.25)
                            .put("peak_quality_index", 2.05)
                            .put("quality_index", 1.90)
                            .put("peak_quality_level", "绝佳")
                            .put("best_time_local", "2026-11-24T17:45:00")
                            .put("dominant_burnable_pressure_family", "high")
                            .put("confidence_score", 0.85)
                            .put("quality_components", JSONObject().put("burnable_cloud_coverage_effective", 0.64).put("duration_minutes", 40.0)),
                    )
                    .put(
                        JSONObject()
                            .put("lat", 31.25)
                            .put("lon", 121.50)
                            .put("peak_quality_index", 1.30)
                            .put("quality_level", "优秀"),
                    ),
            )
            .put("errors", JSONArray())
            .put(
                "city_ranking",
                JSONObject()
                    .put("status", "available")
                    .put(
                        "items",
                        JSONArray()
                            .put(JSONObject().put("name", "上海").put("lat", 31.23).put("lon", 121.47).put("peak_quality_index", 1.72))
                            .put(JSONObject().put("name", "杭州").put("lat", 30.27).put("lon", 120.15).put("peak_quality_index", 2.15)),
                    ),
            )

        val forecast = parseRegionalForecast(
            JSONObject().put("model_results", JSONObject().put("gfs", report)).put("model_errors", JSONObject()),
        )
        val gfs = forecast.model("gfs")
        assertTrue(gfs.available)
        assertEquals(2, gfs.validCount)
        assertEquals(1, gfs.highCount)
        assertEquals(0, gfs.missingCount)
        assertEquals(2.05, gfs.peak!!.quality!!, 1e-6)
        assertEquals("available", gfs.cityRankingStatus)
        assertEquals("杭州", gfs.cityRanking.first().name)
        assertEquals(2.15, gfs.cityRanking.first().quality!!, 1e-6)
        assertEquals("点 0.25° · 模型 0.25°", gfs.precision)
    }

    @Test
    fun parsesNowcastProfileStatsAndCells() {
        val report = JSONObject()
            .put("best_time_local", "2026-11-24T17:55:00")
            .put(
                "steps",
                JSONArray().put(
                    JSONObject()
                        .put("valid_time_local", "2026-11-24T17:55:00")
                        .put("valid", true)
                        .put("solar_azimuth_deg", 250.0),
                ),
            )
            .put(
                "nowcast",
                JSONObject().put(
                    "satellite_vertical_profile",
                    JSONObject()
                        .put("status", "available")
                        .put("valid_time_utc", "2026-11-24T09:20:00Z")
                        .put("sample_count", 2)
                        .put("usable_sample_count", 2)
                        .put("coverage", 0.95)
                        .put("pressure_levels_hpa", JSONArray(listOf(300, 500)))
                        .put("bands", JSONArray(listOf("B07", "B13", "B15")))
                        .put(
                            "samples",
                            JSONArray().put(
                                JSONObject()
                                    .put("distance_km", 80.0)
                                    .put("bearing_deg", 250.0)
                                    .put("status", "retrieved")
                                    .put(
                                        "levels",
                                        JSONArray()
                                            .put(JSONObject().put("pressure_hpa", 300).put("height_m", 9100.0).put("status", "retrieved").put("hsd_posterior_percent", 75.0))
                                            .put(JSONObject().put("pressure_hpa", 500).put("height_m", 5600.0).put("status", "retrieved").put("hsd_posterior_percent", 35.0)),
                                    ),
                            ),
                        ),
                ),
            )

        val stats = parseNowcastProfileStats(report)
        assertTrue(stats.present)
        assertEquals("完整覆盖", stats.statusLabel)
        assertEquals("2/2", stats.sampleText)
        assertEquals("95%", stats.coverageText)
        assertEquals(listOf("B07", "B13", "B15"), stats.bands)

        val cells = parseNowcastProfileCells(report)
        assertEquals(2, cells.size)
        assertEquals(35.0, cells[0].probability!!, 1e-6)
        assertEquals(75.0, cells[1].probability!!, 1e-6)
    }

    @Test
    fun parsesHelloRegionsAndBuildsRegionalAndNowcastPayloads() {
        val hello = JSONObject().put(
            "regions",
            JSONArray()
                .put(JSONObject().put("code", "china").put("label", "中国").put("bounds", JSONArray(listOf(73.0, 18.0, 136.0, 54.0))))
                .put(JSONObject().put("code", "east-asia").put("label", "东亚").put("bounds", JSONArray(listOf(63.0, 12.0, 155.0, 60.0)))),
        )
        val presets = parseRegionalPresets(hello)
        assertEquals(2, presets.size)
        assertEquals("china", presets[0].code)

        val state = WorkbenchState(workbenchMode = "regional", selectedRegionCode = "china", regionalModel = "ec")
        val regionalPayload = buildForecastPayload(state, "regional")
        assertEquals("ec", regionalPayload.optString("model_source"))
        assertEquals("china", regionalPayload.optJSONArray("regions")?.optString(0))

        val nowcastPayload = buildForecastPayload(state, "nowcast")
        assertEquals("nowcast", nowcastPayload.optString("forecast_mode"))
        assertEquals("satellite", nowcastPayload.optString("model_source"))
        assertEquals("auto", nowcastPayload.optString("satellite_source"))
    }

    @Test
    fun parsesPairingUriWithAndWithoutToken() {
        val withToken = parsePairingUri(
            "firesky://connect?url=https%3A%2F%2Fsky.example.com%2F&token=abc%2B123%3D",
        )
        assertEquals("https://sky.example.com" to "abc+123=", withToken)

        val lanNoToken = parsePairingUri("firesky://connect?url=http://192.168.1.42:8765/api/v1/firesky")
        assertEquals("http://192.168.1.42:8765" to null, lanNoToken)

        val blankToken = parsePairingUri("firesky://connect?url=http://10.0.0.5:8765&token=%20%20")
        assertEquals("http://10.0.0.5:8765" to null, blankToken)

        assertNull(parsePairingUri(""))
        assertNull(parsePairingUri("https://sky.example.com"))
        assertNull(parsePairingUri("firesky://other?url=https://sky.example.com"))
        assertNull(parsePairingUri("firesky://connect?token=onlytoken"))
        assertNull(parsePairingUri("firesky://connect?url=ftp://invalid.host"))
    }

    @Test
    fun detectsBestTimeWithinFourHoursOfNow() {
        val anchor = Instant.parse("2026-11-26T10:00:00Z").toEpochMilli()
        assertTrue(isBestTimeWithinFourHours("2026-11-26T12:30:00Z", nowMillis = anchor))
        assertTrue(isBestTimeWithinFourHours("2026-11-26T06:00:00Z", nowMillis = anchor))
        assertTrue(isBestTimeWithinFourHours("2026-11-26T18:30:00", timezoneName = "Asia/Shanghai", nowMillis = anchor))
        assertFalse(isBestTimeWithinFourHours("2026-11-26T14:01:00Z", nowMillis = anchor))
        assertFalse(isBestTimeWithinFourHours("2026-11-26T05:59:00Z", nowMillis = anchor))
        assertFalse(isBestTimeWithinFourHours(null, nowMillis = anchor))
        assertFalse(isBestTimeWithinFourHours("not-a-timestamp", nowMillis = anchor))
    }
}
