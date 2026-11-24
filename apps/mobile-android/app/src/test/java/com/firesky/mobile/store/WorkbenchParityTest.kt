package com.firesky.mobile.store

import com.firesky.mobile.ui.cloudGenusRawBrief
import com.firesky.mobile.ui.cloudLayerMetricValue
import com.firesky.mobile.ui.cycleLabel
import com.firesky.mobile.ui.friendlyEngineMessage
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

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
}
