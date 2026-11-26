package com.firesky.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.SatelliteAlt
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material.icons.filled.WbTwilight
import androidx.compose.material.icons.filled.ZoomIn
import androidx.compose.material.icons.filled.ZoomOut
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
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
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * 结果卡片 —— WorkHeroView / WorkResultsView（point 作用域）同构：
 * 双模型 hero 卡 + 分析页签（概览/大气剖面/诊断与时间窗/逐时台账）。
 */
@Composable
fun ResultSection(store: WorkbenchStore, state: WorkbenchState) {
    val activeResult = if (state.resultKind == null || state.resultKind == "point") state.result else null
    if (activeResult == null) {
        FirstRunEmptyCanvas()
        return
    }
    val preferred = modelReport(activeResult, state.selectedModel)
        ?: modelReport(activeResult, MODEL_GFS)
        ?: modelReport(activeResult, MODEL_EC)
    val resultTz = state.resultParams?.optString("timezone_name")?.ifEmpty { null }
        ?: state.location.timezone
    val bestTimeForWindow = preferred?.optString("best_time_local")?.ifEmpty { null }
        ?: modelReport(activeResult, MODEL_GFS)?.optString("best_time_local")?.ifEmpty { null }
        ?: modelReport(activeResult, MODEL_EC)?.optString("best_time_local")?.ifEmpty { null }
    val showNowcastSwitchChip = isBestTimeWithinFourHours(bestTimeForWindow, resultTz)

    WorkSurface(Modifier.fillMaxWidth(), emphasized = true) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "${state.resultTitle} " +
                        stringResource(
                            if (state.resultPhenomenon == "dawn") R.string.phenomenon_dawn else R.string.phenomenon_dusk,
                        ),
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (showNowcastSwitchChip) {
                    Spacer(Modifier.width(8.dp))
                    val cool = coolAccentColor()
                    Surface(
                        onClick = { store.setWorkbenchMode("nowcast") },
                        shape = CircleShape,
                        color = cool.copy(alpha = 0.14f),
                        border = BorderStroke(0.75.dp, cool.copy(alpha = 0.36f)),
                    ) {
                        Row(
                            Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            Icon(
                                Icons.Filled.SatelliteAlt,
                                contentDescription = null,
                                modifier = Modifier.size(12.dp),
                                tint = cool,
                            )
                            Text(
                                stringResource(R.string.nowcast_quick_switch_chip),
                                fontSize = 10.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = cool,
                                maxLines = 1,
                            )
                        }
                    }
                }
            }
            if (state.resultParametersStale) {
                StaleResultBanner(store, state)
            }
            Row(verticalAlignment = Alignment.Top) {
                HeroSummary(activeResult, MODEL_GFS, Modifier.weight(1f))
                Box(
                    Modifier
                        .width(0.5.dp)
                        .height(164.dp)
                        .background(MaterialTheme.colorScheme.onBackground.copy(alpha = 0.14f)),
                )
                HeroSummary(activeResult, MODEL_EC, Modifier.weight(1f))
            }
        }
    }

    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            AnalysisTabs(store, state)
            if (state.resultTab != "概览") {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Segmented(
                        options = COMPARISON_MODELS,
                        titles = listOf("GFS", "EC"),
                        selection = state.selectedModel,
                        onSelect = { store.selectModel(it) },
                        modifier = Modifier.width(150.dp),
                    )
                    Spacer(Modifier.weight(1f))
                    Text(
                        comparisonStatus(activeResult, state.selectedModel),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                }
            }
            when (state.resultTab) {
                "概览" -> OverviewContent(activeResult, state.selectedModel)
                "大气剖面" -> ProfileSection(activeResult, state.selectedModel)
                "逐时台账" -> LedgerContent(activeResult, state.selectedModel)
                else -> DiagnosticContent(activeResult, state.selectedModel)
            }
        }
    }
}

/** 首次无结果空态：隐藏十余张空「—」占位卡，聚焦能力路径。 */
@Composable
private fun FirstRunEmptyCanvas() {
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                FireSkyMarkScene(size = 56.dp)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(stringResource(R.string.empty_ready_title), fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        stringResource(R.string.empty_ready_body),
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    )
                }
            }
            EmptyFeatureCard(
                icon = Icons.Filled.WbTwilight,
                title = stringResource(R.string.empty_feature_models_title),
                detail = stringResource(R.string.empty_feature_models_desc),
            )
            EmptyFeatureCard(
                icon = Icons.Filled.Cloud,
                title = stringResource(R.string.empty_feature_profile_title),
                detail = stringResource(R.string.empty_feature_profile_desc),
            )
            EmptyFeatureCard(
                icon = Icons.Filled.Schedule,
                title = stringResource(R.string.empty_feature_window_title),
                detail = stringResource(R.string.empty_feature_window_desc),
            )
        }
    }
}

@Composable
private fun EmptyFeatureCard(icon: ImageVector, title: String, detail: String) {
    WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
        Row(
            Modifier.padding(12.dp),
            verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            IconBadge(icon, tint = MaterialTheme.colorScheme.primary)
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                Text(detail, fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f))
            }
        }
    }
}

@Composable
private fun StaleResultBanner(store: WorkbenchStore, state: WorkbenchState) {
    val warnColor = if (LocalFireSkyDark.current) Color(0xFFFFC273) else Color(0xFF8A6321)
    Surface(
        Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = warnColor.copy(alpha = 0.10f),
        border = BorderStroke(0.75.dp, warnColor.copy(alpha = 0.34f)),
    ) {
        Row(
            Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            IconBadge(Icons.Filled.Refresh, tint = warnColor)
            Text(
                stringResource(R.string.stale_result_banner),
                fontSize = 11.sp,
                fontWeight = FontWeight.Medium,
                color = warnColor,
                modifier = Modifier.weight(1f),
            )
            TextButton(
                onClick = { store.beginCheck() },
                enabled = !state.busy && state.online,
            ) {
                Text(stringResource(R.string.rerun_with_new_params), fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HeroSummary(result: JSONObject, model: String, modifier: Modifier = Modifier) {
    val report = modelReport(result, model)
    val error = modelError(result, model)
    val color = if (model == MODEL_EC) coolAccentColor() else MaterialTheme.colorScheme.primary
    val tnumStyle = TextStyle(fontFeatureSettings = "tnum")

    Column(modifier.padding(horizontal = 10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).background(color, CircleShape))
            Spacer(Modifier.width(6.dp))
            Text(model.uppercase(), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.weight(1f))
            Text(
                comparisonStatus(result, model),
                fontSize = 11.sp,
                color = if (error == null) MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
                else MaterialTheme.colorScheme.error,
            )
        }
        Row(verticalAlignment = Alignment.Bottom) {
            Text(
                fmt(report.optNum("peak_quality_index") ?: report.optNum("quality_index")),
                fontSize = 34.sp,
                fontWeight = FontWeight.Bold,
                style = tnumStyle,
                color = if (report == null) MaterialTheme.colorScheme.onBackground.copy(alpha = 0.4f) else color,
                maxLines = 1,
            )
            Spacer(Modifier.width(4.dp))
            Text(
                "/ 2.5",
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                modifier = Modifier.padding(bottom = 5.dp),
            )
        }
        Text(
            report?.optString("quality_level", "")?.ifEmpty { stringResource(R.string.quality_index_fallback) }
                ?: stringResource(R.string.quality_index_fallback),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.68f),
        )
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(stringResource(R.string.best_time), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
            Text(
                timeLabel(report?.optString("best_time_local")),
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
                style = tnumStyle,
            )
        }
        Text(
            stringResource(R.string.recommended_window, windowLabel(report)),
            fontSize = 11.sp,
            style = tnumStyle,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
        )
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(5.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            cycleLabel(model, report)?.let { cycle ->
                ChipText(stringResource(R.string.cycle_chip_fmt, cycle))
            }
            ChipText(
                if (report == null) stringResource(R.string.not_forecasted)
                else familyDisplayName(report.optString("dominant_burnable_pressure_family", "")),
            )
            cloudGenusText(report?.optJSONObject("cloud_genus_proxy"))?.let { genus ->
                ChipText(genus)
            }
        }
        Text(
            modelSourceNote(model),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.56f),
            lineHeight = 14.sp,
        )
        if (error != null) {
            Text(error, fontSize = 11.sp, color = MaterialTheme.colorScheme.error, maxLines = 2)
        }
    }
}

/** 页签键（中文，状态键）→ 当前语言标题。 */
@Composable
private fun tabTitle(tab: String): String = when (tab) {
    "概览" -> stringResource(R.string.tab_overview)
    "大气剖面" -> stringResource(R.string.tab_profile)
    "诊断与时间窗" -> stringResource(R.string.tab_diagnostic)
    else -> stringResource(R.string.tab_ledger)
}

@Composable
private fun AnalysisTabs(store: WorkbenchStore, state: WorkbenchState) {
    val dark = LocalFireSkyDark.current
    val trackFill = if (dark) Color(0xFF161E2E) else Color(0xFFEAF0F6)
    val trackBorder = if (dark) Color(0xFF50617B).copy(alpha = 0.45f) else Color(0xFFD8E1EA).copy(alpha = 0.85f)
    val warm = MaterialTheme.colorScheme.primary

    Box(
        Modifier
            .fillMaxWidth()
            .background(trackFill, CircleShape)
            .border(0.75.dp, trackBorder, CircleShape)
            .padding(4.dp),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .selectableGroup()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            for (tab in listOf("概览", "大气剖面", "诊断与时间窗", "逐时台账")) {
                val selected = state.resultTab == tab
                val pillFill = when {
                    selected && dark -> Color.White.copy(alpha = 0.11f)
                    selected -> Color.White.copy(alpha = 0.95f)
                    else -> Color.Transparent
                }
                val pillShadow = if (selected && !dark) {
                    Modifier.shadow(
                        elevation = 1.dp,
                        shape = CircleShape,
                        clip = false,
                        ambientColor = Color(0xFF2A3C58).copy(alpha = 0.16f),
                        spotColor = Color(0xFF2A3C58).copy(alpha = 0.12f),
                    )
                } else {
                    Modifier
                }
                Box(
                    Modifier
                        .heightIn(min = 36.dp)
                        .then(pillShadow)
                        .clip(CircleShape)
                        .background(pillFill, CircleShape)
                        .selectable(selected = selected, role = Role.Tab, onClick = { store.selectResultTab(tab) }),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        tabTitle(tab),
                        fontSize = 12.sp,
                        fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                        color = if (selected) MaterialTheme.colorScheme.onSurface
                        else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.72f),
                        maxLines = 1,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 7.dp),
                    )
                    if (selected) {
                        Box(
                            Modifier
                                .align(Alignment.BottomCenter)
                                .padding(bottom = 2.dp)
                                .width(20.dp)
                                .height(2.5.dp)
                                .background(warm, CircleShape),
                        )
                    }
                }
            }
        }
    }
}

/** 概览：光谱色彩预览 + 16 方位雷达 + 三组指标卡（2 列自适应网格）+ 质量曲线。 */
@Composable
private fun OverviewContent(result: JSONObject, selectedModel: String = MODEL_GFS) {
    val spectral = findReportObject(result, selectedModel, "spectral_color")
    val radar = findReportObject(result, selectedModel, "azimuth_radar")
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        spectral?.let { SpectralColorCard(it) }
        radar?.let { AzimuthRadarCard(it) }
        for ((groupTitleRes, specs) in METRIC_GROUPS) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    stringResource(groupTitleRes),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                )
                for (rowSpecs in specs.chunked(2)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        for (spec in rowSpecs) {
                            MetricCard(spec, result, Modifier.weight(1f))
                        }
                        if (rowSpecs.size == 1) {
                            Spacer(Modifier.weight(1f))
                        }
                    }
                }
            }
        }
        CurveCard(result)
    }
}

private fun findReportObject(result: JSONObject, selectedModel: String, key: String): JSONObject? {
    val candidates = listOfNotNull(
        modelReport(result, selectedModel),
        modelReport(result, MODEL_GFS),
        modelReport(result, MODEL_EC),
        result,
    )
    return candidates.firstNotNullOfOrNull { it.optJSONObject(key) }
}

private fun parseHexColor(hex: String?): Color? {
    val trimmed = hex?.trim()?.takeIf { it.isNotEmpty() } ?: return null
    val normalized = if (trimmed.startsWith("#")) trimmed else "#$trimmed"
    return runCatching { Color(android.graphics.Color.parseColor(normalized)) }.getOrNull()
}

/** 判断 `best_time_local` 是否处于当前时刻 ±4 小时以内（用于展示一键切卫星实况提示）。 */
fun isBestTimeWithinFourHours(
    bestTimeLocal: String?,
    timezoneName: String? = null,
    nowMillis: Long = System.currentTimeMillis(),
): Boolean {
    val raw = bestTimeLocal?.trim()?.takeIf { it.isNotEmpty() } ?: return false
    val instant = runCatching { OffsetDateTime.parse(raw).toInstant() }.getOrNull()
        ?: runCatching { ZonedDateTime.parse(raw).toInstant() }.getOrNull()
        ?: runCatching {
            val ldt = LocalDateTime.parse(raw, DateTimeFormatter.ISO_LOCAL_DATE_TIME)
            val zone = timezoneName?.takeIf { it.isNotBlank() }?.let {
                runCatching { ZoneId.of(it) }.getOrNull()
            } ?: ZoneId.systemDefault()
            ldt.atZone(zone).toInstant()
        }.getOrNull()
        ?: return false
    return abs(instant.toEpochMilli() - nowMillis) <= 4L * 3600L * 1000L
}

@Composable
internal fun SpectralColorCard(spectral: JSONObject) {
    val hexArr = spectral.optJSONArray("sky_gradient_hex")
    val gradientColors = buildList {
        if (hexArr != null) {
            for (i in 0 until hexArr.length()) {
                parseHexColor(hexArr.optString(i))?.let { add(it) }
            }
        }
    }
    val peakHex = spectral.optString("peak_hex", "")
    val peakColor = parseHexColor(peakHex)
        ?: gradientColors.getOrNull(gradientColors.size / 2)
        ?: MaterialTheme.colorScheme.primary
    val stripColors = when {
        gradientColors.size >= 2 -> gradientColors
        gradientColors.size == 1 -> listOf(gradientColors[0].copy(alpha = 0.7f), gradientColors[0])
        else -> listOf(peakColor.copy(alpha = 0.45f), peakColor)
    }
    val isEnglish = LocalConfiguration.current.locales[0]?.language == "en"
    val labelZh = spectral.optString("peak_label_zh", "").trim()
    val labelEn = spectral.optString("peak_label_en", "").trim()
    val primaryLabel = if (isEnglish) labelEn.ifEmpty { labelZh } else labelZh.ifEmpty { labelEn }
    val secondaryLabel = if (isEnglish) {
        labelZh.takeIf { it.isNotEmpty() && it != primaryLabel }
    } else {
        labelEn.takeIf { it.isNotEmpty() && it != primaryLabel }
    }
    val azim = spectral.optNum("solar_azimuth_deg")
    val elev = spectral.optNum("solar_elevation_deg")

    WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 14.dp) {
        Row(
            Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Box(
                Modifier
                    .width(36.dp)
                    .height(68.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(Brush.verticalGradient(stripColors))
                    .border(0.75.dp, Color.White.copy(alpha = 0.28f), RoundedCornerShape(10.dp)),
            )
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    stringResource(R.string.spectral_color_title),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(7.dp),
                ) {
                    Box(
                        Modifier
                            .size(11.dp)
                            .background(peakColor, CircleShape)
                            .border(0.75.dp, MaterialTheme.colorScheme.onBackground.copy(alpha = 0.25f), CircleShape),
                    )
                    Text(
                        primaryLabel.ifEmpty { peakHex.ifEmpty { "—" } },
                        fontSize = 14.sp,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (!secondaryLabel.isNullOrEmpty()) {
                        Text(
                            secondaryLabel,
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
                val caption = if (azim != null && elev != null) {
                    stringResource(R.string.spectral_solar_caption_fmt, azim, elev)
                } else {
                    peakHex
                }
                if (caption.isNotEmpty()) {
                    Text(
                        caption,
                        fontSize = 11.sp,
                        style = TextStyle(fontFeatureSettings = "tnum"),
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                    )
                }
            }
            if (peakHex.isNotEmpty()) {
                Text(
                    peakHex.uppercase(),
                    fontSize = 10.sp,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
                )
            }
        }
    }
}

@Composable
internal fun AzimuthRadarCard(radar: JSONObject) {
    val bearingsArr = radar.optJSONArray("bearings") ?: return
    val bearings = (0 until bearingsArr.length()).mapNotNull { i ->
        val obj = bearingsArr.optJSONObject(i) ?: return@mapNotNull null
        val deg = obj.optNum("deg") ?: return@mapNotNull null
        val score = obj.optNum("score") ?: 0.0
        deg to score.coerceIn(0.0, 1.0)
    }.sortedBy { it.first }
    if (bearings.isEmpty()) return

    val solarAzim = radar.optNum("solar_azimuth_deg")
    val bestAzim = radar.optNum("best_azimuth_deg")
    val isEnglish = LocalConfiguration.current.locales[0]?.language == "en"
    val recZh = radar.optString("recommendation_zh", "").trim()
    val recEn = radar.optString("recommendation_en", "").trim()
    val recommendation = if (isEnglish) recEn.ifEmpty { recZh } else recZh.ifEmpty { recEn }
    val bestDir = if (isEnglish) {
        radar.optString("best_direction_en", "").ifEmpty { radar.optString("best_direction_zh", "") }
    } else {
        radar.optString("best_direction_zh", "").ifEmpty { radar.optString("best_direction_en", "") }
    }

    val dark = LocalFireSkyDark.current
    val warm = MaterialTheme.colorScheme.primary
    val cool = coolAccentColor()
    val ringColor = MaterialTheme.colorScheme.onBackground.copy(alpha = if (dark) 0.16f else 0.14f)
    val labelColor = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.72f)
    val sunColor = Color(0xFFFFB74D)
    val textMeasurer = rememberTextMeasurer()

    WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 14.dp) {
        Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    stringResource(R.string.azimuth_radar_title),
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                if (bestDir.isNotEmpty() && bestAzim != null) {
                    SoftPillChip(
                        text = stringResource(R.string.azimuth_radar_best_fmt, bestDir, bestAzim),
                        tint = warm,
                    )
                }
                if (solarAzim != null) {
                    SoftPillChip(
                        text = stringResource(R.string.azimuth_radar_solar_fmt, solarAzim),
                        tint = sunColor,
                        leadingDotColor = sunColor,
                    )
                }
            }

            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                Canvas(Modifier.size(204.dp)) {
                    val cx = size.width / 2f
                    val cy = size.height / 2f
                    val labelPad = 18.dp.toPx()
                    val maxR = (min(size.width, size.height) / 2f - labelPad).coerceAtLeast(24f)

                    for (frac in listOf(0.25f, 0.50f, 0.75f, 1.0f)) {
                        drawCircle(
                            color = ringColor,
                            radius = maxR * frac,
                            center = Offset(cx, cy),
                            style = Stroke(width = if (frac == 1.0f) 1.2.dp.toPx() else 0.75.dp.toPx()),
                        )
                    }

                    for (i in 0 until 16) {
                        val deg = i * 22.5
                        val rad = Math.toRadians(deg - 90.0)
                        val end = Offset(cx + (cos(rad) * maxR).toFloat(), cy + (sin(rad) * maxR).toFloat())
                        drawLine(
                            color = ringColor.copy(alpha = if (i % 4 == 0) 0.24f else 0.10f),
                            start = Offset(cx, cy),
                            end = end,
                            strokeWidth = if (i % 4 == 0) 1.dp.toPx() else 0.6.dp.toPx(),
                        )
                    }

                    val path = Path()
                    val pts = bearings.map { (deg, score) ->
                        val rad = Math.toRadians(deg - 90.0)
                        val r = maxR * score.toFloat()
                        Offset(cx + (cos(rad) * r).toFloat(), cy + (sin(rad) * r).toFloat())
                    }
                    pts.forEachIndexed { idx, pt ->
                        if (idx == 0) path.moveTo(pt.x, pt.y) else path.lineTo(pt.x, pt.y)
                    }
                    path.close()
                    drawPath(path, warm.copy(alpha = if (dark) 0.28f else 0.22f))
                    drawPath(path, warm, style = Stroke(width = 1.8.dp.toPx()))
                    pts.forEachIndexed { idx, pt ->
                        val isBest = bestAzim != null &&
                            abs(((bearings[idx].first - bestAzim + 540.0) % 360.0) - 180.0) < 1.0
                        drawCircle(
                            color = if (isBest) cool else warm,
                            radius = if (isBest) 4.2.dp.toPx() else 2.5.dp.toPx(),
                            center = pt,
                        )
                    }

                    if (solarAzim != null && solarAzim.isFinite()) {
                        val sunRad = Math.toRadians(solarAzim - 90.0)
                        val sunEdge = Offset(cx + (cos(sunRad) * maxR).toFloat(), cy + (sin(sunRad) * maxR).toFloat())
                        drawLine(
                            color = sunColor,
                            start = Offset(cx, cy),
                            end = sunEdge,
                            strokeWidth = 1.6.dp.toPx(),
                            pathEffect = PathEffect.dashPathEffect(floatArrayOf(5.dp.toPx(), 4.dp.toPx())),
                        )
                        drawCircle(color = sunColor, radius = 4.8.dp.toPx(), center = sunEdge)
                        drawCircle(
                            color = Color.White.copy(alpha = 0.85f),
                            radius = 4.8.dp.toPx(),
                            center = sunEdge,
                            style = Stroke(width = 1.dp.toPx()),
                        )
                    }

                    val cardinalStyle = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = labelColor)
                    for ((label, deg) in listOf("N" to 0.0, "E" to 90.0, "S" to 180.0, "W" to 270.0)) {
                        val rad = Math.toRadians(deg - 90.0)
                        val measured = textMeasurer.measure(label, cardinalStyle)
                        val anchorR = maxR + 10.dp.toPx()
                        val lx = cx + (cos(rad) * anchorR).toFloat() - measured.size.width / 2f
                        val ly = cy + (sin(rad) * anchorR).toFloat() - measured.size.height / 2f
                        drawText(measured, topLeft = Offset(lx, ly))
                    }
                }
            }

            if (recommendation.isNotEmpty()) {
                Text(
                    recommendation,
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.78f),
                    lineHeight = 15.sp,
                )
            }
        }
    }
}

@Composable
private fun MetricCard(spec: MetricSpec, result: JSONObject, modifier: Modifier = Modifier) {
    val tnumStyle = TextStyle(fontFeatureSettings = "tnum")
    WorkSurface(modifier, grouped = true, cornerRadius = 12.dp) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Text(
                stringResource(spec.titleRes),
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.85f),
            )
            when (spec.key) {
                "aod" -> {
                    val aod = aodSummary(result) { modelReport(result, it) }
                    Text(
                        when (aod.sourceKind) {
                            "cams" -> "CAMS"
                            "geos-fp" -> "GEOS-FP"
                            "mixed" -> stringResource(R.string.aod_mixed_source)
                            else -> stringResource(R.string.aod_other_source)
                        },
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.secondary,
                    )
                    Text(
                        aod.value,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        style = tnumStyle,
                        color = if (aod.available) MaterialTheme.colorScheme.onBackground
                        else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f),
                        maxLines = 1,
                    )
                }
                "cloud_layer" -> {
                    for (model in COMPARISON_MODELS) {
                        val report = modelReport(result, model)
                        val color = if (model == MODEL_EC) coolAccentColor() else MaterialTheme.colorScheme.primary
                        val genus = cloudGenusText(report?.optJSONObject("cloud_genus_proxy"))
                        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text(model.uppercase(), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = color)
                                Text(
                                    metricValue(spec.key, report),
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    style = tnumStyle,
                                    color = if (report == null) MaterialTheme.colorScheme.onBackground.copy(alpha = 0.45f)
                                    else MaterialTheme.colorScheme.onBackground,
                                    maxLines = 1,
                                )
                            }
                            if (genus != null) {
                                Text(
                                    genus,
                                    fontSize = 10.sp,
                                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }
                }
                else -> {
                    for (model in COMPARISON_MODELS) {
                        val report = modelReport(result, model)
                        val color = if (model == MODEL_EC) coolAccentColor() else MaterialTheme.colorScheme.primary
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(model.uppercase(), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = color)
                            Text(
                                metricValue(spec.key, report),
                                fontSize = 17.sp,
                                fontWeight = FontWeight.Bold,
                                style = tnumStyle,
                                color = if (report == null) MaterialTheme.colorScheme.onBackground.copy(alpha = 0.45f)
                                else MaterialTheme.colorScheme.onBackground,
                                maxLines = 1,
                            )
                        }
                    }
                }
            }
            Text(
                stringResource(spec.captionRes),
                fontSize = 10.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f),
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** 霞光质量变化（WorkComparisonCurveView 同构：gfs 实线 / ec 虚线，y 0…2.5）。 */
@Composable
private fun CurveCard(result: JSONObject) {
    val gfsPoints = curvePoints(result, MODEL_GFS)
    val ecPoints = curvePoints(result, MODEL_EC)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.curve_title), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.weight(1f))
            for (model in COMPARISON_MODELS) {
                val color = if (model == MODEL_EC) coolAccentColor() else MaterialTheme.colorScheme.primary
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    Box(Modifier.size(width = 14.dp, height = 2.dp).background(color))
                    Text(model.uppercase(), fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                    Spacer(Modifier.width(8.dp))
                }
            }
        }
        Box(Modifier.fillMaxWidth().height(164.dp)) {
            val warm = MaterialTheme.colorScheme.primary
            val cool = coolAccentColor()
            val faint = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.18f)
            Canvas(Modifier.fillMaxWidth().height(164.dp)) {
                val allTimes = (gfsPoints + ecPoints).map { it.first }
                if (allTimes.isEmpty()) return@Canvas
                val minTime = allTimes.min()
                val maxTime = allTimes.max()
                val span = (maxTime - minTime).coerceAtLeast(1)
                fun y(value: Double): Float = size.height * (1f - (value / 2.5).toFloat())
                fun x(time: Long): Float = size.width * ((time - minTime).toFloat() / span)
                for (level in listOf(0.0, 1.25, 2.5)) {
                    drawLine(faint, Offset(0f, y(level)), Offset(size.width, y(level)), strokeWidth = 1f)
                }
                drawSeries(gfsPoints, warm, dashed = false, ::x, ::y)
                drawSeries(ecPoints, cool, dashed = true, ::x, ::y)
            }
            if (gfsPoints.isEmpty() && ecPoints.isEmpty()) {
                Text(
                    stringResource(R.string.no_curve),
                    fontSize = 10.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
                    modifier = Modifier.align(Alignment.Center),
                )
            }
        }
    }
}

private fun DrawScope.drawSeries(
    points: List<Pair<Long, Double>>,
    color: Color,
    dashed: Boolean,
    x: (Long) -> Float,
    y: (Double) -> Float,
) {
    if (points.size < 2) {
        points.firstOrNull()?.let { drawCircle(color, radius = 4f, center = Offset(x(it.first), y(it.second))) }
        return
    }
    val path = Path()
    points.forEachIndexed { index, point ->
        val offset = Offset(x(point.first), y(point.second))
        if (index == 0) path.moveTo(offset.x, offset.y) else path.lineTo(offset.x, offset.y)
    }
    drawPath(
        path,
        color,
        style = Stroke(
            width = 4f,
            cap = StrokeCap.Round,
            pathEffect = if (dashed) PathEffect.dashPathEffect(floatArrayOf(16f, 12f)) else null,
        ),
    )
    for (point in points) {
        drawCircle(color, radius = 4f, center = Offset(x(point.first), y(point.second)))
    }
}

/** 逐时台账（WorkLedgerView 同构：默认隐藏 valid == false 未判断时次，支持行内切换）。 */
@Composable
private fun LedgerContent(result: JSONObject, model: String) {
    val report = modelReport(result, model)
    val err = modelError(result, model)
    if (err != null) {
        Text(
            stringResource(R.string.model_incomplete, model.uppercase(), err),
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.error,
        )
        return
    }
    if (report == null) {
        Text(
            stringResource(R.string.model_no_result, model.uppercase()),
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        return
    }
    var showInvalid by remember(result, model) { mutableStateOf(false) }
    val allSteps = stepsOf(report)
    val hiddenInvalidCount = allSteps.count { !it.optBoolean("valid", true) }
    val visibleSteps = if (showInvalid) allSteps else allSteps.filter { it.optBoolean("valid", true) }

    Column {
        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
            Text(
                stringResource(R.string.col_time),
                Modifier.weight(1f),
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            Text(
                stringResource(R.string.col_quality),
                Modifier.weight(1f),
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            Text(
                stringResource(R.string.col_solar),
                Modifier.weight(1f),
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        }
        HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
        visibleSteps.forEach { step ->
            val isValid = step.optBoolean("valid", true)
            val rowAlpha = if (isValid) 1f else 0.55f
            Row(
                Modifier.fillMaxWidth().padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    timeLabel(step.optString("valid_time_local")),
                    Modifier.weight(1f),
                    fontSize = 11.sp,
                    style = TextStyle(fontFeatureSettings = "tnum"),
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                )
                Text(
                    if (isValid) fmt(step.optNum("instant_quality_index"), 3) else "—",
                    Modifier.weight(1f),
                    fontSize = 11.sp,
                    style = TextStyle(fontFeatureSettings = "tnum"),
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                )
                Text(
                    fmt(step.optNum("solar_elevation_deg")),
                    Modifier.weight(1f),
                    fontSize = 11.sp,
                    style = TextStyle(fontFeatureSettings = "tnum"),
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                )
            }
            HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f))
        }
        if (allSteps.isEmpty()) {
            Text(
                stringResource(R.string.no_ledger),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
                modifier = Modifier.padding(vertical = 24.dp),
            )
        } else if (visibleSteps.isEmpty()) {
            Text(
                stringResource(R.string.ledger_no_valid_steps),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
                modifier = Modifier.padding(vertical = 20.dp),
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
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
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

/** 诊断与时间窗（WorkDiagnosticView 同构：含云属/相态模型代理与内部码抑制）。 */
@Composable
private fun DiagnosticContent(result: JSONObject, model: String) {
    val report = modelReport(result, model)
    val err = modelError(result, model)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.tab_diagnostic), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        if (report == null) {
            Text(
                if (err != null) stringResource(R.string.model_incomplete, model.uppercase(), err)
                else stringResource(R.string.awaiting_forecast),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        } else {
            Text(report.optString("summary", ""), fontSize = 12.sp)
            Text(
                stringResource(R.string.cycle_line, report.optString("cycle_time_utc", "--")),
                fontSize = 11.sp,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            report.optJSONObject("cloud_genus_proxy")?.let { proxy ->
                CloudGenusProxyCard(proxy)
            }
            val mapCapability = report.optJSONObject("map_capability")
            if (mapCapability != null && !mapCapability.optBoolean("available", true)) {
                val reason = friendlyEngineMessage(mapCapability.optString("reason", ""))
                if (reason.isNotEmpty()) {
                    Text(
                        reason,
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------- 大气剖面（CrossSectionFigureView 同构）

private class CrossSectionData(
    val distances: List<Double>,
    val levels: List<Int>,
    val heights: List<List<Double?>>,
    val temps: List<List<Double?>>,
    val rh: List<List<Double?>>,
    val amounts: List<List<Double?>>,
    val presence: List<List<Double?>>,
    val fractions: List<List<Double?>>,
    val familyCodes: List<List<Int>>,
    val statuses: List<List<Int>>,
    val layerBases: List<List<Double?>>,
    val layerTops: List<List<Double?>>,
    val terrain: List<Double>,
    val requiredASL: List<Double>,
    val targets: List<JSONObject>,
) {
    companion object {
        fun parse(section: JSONObject): CrossSectionData? {
            val distances = section.optJSONArray("distances_km")?.toDoubleList() ?: return null
            if (distances.isEmpty()) return null
            val fields = section.optJSONObject("fields") ?: JSONObject()
            fun doubleGrid(key: String): List<List<Double?>> {
                val outer = fields.optJSONArray(key) ?: return emptyList()
                return (0 until outer.length()).map { i ->
                    val inner = outer.optJSONArray(i) ?: return@map emptyList()
                    (0 until inner.length()).map { j ->
                        if (inner.isNull(j)) null else inner.optDouble(j).takeUnless { it.isNaN() }
                    }
                }
            }
            fun intGrid(key: String): List<List<Int>> {
                val outer = fields.optJSONArray(key) ?: return emptyList()
                return (0 until outer.length()).map { i ->
                    val inner = outer.optJSONArray(i) ?: return@map emptyList()
                    (0 until inner.length()).map { j -> inner.optInt(j, 0) }
                }
            }
            val levelsArr = section.optJSONArray("pressure_levels_hpa")
            val levels = if (levelsArr != null) {
                (0 until levelsArr.length()).map { levelsArr.optInt(it, 0) }
            } else emptyList()
            val targetsArr = section.optJSONArray("theoretical_targets")
            val targets = if (targetsArr != null) {
                (0 until targetsArr.length()).mapNotNull { targetsArr.optJSONObject(it) }
            } else emptyList()
            return CrossSectionData(
                distances = distances,
                levels = levels,
                heights = doubleGrid("geopotential_height_asl_m"),
                temps = doubleGrid("temperature_c"),
                rh = doubleGrid("rh_percent"),
                amounts = doubleGrid("cloud_amount"),
                presence = doubleGrid("cloud_presence"),
                fractions = doubleGrid("cloud_fraction_percent"),
                familyCodes = intGrid("pressure_family_code"),
                statuses = intGrid("cloud_layer_status"),
                layerBases = doubleGrid("cloud_layer_base_asl_m"),
                layerTops = doubleGrid("cloud_layer_top_asl_m"),
                terrain = section.optJSONArray("terrain_elevation_asl_m")?.toDoubleList() ?: emptyList(),
                requiredASL = section.optJSONArray("required_illumination_base_asl_m")?.toDoubleList() ?: emptyList(),
                targets = targets,
            )
        }
    }
}

private fun JSONArray.toDoubleList(): List<Double> =
    (0 until length()).mapNotNull { i ->
        if (isNull(i)) null else optDouble(i).takeUnless { it.isNaN() }
    }

@Composable
private fun ProfileSection(result: JSONObject, model: String) {
    val report = modelReport(result, model)
    val err = modelError(result, model)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.profile_title), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        when {
            err != null -> Text(
                stringResource(R.string.model_incomplete, model.uppercase(), err),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.error,
            )
            report == null -> Text(
                stringResource(R.string.model_no_result, model.uppercase()),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            else -> {
                val section = report.optJSONObject("cross_section")
                if (section == null) {
                    Text(
                        stringResource(R.string.profile_note),
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                } else {
                    CrossSectionFigure(section, report.optJSONObject("cloud_genus_proxy"))
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CrossSectionFigure(section: JSONObject, genusProxy: JSONObject?) {
    val data = remember(section) { CrossSectionData.parse(section) }
    if (data == null) {
        Text(
            stringResource(R.string.profile_no_fields),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            modifier = Modifier.padding(vertical = 24.dp),
        )
        return
    }
    var zoomScale by remember(section) { mutableFloatStateOf(1.0f) }
    val dark = LocalFireSkyDark.current
    val warm = MaterialTheme.colorScheme.primary
    val cool = coolAccentColor()
    val secondaryText = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
    val primaryText = MaterialTheme.colorScheme.onBackground
    val observerLabel = stringResource(R.string.profile_observer_label)
    val textMeasurer = rememberTextMeasurer()

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        // 缩放控制栏
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                stringResource(R.string.profile_axis_label),
                fontSize = 11.sp,
                fontWeight = FontWeight.Medium,
                color = secondaryText,
                modifier = Modifier.weight(1f),
            )
            Text(
                "${(zoomScale * 100f).roundToInt()}%",
                fontSize = 11.sp,
                fontFamily = FontFamily.Monospace,
                color = secondaryText,
            )
            IconButton(
                onClick = { zoomScale = max(1f, zoomScale - 0.25f) },
                enabled = zoomScale > 1f,
                modifier = Modifier.size(32.dp),
            ) {
                Icon(Icons.Filled.ZoomOut, contentDescription = stringResource(R.string.profile_zoom_out), modifier = Modifier.size(16.dp))
            }
            IconButton(
                onClick = { zoomScale = min(2.5f, zoomScale + 0.25f) },
                enabled = zoomScale < 2.5f,
                modifier = Modifier.size(32.dp),
            ) {
                Icon(Icons.Filled.ZoomIn, contentDescription = stringResource(R.string.profile_zoom_in), modifier = Modifier.size(16.dp))
            }
            IconButton(
                onClick = { zoomScale = 1f },
                enabled = zoomScale > 1f,
                modifier = Modifier.size(32.dp),
            ) {
                Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.profile_zoom_reset), modifier = Modifier.size(15.dp))
            }
        }

        // 横向可滚动的剖面画布
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val baseWidth = maxWidth
            val canvasWidth = baseWidth * zoomScale
            val canvasHeight = 200.dp
            Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
                Canvas(Modifier.width(canvasWidth).height(canvasHeight)) {
                    drawCrossSection(
                        data = data,
                        dark = dark,
                        warm = warm,
                        coolAccent = cool,
                        secondaryText = secondaryText,
                        primaryText = primaryText,
                        observerLabel = observerLabel,
                        scale = zoomScale,
                        textMeasurer = textMeasurer,
                    )
                }
            }
        }

        CrossSectionLegend(warm, cool)

        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ProfileMetaChip(stringResource(R.string.meta_solar_elev), fmt(section.optNum("solar_elevation_deg"), 1) + "°")
            ProfileMetaChip(stringResource(R.string.meta_solar_azim), fmt(section.optNum("solar_azimuth_deg"), 1) + "°")
            ProfileMetaChip(stringResource(R.string.meta_range), fmt(section.optNum("analysis_radius_km"), 0) + " km")
            val offsetMin = section.optNum("offset_minutes")?.roundToInt()
            ProfileMetaChip(
                stringResource(R.string.meta_offset),
                offsetMin?.let { "${if (it > 0) "+" else ""}$it min" } ?: "—",
            )
            ProfileMetaChip(stringResource(R.string.meta_valid_time), timeLabel(section.optString("valid_time_local", "")))
            ProfileMetaChip(stringResource(R.string.meta_sampling), section.optString("spatial_sampling", "—").ifEmpty { "—" })
        }

        if (genusProxy != null) {
            CloudGenusProxyCard(genusProxy)
        }

        TargetStrip(data.targets)
    }
}

private fun DrawScope.drawCrossSection(
    data: CrossSectionData,
    dark: Boolean,
    warm: Color,
    coolAccent: Color,
    secondaryText: Color,
    primaryText: Color,
    observerLabel: String,
    scale: Float,
    textMeasurer: TextMeasurer,
) {
    val top = 8.dp.toPx()
    val bottom = size.height - 22.dp.toPx()
    if (bottom <= top + 20f) return
    val maxHeight = 18_000.0
    val labelSp = min(13f, max(9f, 9.5f * scale)).sp
    val labelStyle = TextStyle(fontSize = labelSp, color = secondaryText, fontFamily = FontFamily.Monospace)
    val left = 38.dp.toPx()
    val right = size.width - 10.dp.toPx()
    if (right <= left + 20f) return
    val maxDistance = max(1.0, data.distances.last())
    fun x(distance: Double): Float = (left + (distance / maxDistance) * (right - left)).toFloat()
    fun y(height: Double): Float = (top + (1.0 - height.coerceIn(0.0, maxHeight) / maxHeight) * (bottom - top)).toFloat()

    // 1. Twilight-to-warm-horizon sky gradient backdrop inside rounded plot frame
    val plotCorner = 8.dp.toPx()
    val plotPath = Path().apply {
        addRoundRect(RoundRect(Rect(left, top, right, bottom), CornerRadius(plotCorner, plotCorner)))
    }
    val skyColors = if (dark) {
        listOf(
            Color(0.09f, 0.13f, 0.22f, 0.78f),
            Color(0.14f, 0.19f, 0.28f, 0.48f),
            Color(0.28f, 0.20f, 0.19f, 0.35f),
        )
    } else {
        listOf(
            Color(0.85f, 0.91f, 0.97f, 0.72f),
            Color(0.94f, 0.95f, 0.98f, 0.52f),
            Color(0.99f, 0.93f, 0.87f, 0.55f),
        )
    }
    drawPath(plotPath, Brush.verticalGradient(skyColors, startY = top, endY = bottom))
    drawPath(plotPath, secondaryText.copy(alpha = if (dark) 0.35f else 0.28f), style = Stroke(width = 1.dp.toPx()))

    // 2. Soft golden solar illumination corridor above requiredASL
    if (data.requiredASL.size == data.distances.size) {
        val sunlit = Path().apply {
            moveTo(left, top)
            lineTo(right, top)
            for (index in data.distances.indices.reversed()) {
                lineTo(x(data.distances[index]), y(data.requiredASL[index]))
            }
            close()
        }
        drawPath(
            sunlit,
            Brush.verticalGradient(
                listOf(
                    Color(0.98f, 0.69f, 0.30f, if (dark) 0.04f else 0.05f),
                    Color(0.96f, 0.57f, 0.22f, if (dark) 0.16f else 0.15f),
                ),
                startY = top,
                endY = bottom,
            ),
        )
    }

    // 3. Altitude & distance grids
    val targetAltStep = maxHeight * 34.dp.toPx() / (bottom - top)
    val altStep = listOf(1000.0, 1500.0, 2000.0, 3000.0, 5000.0, 7500.0, 10_000.0).firstOrNull { it >= targetAltStep }
        ?: maxHeight
    var alt = 0.0
    while (alt <= maxHeight + 1.0) {
        val ly = y(alt)
        drawLine(secondaryText.copy(alpha = 0.18f), Offset(left, ly), Offset(right, ly), strokeWidth = 0.6.dp.toPx())
        val label = if (alt % 1000.0 == 0.0) "${(alt / 1000).roundToInt()}km" else "%.1fkm".format(alt / 1000)
        val measured = textMeasurer.measure(label, labelStyle)
        drawText(
            measured,
            topLeft = Offset((left - measured.size.width - 4.dp.toPx()).coerceAtLeast(0f), ly - measured.size.height / 2f),
        )
        alt += altStep
    }
    data.distances.forEachIndexed { index, distance ->
        if (index % 4 == 0) {
            val lx = x(distance)
            drawLine(
                secondaryText.copy(alpha = 0.14f),
                Offset(lx, top),
                Offset(lx, bottom),
                strokeWidth = 0.6.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 4.dp.toPx())),
            )
            val measured = textMeasurer.measure("${distance.roundToInt()}km", labelStyle)
            val textX = if (distance == maxDistance) {
                (lx - measured.size.width).coerceAtLeast(left)
            } else {
                (lx - measured.size.width / 2f).coerceIn(left, right - measured.size.width)
            }
            drawText(measured, topLeft = Offset(textX, bottom + 4.dp.toPx()))
        }
    }

    // 4. Gradient mountain terrain silhouette + observer halo dot
    if (data.terrain.size == data.distances.size) {
        val fill = Path().apply {
            moveTo(x(data.distances[0]), y(data.terrain[0]))
            for (i in 1 until data.distances.size) {
                lineTo(x(data.distances[i]), y(data.terrain[i]))
            }
            lineTo(right, bottom)
            lineTo(left, bottom)
            close()
        }
        val terrainMinY = data.terrain.minOfOrNull { y(it) } ?: bottom
        val terrainColors = if (dark) {
            listOf(Color(0.36f, 0.42f, 0.44f, 0.65f), Color(0.20f, 0.24f, 0.28f, 0.88f))
        } else {
            listOf(Color(0.46f, 0.51f, 0.49f, 0.40f), Color(0.32f, 0.37f, 0.36f, 0.60f))
        }
        drawPath(fill, Brush.verticalGradient(terrainColors, startY = terrainMinY, endY = bottom))
        val ridge = Path().apply {
            moveTo(x(data.distances[0]), y(data.terrain[0]))
            for (i in 1 until data.distances.size) {
                lineTo(x(data.distances[i]), y(data.terrain[i]))
            }
        }
        drawPath(ridge, Color(0.41f, 0.46f, 0.45f), style = Stroke(width = 1.6.dp.toPx()))
    }

    // 5. Rounded layered cloud envelopes per pressure family (1..3) with lit burnable undersides
    val sampleCount = max(1, data.distances.size - 1)
    val columnSpan = (right - left) / sampleCount.toFloat()
    val cellWidth = max(5.dp.toPx(), columnSpan * 0.92f)
    fun scalar(grid: List<List<Double?>>, sample: Int, level: Int): Double? =
        grid.getOrNull(sample)?.getOrNull(level)
    fun integer(grid: List<List<Int>>, sample: Int, level: Int): Int? =
        grid.getOrNull(sample)?.getOrNull(level)

    for (i in data.distances.indices) {
        for (family in 1..3) {
            val cloudyLevels = data.levels.indices.filter { level ->
                if (integer(data.familyCodes, i, level) != family) return@filter false
                val amount = scalar(data.amounts, i, level)
                    ?: max(scalar(data.presence, i, level) ?: 0.0, (scalar(data.fractions, i, level) ?: 0.0) / 100.0)
                amount >= 0.06
            }
            if (cloudyLevels.isEmpty()) continue

            val layerBase = cloudyLevels.mapNotNull { scalar(data.layerBases, i, it) }.minOrNull()
                ?: cloudyLevels.mapNotNull { scalar(data.heights, i, it) }.minOrNull()?.minus(700.0)
            val layerTop = cloudyLevels.mapNotNull { scalar(data.layerTops, i, it) }.maxOrNull()
                ?: cloudyLevels.mapNotNull { scalar(data.heights, i, it) }.maxOrNull()?.plus(700.0)
            if (layerBase == null || layerTop == null || layerTop <= layerBase) continue

            val maxAmount = cloudyLevels.mapNotNull {
                scalar(data.amounts, i, it)
                    ?: max(scalar(data.presence, i, it) ?: 0.0, (scalar(data.fractions, i, it) ?: 0.0) / 100.0)
            }.maxOrNull()
            val maxPresence = cloudyLevels.mapNotNull { scalar(data.presence, i, it) }.maxOrNull()
            val maxRh = cloudyLevels.mapNotNull { scalar(data.rh, i, it) }.maxOrNull()
            val maxFraction = cloudyLevels.mapNotNull { scalar(data.fractions, i, it) }.maxOrNull()
            val status = cloudyLevels.mapNotNull { integer(data.statuses, i, it) }.maxOrNull() ?: 0

            val centerX = x(data.distances[i])
            val rectLeft = max(left, centerX - cellWidth / 2f)
            val rectRight = min(right, centerX + cellWidth / 2f)
            val terrainY = data.terrain.getOrNull(i)?.let { y(it) } ?: bottom
            val rectTop = max(top, y(layerTop))
            val rectBottom = min(terrainY, y(layerBase))
            if (rectRight <= rectLeft || rectBottom <= rectTop) continue

            val w = rectRight - rectLeft
            val h = rectBottom - rectTop
            val r = min(4.5.dp.toPx(), min(w, h) * 0.35f)
            val cloudShape = Path().apply {
                addRoundRect(RoundRect(Rect(rectLeft, rectTop, rectRight, rectBottom), CornerRadius(r, r)))
            }
            val fill = cloudCellFillColor(maxAmount, maxPresence, maxRh, maxFraction, family)
            if (fill != null) {
                val topColor = fill.copy(alpha = fill.alpha * 0.82f)
                val bottomColor = if (status == 3) Color(0.97f, 0.52f, 0.28f, 0.62f) else fill
                drawPath(cloudShape, Brush.verticalGradient(listOf(topColor, bottomColor), startY = rectTop, endY = rectBottom))
            }
            if (status == 3) {
                drawPath(cloudShape, Color(0.96f, 0.43f, 0.20f), style = Stroke(width = 1.6.dp.toPx()))
            } else if (status == 2) {
                drawPath(
                    cloudShape,
                    Color(0.85f, 0.56f, 0.22f),
                    style = Stroke(
                        width = 1.2.dp.toPx(),
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx())),
                    ),
                )
            }
        }
    }

    // 6. Temperature layer lines
    for (l in data.levels.indices) {
        val points = mutableListOf<Offset>()
        val temps = mutableListOf<Double>()
        for (i in data.distances.indices) {
            val h = scalar(data.heights, i, l) ?: continue
            points.add(Offset(x(data.distances[i]), y(h)))
            scalar(data.temps, i, l)?.let { temps.add(it) }
        }
        if (points.size <= 1) continue
        val line = Path().apply {
            moveTo(points[0].x, points[0].y)
            for (k in 1 until points.size) lineTo(points[k].x, points[k].y)
        }
        val avg = if (temps.isEmpty()) 0.0 else temps.average()
        val color = when {
            avg < -30.0 -> Color(0.55f, 0.40f, 0.45f, 0.62f)
            avg < 0.0 -> Color(0.69f, 0.42f, 0.36f, 0.62f)
            else -> Color(0.78f, 0.31f, 0.22f, 0.62f)
        }
        drawPath(line, color, style = Stroke(width = 1.dp.toPx()))
        if (l % 2 == 0) {
            val last = points.last()
            val measured = textMeasurer.measure("${data.levels[l]}hPa", labelStyle)
            drawText(
                measured,
                topLeft = Offset(last.x - measured.size.width - 4.dp.toPx(), (last.y - measured.size.height - 2f).coerceAtLeast(top)),
            )
        }
    }

    // 7. Required illumination threshold (ASL), dashed amber
    if (data.requiredASL.size == data.distances.size) {
        val reqLine = Path().apply {
            moveTo(x(data.distances[0]), y(data.requiredASL[0]))
            for (i in 1 until data.distances.size) {
                lineTo(x(data.distances[i]), y(data.requiredASL[i]))
            }
        }
        drawPath(
            reqLine,
            Color(0.83f, 0.55f, 0.22f),
            style = Stroke(
                width = 1.8.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 4.dp.toPx())),
            ),
        )
    }

    // 8. Theoretical target cloud base/top markers
    for (target in data.targets) {
        val distance = target.optNum("distance_km") ?: continue
        val base = target.optNum("cloud_base_asl_m") ?: continue
        val radius = 3.5.dp.toPx()
        drawCircle(warm, radius = radius, center = Offset(x(distance), y(base)))
        target.optNum("cloud_top_asl_m")?.let { topM ->
            drawCircle(coolAccent, radius = radius, center = Offset(x(distance), y(topM)))
        }
    }

    // 9. Observer halo dot on top of terrain ridge
    if (data.terrain.isNotEmpty() && data.distances.isNotEmpty()) {
        val obs = Offset(x(data.distances[0]), y(data.terrain[0]))
        val haloRadius = 8.dp.toPx()
        val dotRadius = 4.2.dp.toPx()
        drawCircle(Color(0.95f, 0.49f, 0.26f, 0.26f), radius = haloRadius, center = obs)
        drawCircle(Color(0.95f, 0.49f, 0.26f), radius = dotRadius, center = obs)
        drawCircle(Color.White.copy(alpha = 0.9f), radius = dotRadius, center = obs, style = Stroke(width = 1.2.dp.toPx()))
        val obsMeasured = textMeasurer.measure(
            observerLabel,
            TextStyle(fontSize = labelSp, fontWeight = FontWeight.SemiBold, color = primaryText),
        )
        drawText(
            obsMeasured,
            topLeft = Offset(obs.x + dotRadius + 5.dp.toPx(), (obs.y - obsMeasured.size.height - 4.dp.toPx()).coerceAtLeast(top)),
        )
    }
}

private fun cloudCellFillColor(
    amount: Double?,
    presence: Double?,
    rh: Double?,
    fraction: Double?,
    family: Int,
): Color? {
    val cloudAmount = amount ?: max(presence ?: 0.0, (fraction ?: 0.0) / 100.0)
    val humidity = max(0.0, (rh ?: 0.0) - 55.0) / 45.0
    if (cloudAmount < 0.06) {
        if (fraction != null && fraction <= 0.0) return null
        if (humidity < 0.06) return null
        return Color(112, 151, 126).copy(alpha = min(0.34f, (0.04 + humidity * 0.26).toFloat()))
    }
    val alpha = min(0.82f, (0.10 + cloudAmount * 0.68).toFloat())
    return when (family) {
        1 -> Color(116, 157, 194).copy(alpha = alpha)
        2 -> Color(105, 154, 142).copy(alpha = alpha)
        else -> Color(126, 136, 145).copy(alpha = alpha)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CrossSectionLegend(warm: Color, cool: Color) {
    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            LegendChip(stringResource(R.string.legend_high_cloud), Color(116, 157, 194))
            LegendChip(stringResource(R.string.legend_mid_cloud), Color(105, 154, 142))
            LegendChip(stringResource(R.string.legend_low_cloud), Color(126, 136, 145))
            LegendChip(stringResource(R.string.legend_humid), Color(112, 151, 126))
            LegendLine(stringResource(R.string.legend_temp), Color(0.69f, 0.42f, 0.36f))
            LegendLine(stringResource(R.string.legend_terrain), Color(0.41f, 0.45f, 0.44f))
            LegendDot(stringResource(R.string.legend_cloud_base), warm)
            LegendDot(stringResource(R.string.legend_cloud_top), cool)
            LegendLine(stringResource(R.string.legend_required_asl), Color(0.83f, 0.55f, 0.22f))
            LegendFrame(stringResource(R.string.legend_burnable_candidate), Color(0.94f, 0.42f, 0.21f))
            LegendFrame(stringResource(R.string.legend_occluded_candidate), Color(0.83f, 0.55f, 0.22f))
        }
        Text(
            stringResource(R.string.legend_footnote),
            fontSize = 9.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
            lineHeight = 12.sp,
        )
    }
}

@Composable
private fun LegendChip(title: String, color: Color) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Box(Modifier.size(width = 11.dp, height = 7.dp).background(color, RoundedCornerShape(1.5.dp)))
        Text(title, fontSize = 9.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f))
    }
}

@Composable
private fun LegendLine(title: String, color: Color) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Box(Modifier.size(width = 11.dp, height = 2.dp).background(color))
        Text(title, fontSize = 9.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f))
    }
}

@Composable
private fun LegendDot(title: String, color: Color) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Box(Modifier.size(7.dp).background(color, CircleShape))
        Text(title, fontSize = 9.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f))
    }
}

@Composable
private fun LegendFrame(title: String, color: Color) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Box(Modifier.size(width = 10.dp, height = 7.dp).border(1.2.dp, color, RoundedCornerShape(1.5.dp)))
        Text(title, fontSize = 9.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f))
    }
}

@Composable
private fun ProfileMetaChip(title: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(1.dp)) {
        Text(title, fontSize = 8.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f))
        Text(value, fontSize = 10.sp, fontWeight = FontWeight.Medium, fontFamily = FontFamily.Monospace)
    }
}

@Composable
private fun CloudGenusProxyCard(proxy: JSONObject) {
    val label = proxy.optString("label", "unknown")
    val compatibleArr = proxy.optJSONArray("compatible_genera")
    val compatibleNames = if (compatibleArr != null) {
        (0 until compatibleArr.length()).mapNotNull { compatibleArr.optString(it).ifEmpty { null } }
            .map { genusDisplayName(it) }
            .joinToString("、")
    } else ""
    val scores = proxy.optJSONObject("candidate_scores")
    val candidateEntries = if (scores != null) {
        scores.keys().asSequence().mapNotNull { key ->
            val v = scores.optDouble(key)
            if (v.isNaN() || !v.isFinite()) null else key to v
        }.sortedByDescending { it.second }.take(3).toList()
    } else emptyList()
    val candidatesText = buildList {
        for ((k, v) in candidateEntries) {
            add("${genusDisplayName(k)} ${(v * 100).roundToInt()}%")
        }
    }.joinToString(" · ")

    WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
        Row(
            Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.Top,
        ) {
            IconBadge(Icons.Filled.Cloud, tint = coolAccentColor())
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(stringResource(R.string.cloud_genus_proxy_card_title), fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        if (label != "unknown" && label.isNotEmpty()) genusDisplayName(label)
                        else if (compatibleNames.isNotEmpty()) stringResource(R.string.cloud_genus_compatible_line_fmt, compatibleNames)
                        else stringResource(R.string.cloud_genus_undetermined),
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.weight(1f))
                    proxy.optNum("confidence")?.takeIf { it.isFinite() }?.let { conf ->
                        Text(
                            "${(conf * 100).roundToInt()}%",
                            fontSize = 11.sp,
                            style = TextStyle(fontFeatureSettings = "tnum"),
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                        )
                    }
                }
                if (candidatesText.isNotEmpty()) {
                    Text(
                        stringResource(R.string.cloud_genus_candidates_fmt, candidatesText),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    )
                }
                Text(
                    stringResource(R.string.cloud_genus_proxy_disclaimer),
                    fontSize = 10.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f),
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TargetStrip(targets: List<JSONObject>) {
    if (targets.isEmpty()) return
    val burnable = targets.filter { it.optString("status") == "burnable" }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(stringResource(R.string.targets_header), fontSize = 11.sp, fontWeight = FontWeight.Medium)
        if (burnable.isNotEmpty()) {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                for (target in burnable.take(8)) {
                    val family = familyDisplayName(target.optString("pressure_family"))
                    val phase = phaseDisplayName(target.optJSONObject("cloud_phase")?.optString("label"))
                    val dist = target.optNum("distance_km")?.let { "%.0f".format(it) } ?: "—"
                    val base = target.optNum("cloud_base_asl_m")?.let { "%.1f".format(it / 1000.0) } ?: "—"
                    val top = target.optNum("cloud_top_asl_m")?.let { "%.1f".format(it / 1000.0) } ?: "—"
                    Surface(
                        shape = CircleShape,
                        color = Color(0xFFF06B35).copy(alpha = 0.09f),
                        border = BorderStroke(0.75.dp, Color(0xFFF06B35).copy(alpha = 0.36f)),
                    ) {
                        Text(
                            "$family · $phase · $dist km · ASL $base–$top km",
                            fontSize = 10.sp,
                            style = TextStyle(fontFeatureSettings = "tnum"),
                            modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
                        )
                    }
                }
            }
        } else {
            val diag = targets.firstOrNull { it.optString("status") != "burnable" } ?: return
            val family = familyDisplayName(diag.optString("pressure_family"))
            val reason = diag.optString("status_reason").ifEmpty { stringResource(R.string.targets_reason_fallback) }
            Text(
                stringResource(R.string.targets_none_fmt, family, reason),
                fontSize = 10.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        }
    }
}

// ---------------------------------------------------------------- 数据整形

private fun stepsOf(report: JSONObject?): List<JSONObject> {
    val array = report?.optJSONArray("steps") ?: return emptyList()
    return (0 until array.length()).mapNotNull { array.optJSONObject(it) }
}

/** steps → (时间轴分钟, 质量指数)，过滤缺测样本（同 ForecastComparison.curve 语义）。 */
private fun curvePoints(result: JSONObject, model: String): List<Pair<Long, Double>> {
    val report = modelReport(result, model) ?: return emptyList()
    val points = mutableListOf<Pair<Long, Double>>()
    for (step in stepsOf(report)) {
        if (!step.optBoolean("valid", true)) continue
        val quality = step.optNum("instant_quality_index") ?: continue
        val time = parseLocalTime(step.optString("valid_time_local")) ?: continue
        points.add(time.toEpochSecond(java.time.ZoneOffset.UTC) / 60 to quality)
    }
    return points.sortedBy { it.first }
}
