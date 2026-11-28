package com.firesky.mobile.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Air
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.filled.SatelliteAlt
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material.icons.filled.ZoomIn
import androidx.compose.material.icons.filled.ZoomOut
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.R
import com.firesky.mobile.store.NowcastPressureRow
import com.firesky.mobile.store.NowcastProfileCell
import com.firesky.mobile.store.NowcastProfileStats
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import com.firesky.mobile.store.isStepValid
import com.firesky.mobile.store.parseNowcastProfileCells
import com.firesky.mobile.store.parseNowcastProfileStats
import com.firesky.mobile.store.resolveNowcastProfileBearing
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * 卫星实况模式（WorkNowcastResultsView / WorkSatelliteProfileView 同构）：
 * 4 小时实况窗口引导横幅 + 实况 Hero 卡 + 3 主页签（概览 / 大气剖面 / 逐时台账）+ ⓘ 诊断 ModalBottomSheet。
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

@OptIn(ExperimentalMaterial3Api::class)
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
            StreamlinedAnalysisTabs(
                selectedTab = state.resultTab,
                onSelectTab = { store.selectResultTab(it) },
                onOpenDiagnostics = { store.setOpenDiagnostics(true) },
            )
            when (state.resultTab) {
                "大气剖面" -> NowcastProfileContent(store, state, report, nowcast, profileStats)
                "逐时台账" -> NowcastLedgerContent(report)
                else -> NowcastOverviewContent(report, nowcast)
            }
        }
    }

    if (state.openDiagnostics) {
        val dark = LocalFireSkyDark.current
        ModalBottomSheet(
            onDismissRequest = { store.setOpenDiagnostics(false) },
            containerColor = if (dark) Color(0xFF182132) else Color(0xFFFCFDFF),
        ) {
            val maxHeight = LocalConfiguration.current.screenHeightDp.dp * 0.85f
            Column(
                Modifier
                    .fillMaxWidth()
                    .heightIn(max = maxHeight)
                    .padding(horizontal = 18.dp)
                    .padding(bottom = 24.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text(
                        stringResource(R.string.diag_sheet_title),
                        fontSize = 16.sp,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.weight(1f),
                    )
                    IconButton(
                        onClick = { store.setOpenDiagnostics(false) },
                        modifier = Modifier.size(32.dp),
                    ) {
                        Icon(
                            Icons.Filled.Close,
                            contentDescription = stringResource(R.string.dismiss),
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }
                HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
                Column(
                    Modifier
                        .weight(1f, fill = false)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    NowcastDiagnosticContent(report, nowcast, profileStats, showHeader = false)
                }
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
                        TextButton(onClick = { store.beginCheck() }, enabled = !state.busy && state.online) {
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

private data class NowcastMetricSpec(val key: String, val title: String, val caption: String)

/**
 * 卫星实况概览：按「卫星云况 / 运动与采样 / 光学与雷达」分组卡片（跳过未计算的 cloud_type_label，
 * 并在光学与雷达卡片标题行内嵌雷达时效状态），接续 5 分钟质量曲线、光谱预览与方位雷达。
 */
@Composable
private fun NowcastOverviewContent(report: JSONObject, nowcast: JSONObject) {
    val cloudMetrics = buildList {
        add(NowcastMetricSpec("near_cloud_ratio", stringResource(R.string.nc_near_cloud), stringResource(R.string.nc_near_cloud_cap)))
        add(NowcastMetricSpec("far_cloud_ratio", stringResource(R.string.nc_far_cloud), stringResource(R.string.nc_far_cloud_cap)))
        add(NowcastMetricSpec("cloud_blanket_score", stringResource(R.string.nc_blanket), stringResource(R.string.nc_blanket_cap)))
        add(NowcastMetricSpec("cloud_texture_score", stringResource(R.string.nc_texture), stringResource(R.string.nc_texture_cap)))
        add(NowcastMetricSpec("horizon_clearance_score", stringResource(R.string.nc_horizon), stringResource(R.string.nc_horizon_cap)))
        add(NowcastMetricSpec("satellite_low_cloud_state", stringResource(R.string.nc_low_cloud), stringResource(R.string.nc_low_cloud_cap)))
        val cloudTypeLabel = nowcast.optString("cloud_type_label", "").trim()
        if (cloudTypeLabel.isNotEmpty()) {
            add(NowcastMetricSpec("cloud_type_label", "多光谱云类", "多光谱云型反演"))
        }
    }

    val pathSamples = nowcast.optNum("geometric_path_samples")?.roundToInt()
    val pathCaption = if (pathSamples != null) {
        stringResource(R.string.nc_path_samples_fmt, pathSamples)
    } else {
        stringResource(R.string.nc_path_samples_missing)
    }
    val motionMetrics = listOf(
        NowcastMetricSpec("satellite_motion_quality", stringResource(R.string.nc_motion), stringResource(R.string.nc_motion_cap)),
        NowcastMetricSpec("geometric_path_blocking_risk", stringResource(R.string.nc_path_risk), pathCaption),
        NowcastMetricSpec("calibrated_sample_coverage_ratio", stringResource(R.string.nc_sample_cov), stringResource(R.string.nc_sample_cov_cap)),
        NowcastMetricSpec("confidence", stringResource(R.string.nc_confidence), stringResource(R.string.nc_confidence_cap)),
    )

    val radarSource = nowcast.optString("radar_source", "").trim()
    val radarTimeliness = if (radarSource.isEmpty()) {
        stringResource(R.string.nc_radar_unconnected)
    } else {
        val obsTime = timeLabel(nowcast.optString("radar_valid_time_utc"))
        val age = nowcast.optNum("radar_age_minutes")?.let { "%.0f".format(it) } ?: "—"
        stringResource(R.string.nc_radar_timeliness_fmt, radarSource, obsTime, age)
    }
    val opticsRadarMetrics = listOf(
        NowcastMetricSpec("aod", stringResource(R.string.metric_aod), stringResource(R.string.metric_aod_caption)),
        NowcastMetricSpec("effective_convective_risk", stringResource(R.string.nc_eff_convective), stringResource(R.string.nc_eff_convective_cap)),
        NowcastMetricSpec("effective_path_blocking_risk", stringResource(R.string.nc_eff_path_block), stringResource(R.string.nc_eff_path_block_cap)),
        NowcastMetricSpec("convective_score_ceiling", stringResource(R.string.nc_convective_ceiling), stringResource(R.string.nc_convective_ceiling_cap)),
    )

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        NowcastGroupedCard(
            title = stringResource(R.string.nc_group_clouds),
            subtitle = null,
            specs = cloudMetrics,
            report = report,
            nowcast = nowcast,
        )
        NowcastGroupedCard(
            title = stringResource(R.string.nc_group_motion),
            subtitle = null,
            specs = motionMetrics,
            report = report,
            nowcast = nowcast,
        )
        NowcastGroupedCard(
            title = stringResource(R.string.nc_group_optics_radar),
            subtitle = radarTimeliness,
            specs = opticsRadarMetrics,
            report = report,
            nowcast = nowcast,
        )
        NowcastCurveCard(report)
        report.optJSONObject("spectral_color")?.let { SpectralColorCard(it) }
        report.optJSONObject("azimuth_radar")?.let { AzimuthRadarCard(it) }
    }
}

@Composable
private fun NowcastGroupedCard(
    title: String,
    subtitle: String?,
    specs: List<NowcastMetricSpec>,
    report: JSONObject,
    nowcast: JSONObject,
) {
    val secondary = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f)
    val tnum = TextStyle(fontFeatureSettings = "tnum")

    WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 14.dp) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(title, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                if (subtitle != null) {
                    Spacer(Modifier.weight(1f))
                    Text(
                        subtitle,
                        fontSize = 9.5.sp,
                        color = secondary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            specs.chunked(2).forEachIndexed { rowIdx, pair ->
                if (rowIdx > 0) {
                    HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.10f))
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    for (spec in pair) {
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                            Text(
                                spec.title,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Medium,
                                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.84f),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            val valueText = if (spec.key == "aod") {
                                fmt(report.optJSONObject("aerosol")?.optNum("aod550"))
                            } else {
                                formatNowcastMetricValue(spec.key, nowcast)
                            }
                            Text(
                                valueText,
                                fontSize = 16.sp,
                                fontWeight = FontWeight.Bold,
                                style = tnum,
                                maxLines = 1,
                            )
                            Text(
                                spec.caption,
                                fontSize = 9.5.sp,
                                color = secondary,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                    if (pair.size == 1) Spacer(Modifier.weight(1f))
                }
            }
        }
    }
}

@Composable
private fun NowcastCurveCard(report: JSONObject) {
    val points = remember(report) {
        val arr = report.optJSONArray("steps") ?: JSONArray()
        (0 until arr.length()).mapNotNull { i ->
            val step = arr.optJSONObject(i) ?: return@mapNotNull null
            if (!isStepValid(step)) return@mapNotNull null
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

// ---------------------------------------------------------------- v3 太阳方向卫星剖面

private val RetrievedColor = Color(86, 143, 155)
private val RetrievedBrightColor = Color(98, 170, 188)
private val UnknownAmberColor = Color(183, 121, 31)
private val UnknownFillColor = Color(183, 121, 31).copy(alpha = 0.11f)

private data class NowcastColumnSlice(
    val startKM: Double,
    val endKM: Double,
    val cells: List<NowcastProfileCell>,
) {
    val isFullyUnknown: Boolean get() = cells.all { it.probability == null }
}

private fun buildNowcastColumns(cells: List<NowcastProfileCell>): List<NowcastColumnSlice> {
    val grouped = linkedMapOf<String, Pair<Pair<Double, Double>, MutableList<NowcastProfileCell>>>()
    for (cell in cells) {
        val key = "${(cell.startKM * 100).roundToInt()}-${(cell.endKM * 100).roundToInt()}"
        val entry = grouped[key]
        if (entry != null) {
            entry.second.add(cell)
        } else {
            grouped[key] = (cell.startKM to cell.endKM) to mutableListOf(cell)
        }
    }
    return grouped.values
        .sortedBy { it.first.first }
        .map { (range, list) ->
            NowcastColumnSlice(
                startKM = range.first,
                endKM = range.second,
                cells = list.sortedBy { it.bottomKM },
            )
        }
}

private fun bandFillColor(probability: Double, dark: Boolean): Color {
    val clamped = probability.coerceIn(0.0, 100.0)
    return if (clamped < 15.0) {
        RetrievedColor.copy(alpha = (0.03 + clamped / 15.0 * 0.11).toFloat())
    } else if (clamped <= 50.0) {
        val t = (clamped - 15.0) / 35.0
        RetrievedColor.copy(alpha = ((if (dark) 0.22 else 0.18) + t * 0.32).toFloat())
    } else {
        val t = (clamped - 50.0) / 50.0
        RetrievedBrightColor.copy(alpha = ((if (dark) 0.56 else 0.52) + t * 0.30).toFloat())
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun NowcastProfileContent(
    store: WorkbenchStore,
    state: WorkbenchState,
    report: JSONObject,
    nowcast: JSONObject,
    stats: NowcastProfileStats,
) {
    val cells = remember(report) { parseNowcastProfileCells(report) }
    val bearing = remember(report) { resolveNowcastProfileBearing(report) }
    val dark = LocalFireSkyDark.current
    val secondary = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f)
    val primary = MaterialTheme.colorScheme.onBackground
    val warm = MaterialTheme.colorScheme.primary
    val textMeasurer = rememberTextMeasurer()

    var zoomScale by remember(report) { mutableFloatStateOf(1.0f) }
    var showIsobars by remember(report) { mutableStateOf(stats.pressureRows.isNotEmpty()) }
    var showFrozenQuery by remember(report) { mutableStateOf(false) }
    var scrubDistanceKM by remember(report) { mutableStateOf<Double?>(null) }
    var scrubHeightKM by remember(report) { mutableStateOf<Double?>(null) }

    val steps = remember(report) {
        val arr = report.optJSONArray("steps") ?: JSONArray()
        (0 until arr.length()).mapNotNull { arr.optJSONObject(it)?.optString("valid_time_local")?.ifEmpty { null } }
    }
    LaunchedEffect(report) {
        if (state.nowcastProfileStep.isEmpty() || state.nowcastProfileStep !in steps) {
            val defaultStep = report.optString("best_time_local", "").ifEmpty { steps.firstOrNull().orEmpty() }
            if (defaultStep.isNotEmpty()) store.setNowcastProfileStep(defaultStep)
        }
    }

    val bearingCaption = if (bearing != null) {
        val basis = if (report.optString("best_time_local", "").isEmpty()) "诊断采样方位" else "峰值附近采样方位"
        stringResource(R.string.nc_profile_bearing_fmt, basis, bearing)
    } else {
        stringResource(R.string.nc_profile_bearing_fallback)
    }

    val hcai = "CLOUD_MASK" in stats.bands
    val observerLabel = stringResource(R.string.profile_observer_label)
    val unknownBadgeText = stringResource(R.string.nc_profile_unknown_badge)
    val sunCueText = if (bearing != null) {
        stringResource(R.string.profile_sun_dir_fmt, bearing)
    } else {
        stringResource(R.string.profile_sun_dir_no_az)
    }

    Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(stringResource(R.string.nc_profile_heading), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                Text(bearingCaption, fontSize = 10.sp, color = secondary, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }

        // Isobar toggle + Zoom controls
        if (cells.isNotEmpty()) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (stats.pressureRows.isNotEmpty()) {
                    Surface(
                        onClick = { showIsobars = !showIsobars },
                        shape = CircleShape,
                        color = if (showIsobars) RetrievedColor.copy(alpha = if (dark) 0.22f else 0.14f) else secondary.copy(alpha = 0.08f),
                        border = BorderStroke(0.85.dp, if (showIsobars) RetrievedColor.copy(alpha = 0.50f) else secondary.copy(alpha = 0.22f)),
                    ) {
                        Row(
                            Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            Icon(
                                Icons.Filled.Layers,
                                contentDescription = null,
                                modifier = Modifier.size(12.dp),
                                tint = if (showIsobars) RetrievedColor else secondary,
                            )
                            Text(
                                stringResource(R.string.profile_isobars),
                                fontSize = 10.5.sp,
                                fontWeight = FontWeight.Medium,
                                color = if (showIsobars) RetrievedColor else secondary,
                            )
                        }
                    }
                }
                Spacer(Modifier.weight(1f))
                Text(
                    "${(zoomScale * 100f).roundToInt()}%",
                    fontSize = 10.5.sp,
                    fontFamily = FontFamily.Monospace,
                    color = secondary,
                )
                IconButton(
                    onClick = { zoomScale = max(1f, zoomScale - 0.25f) },
                    enabled = zoomScale > 1f,
                    modifier = Modifier.size(28.dp),
                ) {
                    Icon(Icons.Filled.ZoomOut, contentDescription = stringResource(R.string.profile_zoom_out), modifier = Modifier.size(15.dp))
                }
                IconButton(
                    onClick = { zoomScale = min(2.5f, zoomScale + 0.25f) },
                    enabled = zoomScale < 2.5f,
                    modifier = Modifier.size(28.dp),
                ) {
                    Icon(Icons.Filled.ZoomIn, contentDescription = stringResource(R.string.profile_zoom_in), modifier = Modifier.size(15.dp))
                }
                IconButton(
                    onClick = { zoomScale = 1f },
                    enabled = zoomScale > 1f,
                    modifier = Modifier.size(28.dp),
                ) {
                    Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.profile_zoom_reset), modifier = Modifier.size(14.dp))
                }
            }
        }

        if (stats.present) {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                NowcastStatChip(stringResource(R.string.nc_profile_stat_time), stats.validTimeText ?: "—")
                NowcastStatChip(stringResource(R.string.nc_profile_stat_status), stats.statusLabel)
                NowcastStatChip(stringResource(R.string.nc_profile_stat_samples), stats.sampleText)
                NowcastStatChip(stringResource(R.string.nc_profile_stat_coverage), stats.coverageText)
                NowcastStatChip(stringResource(R.string.nc_profile_stat_pressures), "${stats.pressureCount}")
                NowcastStatChip(stringResource(R.string.nc_profile_stat_unknown), stats.unknownCount?.toString() ?: "—")
            }
        }

        if (cells.isEmpty()) {
            val unavailableReason = nowcast.optJSONObject("satellite_vertical_profile")?.optString("reason")?.ifEmpty { null }
                ?: stringResource(R.string.nowcast_profile_empty)
            Text(unavailableReason, fontSize = 11.sp, color = secondary, modifier = Modifier.padding(vertical = 24.dp))
        } else {
            // 1-line takeaway banner
            val peakCell = cells.filter { it.probability != null }.maxByOrNull { it.probability!! }
            val hasSignal = (peakCell?.probability ?: 0.0) >= 15.0
            val bearingText = bearing?.let { "%.1f°".format(it) } ?: "—"
            val detailText = if (peakCell != null && hasSignal) {
                val midKM = (peakCell.bottomKM + peakCell.topKM) * 0.5
                stringResource(R.string.nc_profile_takeaway_peak_fmt, peakCell.probability!!.roundToInt(), midKM)
            } else {
                stringResource(R.string.nc_profile_takeaway_none)
            }
            val bannerText = stringResource(R.string.nc_profile_takeaway_fmt, bearingText, stats.statusLabel, stats.coverageText, detailText)
            val tint = if (hasSignal) RetrievedColor else UnknownAmberColor
            Surface(
                Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(8.dp),
                color = tint.copy(alpha = if (dark) 0.14f else 0.09f),
                border = BorderStroke(0.85.dp, tint.copy(alpha = 0.32f)),
            ) {
                Row(
                    Modifier.padding(horizontal = 9.dp, vertical = 5.5.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Icon(
                        if (hasSignal) Icons.Filled.Cloud else Icons.Filled.Air,
                        contentDescription = null,
                        modifier = Modifier.size(12.dp),
                        tint = tint,
                    )
                    Text(
                        bannerText,
                        fontSize = 10.5.sp,
                        fontWeight = FontWeight.Medium,
                        color = primary,
                        maxLines = 2,
                        lineHeight = 14.sp,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }

            // Atmospheric Canvas + Scrub Readout
            val showRightAxis = showIsobars && stats.pressureRows.isNotEmpty()
            val maxDataHeight = max(
                cells.maxOfOrNull { it.topKM } ?: 16.0,
                stats.pressureRows.maxOfOrNull { it.heightKM } ?: 15.0,
            )
            val maxHeightKM = max(16.0, ceil(maxDataHeight))
            val maxDistanceKM = max(1.0, cells.maxOfOrNull { it.endKM } ?: 100.0)

            BoxWithConstraints(Modifier.fillMaxWidth()) {
                val canvasWidth = maxWidth * zoomScale
                val canvasHeight = 220.dp
                Box(Modifier.fillMaxWidth()) {
                    Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
                        Canvas(
                            Modifier
                                .width(canvasWidth)
                                .height(canvasHeight)
                                .pointerInput(cells, showRightAxis, zoomScale) {
                                    val leftPx = 38.dp.toPx()
                                    val rightPx = size.width - (if (showRightAxis) 46.dp.toPx() else 14.dp.toPx())
                                    val topPx = 12.dp.toPx()
                                    val bottomPx = size.height - 22.dp.toPx()
                                    fun updateScrub(pos: Offset) {
                                        if (rightPx <= leftPx + 10f || bottomPx <= topPx + 10f ||
                                            pos.x < leftPx || pos.x > rightPx ||
                                            pos.y < topPx || pos.y > bottomPx
                                        ) {
                                            scrubDistanceKM = null
                                            scrubHeightKM = null
                                            return
                                        }
                                        val xr = ((pos.x - leftPx) / (rightPx - leftPx)).coerceIn(0f, 1f)
                                        val yr = (1f - (pos.y - topPx) / (bottomPx - topPx)).coerceIn(0f, 1f)
                                        scrubDistanceKM = xr * maxDistanceKM
                                        scrubHeightKM = yr * maxHeightKM
                                    }
                                    detectTapGestures(onTap = { updateScrub(it) })
                                }
                                .pointerInput(cells, showRightAxis, zoomScale) {
                                    val leftPx = 38.dp.toPx()
                                    val rightPx = size.width - (if (showRightAxis) 46.dp.toPx() else 14.dp.toPx())
                                    val topPx = 12.dp.toPx()
                                    val bottomPx = size.height - 22.dp.toPx()
                                    fun scrubAt(pos: Offset) {
                                        if (rightPx <= leftPx + 10f || bottomPx <= topPx + 10f) return
                                        val xr = ((pos.x - leftPx) / (rightPx - leftPx)).coerceIn(0f, 1f)
                                        val yr = (1f - (pos.y - topPx) / (bottomPx - topPx)).coerceIn(0f, 1f)
                                        scrubDistanceKM = xr * maxDistanceKM
                                        scrubHeightKM = yr * maxHeightKM
                                    }
                                    if (zoomScale <= 1.01f) {
                                        detectHorizontalDragGestures(
                                            onDragStart = { scrubAt(it) },
                                            onHorizontalDrag = { change, _ ->
                                                change.consume()
                                                scrubAt(change.position)
                                            },
                                        )
                                    } else {
                                        detectDragGesturesAfterLongPress(
                                            onDragStart = { scrubAt(it) },
                                            onDrag = { change, _ ->
                                                change.consume()
                                                scrubAt(change.position)
                                            },
                                        )
                                    }
                                },
                        ) {
                            drawNowcastProfileV3(
                                cells = cells,
                                rows = stats.pressureRows,
                                dark = dark,
                                warm = warm,
                                secondaryText = secondary,
                                primaryText = primary,
                                observerLabel = observerLabel,
                                unknownBadgeText = unknownBadgeText,
                                sunCueText = sunCueText,
                                scale = zoomScale,
                                showIsobars = showRightAxis,
                                scrubDistanceKM = scrubDistanceKM,
                                scrubHeightKM = scrubHeightKM,
                                textMeasurer = textMeasurer,
                            )
                        }
                    }

                    val hd = scrubDistanceKM
                    val hh = scrubHeightKM
                    if (hd != null && hh != null) {
                        Box(
                            Modifier
                                .align(Alignment.TopStart)
                                .padding(start = 44.dp, top = 16.dp),
                        ) {
                            NowcastScrubReadout(
                                cells = cells,
                                distanceKM = hd,
                                heightKM = hh,
                                onDismiss = {
                                    scrubDistanceKM = null
                                    scrubHeightKM = null
                                },
                            )
                        }
                    }
                }
            }

            // Legend
            NowcastProfileLegendV3(hcai)
        }

        // Collapsible frozen profile query
        HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
        Row(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
                .clickable { showFrozenQuery = !showFrozenQuery }
                .padding(vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                stringResource(R.string.nc_frozen_query_title),
                fontSize = 11.5.sp,
                fontWeight = FontWeight.Medium,
                color = secondary,
                modifier = Modifier.weight(1f),
            )
            Icon(
                if (showFrozenQuery) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                contentDescription = null,
                modifier = Modifier.size(16.dp),
                tint = secondary,
            )
        }
        AnimatedVisibility(visible = showFrozenQuery) {
            NowcastFrozenQueryBlock(store, state, report, steps)
        }
    }
}

@Composable
private fun NowcastScrubReadout(
    cells: List<NowcastProfileCell>,
    distanceKM: Double,
    heightKM: Double,
    onDismiss: () -> Unit,
) {
    val exact = cells.firstOrNull {
        distanceKM in it.startKM..it.endKM && heightKM in it.bottomKM..it.topKM
    }
    val sameColumn = if (exact == null) cells.filter { distanceKM in it.startKM..it.endKM } else emptyList()
    val columnUnknown = sameColumn.isNotEmpty() && sameColumn.all { it.probability == null }

    val statusText = when {
        exact != null && exact.probability != null -> stringResource(R.string.nc_profile_posterior_fmt, exact.probability.roundToInt())
        exact != null || columnUnknown -> stringResource(R.string.nc_profile_unknown_badge)
        else -> stringResource(R.string.nc_profile_no_sample)
    }
    val statusColor = when {
        exact != null && exact.probability != null -> RetrievedColor
        exact != null || columnUnknown -> UnknownAmberColor
        else -> MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
    }
    val dark = LocalFireSkyDark.current
    Surface(
        onClick = onDismiss,
        shape = CircleShape,
        color = if (dark) Color(0xFF141C2B).copy(alpha = 0.92f) else Color.White.copy(alpha = 0.94f),
        border = BorderStroke(0.75.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.55f)),
    ) {
        Row(
            Modifier.padding(horizontal = 9.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Box(Modifier.size(6.dp).background(statusColor, CircleShape))
            Text(
                "%.0f km · %.1f km · %s".format(distanceKM, heightKM, statusText),
                fontSize = 10.sp,
                fontWeight = FontWeight.Medium,
                style = TextStyle(fontFeatureSettings = "tnum"),
                maxLines = 1,
            )
            Icon(
                Icons.Filled.Close,
                contentDescription = stringResource(R.string.dismiss),
                modifier = Modifier.size(11.dp),
                tint = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f),
            )
        }
    }
}

private fun DrawScope.drawNowcastProfileV3(
    cells: List<NowcastProfileCell>,
    rows: List<NowcastPressureRow>,
    dark: Boolean,
    warm: Color,
    secondaryText: Color,
    primaryText: Color,
    observerLabel: String,
    unknownBadgeText: String,
    sunCueText: String,
    scale: Float,
    showIsobars: Boolean,
    scrubDistanceKM: Double?,
    scrubHeightKM: Double?,
    textMeasurer: TextMeasurer,
) {
    val top = 12.dp.toPx()
    val bottom = size.height - 22.dp.toPx()
    if (bottom <= top + 20f) return
    val maxDataHeight = max(cells.maxOfOrNull { it.topKM } ?: 16.0, rows.maxOfOrNull { it.heightKM } ?: 15.0)
    val maxHeightKM = max(16.0, ceil(maxDataHeight))
    val maxDistanceKM = max(1.0, cells.maxOfOrNull { it.endKM } ?: 100.0)
    val labelSp = min(12f, max(9f, 9.2f * scale)).sp
    val labelStyle = TextStyle(fontSize = labelSp, color = secondaryText, fontFamily = FontFamily.Monospace)
    val left = 38.dp.toPx()
    val right = size.width - (if (showIsobars) 46.dp.toPx() else 14.dp.toPx())
    if (right <= left + 24f) return

    fun x(km: Double): Float = (left + (km.coerceIn(0.0, maxDistanceKM) / maxDistanceKM) * (right - left)).toFloat()
    fun y(km: Double): Float = (top + (1.0 - km.coerceIn(0.0, maxHeightKM) / maxHeightKM) * (bottom - top)).toFloat()

    val plotCorner = 10.dp.toPx()
    val plotRect = Rect(left, top, right, bottom)
    val plotPath = Path().apply {
        addRoundRect(RoundRect(plotRect, CornerRadius(plotCorner, plotCorner)))
    }
    val columns = buildNowcastColumns(cells)

    clipPath(plotPath) {
        // Atmospheric twilight sky backdrop warming toward solar horizon on right
        val skyColors = if (dark) {
            listOf(Color(0xFF0F1A30), Color(0xFF1F2B45), Color(0xFF383038))
        } else {
            listOf(Color(0xFFCCE0F5), Color(0xFFE8F0FA), Color(0xFFFAEBD9))
        }
        drawPath(plotPath, Brush.verticalGradient(skyColors, startY = top, endY = bottom))
        drawPath(
            plotPath,
            Brush.linearGradient(
                colors = listOf(Color.Transparent, Color(0xFFFA9E57).copy(alpha = if (dark) 0.16f else 0.18f)),
                start = Offset(left + (right - left) * 0.40f, top + (bottom - top) * 0.25f),
                end = Offset(right, bottom),
            ),
        )

        // Gridlines
        val heightStep = if (maxHeightKM <= 16.0) 4.0 else 5.0
        var hKm = 0.0
        while (hKm <= maxHeightKM + 0.01) {
            val lineY = y(hKm)
            drawLine(secondaryText.copy(alpha = 0.14f), Offset(left, lineY), Offset(right, lineY), strokeWidth = 0.6.dp.toPx())
            hKm += heightStep
        }
        val distStep = when {
            maxDistanceKM <= 120.0 -> 20.0
            maxDistanceKM <= 300.0 -> 50.0
            else -> 100.0
        }
        var dKm = distStep
        while (dKm <= maxDistanceKM + 0.1) {
            val lineX = x(dKm)
            drawLine(
                secondaryText.copy(alpha = 0.11f),
                Offset(lineX, top),
                Offset(lineX, bottom),
                strokeWidth = 0.6.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 4.dp.toPx())),
            )
            dKm += distStep
        }

        // Contiguous unknown curtains & smooth horizontal probability bands
        var colIdx = 0
        while (colIdx < columns.size) {
            if (columns[colIdx].isFullyUnknown) {
                var endIdx = colIdx
                while (endIdx + 1 < columns.size && columns[endIdx + 1].isFullyUnknown) {
                    endIdx += 1
                }
                val veilLeft = max(left, x(columns[colIdx].startKM))
                val veilRight = min(right, x(columns[endIdx].endKM))
                val veilRect = Rect(veilLeft, top + 1.dp.toPx(), max(veilLeft + 2f, veilRight), bottom - 1.dp.toPx())
                val veilPath = Path().apply {
                    addRoundRect(RoundRect(veilRect, CornerRadius(4.dp.toPx(), 4.dp.toPx())))
                }
                drawPath(veilPath, UnknownFillColor)
                clipPath(veilPath) {
                    val span = veilRect.width + veilRect.height
                    val stepPx = 10.dp.toPx()
                    var off = -veilRect.height
                    while (off <= span) {
                        drawLine(
                            UnknownAmberColor.copy(alpha = 0.16f),
                            Offset(veilRect.left + off, veilRect.bottom),
                            Offset(veilRect.left + off + veilRect.height * 0.45f, veilRect.top),
                            strokeWidth = 0.8.dp.toPx(),
                        )
                        off += stepPx
                    }
                }
                drawPath(
                    veilPath,
                    UnknownAmberColor.copy(alpha = 0.75f),
                    style = Stroke(
                        width = 1.dp.toPx(),
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx())),
                    ),
                )
                if (veilRect.width >= 20.dp.toPx()) {
                    val measured = textMeasurer.measure(
                        unknownBadgeText,
                        TextStyle(fontSize = 8.sp, fontWeight = FontWeight.SemiBold, color = UnknownAmberColor),
                    )
                    val pw = measured.size.width + 8.dp.toPx()
                    val ph = measured.size.height + 4.dp.toPx()
                    val clampedLeft = (veilRect.center.x - pw * 0.5f)
                        .coerceIn(left + 4.dp.toPx(), (right - pw - 4.dp.toPx()).coerceAtLeast(left + 4.dp.toPx()))
                    val pRect = Rect(
                        clampedLeft,
                        veilRect.center.y - ph * 0.5f,
                        clampedLeft + pw,
                        veilRect.center.y + ph * 0.5f,
                    )
                    val pPath = Path().apply {
                        addRoundRect(RoundRect(pRect, CornerRadius(ph * 0.5f, ph * 0.5f)))
                    }
                    drawPath(pPath, (if (dark) Color(0xFF141D2E) else Color.White).copy(alpha = 0.84f))
                    drawPath(pPath, UnknownAmberColor.copy(alpha = 0.60f), style = Stroke(width = 0.75.dp.toPx()))
                    drawText(measured, topLeft = Offset(pRect.left + 4.dp.toPx(), pRect.top + 2.dp.toPx()))
                }
                colIdx = endIdx + 1
                continue
            }

            var endIdx = colIdx
            while (endIdx + 1 < columns.size && !columns[endIdx + 1].isFullyUnknown) {
                endIdx += 1
            }
            val runCols = columns.subList(colIdx, endIdx + 1)
            val maxLayers = runCols.maxOfOrNull { it.cells.size } ?: 0

            for (layerIndex in 0 until maxLayers) {
                var segStart = 0
                while (segStart < runCols.size) {
                    val startCell = runCols[segStart].cells.getOrNull(layerIndex)
                    if (startCell?.probability == null) {
                        segStart += 1
                        continue
                    }
                    var segEnd = segStart
                    while (segEnd + 1 < runCols.size &&
                        runCols[segEnd + 1].cells.getOrNull(layerIndex)?.probability != null
                    ) {
                        segEnd += 1
                    }
                    val bandCells = (segStart..segEnd).map { runCols[it].cells[layerIndex] }
                    val bandLeft = max(left, x(bandCells.first().startKM))
                    val bandRight = min(right, x(bandCells.last().endKM))
                    val bandWidth = max(1f, bandRight - bandLeft)

                    val topPts = mutableListOf<Offset>()
                    val botPts = mutableListOf<Offset>()
                    bandCells.forEachIndexed { idx, cell ->
                        val cLeft = max(left, x(cell.startKM))
                        val cRight = min(right, x(cell.endKM))
                        val cMid = (cLeft + cRight) * 0.5f
                        val tY = max(top + 1f, y(cell.topKM))
                        val bY = min(bottom - 1f, max(tY + 2f, y(cell.bottomKM)))
                        if (idx == 0) {
                            topPts.add(Offset(cLeft, tY))
                            botPts.add(Offset(cLeft, bY))
                        }
                        topPts.add(Offset(cMid, tY))
                        botPts.add(Offset(cMid, bY))
                        if (idx == bandCells.lastIndex) {
                            topPts.add(Offset(cRight, tY))
                            botPts.add(Offset(cRight, bY))
                        }
                    }

                    val bandPath = if (bandCells.size == 1) {
                        val rect = Rect(bandLeft, topPts[0].y, bandLeft + bandWidth, max(topPts[0].y + 2f, botPts[0].y))
                        val r = min(4.dp.toPx(), rect.height * 0.35f)
                        Path().apply { addRoundRect(RoundRect(rect, CornerRadius(r, r))) }
                    } else {
                        val revBot = botPts.asReversed()
                        Path().apply {
                            moveTo(topPts[0].x, topPts[0].y)
                            appendSmoothSegment(topPts)
                            lineTo(revBot[0].x, revBot[0].y)
                            appendSmoothSegment(revBot)
                            close()
                        }
                    }

                    val stops = bandCells.map { cell ->
                        val prob = cell.probability ?: 0.0
                        val cMid = (max(left, x(cell.startKM)) + min(right, x(cell.endKM))) * 0.5f
                        val loc = ((cMid - bandLeft) / bandWidth).coerceIn(0f, 1f)
                        loc to bandFillColor(prob, dark)
                    }.sortedBy { it.first }
                    if (stops.size <= 1) {
                        drawPath(bandPath, stops.firstOrNull()?.second ?: RetrievedColor.copy(alpha = 0.2f))
                    } else {
                        drawPath(
                            bandPath,
                            Brush.horizontalGradient(
                                colorStops = stops.toTypedArray(),
                                startX = bandLeft,
                                endX = bandRight,
                            ),
                        )
                    }

                    // Soft contour highlight on dense cores >= 45%
                    var coreStart = 0
                    while (coreStart < bandCells.size) {
                        if ((bandCells[coreStart].probability ?: 0.0) < 45.0) {
                            coreStart += 1
                            continue
                        }
                        var coreEnd = coreStart
                        while (coreEnd + 1 < bandCells.size && (bandCells[coreEnd + 1].probability ?: 0.0) >= 45.0) {
                            coreEnd += 1
                        }
                        val coreTop = mutableListOf<Offset>()
                        val coreBot = mutableListOf<Offset>()
                        for (ci in coreStart..coreEnd) {
                            val c = bandCells[ci]
                            val cLeft = max(left, x(c.startKM))
                            val cRight = min(right, x(c.endKM))
                            val cMid = (cLeft + cRight) * 0.5f
                            val tY = max(top + 1f, y(c.topKM))
                            val bY = min(bottom - 1f, max(tY + 2f, y(c.bottomKM)))
                            if (ci == coreStart) {
                                coreTop.add(Offset(cLeft, tY))
                                coreBot.add(Offset(cLeft, bY))
                            }
                            coreTop.add(Offset(cMid, tY))
                            coreBot.add(Offset(cMid, bY))
                            if (ci == coreEnd) {
                                coreTop.add(Offset(cRight, tY))
                                coreBot.add(Offset(cRight, bY))
                            }
                        }
                        val revCoreBot = coreBot.asReversed()
                        val corePath = Path().apply {
                            moveTo(coreTop[0].x, coreTop[0].y)
                            appendSmoothSegment(coreTop)
                            lineTo(revCoreBot[0].x, revCoreBot[0].y)
                            appendSmoothSegment(revCoreBot)
                            close()
                        }
                        drawPath(corePath, RetrievedBrightColor.copy(alpha = 0.62f), style = Stroke(width = 1.1.dp.toPx()))
                        coreStart = coreEnd + 1
                    }

                    segStart = segEnd + 1
                }
            }

            // Individual unknown cells inside retrieved columns
            for (col in runCols) {
                for (cell in col.cells) {
                    if (cell.probability != null) continue
                    val cLeft = max(left, x(cell.startKM))
                    val cRight = min(right, x(cell.endKM))
                    val cTop = max(top + 1f, y(cell.topKM))
                    val cBottom = min(bottom - 1f, y(cell.bottomKM))
                    if (cRight <= cLeft || cBottom <= cTop) continue
                    val shape = Path().apply {
                        addRoundRect(RoundRect(Rect(cLeft, cTop, cRight, cBottom), CornerRadius(3.dp.toPx(), 3.dp.toPx())))
                    }
                    drawPath(shape, UnknownFillColor)
                    drawPath(
                        shape,
                        UnknownAmberColor.copy(alpha = 0.80f),
                        style = Stroke(
                            width = 0.9.dp.toPx(),
                            pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 2.dp.toPx())),
                        ),
                    )
                }
            }

            colIdx = endIdx + 1
        }

        // GFS-prior pressure lines (toggleable)
        if (showIsobars) {
            for (row in rows.sortedByDescending { it.heightKM }) {
                if (row.heightKM !in 0.0..maxHeightKM) continue
                val lineY = y(row.heightKM)
                drawLine(
                    RetrievedColor.copy(alpha = 0.30f),
                    Offset(left, lineY),
                    Offset(right, lineY),
                    strokeWidth = 0.7.dp.toPx(),
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx())),
                )
            }
        }

        // Observer beacon & top-right sun direction cue
        val observer = Offset(x(0.0), bottom - 5.dp.toPx())
        drawCircle(warm.copy(alpha = 0.26f), radius = 7.dp.toPx(), center = observer)
        drawCircle(warm, radius = 4.dp.toPx(), center = observer)
        drawCircle(Color.White.copy(alpha = 0.90f), radius = 4.dp.toPx(), center = observer, style = Stroke(width = 1.2.dp.toPx()))
        val obsMeasured = textMeasurer.measure(
            observerLabel,
            TextStyle(fontSize = labelSp, fontWeight = FontWeight.SemiBold, color = primaryText),
        )
        drawText(obsMeasured, topLeft = Offset(observer.x + 9.dp.toPx(), observer.y - obsMeasured.size.height - 2.dp.toPx()))

        val sunColor = if (dark) Color(0xFFFAB857) else Color(0xFFB85C08)
        val sunMeasured = textMeasurer.measure(
            sunCueText,
            TextStyle(fontSize = 9.5.sp, fontWeight = FontWeight.SemiBold, color = sunColor),
        )
        val sunPadH = 7.dp.toPx()
        val sunPadV = 3.dp.toPx()
        val sunPillW = sunMeasured.size.width + sunPadH * 2f
        val sunPillH = sunMeasured.size.height + sunPadV * 2f
        val sunPillRect = Rect(
            right - sunPillW - 6.dp.toPx(),
            top + 6.dp.toPx(),
            right - 6.dp.toPx(),
            top + 6.dp.toPx() + sunPillH,
        )
        val sunPillPath = Path().apply {
            addRoundRect(RoundRect(sunPillRect, CornerRadius(sunPillH * 0.5f, sunPillH * 0.5f)))
        }
        drawPath(sunPillPath, (if (dark) Color(0xFF141D2E) else Color.White).copy(alpha = if (dark) 0.84f else 0.88f))
        drawPath(sunPillPath, Color(0xFFFAB857).copy(alpha = 0.55f), style = Stroke(width = 0.75.dp.toPx()))
        drawText(sunMeasured, topLeft = Offset(sunPillRect.left + sunPadH, sunPillRect.top + sunPadV))

        // Scrub crosshair
        if (scrubDistanceKM != null && scrubHeightKM != null) {
            val hx = x(scrubDistanceKM)
            val hy = y(scrubHeightKM)
            drawLine(
                primaryText.copy(alpha = 0.42f),
                Offset(hx, top),
                Offset(hx, bottom),
                strokeWidth = 0.9.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 3.dp.toPx())),
            )
            drawLine(
                primaryText.copy(alpha = 0.30f),
                Offset(left, hy),
                Offset(right, hy),
                strokeWidth = 0.8.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 3.dp.toPx())),
            )
        }
    }

    drawPath(plotPath, secondaryText.copy(alpha = if (dark) 0.28f else 0.22f), style = Stroke(width = 0.9.dp.toPx()))

    // Outside axis labels: height (left)
    val heightStep = if (maxHeightKM <= 16.0) 4.0 else 5.0
    var hLabel = 0.0
    while (hLabel <= maxHeightKM + 0.01) {
        val ly = y(hLabel)
        val measured = textMeasurer.measure("${hLabel.roundToInt()}km", labelStyle)
        drawText(measured, topLeft = Offset((left - measured.size.width - 5.dp.toPx()).coerceAtLeast(0f), ly - measured.size.height / 2f))
        hLabel += heightStep
    }

    // Outside axis labels: distance (bottom, km > 0)
    val distStep = when {
        maxDistanceKM <= 120.0 -> 20.0
        maxDistanceKM <= 300.0 -> 50.0
        else -> 100.0
    }
    var dLabel = distStep
    while (dLabel <= maxDistanceKM + 0.1) {
        val lx = x(dLabel)
        val measured = textMeasurer.measure("${dLabel.roundToInt()}km", labelStyle)
        val textX = if (dLabel >= maxDistanceKM - 1.0) {
            (lx - measured.size.width).coerceAtLeast(left)
        } else {
            (lx - measured.size.width / 2f).coerceIn(left, right - measured.size.width)
        }
        drawText(measured, topLeft = Offset(textX, bottom + 4.dp.toPx()))
        dLabel += distStep
    }

    // Outside axis labels: pressure rows (right)
    if (showIsobars) {
        val isoStyle = labelStyle.copy(fontSize = max(8.5f, labelSp.value - 0.5f).sp, color = RetrievedColor)
        var lastLabelY = Float.NEGATIVE_INFINITY
        val minSpacing = 12.dp.toPx()
        for (row in rows.sortedByDescending { it.heightKM }) {
            if (row.heightKM !in 0.0..maxHeightKM) continue
            val lineY = y(row.heightKM)
            if (abs(lineY - lastLabelY) >= minSpacing) {
                lastLabelY = lineY
                val measured = textMeasurer.measure("${row.pressure}hPa", isoStyle)
                drawText(measured, topLeft = Offset(right + 5.dp.toPx(), lineY - measured.size.height / 2f))
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NowcastProfileLegendV3(hcai: Boolean) {
    val secondary = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f)
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        FlowRow(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                Box(
                    Modifier
                        .size(width = 46.dp, height = 6.dp)
                        .clip(CircleShape)
                        .background(Brush.horizontalGradient(listOf(RetrievedColor.copy(alpha = 0.14f), RetrievedBrightColor.copy(alpha = 0.84f)))),
                )
                Text(stringResource(R.string.nc_legend_posterior), fontSize = 9.5.sp, color = secondary)
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                Box(
                    Modifier
                        .size(width = 11.dp, height = 7.dp)
                        .background(UnknownFillColor)
                        .border(0.9.dp, UnknownAmberColor),
                )
                Text(stringResource(R.string.nc_legend_unknown), fontSize = 9.5.sp, color = secondary)
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                Box(Modifier.size(width = 12.dp, height = 1.5.dp).background(RetrievedColor.copy(alpha = 0.65f)))
                Text(stringResource(R.string.nc_legend_isobars), fontSize = 9.5.sp, color = secondary)
            }
        }
        Text(
            stringResource(if (hcai) R.string.nc_legend_hcai else R.string.nc_legend_hsd),
            fontSize = 9.sp,
            color = secondary,
            lineHeight = 12.sp,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NowcastFrozenQueryBlock(
    store: WorkbenchStore,
    state: WorkbenchState,
    report: JSONObject,
    steps: List<String>,
) {
    val available = report.optJSONObject("profile_source")?.optBoolean("available", false) == true
    val secondary = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f)
    var dropdownExpanded by remember { mutableStateOf(false) }

    Column(Modifier.padding(top = 6.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (!available || steps.isEmpty()) {
            Text(stringResource(R.string.nc_frozen_query_unavailable), fontSize = 10.5.sp, color = secondary)
            return@Column
        }
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ExposedDropdownMenuBox(
                expanded = dropdownExpanded,
                onExpandedChange = { dropdownExpanded = it },
                modifier = Modifier.weight(1f),
            ) {
                OutlinedTextField(
                    value = state.nowcastProfileStep.replace("T", " "),
                    onValueChange = {},
                    readOnly = true,
                    singleLine = true,
                    shape = SoftInputShape,
                    colors = softInputColors(),
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(dropdownExpanded) },
                    textStyle = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.menuAnchor().fillMaxWidth(),
                )
                ExposedDropdownMenu(
                    expanded = dropdownExpanded,
                    onDismissRequest = { dropdownExpanded = false },
                ) {
                    steps.forEach { step ->
                        DropdownMenuItem(
                            text = { Text(step.replace("T", " "), fontSize = 12.sp) },
                            onClick = {
                                store.setNowcastProfileStep(step)
                                dropdownExpanded = false
                            },
                        )
                    }
                }
            }
            Button(
                onClick = { store.queryNowcastProfile(state.nowcastProfileStep) },
                enabled = !state.busy && state.nowcastProfileStep.isNotEmpty() && state.frozenProfileState != "loading",
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.heightIn(min = 46.dp),
            ) {
                Text(stringResource(R.string.nc_frozen_query_read), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }
        }

        WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 10.dp) {
            Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                when (state.frozenProfileState) {
                    "loading" -> Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.6.dp)
                        Text(stringResource(R.string.nc_frozen_query_loading), fontSize = 10.5.sp, color = secondary)
                    }
                    "ready" -> {
                        val sec = state.frozenProfile
                        val timesArr = sec?.optJSONArray("source_times_utc")
                        val times = if (timesArr != null) {
                            (0 until timesArr.length()).mapNotNull { timesArr.optString(it).ifEmpty { null } }.joinToString(" · ")
                        } else ""
                        Text(state.frozenProfileTime.replace("T", " "), fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold)
                        val distCount = sec?.optJSONArray("distances_km")?.length() ?: 0
                        val azText = sec?.optNum("solar_azimuth_deg")?.let { "%.1f°".format(it) } ?: "—"
                        val elText = sec?.optNum("solar_elevation_deg")?.let { "%.1f°".format(it) } ?: "—"
                        Text("方位 $azText · 仰角 $elText · 距离样本 $distCount", fontSize = 10.sp, color = secondary)
                        if (times.isNotEmpty()) {
                            Text("卫星源时次 $times", fontSize = 9.5.sp, color = secondary)
                        }
                    }
                    "idle" -> Text(stringResource(R.string.nc_frozen_query_idle), fontSize = 10.sp, color = secondary)
                    else -> Text(
                        state.frozenProfileError.ifEmpty { stringResource(R.string.nc_frozen_query_unavailable) },
                        fontSize = 10.sp,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}

@Composable
private fun NowcastDiagnosticContent(
    report: JSONObject,
    nowcast: JSONObject,
    profileStats: NowcastProfileStats,
    showHeader: Boolean = true,
) {
    val lagrangianTrend = nowcast.optNum("lagrangian_b13_trend_k_per_hour")
    val horizonGap = nowcast.optNum("horizon_gap_score")
    val overcastShadow = nowcast.optNum("overcast_shadow_factor")
    val components = report.optJSONObject("quality_components")

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (showHeader) {
            Text(stringResource(R.string.nowcast_diag_title), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }

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

        if (components != null && components.length() > 0) {
            WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    Text(stringResource(R.string.breakdown_title), fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold)
                    DiagLine("云量基础分", fmt(components.optNum("coverage_base_score") ?: components.optNum("coverage_score"), 3))
                    DiagLine("修正后原始分", fmt(components.optNum("raw_quality_index"), 3))
                    DiagLine("可烧云占比（峰值）", percent(components.optNum("forward_burnable_cloud_ratio_peak")))
                    DiagLine("可烧载荷（有效）", fmt(components.optNum("forward_burnable_cloud_load_effective"), 3))
                    DiagLine("入射遮挡（均值）", percent(components.optNum("solar_incoming_block_risk_mean")))
                    DiagLine("质量用 AOD 550", fmt(components.optNum("quality_aod550"), 3))
                }
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
    var showInvalid by remember(report) { mutableStateOf(false) }
    val arr = report.optJSONArray("steps") ?: JSONArray()
    val allSteps = (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    val hiddenInvalidCount = allSteps.count { !isStepValid(it) }
    val visibleSteps = if (showInvalid) allSteps else allSteps.filter { isStepValid(it) }
    val secondary = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
    val warm = MaterialTheme.colorScheme.primary
    val cool = coolAccentColor()
    val tnum = TextStyle(fontFeatureSettings = "tnum")

    if (allSteps.isEmpty()) {
        Text(stringResource(R.string.no_ledger), fontSize = 12.sp, color = secondary)
        return
    }
    Column {
        Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
            Column(Modifier.width(420.dp)) {
                Row(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                    Text(stringResource(R.string.col_time), Modifier.width(62.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_quality), Modifier.width(64.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_level), Modifier.width(104.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_burnable), Modifier.width(44.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_coverage), Modifier.width(74.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_solar), Modifier.width(72.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                }
                HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
                visibleSteps.forEach { step ->
                    val isValid = isStepValid(step)
                    val rowAlpha = if (isValid) 1f else 0.55f
                    val burnable = if (step.has("physically_burnable") && !step.isNull("physically_burnable")) {
                        step.optBoolean("physically_burnable")
                    } else null
                    val levelText = if (isValid) {
                        step.optString("instant_quality_level", "").ifEmpty { "—" }
                    } else {
                        stringResource(R.string.ledger_unevaluated)
                    }
                    Row(Modifier.fillMaxWidth().padding(vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            timeLabel(step.optString("valid_time_local")),
                            Modifier.width(62.dp),
                            fontSize = 11.sp,
                            style = tnum,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                        )
                        Text(
                            if (isValid) fmt(step.optNum("instant_quality_index"), 3) else "—",
                            Modifier.width(64.dp),
                            fontSize = 11.sp,
                            style = tnum,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                        )
                        Text(
                            levelText,
                            Modifier.width(104.dp),
                            fontSize = 11.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            color = if (isValid) MaterialTheme.colorScheme.onBackground else secondary,
                        )
                        Box(Modifier.width(44.dp), contentAlignment = Alignment.CenterStart) {
                            when {
                                !isValid || burnable == null -> Icon(Icons.Filled.Remove, contentDescription = null, modifier = Modifier.size(13.dp), tint = secondary)
                                burnable -> Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(13.dp), tint = cool)
                                else -> Icon(Icons.Filled.Close, contentDescription = null, modifier = Modifier.size(13.dp), tint = warm)
                            }
                        }
                        Text(
                            if (isValid) percent(step.optNum("burnable_cloud_coverage")) else "—",
                            Modifier.width(74.dp),
                            fontSize = 11.sp,
                            style = tnum,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                        )
                        Text(
                            if (isValid) fmt(step.optNum("solar_elevation_deg"), 1) else "—",
                            Modifier.width(72.dp),
                            fontSize = 11.sp,
                            style = tnum,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                        )
                    }
                    HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f))
                }
            }
        }
        if (visibleSteps.isEmpty()) {
            Text(
                stringResource(R.string.ledger_no_valid_steps),
                fontSize = 11.sp,
                color = secondary,
                modifier = Modifier.padding(vertical = 18.dp),
            )
        }
        Row(
            Modifier.fillMaxWidth().padding(top = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                stringResource(R.string.ledger_footer_note),
                fontSize = 9.sp,
                color = secondary,
                modifier = Modifier.weight(1f),
            )
            if (hiddenInvalidCount > 0) {
                Surface(
                    shape = CircleShape,
                    color = MaterialTheme.colorScheme.primary.copy(alpha = 0.10f),
                    border = BorderStroke(0.75.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.25f)),
                    modifier = Modifier.clip(CircleShape).clickable { showInvalid = !showInvalid },
                ) {
                    Row(
                        Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Icon(
                            if (showInvalid) Icons.Filled.VisibilityOff else Icons.Filled.Visibility,
                            contentDescription = null,
                            modifier = Modifier.size(13.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                        Text(
                            stringResource(
                                if (showInvalid) R.string.ledger_hide_unevaluated_fmt
                                else R.string.ledger_show_unevaluated_fmt,
                                hiddenInvalidCount,
                            ),
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun NowcastStatChip(title: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(1.dp)) {
        Text(title, fontSize = 8.5.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.56f))
        Text(value, fontSize = 10.5.sp, fontWeight = FontWeight.Medium, fontFamily = FontFamily.Monospace)
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
