package com.firesky.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material.icons.filled.SatelliteAlt
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.R
import com.firesky.mobile.store.NowcastProfileStats
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import com.firesky.mobile.store.parseNowcastProfileCells
import com.firesky.mobile.store.parseNowcastProfileStats
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * 卫星实况模式（NowcastPresentation.swift / Workbench.swift 同构）：
 * 4 小时实况窗口引导横幅 + 实况 Hero 卡 + 5 分钟质量曲线 + 卫星/运动/雷达指标
 * + Lagrangian 趋势 & 视场缺口/阴天遮蔽诊断 + 卫星概率剖面 & 大气剖面。
 */
@Composable
fun NowcastGuidanceBanner() {
    val cool = coolAccentColor()
    Surface(
        Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = cool.copy(alpha = 0.10f),
        border = BorderStroke(0.75.dp, cool.copy(alpha = 0.30f)),
    ) {
        Row(
            Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            IconBadge(Icons.Filled.Schedule, tint = cool)
            Text(
                stringResource(R.string.nowcast_window_guidance),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.78f),
                lineHeight = 15.sp,
            )
        }
    }
}

@Composable
fun NowcastResultSection(store: WorkbenchStore, state: WorkbenchState) {
    val report = if (state.resultKind == "nowcast") state.result else null
    if (report == null) {
        NowcastEmptyCard()
        return
    }

    val profileStats = remember(report) { parseNowcastProfileStats(report) }
    val nowcast = report.optJSONObject("nowcast") ?: JSONObject()

    NowcastHeroCard(store, state, report, nowcast, profileStats)

    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            NowcastTabs(store, state)
            when (state.resultTab) {
                "概览" -> NowcastOverviewContent(report, nowcast)
                "大气剖面" -> NowcastProfileContent(report, profileStats)
                "逐时台账" -> NowcastLedgerContent(report)
                else -> NowcastDiagnosticContent(report, nowcast, profileStats)
            }
        }
    }
}

@Composable
private fun NowcastEmptyCard() {
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                FireSkyMarkScene(size = 54.dp)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(stringResource(R.string.nowcast_empty_title), fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        stringResource(R.string.nowcast_empty_body),
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    )
                }
            }
        }
    }
}

@Composable
private fun NowcastHeroCard(
    store: WorkbenchStore,
    state: WorkbenchState,
    report: JSONObject,
    nowcast: JSONObject,
    profileStats: NowcastProfileStats,
) {
    val warm = MaterialTheme.colorScheme.primary
    val cool = coolAccentColor()
    val tnumStyle = TextStyle(fontFeatureSettings = "tnum")

    WorkSurface(Modifier.fillMaxWidth(), emphasized = true) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                "${state.resultTitle} " + stringResource(
                    if (state.resultPhenomenon == "dawn") R.string.phenomenon_dawn else R.string.phenomenon_dusk,
                ),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
            )

            // 抑制内部码 native-nowcast-map-publication-not-requested
            val mapCapability = report.optJSONObject("map_capability")
            if (mapCapability != null && !mapCapability.optBoolean("available", true)) {
                val friendlyReason = friendlyEngineMessage(mapCapability.optString("reason", ""))
                if (friendlyReason.isNotEmpty()) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Icon(Icons.Filled.Warning, contentDescription = null, modifier = Modifier.size(14.dp), tint = Color(0xFFD98F24))
                        Text(friendlyReason, fontSize = 11.sp, color = Color(0xFFD98F24))
                    }
                }
            }

            if (state.resultParametersStale) {
                Surface(
                    Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(10.dp),
                    color = warm.copy(alpha = 0.10f),
                ) {
                    Row(Modifier.padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(stringResource(R.string.stale_result_banner), fontSize = 11.sp, modifier = Modifier.weight(1f))
                        androidx.compose.material3.TextButton(onClick = { store.beginCheck() }, enabled = !state.busy && state.online) {
                            Text(stringResource(R.string.rerun_with_new_params), fontSize = 11.sp)
                        }
                    }
                }
            }

            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f).padding(end = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Box(Modifier.size(7.dp).background(cool, CircleShape))
                        Text(stringResource(R.string.nowcast_mode_label), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    }
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text(
                            fmt(report.optNum("peak_quality_index") ?: report.optNum("quality_index")),
                            fontSize = 34.sp,
                            fontWeight = FontWeight.Bold,
                            style = tnumStyle,
                            color = warm,
                        )
                        Spacer(Modifier.width(4.dp))
                        Text("/ 2.5", fontSize = 12.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f), modifier = Modifier.padding(bottom = 5.dp))
                    }
                    Text(
                        report.optString("peak_quality_level", "").ifEmpty {
                            report.optString("quality_level", stringResource(R.string.quality_index_fallback))
                        },
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.72f),
                    )
                    Text(
                        stringResource(R.string.recommended_window, windowLabel(report)),
                        fontSize = 11.sp,
                        style = tnumStyle,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    )
                }

                Box(Modifier.width(0.5.dp).height(128.dp).background(MaterialTheme.colorScheme.onBackground.copy(alpha = 0.14f)))

                Column(Modifier.weight(1f).padding(start = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(stringResource(R.string.best_time), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                    Text(timeLabel(report.optString("best_time_local")), fontSize = 24.sp, fontWeight = FontWeight.Bold, style = tnumStyle)
                    Text(
                        stringResource(R.string.nowcast_coverage_fmt, "${profileStats.statusLabel} · ${profileStats.coverageText}"),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    )
                    val src = nowcast.optString("source", "").ifEmpty { stringResource(R.string.not_provided) }
                    val ch = nowcast.optString("image_channel", "").ifEmpty { "—" }
                    Text(
                        stringResource(R.string.nowcast_source_channel_fmt, src, ch),
                        fontSize = 10.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f),
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
    }
}

@Composable
private fun NowcastTabs(store: WorkbenchStore, state: WorkbenchState) {
    Segmented(
        options = listOf("概览", "大气剖面", "诊断与时间窗", "逐时台账"),
        titles = listOf(
            stringResource(R.string.tab_overview),
            stringResource(R.string.tab_profile),
            stringResource(R.string.tab_diagnostic),
            stringResource(R.string.tab_ledger),
        ),
        selection = state.resultTab,
        onSelect = { store.selectResultTab(it) },
        accent = true,
    )
}

@Composable
private fun NowcastOverviewContent(report: JSONObject, nowcast: JSONObject) {
    val metrics = listOf(
        Triple("near_cloud_ratio", stringResource(R.string.nc_near_cloud), stringResource(R.string.nc_near_cloud_cap)),
        Triple("far_cloud_ratio", stringResource(R.string.nc_far_cloud), stringResource(R.string.nc_far_cloud_cap)),
        Triple("cloud_blanket_score", stringResource(R.string.nc_blanket), stringResource(R.string.nc_blanket_cap)),
        Triple("cloud_texture_score", stringResource(R.string.nc_texture), stringResource(R.string.nc_texture_cap)),
        Triple("horizon_clearance_score", stringResource(R.string.nc_horizon), stringResource(R.string.nc_horizon_cap)),
        Triple("satellite_low_cloud_state", stringResource(R.string.nc_low_cloud), stringResource(R.string.nc_low_cloud_cap)),
        Triple("satellite_motion_quality", stringResource(R.string.nc_motion), stringResource(R.string.nc_motion_cap)),
        Triple("geometric_path_blocking_risk", stringResource(R.string.nc_path_risk), stringResource(R.string.nc_path_risk_cap)),
        Triple("calibrated_sample_coverage_ratio", stringResource(R.string.nc_sample_cov), stringResource(R.string.nc_sample_cov_cap)),
        Triple("confidence", stringResource(R.string.nc_confidence), stringResource(R.string.nc_confidence_cap)),
    )

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        for (pair in metrics.chunked(2)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                for ((key, title, caption) in pair) {
                    WorkSurface(Modifier.weight(1f), grouped = true, cornerRadius = 12.dp) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                            Text(title, fontSize = 11.sp, fontWeight = FontWeight.Medium)
                            Text(
                                formatNowcastMetricValue(key, nowcast),
                                fontSize = 17.sp,
                                fontWeight = FontWeight.Bold,
                                style = TextStyle(fontFeatureSettings = "tnum"),
                            )
                            Text(caption, fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f), maxLines = 2)
                        }
                    }
                }
                if (pair.size == 1) Spacer(Modifier.weight(1f))
            }
        }

        NowcastCurveCard(report)
        report.optJSONObject("spectral_color")?.let { SpectralColorCard(it) }
        report.optJSONObject("azimuth_radar")?.let { AzimuthRadarCard(it) }
    }
}

@Composable
private fun NowcastCurveCard(report: JSONObject) {
    val points = remember(report) {
        val arr = report.optJSONArray("steps") ?: JSONArray()
        (0 until arr.length()).mapNotNull { i ->
            val step = arr.optJSONObject(i) ?: return@mapNotNull null
            if (!step.optBoolean("valid", true)) return@mapNotNull null
            val q = step.optNum("instant_quality_index") ?: return@mapNotNull null
            val t = parseLocalTime(step.optString("valid_time_local")) ?: return@mapNotNull null
            (t.toEpochSecond(java.time.ZoneOffset.UTC) / 60) to q
        }.sortedBy { it.first }
    }
    val cool = coolAccentColor()
    val faint = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.18f)

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.nowcast_curve_title), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.weight(1f))
            Text("5 min", fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f))
        }
        Box(Modifier.fillMaxWidth().height(150.dp)) {
            Canvas(Modifier.fillMaxWidth().height(150.dp)) {
                if (points.isEmpty()) return@Canvas
                val minT = points.first().first
                val maxT = points.last().first
                val span = (maxT - minT).coerceAtLeast(1L)
                fun x(t: Long): Float = size.width * ((t - minT).toFloat() / span)
                fun y(v: Double): Float = size.height * (1f - (v / 2.5).toFloat().coerceIn(0f, 1f))
                for (level in listOf(0.0, 1.25, 2.5)) {
                    drawLine(faint, Offset(0f, y(level)), Offset(size.width, y(level)), strokeWidth = 1f)
                }
                if (points.size >= 2) {
                    val path = Path()
                    points.forEachIndexed { index, pt ->
                        val off = Offset(x(pt.first), y(pt.second))
                        if (index == 0) path.moveTo(off.x, off.y) else path.lineTo(off.x, off.y)
                    }
                    drawPath(path, cool, style = Stroke(width = 4f, cap = StrokeCap.Round))
                }
                for (pt in points) {
                    drawCircle(cool, radius = 4f, center = Offset(x(pt.first), y(pt.second)))
                }
            }
            if (points.isEmpty()) {
                Text(stringResource(R.string.no_curve), fontSize = 10.sp, modifier = Modifier.align(Alignment.Center))
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NowcastProfileContent(report: JSONObject, stats: NowcastProfileStats) {
    val cells = remember(report) { parseNowcastProfileCells(report) }
    val cool = coolAccentColor()
    val dark = LocalFireSkyDark.current

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            IconBadge(Icons.Filled.SatelliteAlt, tint = cool)
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.nowcast_profile_title), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                Text(
                    "${stats.statusLabel} · ${stats.sampleText} · ${stats.coverageText}",
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            }
        }

        if (cells.isNotEmpty()) {
            val maxDist = max(100.0, cells.maxOf { it.endKM })
            val maxHeight = max(12.0, cells.maxOf { it.topKM })
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(170.dp)
                    .background(if (dark) Color(0xFF0E1728) else Color(0xFFEEF3F8), RoundedCornerShape(10.dp)),
            ) {
                Canvas(Modifier.matchParentSize().padding(8.dp)) {
                    for (cell in cells) {
                        val l = (cell.startKM / maxDist * size.width).toFloat()
                        val r = (cell.endKM / maxDist * size.width).toFloat()
                        val t = ((1.0 - cell.topKM / maxHeight) * size.height).toFloat()
                        val b = ((1.0 - cell.bottomKM / maxHeight) * size.height).toFloat()
                        val w = max(2f, r - l)
                        val h = max(2f, b - t)
                        val color = if (cell.probability != null) {
                            cool.copy(alpha = (0.12f + 0.78f * (cell.probability / 100.0).toFloat()).coerceIn(0.12f, 0.90f))
                        } else {
                            Color(0xFFD98F24).copy(alpha = 0.25f)
                        }
                        drawRect(color = color, topLeft = Offset(l, t), size = Size(w, h))
                    }
                }
            }
        } else {
            Text(stringResource(R.string.nowcast_profile_empty), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        }

        if (stats.bands.isNotEmpty()) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                stats.bands.forEach { ChipText(it) }
                stats.validTimeText?.let { ChipText("$it UTC") }
            }
        }
    }
}

@Composable
private fun NowcastDiagnosticContent(
    report: JSONObject,
    nowcast: JSONObject,
    profileStats: NowcastProfileStats,
) {
    val lagrangianTrend = nowcast.optNum("lagrangian_b13_trend_k_per_hour")
    val horizonGap = nowcast.optNum("horizon_gap_score")
    val overcastShadow = nowcast.optNum("overcast_shadow_factor")

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.nowcast_diag_title), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)

        WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                DiagLine(stringResource(R.string.nowcast_diag_coverage), "${profileStats.statusLabel} · ${profileStats.coverageText}")
                DiagLine(
                    stringResource(R.string.nowcast_diag_lagrangian),
                    lagrangianTrend?.let { "%+.2f K/h".format(it) } ?: "—",
                )
                if (horizonGap != null) {
                    DiagLine(stringResource(R.string.nowcast_diag_horizon_gap), "%.0f%%".format(horizonGap * 100.0))
                }
                if (overcastShadow != null) {
                    DiagLine(stringResource(R.string.nowcast_diag_overcast_shadow), "%.2f".format(overcastShadow))
                }
                DiagLine(stringResource(R.string.nc_horizon), formatNowcastMetricValue("horizon_clearance_score", nowcast))
                DiagLine(stringResource(R.string.nc_blanket), formatNowcastMetricValue("cloud_blanket_score", nowcast))
                DiagLine(stringResource(R.string.nowcast_diag_motion_corr), fmt(nowcast.optNum("motion_correlation"), 2))
                DiagLine(
                    stringResource(R.string.nowcast_diag_advection_horizon),
                    nowcast.optNum("satellite_advection_horizon_hours")?.let { "%.2f h".format(it) } ?: "—",
                )
            }
        }

        // 雷达信息
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            IconBadge(Icons.Filled.Radar, tint = coolAccentColor())
            Text(stringResource(R.string.nowcast_radar_title), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
        }
        WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                DiagLine(stringResource(R.string.nowcast_radar_source), nowcast.optString("radar_source", "").ifEmpty { "—" })
                DiagLine(stringResource(R.string.nowcast_radar_max_dbz), nowcast.optNum("radar_max_dbz")?.let { "%.1f dBZ".format(it) } ?: "—")
                DiagLine(stringResource(R.string.nowcast_radar_reliability), formatNowcastMetricValue("radar_reliability_score", nowcast))
                DiagLine(stringResource(R.string.nowcast_radar_convective), formatNowcastMetricValue("effective_convective_risk", nowcast))
            }
        }

        val summaryArr = report.optJSONArray("summary")
        if (summaryArr != null && summaryArr.length() > 0) {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                for (i in 0 until summaryArr.length()) {
                    val line = summaryArr.optString(i)
                    if (line.isNotEmpty()) {
                        Text("• $line", fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.72f))
                    }
                }
            }
        }

        Text(
            stringResource(R.string.cycle_line, report.optString("cycle_time_utc", "--")),
            fontSize = 10.sp,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
        )
    }
}

@Composable
private fun NowcastLedgerContent(report: JSONObject) {
    val arr = report.optJSONArray("steps") ?: JSONArray()
    val steps = (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    val tnum = TextStyle(fontFeatureSettings = "tnum")
    if (steps.isEmpty()) {
        Text(stringResource(R.string.no_ledger), fontSize = 12.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        return
    }
    Column {
        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
            Text(stringResource(R.string.col_time), Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
            Text(stringResource(R.string.col_quality), Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
            Text(stringResource(R.string.col_solar), Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
        }
        HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
        steps.forEach { step ->
            Row(Modifier.fillMaxWidth().padding(vertical = 7.dp)) {
                Text(timeLabel(step.optString("valid_time_local")), Modifier.weight(1f), fontSize = 11.sp, style = tnum)
                Text(fmt(step.optNum("instant_quality_index"), 3), Modifier.weight(1f), fontSize = 11.sp, style = tnum)
                Text(fmt(step.optNum("solar_elevation_deg"), 1), Modifier.weight(1f), fontSize = 11.sp, style = tnum)
            }
            HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f))
        }
    }
}

@Composable
private fun DiagLine(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f))
        Spacer(Modifier.weight(1f))
        Text(value, fontSize = 11.sp, fontWeight = FontWeight.Medium, style = TextStyle(fontFeatureSettings = "tnum"))
    }
}

private fun formatNowcastMetricValue(key: String, obs: JSONObject): String {
    if (key == "satellite_motion_quality") {
        return when (val v = obs.optString(key)) {
            "observation" -> "原始观测"
            "strong" -> "可靠"
            "decayed" -> "已衰减"
            "unknown" -> "未知"
            "diagnostic-only" -> "仅供诊断"
            "" -> "—"
            else -> v
        }
    }
    if (key == "satellite_low_cloud_state") {
        return when (val v = obs.optString(key)) {
            "detected" -> "检测到低云"
            "clear" -> "无低云"
            "unknown" -> "未知"
            "" -> "—"
            else -> v
        }
    }
    val value = obs.optNum(key) ?: return "—"
    return when (key) {
        "near_cloud_ratio", "far_cloud_ratio", "geometric_path_blocking_risk",
        "calibrated_sample_coverage_ratio", "confidence", "convective_score_ceiling",
        "radar_reliability_score", "cloud_blanket_score", "cloud_texture_score",
        "horizon_clearance_score", "effective_convective_risk", "effective_path_blocking_risk",
        -> "%.0f%%".format(value * 100.0)
        else -> "%.2f".format(value)
    }
}
