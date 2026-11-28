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
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.filled.SatelliteAlt
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material.icons.filled.WbTwilight
import androidx.compose.material.icons.filled.ZoomIn
import androidx.compose.material.icons.filled.ZoomOut
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
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
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
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
import com.firesky.mobile.store.isStepValid
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * 结果卡片 —— WorkHeroView / WorkResultsView（point 作用域）同构：
 * 双模型 hero 卡 + 三页签（概览/大气剖面/逐时台账）+ ⓘ 诊断 ModalBottomSheet。
 */
@OptIn(ExperimentalMaterial3Api::class)
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
            StreamlinedAnalysisTabs(
                selectedTab = state.resultTab,
                onSelectTab = { store.selectResultTab(it) },
                onOpenDiagnostics = { store.setOpenDiagnostics(true) },
            )
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
                "大气剖面" -> ProfileSection(activeResult, state.selectedModel)
                "逐时台账" -> LedgerContent(activeResult, state.selectedModel)
                else -> OverviewContent(activeResult, state.selectedModel)
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
                    Segmented(
                        options = COMPARISON_MODELS,
                        titles = listOf("GFS", "EC"),
                        selection = state.selectedModel,
                        onSelect = { store.selectModel(it) },
                        modifier = Modifier.width(116.dp),
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
                    DiagnosticContent(activeResult, state.selectedModel, showHeader = false)
                }
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
    else -> stringResource(R.string.tab_ledger)
}

/** 3 主页签（概览 / 大气剖面 / 逐时台账）+ 尾随 ⓘ 诊断按钮（单点与卫星实况共用）。 */
@Composable
internal fun StreamlinedAnalysisTabs(
    selectedTab: String,
    onSelectTab: (String) -> Unit,
    onOpenDiagnostics: () -> Unit,
) {
    val dark = LocalFireSkyDark.current
    val trackFill = if (dark) Color(0xFF161E2E) else Color(0xFFEAF0F6)
    val trackBorder = if (dark) Color(0xFF50617B).copy(alpha = 0.45f) else Color(0xFFD8E1EA).copy(alpha = 0.85f)
    val warm = MaterialTheme.colorScheme.primary
    val activeTab = if (selectedTab in listOf("概览", "大气剖面", "逐时台账")) selectedTab else "概览"

    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(
            Modifier
                .weight(1f)
                .background(trackFill, CircleShape)
                .border(0.75.dp, trackBorder, CircleShape)
                .padding(3.dp),
        ) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .selectableGroup(),
                horizontalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                for (tab in listOf("概览", "大气剖面", "逐时台账")) {
                    val selected = activeTab == tab
                    val tabWeight = when (tab) {
                        "逐时台账" -> 1.26f
                        "概览" -> 0.90f
                        else -> 0.84f
                    }
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
                            .weight(tabWeight)
                            .heightIn(min = 34.dp)
                            .then(pillShadow)
                            .clip(CircleShape)
                            .background(pillFill, CircleShape)
                            .selectable(selected = selected, role = Role.Tab, onClick = { onSelectTab(tab) }),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            tabTitle(tab),
                            fontSize = 11.sp,
                            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                            color = if (selected) MaterialTheme.colorScheme.onSurface
                            else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.72f),
                            maxLines = 1,
                            softWrap = false,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.padding(horizontal = 4.dp, vertical = 7.dp),
                        )
                        if (selected) {
                            Box(
                                Modifier
                                    .align(Alignment.BottomCenter)
                                    .padding(bottom = 2.dp)
                                    .width(18.dp)
                                    .height(2.dp)
                                    .background(warm, CircleShape),
                            )
                        }
                    }
                }
            }
        }

        Surface(
            onClick = onOpenDiagnostics,
            shape = CircleShape,
            color = trackFill,
            border = BorderStroke(0.75.dp, trackBorder),
            modifier = Modifier.heightIn(min = 38.dp),
        ) {
            Row(
                Modifier.padding(horizontal = 9.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(3.dp),
            ) {
                Icon(
                    Icons.Filled.Info,
                    contentDescription = stringResource(R.string.cd_open_diagnostics),
                    modifier = Modifier.size(12.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.80f),
                )
                Text(
                    stringResource(R.string.diag_chip_label),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    softWrap = false,
                )
            }
        }
    }
}

/** 概览：光谱色彩预览 + 16 方位雷达 + 三组指标卡 + 评分组成（GFS vs EC）+ 质量曲线。 */
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
                    }
                }
            }
        }
        QualityBreakdownCard(result)
        CurveCard(result)
    }
}

private data class BreakdownCellContent(
    val primary: String,
    val detail: String,
    val primaryColor: Color,
)

private fun signedDeltaText(value: Double?): String? {
    if (value == null || !value.isFinite()) return null
    if (abs(value) < 0.0005) return "0.000"
    return "%+.3f".format(value)
}

/** 统一 3 列「评分组成」（评分项 | GFS | EC）对比卡 + 单行限制因素 + 可展开「详情与口径」。 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun QualityBreakdownCard(result: JSONObject) {
    var expanded by remember(result) { mutableStateOf(false) }
    val dark = LocalFireSkyDark.current
    val warm = MaterialTheme.colorScheme.primary
    val cool = coolAccentColor()
    val secondary = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.60f)
    val positiveColor = if (dark) Color(0xFF63D6AB) else Color(0xFF1F6E5A)
    val negativeColor = MaterialTheme.colorScheme.error
    val tnumStyle = TextStyle(fontFeatureSettings = "tnum")

    val gfsReport = modelReport(result, MODEL_GFS)
    val ecReport = modelReport(result, MODEL_EC)

    @Composable
    fun deltaColor(delta: String?): Color = when {
        delta == null || delta == "0.000" -> secondary
        delta.startsWith("-") -> negativeColor
        else -> positiveColor
    }

    @Composable
    fun cellContent(idx: Int, report: JSONObject): BreakdownCellContent {
        val c = report.optJSONObject("quality_components") ?: JSONObject()
        return when (idx) {
            0 -> {
                val delta = signedDeltaText(c.optNum("coverage_base_score") ?: c.optNum("coverage_score"))
                val eff = percent(c.optNum("burnable_cloud_coverage_effective"))
                val load = fmt(c.optNum("forward_burnable_cloud_load_effective") ?: c.optNum("forward_burnable_cloud_load_peak"), 2)
                BreakdownCellContent(
                    primary = delta ?: "—",
                    detail = stringResource(R.string.breakdown_effective_load_fmt, eff, load),
                    primaryColor = deltaColor(delta),
                )
            }
            1 -> {
                val ratio = percent(c.optNum("forward_burnable_cloud_ratio_peak"))
                val fwd = percent(c.optNum("forward_burnable_cloud_field_amount_peak"))
                val total = percent(c.optNum("forward_view_cloud_field_amount_peak"))
                BreakdownCellContent(
                    primary = ratio,
                    detail = stringResource(R.string.breakdown_forward_total_fmt, fwd, total),
                    primaryColor = MaterialTheme.colorScheme.onBackground,
                )
            }
            2 -> {
                val risk = percent(c.optNum("solar_incoming_block_risk_at_best_cloud") ?: c.optNum("solar_incoming_block_risk_mean"))
                BreakdownCellContent(
                    primary = risk,
                    detail = stringResource(R.string.breakdown_included_in_burnable),
                    primaryColor = MaterialTheme.colorScheme.onBackground,
                )
            }
            3 -> {
                val delta = signedDeltaText(c.optNum("duration_adjustment"))
                val mins = fmt(report.optNum("duration_minutes") ?: c.optNum("duration_minutes"), 0)
                val factor = fmt(c.optNum("duration_factor"), 3)
                BreakdownCellContent(
                    primary = delta ?: "—",
                    detail = stringResource(R.string.breakdown_duration_fmt, mins, factor),
                    primaryColor = deltaColor(delta),
                )
            }
            4 -> {
                val delta = signedDeltaText(c.optNum("incoming_aod_adjustment"))
                val aod = fmt(c.optNum("quality_aod550"), 3)
                val factor = fmt(c.optNum("incoming_aod_illumination_factor"), 3)
                BreakdownCellContent(
                    primary = delta ?: "—",
                    detail = stringResource(R.string.breakdown_aod_factor_fmt, aod, factor),
                    primaryColor = deltaColor(delta),
                )
            }
            else -> {
                val delta = signedDeltaText(c.optNum("observer_aod_adjustment"))
                val factor = fmt(c.optNum("observer_aod_contrast_factor"), 3)
                BreakdownCellContent(
                    primary = delta ?: "—",
                    detail = stringResource(R.string.breakdown_contrast_factor_fmt, factor),
                    primaryColor = deltaColor(delta),
                )
            }
        }
    }

    val rowTitles = listOf(
        R.string.breakdown_row_coverage,
        R.string.breakdown_row_ratio,
        R.string.breakdown_row_incoming_block,
        R.string.breakdown_row_duration,
        R.string.breakdown_row_incoming_aod,
        R.string.breakdown_row_observer_aod,
    )

    val gfsLimit = gfsReport?.optJSONObject("quality_components")?.optString("quality_limiting_factor")?.ifEmpty { null }
    val ecLimit = ecReport?.optJSONObject("quality_components")?.optString("quality_limiting_factor")?.ifEmpty { null }
    val limitingSummary = when {
        gfsLimit == null && ecLimit == null -> null
        gfsLimit != null && ecLimit != null && gfsLimit == ecLimit ->
            stringResource(R.string.breakdown_limit_same_fmt, gfsLimit)
        gfsLimit != null && ecLimit != null ->
            stringResource(R.string.breakdown_limit_both_fmt, gfsLimit, ecLimit)
        gfsLimit != null ->
            stringResource(R.string.breakdown_limit_model_fmt, "GFS", gfsLimit)
        else ->
            stringResource(R.string.breakdown_limit_model_fmt, "EC", ecLimit!!)
    }

    WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 14.dp) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    stringResource(R.string.breakdown_title),
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                Row(
                    Modifier
                        .clip(CircleShape)
                        .clickable { expanded = !expanded }
                        .padding(horizontal = 6.dp, vertical = 3.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    Text(
                        stringResource(R.string.breakdown_details_toggle),
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Medium,
                        color = secondary,
                    )
                    Icon(
                        if (expanded) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp),
                        tint = secondary,
                    )
                }
            }

            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                for (model in COMPARISON_MODELS) {
                    val mReport = modelReport(result, model)
                    val mError = modelError(result, model)
                    val mColor = if (model == MODEL_EC) cool else warm
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Box(
                            Modifier
                                .size(6.dp)
                                .background(
                                    if (mReport != null) mColor else if (mError != null) negativeColor else secondary,
                                    CircleShape,
                                ),
                        )
                        if (mReport != null) {
                            Text(model.uppercase(), fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
                            Text(
                                "${fmt(mReport.optNum("quality_index"), 3)} / 2.5",
                                fontSize = 11.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = mColor,
                                style = tnumStyle,
                            )
                            val lvl = mReport.optString("quality_level", "")
                            if (lvl.isNotEmpty()) {
                                Text(lvl, fontSize = 10.sp, color = secondary)
                            }
                        } else if (mError != null) {
                            Text("${model.uppercase()} ${stringResource(R.string.status_incomplete)}", fontSize = 10.sp, color = negativeColor)
                        } else {
                            Text("${model.uppercase()} ${stringResource(R.string.status_no_result)}", fontSize = 10.sp, color = secondary)
                        }
                    }
                }
            }

            HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))

            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    stringResource(R.string.breakdown_col_item),
                    Modifier.weight(0.9f),
                    fontSize = 10.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = secondary,
                )
                for (model in COMPARISON_MODELS) {
                    val mColor = if (model == MODEL_EC) cool else warm
                    Row(
                        Modifier.weight(1f),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Box(Modifier.size(5.dp).background(mColor, CircleShape))
                        Text(model.uppercase(), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    }
                }
            }

            for (idx in rowTitles.indices) {
                HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f))
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    verticalAlignment = Alignment.Top,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        stringResource(rowTitles[idx]),
                        Modifier.weight(0.9f),
                        fontSize = 10.5.sp,
                        color = secondary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    for (model in COMPARISON_MODELS) {
                        val mReport = if (model == MODEL_GFS) gfsReport else ecReport
                        if (mReport != null) {
                            val cell = cellContent(idx, mReport)
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(1.dp)) {
                                Text(
                                    cell.primary,
                                    fontSize = 11.5.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    color = cell.primaryColor,
                                    style = tnumStyle,
                                    maxLines = 1,
                                )
                                Text(
                                    cell.detail,
                                    fontSize = 9.5.sp,
                                    color = secondary,
                                    style = tnumStyle,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        } else {
                            Text("—", Modifier.weight(1f), fontSize = 11.sp, color = secondary)
                        }
                    }
                }
            }

            if (limitingSummary != null) {
                Text(
                    limitingSummary,
                    fontSize = 10.sp,
                    color = secondary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            AnimatedVisibility(visible = expanded) {
                val sample = gfsReport ?: ecReport
                val sampleComp = sample?.optJSONObject("quality_components") ?: JSONObject()
                val radiusVal = sampleComp.optNum("forward_field_radius_km") ?: sample?.optNum("analysis_radius_km")
                val radiusText = radiusVal?.let { "%.0f km".format(it) } ?: "—"
                val sectorText = sampleComp.optNum("forward_sector_half_angle_deg")?.let { "±%.0f°".format(it) } ?: "—"
                Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
                    Text(
                        stringResource(R.string.breakdown_scope_fmt, radiusText, sectorText),
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Medium,
                        color = secondary,
                        style = tnumStyle,
                    )
                    for (model in COMPARISON_MODELS) {
                        val r = if (model == MODEL_GFS) gfsReport else ecReport
                        if (r != null) {
                            val c = r.optJSONObject("quality_components") ?: JSONObject()
                            val srcLabel = if ((r.optString("model_source", model)) == "ec") {
                                stringResource(R.string.breakdown_src_ec)
                            } else {
                                stringResource(R.string.breakdown_src_gfs)
                            }
                            val mColor = if (model == MODEL_EC) cool else warm
                            Row(
                                Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                            ) {
                                Text(model.uppercase(), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = mColor)
                                Text(
                                    stringResource(
                                        R.string.breakdown_model_detail_fmt,
                                        fmt(c.optNum("raw_quality_index"), 3),
                                        srcLabel,
                                        fmt(c.optNum("observer_return_optical_depth_mean"), 3),
                                        fmt(c.optNum("observer_return_transmission_mean"), 3),
                                    ),
                                    fontSize = 10.sp,
                                    color = secondary,
                                    style = tnumStyle,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }
                    Text(
                        stringResource(R.string.breakdown_footnote),
                        fontSize = 9.5.sp,
                        color = secondary,
                        lineHeight = 13.sp,
                    )
                }
            }
        }
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
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        stringResource(R.string.spectral_color_title),
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                        modifier = Modifier.weight(1f),
                    )
                    if (peakHex.isNotEmpty()) {
                        Text(
                            peakHex.uppercase(),
                            fontSize = 10.sp,
                            fontFamily = FontFamily.Monospace,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                            modifier = Modifier
                                .background(
                                    MaterialTheme.colorScheme.onBackground.copy(alpha = 0.06f),
                                    RoundedCornerShape(6.dp),
                                )
                                .padding(horizontal = 6.dp, vertical = 2.dp),
                        )
                    }
                }
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
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                val caption = if (azim != null && elev != null) {
                    stringResource(R.string.spectral_solar_caption_fmt, azim, elev)
                } else {
                    ""
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

/** 逐时台账（WorkLedgerView 同构：默认隐藏 isStepValid == false 未判断时次，支持行内切换）。 */
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
    val hiddenInvalidCount = allSteps.count { !isStepValid(it) }
    val visibleSteps = if (showInvalid) allSteps else allSteps.filter { isStepValid(it) }
    val secondary = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
    val warm = MaterialTheme.colorScheme.primary
    val cool = coolAccentColor()
    val tnum = TextStyle(fontFeatureSettings = "tnum")

    Column {
        Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
            Column(Modifier.width(500.dp)) {
                Row(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                    Text(stringResource(R.string.col_time), Modifier.width(64.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_quality), Modifier.width(64.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_level), Modifier.width(104.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_burnable), Modifier.width(44.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_coverage), Modifier.width(72.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_family), Modifier.width(86.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
                    Text(stringResource(R.string.col_solar), Modifier.width(66.dp), fontSize = 10.sp, fontWeight = FontWeight.SemiBold, color = secondary)
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
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            timeLabel(step.optString("valid_time_local")),
                            Modifier.width(64.dp),
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
                            Modifier.width(72.dp),
                            fontSize = 11.sp,
                            style = tnum,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                        )
                        Text(
                            if (isValid) familyDisplayName(step.optString("dominant_burnable_pressure_family", "").ifEmpty { null }) else "—",
                            Modifier.width(86.dp),
                            fontSize = 11.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                        )
                        Text(
                            if (isValid) fmt(step.optNum("solar_elevation_deg"), 1) else "—",
                            Modifier.width(66.dp),
                            fontSize = 11.sp,
                            style = tnum,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = rowAlpha),
                        )
                    }
                    HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f))
                }
            }
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
private fun DiagnosticContent(result: JSONObject, model: String, showHeader: Boolean = true) {
    val report = modelReport(result, model)
    val err = modelError(result, model)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (showHeader) {
            Text(stringResource(R.string.tab_diagnostic), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }
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

private fun midpoint(a: Offset, b: Offset): Offset = Offset((a.x + b.x) * 0.5f, (a.y + b.y) * 0.5f)

internal fun Path.appendSmoothSegment(points: List<Offset>) {
    if (points.size <= 1) return
    if (points.size == 2) {
        lineTo(points[1].x, points[1].y)
        return
    }
    val m0 = midpoint(points[0], points[1])
    lineTo(m0.x, m0.y)
    for (idx in 1 until points.size - 1) {
        val mid = midpoint(points[idx], points[idx + 1])
        quadraticBezierTo(points[idx].x, points[idx].y, mid.x, mid.y)
    }
    val last = points.last()
    lineTo(last.x, last.y)
}

internal fun smoothOpenCurvePath(points: List<Offset>): Path {
    val path = Path()
    val first = points.firstOrNull() ?: return path
    path.moveTo(first.x, first.y)
    path.appendSmoothSegment(points)
    return path
}

private data class ColumnStratum(
    val baseASL: Double,
    val topASL: Double,
    val amount: Double,
    val status: Int,
)

private data class CloudRunPoint(
    val sampleIndex: Int,
    val cx: Float,
    val topY: Float,
    val bottomY: Float,
    val stratum: ColumnStratum,
)

private data class BurnableTargetFocus(
    val distanceKM: Double,
    val baseASL: Double,
)

private data class CrossSectionTakeaway(
    val text: String,
    val status: Int,
)

private fun gridScalar(grid: List<List<Double?>>, sample: Int, level: Int): Double? =
    grid.getOrNull(sample)?.getOrNull(level)

private fun gridInt(grid: List<List<Int>>, sample: Int, level: Int): Int? =
    grid.getOrNull(sample)?.getOrNull(level)

private fun columnStratum(data: CrossSectionData, sample: Int, family: Int): ColumnStratum? {
    val cloudyLevels = data.levels.indices.filter { level ->
        if (gridInt(data.familyCodes, sample, level) != family) return@filter false
        val amount = gridScalar(data.amounts, sample, level)
            ?: max(gridScalar(data.presence, sample, level) ?: 0.0, (gridScalar(data.fractions, sample, level) ?: 0.0) / 100.0)
        amount >= 0.06
    }
    if (cloudyLevels.isEmpty()) return null
    val layerBase = cloudyLevels.mapNotNull { gridScalar(data.layerBases, sample, it) }.minOrNull()
        ?: cloudyLevels.mapNotNull { gridScalar(data.heights, sample, it) }.minOrNull()?.minus(700.0)
    val layerTop = cloudyLevels.mapNotNull { gridScalar(data.layerTops, sample, it) }.maxOrNull()
        ?: cloudyLevels.mapNotNull { gridScalar(data.heights, sample, it) }.maxOrNull()?.plus(700.0)
    if (layerBase == null || layerTop == null || layerTop <= layerBase) return null

    val amountMax = cloudyLevels.mapNotNull {
        gridScalar(data.amounts, sample, it)
            ?: max(gridScalar(data.presence, sample, it) ?: 0.0, (gridScalar(data.fractions, sample, it) ?: 0.0) / 100.0)
    }.maxOrNull() ?: 0.2
    val status = cloudyLevels.mapNotNull { gridInt(data.statuses, sample, it) }.maxOrNull() ?: 0
    return ColumnStratum(baseASL = layerBase, topASL = layerTop, amount = amountMax, status = status)
}

private fun primaryBurnableFocus(data: CrossSectionData): BurnableTargetFocus? {
    for (target in data.targets) {
        if (target.optString("status") == "burnable") {
            val dist = target.optNum("distance_km")
            val base = target.optNum("cloud_base_asl_m")
            if (dist != null && base != null) {
                return BurnableTargetFocus(distanceKM = dist, baseASL = base)
            }
        }
    }
    val litPoints = mutableListOf<Triple<Double, Double, Double>>()
    for (i in data.distances.indices) {
        for (family in 1..3) {
            val s = columnStratum(data, i, family)
            if (s != null && s.status == 3) {
                litPoints.add(Triple(data.distances[i], s.baseASL, s.amount))
            }
        }
    }
    if (litPoints.isEmpty()) return null
    val sorted = litPoints.sortedBy { it.first }
    val mid = sorted[sorted.size / 2]
    return BurnableTargetFocus(distanceKM = mid.first, baseASL = mid.second)
}

private data class CloudSilhouette(
    val closed: Path,
    val topEdge: Path,
    val leftX: Float,
    val rightX: Float,
)

private fun buildCloudSilhouette(
    run: List<CloudRunPoint>,
    left: Float,
    right: Float,
    columnSpan: Float,
): CloudSilhouette {
    val taper = min(columnSpan * 0.45f, (right - left) * 0.06f)
    val first = run.first()
    val last = run.last()
    val leftX = max(left, first.cx - taper)
    val rightX = min(right, last.cx + taper)
    val leftMidY = (first.topY + first.bottomY) * 0.5f
    val rightMidY = (last.topY + last.bottomY) * 0.5f

    val topPts = buildList {
        add(Offset(leftX, leftMidY))
        run.forEach { add(Offset(it.cx, it.topY)) }
        add(Offset(rightX, rightMidY))
    }
    val botPts = buildList {
        add(Offset(rightX, rightMidY))
        run.asReversed().forEach { add(Offset(it.cx, it.bottomY)) }
        add(Offset(leftX, leftMidY))
    }
    val closed = Path().apply {
        moveTo(topPts[0].x, topPts[0].y)
        appendSmoothSegment(topPts)
        appendSmoothSegment(botPts)
        close()
    }
    val topEdge = smoothOpenCurvePath(topPts)
    return CloudSilhouette(closed = closed, topEdge = topEdge, leftX = leftX, rightX = rightX)
}

@Composable
private fun buildTakeaway(section: JSONObject, data: CrossSectionData): CrossSectionTakeaway {
    val azText = section.optNum("solar_azimuth_deg")?.let { "%.0f°".format(it) } ?: "—"
    val elText = section.optNum("solar_elevation_deg")?.let { "%.1f°".format(it) } ?: "—"
    val solarPrefix = stringResource(R.string.profile_takeaway_solar_fmt, azText, elText)

    val litDistances = mutableListOf<Double>()
    val litBasesM = mutableListOf<Double>()
    val litFamilies = linkedSetOf<Int>()
    var hasOccluded = false

    for (i in data.distances.indices) {
        for (family in 1..3) {
            val s = columnStratum(data, i, family) ?: continue
            if (s.status == 3) {
                litDistances.add(data.distances[i])
                litBasesM.add(s.baseASL)
                litFamilies.add(family)
            } else if (s.status == 2) {
                hasOccluded = true
            }
        }
    }

    return if (litDistances.isNotEmpty()) {
        val minDist = litDistances.min()
        val maxDist = litDistances.max()
        val minBase = litBasesM.min()
        val maxBase = litBasesM.max()
        val isEn = LocalConfiguration.current.locales[0]?.language == "en"
        val familyNames = listOf(3, 2, 1).filter { it in litFamilies }.joinToString("/") { fam ->
            if (isEn) {
                when (fam) {
                    1 -> "High"
                    2 -> "Mid"
                    else -> "Low"
                }
            } else {
                when (fam) {
                    1 -> "高"
                    2 -> "中"
                    else -> "低"
                }
            }
        }
        val distText = if (abs(maxDist - minDist) < 1.0) "%.0f".format(minDist) else "%.0f–%.0f".format(minDist, maxDist)
        val baseText = if (abs(maxBase - minBase) < 150.0) "%.1f".format(minBase / 1000.0) else "%.1f–%.1f".format(minBase / 1000.0, maxBase / 1000.0)
        val detail = stringResource(R.string.profile_takeaway_lit_fmt, distText, familyNames, baseText)
        CrossSectionTakeaway("$solarPrefix · $detail", status = 3)
    } else if (hasOccluded) {
        CrossSectionTakeaway("$solarPrefix · ${stringResource(R.string.profile_takeaway_occluded)}", status = 2)
    } else {
        CrossSectionTakeaway("$solarPrefix · ${stringResource(R.string.profile_takeaway_none)}", status = 0)
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
    var showIsobars by remember(section) { mutableStateOf(false) }
    var scrubDistanceKM by remember(section) { mutableStateOf<Double?>(null) }

    val dark = LocalFireSkyDark.current
    val cool = coolAccentColor()
    val secondaryText = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
    val primaryText = MaterialTheme.colorScheme.onBackground
    val observerLabel = stringResource(R.string.profile_observer_label)
    val sunlitLineLabel = stringResource(R.string.profile_sunlit_line)
    val sunAz = section.optNum("solar_azimuth_deg")
    val sunCueText = if (sunAz != null) {
        stringResource(R.string.profile_sun_dir_fmt, sunAz)
    } else {
        stringResource(R.string.profile_sun_dir_no_az)
    }
    val textMeasurer = rememberTextMeasurer()
    val takeaway = buildTakeaway(section, data)
    val litBaseText = stringResource(R.string.profile_lit_base_fmt, 0.0)
    val litBasePrefix = litBaseText.substringBefore("0.0")
    val litBaseSuffix = litBaseText.substringAfter("0.0")
    val litBaseTextBuilder: (Double) -> String = { km -> "$litBasePrefix%.1f$litBaseSuffix".format(km) }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        // 1. 1-line 一图看懂 takeaway banner
        val bannerAccent = when (takeaway.status) {
            3 -> Color(0xFFF56B2E)
            2 -> Color(0xFFDB8F33)
            else -> secondaryText
        }
        val bannerIcon = when (takeaway.status) {
            3 -> Icons.Filled.WbTwilight
            2 -> Icons.Filled.Cloud
            else -> Icons.Filled.WbSunny
        }
        Surface(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(9.dp),
            color = bannerAccent.copy(alpha = if (takeaway.status == 0) 0.06f else if (dark) 0.14f else 0.09f),
            border = BorderStroke(0.85.dp, bannerAccent.copy(alpha = if (takeaway.status == 0) 0.18f else 0.36f)),
        ) {
            Row(
                Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Icon(bannerIcon, contentDescription = null, modifier = Modifier.size(13.dp), tint = bannerAccent)
                Text(
                    takeaway.text,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Medium,
                    color = primaryText,
                    maxLines = 2,
                    lineHeight = 14.sp,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
            }
        }

        // 2. Telemetry chips + 等压线 toggle + 缩放控制
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            TelemetryPill(stringResource(R.string.profile_tel_el), fmt(section.optNum("solar_elevation_deg"), 1) + "°")
            TelemetryPill(stringResource(R.string.profile_tel_az), fmt(section.optNum("solar_azimuth_deg"), 1) + "°")
            TelemetryPill(stringResource(R.string.profile_tel_range), fmt(section.optNum("analysis_radius_km"), 0) + " km")
            TelemetryPill(stringResource(R.string.profile_tel_time), timeLabel(section.optString("valid_time_local", "")))
        }

        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Surface(
                onClick = { showIsobars = !showIsobars },
                shape = CircleShape,
                color = if (showIsobars) cool.copy(alpha = if (dark) 0.22f else 0.14f) else secondaryText.copy(alpha = 0.08f),
                border = BorderStroke(0.85.dp, if (showIsobars) cool.copy(alpha = 0.50f) else secondaryText.copy(alpha = 0.22f)),
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
                        tint = if (showIsobars) cool else secondaryText,
                    )
                    Text(
                        stringResource(R.string.profile_isobars),
                        fontSize = 10.5.sp,
                        fontWeight = FontWeight.Medium,
                        color = if (showIsobars) cool else secondaryText,
                    )
                }
            }
            Spacer(Modifier.weight(1f))
            Text(
                "${(zoomScale * 100f).roundToInt()}%",
                fontSize = 10.5.sp,
                fontFamily = FontFamily.Monospace,
                color = secondaryText,
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

        // 3. 横向可滚动的 v3 大气剖面画布 + 点按/拖动探测十字线
        val maxDistance = max(1.0, data.distances.last())
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val baseWidth = maxWidth
            val canvasWidth = baseWidth * zoomScale
            val canvasHeight = 230.dp
            val scrollState = rememberScrollState()
            Box(Modifier.fillMaxWidth()) {
                Box(Modifier.fillMaxWidth().horizontalScroll(scrollState)) {
                    Canvas(
                        Modifier
                            .width(canvasWidth)
                            .height(canvasHeight)
                            .pointerInput(data, showIsobars, zoomScale) {
                                val leftPx = 40.dp.toPx()
                                val rightPx = size.width - (if (showIsobars) 46.dp.toPx() else 14.dp.toPx())
                                val topPx = 12.dp.toPx()
                                val bottomPx = size.height - 22.dp.toPx()
                                fun updateScrub(pos: Offset) {
                                    if (rightPx <= leftPx + 10f ||
                                        pos.x < leftPx || pos.x > rightPx ||
                                        pos.y < topPx || pos.y > bottomPx
                                    ) {
                                        scrubDistanceKM = null
                                        return
                                    }
                                    val ratio = ((pos.x - leftPx) / (rightPx - leftPx)).coerceIn(0f, 1f)
                                    scrubDistanceKM = ratio * maxDistance
                                }
                                detectTapGestures(onTap = { updateScrub(it) })
                            }
                            .pointerInput(data, showIsobars, zoomScale) {
                                val leftPx = 40.dp.toPx()
                                val rightPx = size.width - (if (showIsobars) 46.dp.toPx() else 14.dp.toPx())
                                fun scrubAtX(xPos: Float) {
                                    if (rightPx <= leftPx + 10f) return
                                    val ratio = ((xPos - leftPx) / (rightPx - leftPx)).coerceIn(0f, 1f)
                                    scrubDistanceKM = ratio * maxDistance
                                }
                                if (zoomScale <= 1.01f) {
                                    detectHorizontalDragGestures(
                                        onDragStart = { scrubAtX(it.x) },
                                        onHorizontalDrag = { change, _ ->
                                            change.consume()
                                            scrubAtX(change.position.x)
                                        },
                                    )
                                } else {
                                    detectDragGesturesAfterLongPress(
                                        onDragStart = { scrubAtX(it.x) },
                                        onDrag = { change, _ ->
                                            change.consume()
                                            scrubAtX(change.position.x)
                                        },
                                    )
                                }
                            },
                    ) {
                        drawCrossSectionV3(
                            data = data,
                            dark = dark,
                            secondaryText = secondaryText,
                            primaryText = primaryText,
                            observerLabel = observerLabel,
                            sunlitLineLabel = sunlitLineLabel,
                            sunCueText = sunCueText,
                            litBaseFormatter = litBaseTextBuilder,
                            scale = zoomScale,
                            showIsobars = showIsobars,
                            scrubDistanceKM = scrubDistanceKM,
                            textMeasurer = textMeasurer,
                        )
                    }
                }
                scrubDistanceKM?.let { dist ->
                    Box(
                        Modifier
                            .align(Alignment.TopStart)
                            .padding(start = 46.dp, top = 16.dp),
                    ) {
                        CrossSectionScrubCard(
                            data = data,
                            distanceKM = dist,
                            onDismiss = { scrubDistanceKM = null },
                        )
                    }
                }
            }
        }

        // 4. 紧凑单行图例
        CrossSectionLegendV3()

        // 5. 单行云属代理条
        if (genusProxy != null) {
            CompactGenusSummaryStrip(genusProxy)
        }

        // 6. 理论候选条带
        TargetStrip(data.targets)
    }
}

@Composable
private fun TelemetryPill(label: String, value: String) {
    val dark = LocalFireSkyDark.current
    val secondary = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.60f)
    Surface(
        shape = CircleShape,
        color = MaterialTheme.colorScheme.onBackground.copy(alpha = if (dark) 0.08f else 0.05f),
        border = BorderStroke(0.65.dp, MaterialTheme.colorScheme.onBackground.copy(alpha = 0.14f)),
    ) {
        Row(
            Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(label, fontSize = 9.5.sp, color = secondary)
            Text(
                value,
                fontSize = 10.sp,
                fontWeight = FontWeight.Medium,
                style = TextStyle(fontFeatureSettings = "tnum"),
            )
        }
    }
}

@Composable
private fun CrossSectionScrubCard(
    data: CrossSectionData,
    distanceKM: Double,
    onDismiss: () -> Unit,
) {
    val nearestIndex = data.distances.indices.minByOrNull { abs(data.distances[it] - distanceKM) } ?: return
    val sampleDist = data.distances[nearestIndex]
    val terrainText = data.terrain.getOrNull(nearestIndex)?.let { "%.0f m".format(it) } ?: "—"
    val reqText = data.requiredASL.getOrNull(nearestIndex)?.let { "%.1f km".format(it / 1000.0) } ?: "—"
    val presentStrata = listOf(1, 2, 3).mapNotNull { fam ->
        columnStratum(data, nearestIndex, fam)?.let { fam to it }
    }
    val dark = LocalFireSkyDark.current
    val tnum = TextStyle(fontFeatureSettings = "tnum")
    val isEn = LocalConfiguration.current.locales[0]?.language == "en"

    Surface(
        onClick = onDismiss,
        shape = RoundedCornerShape(9.dp),
        color = if (dark) Color(0xFF141C2B).copy(alpha = 0.92f) else Color.White.copy(alpha = 0.94f),
        border = BorderStroke(0.75.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.55f)),
    ) {
        Column(Modifier.padding(horizontal = 9.dp, vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                Text("%.0f km".format(sampleDist), fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold, style = tnum)
                Text(
                    stringResource(R.string.profile_scrub_terrain_fmt, terrainText),
                    fontSize = 9.5.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    style = tnum,
                )
                Text(
                    stringResource(R.string.profile_scrub_req_fmt, reqText),
                    fontSize = 9.5.sp,
                    color = Color(0xFFE69433),
                    style = tnum,
                )
                Icon(
                    Icons.Filled.Close,
                    contentDescription = stringResource(R.string.dismiss),
                    modifier = Modifier.size(11.dp),
                    tint = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f),
                )
            }
            if (presentStrata.isEmpty()) {
                Text(
                    stringResource(R.string.profile_scrub_empty),
                    fontSize = 9.5.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.60f),
                )
            } else {
                for ((fam, stratum) in presentStrata) {
                    val famName = if (isEn) {
                        when (fam) {
                            1 -> "High"
                            2 -> "Mid"
                            else -> "Low"
                        }
                    } else {
                        when (fam) {
                            1 -> "高"
                            2 -> "中"
                            else -> "低"
                        }
                    }
                    val statusText = when (stratum.status) {
                        3 -> stringResource(R.string.profile_status_lit)
                        2 -> stringResource(R.string.profile_status_occluded)
                        else -> stringResource(R.string.profile_status_shadow)
                    }
                    val statusColor = when (stratum.status) {
                        3 -> Color(0xFFF56B2E)
                        2 -> Color(0xFFE09433)
                        else -> MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f)
                    }
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                        Box(Modifier.size(5.5.dp).background(statusColor, CircleShape))
                        Text(
                            "$famName %.1f–%.1f km".format(stratum.baseASL / 1000.0, stratum.topASL / 1000.0),
                            fontSize = 9.5.sp,
                            fontWeight = FontWeight.Medium,
                            style = tnum,
                        )
                        Text("· $statusText", fontSize = 9.5.sp, color = statusColor)
                    }
                }
            }
        }
    }
}

private fun DrawScope.drawCrossSectionV3(
    data: CrossSectionData,
    dark: Boolean,
    secondaryText: Color,
    primaryText: Color,
    observerLabel: String,
    sunlitLineLabel: String,
    sunCueText: String,
    litBaseFormatter: (Double) -> String,
    scale: Float,
    showIsobars: Boolean,
    scrubDistanceKM: Double?,
    textMeasurer: TextMeasurer,
) {
    val left = 40.dp.toPx()
    val right = size.width - (if (showIsobars) 46.dp.toPx() else 14.dp.toPx())
    val top = 12.dp.toPx()
    val bottom = size.height - 22.dp.toPx()
    if (bottom <= top + 20f || right <= left + 20f) return

    val maxHeight = 18_000.0
    val strokeScale = scale.coerceIn(1f, 1.35f)
    val labelSp = min(12f, max(9f, 9.2f * scale)).sp
    val labelStyle = TextStyle(fontSize = labelSp, color = secondaryText, fontFamily = FontFamily.Monospace)
    val maxDistance = max(1.0, data.distances.last())
    fun x(distance: Double): Float = (left + (distance.coerceIn(0.0, maxDistance) / maxDistance) * (right - left)).toFloat()
    fun y(height: Double): Float = (top + (1.0 - height.coerceIn(0.0, maxHeight) / maxHeight) * (bottom - top)).toFloat()

    val plotCorner = 10.dp.toPx()
    val plotRect = Rect(left, top, right, bottom)
    val plotPath = Path().apply {
        addRoundRect(RoundRect(plotRect, CornerRadius(plotCorner, plotCorner)))
    }
    val hasRequired = data.requiredASL.size == data.distances.size
    val reqPoints = if (hasRequired) {
        data.distances.indices.map { Offset(x(data.distances[it]), y(data.requiredASL[it])) }
    } else emptyList()

    clipPath(plotPath) {
        // Layer 1 — Twilight sky backdrop + horizon warm glow on right
        val skyColors = if (dark) {
            listOf(Color(0xFF0F1A30), Color(0xFF1F2B45), Color(0xFF383038))
        } else {
            listOf(Color(0xFFCCE0F5), Color(0xFFE8F0FA), Color(0xFFFAEBD9))
        }
        drawPath(plotPath, Brush.verticalGradient(skyColors, startY = top, endY = bottom))
        drawPath(
            plotPath,
            Brush.linearGradient(
                colors = listOf(Color.Transparent, Color(0xFFFA9E57).copy(alpha = if (dark) 0.18f else 0.20f)),
                start = Offset(left + (right - left) * 0.38f, top + (bottom - top) * 0.25f),
                end = Offset(right, bottom),
            ),
        )

        // Layer 2 — Earth Shadow (below requiredASL) & Sunlit Corridor (above requiredASL) + subtle rays
        if (reqPoints.size > 1) {
            val shadowPath = Path().apply {
                moveTo(reqPoints[0].x, reqPoints[0].y)
                appendSmoothSegment(reqPoints)
                lineTo(right, bottom)
                lineTo(left, bottom)
                close()
            }
            val shadowColor = if (dark) Color(0xFF080F21).copy(alpha = 0.28f) else Color(0xFF3D5273).copy(alpha = 0.12f)
            drawPath(shadowPath, shadowColor)

            val sunlitPath = Path().apply {
                moveTo(left, top)
                lineTo(right, top)
                lineTo(reqPoints.last().x, reqPoints.last().y)
                appendSmoothSegment(reqPoints.asReversed())
                close()
            }
            drawPath(
                sunlitPath,
                Brush.linearGradient(
                    colors = listOf(
                        Color(0xFFFAAD4D).copy(alpha = 0.02f),
                        Color(0xFFFA993D).copy(alpha = if (dark) 0.16f else 0.15f),
                    ),
                    start = Offset(left, top),
                    end = Offset(right, bottom),
                ),
            )

            clipPath(sunlitPath) {
                val width = right - left
                val height = bottom - top
                for (idx in 0 until 4) {
                    val offsetFrac = idx * 0.19f
                    val xTop = right - width * (0.04f + offsetFrac)
                    val rayW = width * 0.065f
                    val slant = width * 0.34f
                    val ray = Path().apply {
                        moveTo(xTop, top - 4f)
                        lineTo(xTop + rayW, top - 4f)
                        lineTo(xTop + rayW - slant, bottom + 4f)
                        lineTo(xTop - slant, bottom + 4f)
                        close()
                    }
                    drawPath(
                        ray,
                        Brush.linearGradient(
                            colors = listOf(
                                Color(0xFFFFD68F).copy(alpha = 0.07f),
                                Color(0xFFFFB861).copy(alpha = 0f),
                            ),
                            start = Offset(right, top),
                            end = Offset(left, top + height * 0.85f),
                        ),
                    )
                }
            }
        }

        // Layer 3 — Altitude & dashed distance gridlines (+ optional Isobars)
        var alt = 0.0
        while (alt <= maxHeight + 1.0) {
            val ly = y(alt)
            drawLine(
                secondaryText.copy(alpha = if (alt == 0.0) 0.08f else 0.15f),
                Offset(left, ly),
                Offset(right, ly),
                strokeWidth = 0.6.dp.toPx(),
            )
            alt += 3000.0
        }
        val distStep = when {
            maxDistance <= 120.0 -> 25.0
            maxDistance <= 250.0 -> 50.0
            else -> 100.0
        }
        var d = distStep
        while (d <= maxDistance + 0.1) {
            val lx = x(d)
            drawLine(
                secondaryText.copy(alpha = 0.12f),
                Offset(lx, top),
                Offset(lx, bottom),
                strokeWidth = 0.6.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 4.dp.toPx())),
            )
            d += distStep
        }

        if (showIsobars) {
            for (l in data.levels.indices) {
                val pts = mutableListOf<Offset>()
                val temps = mutableListOf<Double>()
                for (i in data.distances.indices) {
                    val h = gridScalar(data.heights, i, l) ?: continue
                    pts.add(Offset(x(data.distances[i]), y(h)))
                    gridScalar(data.temps, i, l)?.let { temps.add(it) }
                }
                if (pts.size <= 1) continue
                val avg = if (temps.isEmpty()) 0.0 else temps.average()
                val isoColor = when {
                    avg < -30.0 -> Color(0xFF7A8FB8)
                    avg < 0.0 -> Color(0xFFAD8075)
                    else -> Color(0xFFCC6B4D)
                }
                drawPath(smoothOpenCurvePath(pts), isoColor.copy(alpha = 0.38f), style = Stroke(width = 1.dp.toPx()))
            }
        }

        // Layer 4 — Continuous organic cloud strata (low 3 -> mid 2 -> high 1)
        val sampleCount = max(1, data.distances.size - 1)
        val columnSpan = (right - left) / sampleCount.toFloat()

        for (family in listOf(3, 2, 1)) {
            val runs = mutableListOf<List<CloudRunPoint>>()
            var currentRun = mutableListOf<CloudRunPoint>()
            for (i in data.distances.indices) {
                val stratum = columnStratum(data, i, family)
                if (stratum != null) {
                    val cx = x(data.distances[i])
                    val terrainY = data.terrain.getOrNull(i)?.let { y(it) } ?: bottom
                    var botY = min(terrainY - 1f, y(stratum.baseASL))
                    var topY = max(top + 2f, y(stratum.topASL))
                    if (topY > botY - 2f) {
                        val mid = ((topY + botY) * 0.5f).coerceIn(top + 3f, bottom - 3f)
                        topY = mid - 1.5f
                        botY = mid + 1.5f
                    }
                    currentRun.add(CloudRunPoint(i, cx, topY, botY, stratum))
                } else if (currentRun.isNotEmpty()) {
                    runs.add(currentRun)
                    currentRun = mutableListOf()
                }
            }
            if (currentRun.isNotEmpty()) runs.add(currentRun)

            for (run in runs) {
                val shape = buildCloudSilhouette(run, left, right, columnSpan)
                val avgAmount = run.map { it.stratum.amount }.average()
                val alpha = min(0.86f, (0.42 + avgAmount * 0.48).toFloat())
                val (topColor, botColor) = when (family) {
                    1 -> {
                        val t = (if (dark) Color(0xFFC7E0F7) else Color(0xFFEBF5FF)).copy(alpha = alpha)
                        val b = (if (dark) Color(0xFF8AB3DE) else Color(0xFFA3C7EB)).copy(alpha = alpha * 0.92f)
                        t to b
                    }
                    2 -> {
                        val t = (if (dark) Color(0xFF8FC9D1) else Color(0xFFADDBE0)).copy(alpha = alpha)
                        val b = (if (dark) Color(0xFF5C99A6) else Color(0xFF70ABB8)).copy(alpha = alpha * 0.94f)
                        t to b
                    }
                    else -> {
                        val t = (if (dark) Color(0xFF808C9C) else Color(0xFF9EABB8)).copy(alpha = alpha)
                        val b = (if (dark) Color(0xFF546170) else Color(0xFF707D8A)).copy(alpha = alpha * 0.96f)
                        t to b
                    }
                }
                val minTopY = run.minOf { it.topY }
                val maxBotY = run.maxOf { it.bottomY }
                drawPath(
                    shape.closed,
                    Brush.verticalGradient(listOf(topColor, botColor), startY = minTopY, endY = maxBotY),
                )
                drawPath(
                    shape.topEdge,
                    Color.White.copy(alpha = if (dark) 0.25f else 0.45f),
                    style = Stroke(width = 1.dp.toPx()),
                )

                // Status 3 fire-sky underside glow & status 2 occluded dashed underside
                var subStart = 0
                while (subStart < run.size) {
                    val st = run[subStart].stratum.status
                    var subEnd = subStart
                    while (subEnd + 1 < run.size && run[subEnd + 1].stratum.status == st) {
                        subEnd += 1
                    }
                    if (st == 3 || st == 2) {
                        val subLeft = if (subStart == 0) shape.leftX else (run[subStart - 1].cx + run[subStart].cx) * 0.5f
                        val subRight = if (subEnd == run.size - 1) shape.rightX else (run[subEnd].cx + run[subEnd + 1].cx) * 0.5f
                        val subRun = run.subList(subStart, subEnd + 1)
                        val subMinTop = subRun.minOf { it.topY }
                        val subMaxBot = subRun.maxOf { it.bottomY }

                        val subBotPts = buildList {
                            if (subStart == 0) {
                                add(Offset(shape.leftX, (run[0].topY + run[0].bottomY) * 0.5f))
                            } else {
                                add(Offset(subLeft, (run[subStart - 1].bottomY + run[subStart].bottomY) * 0.5f))
                            }
                            subRun.forEach { add(Offset(it.cx, it.bottomY)) }
                            if (subEnd == run.size - 1) {
                                add(Offset(shape.rightX, (run[subEnd].topY + run[subEnd].bottomY) * 0.5f))
                            } else {
                                add(Offset(subRight, (run[subEnd].bottomY + run[subEnd + 1].bottomY) * 0.5f))
                            }
                        }
                        val subBotCurve = smoothOpenCurvePath(subBotPts)

                        clipPath(shape.closed) {
                            val bandW = max(1f, subRight - subLeft)
                            if (st == 3) {
                                drawRect(
                                    brush = Brush.verticalGradient(
                                        0.0f to Color.Transparent,
                                        0.55f to Color(0xFFFF9E44).copy(alpha = 0.42f),
                                        1.0f to Color(0xFFF55D28).copy(alpha = 0.86f),
                                        startY = subMinTop,
                                        endY = subMaxBot,
                                    ),
                                    topLeft = Offset(subLeft, top),
                                    size = Size(bandW, bottom - top),
                                )
                            } else {
                                drawRect(
                                    color = Color(0xFFE69938).copy(alpha = 0.16f),
                                    topLeft = Offset(subLeft, top),
                                    size = Size(bandW, bottom - top),
                                )
                            }
                        }

                        if (st == 3) {
                            drawPath(
                                subBotCurve,
                                Color(0xFFFA732E).copy(alpha = 0.30f),
                                style = Stroke(width = 4.5.dp.toPx() * strokeScale, cap = StrokeCap.Round, join = StrokeJoin.Round),
                            )
                            drawPath(
                                subBotCurve,
                                Color(0xFFFF8A33),
                                style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round),
                            )
                        } else {
                            drawPath(
                                subBotCurve,
                                Color(0xFFE09433).copy(alpha = 0.88f),
                                style = Stroke(
                                    width = 1.2.dp.toPx(),
                                    cap = StrokeCap.Round,
                                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx())),
                                ),
                            )
                        }
                    }
                    subStart = subEnd + 1
                }
            }
        }

        // Layer 5 — Smooth requiredASL illumination threshold curve + inline pill
        if (reqPoints.size > 1) {
            val reqCurve = smoothOpenCurvePath(reqPoints)
            drawPath(reqCurve, Color(0xFFF5A83D).copy(alpha = 0.20f), style = Stroke(width = 4.8.dp.toPx()))
            drawPath(
                reqCurve,
                Color(0xFFEB9630),
                style = Stroke(
                    width = 1.8.dp.toPx(),
                    cap = StrokeCap.Round,
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(7.dp.toPx(), 4.dp.toPx())),
                ),
            )
            val pillIdx = ((reqPoints.size - 1) * 0.72).roundToInt().coerceIn(0, reqPoints.lastIndex)
            val anchorPt = reqPoints[pillIdx]
            val pillMeasured = textMeasurer.measure(
                sunlitLineLabel,
                TextStyle(fontSize = 8.5.sp, fontWeight = FontWeight.SemiBold, color = Color(0xFFF5B857)),
            )
            val pillW = pillMeasured.size.width + 10.dp.toPx()
            val pillH = pillMeasured.size.height + 4.dp.toPx()
            val pillX = (anchorPt.x - pillW * 0.5f).coerceIn(left + 6.dp.toPx(), max(left + 6.dp.toPx(), right - pillW - 6.dp.toPx()))
            val pillY = (anchorPt.y - pillH - 6.dp.toPx()).coerceIn(top + 6.dp.toPx(), max(top + 6.dp.toPx(), bottom - pillH - 6.dp.toPx()))
            val pillRect = Rect(pillX, pillY, pillX + pillW, pillY + pillH)
            val pillShape = Path().apply {
                addRoundRect(RoundRect(pillRect, CornerRadius(pillH * 0.5f, pillH * 0.5f)))
            }
            drawPath(pillShape, (if (dark) Color.Black else Color.White).copy(alpha = 0.55f))
            drawPath(pillShape, Color(0xFFEB9630).copy(alpha = 0.45f), style = Stroke(width = 0.7.dp.toPx()))
            drawText(pillMeasured, topLeft = Offset(pillX + 5.dp.toPx(), pillY + 2.dp.toPx()))
        }

        // Layer 6 — Smooth mountain/terrain silhouette
        if (data.terrain.size == data.distances.size && data.terrain.isNotEmpty()) {
            val terrainPts = data.distances.indices.map { Offset(x(data.distances[it]), y(data.terrain[it])) }
            val fillPath = Path().apply {
                moveTo(terrainPts[0].x, terrainPts[0].y)
                appendSmoothSegment(terrainPts)
                lineTo(right, bottom)
                lineTo(left, bottom)
                close()
            }
            val terrainMinY = terrainPts.minOf { it.y }
            val terrainColors = if (dark) {
                listOf(Color(0xFF57666B).copy(alpha = 0.76f), Color(0xFF262E38).copy(alpha = 0.92f))
            } else {
                listOf(Color(0xFF707D75).copy(alpha = 0.48f), Color(0xFF475452).copy(alpha = 0.68f))
            }
            drawPath(fillPath, Brush.verticalGradient(terrainColors, startY = terrainMinY, endY = bottom))
            drawPath(smoothOpenCurvePath(terrainPts), Color(0xFF73827D), style = Stroke(width = 1.6.dp.toPx()))
        }

        // Layer 7 — Observer beacon, right-edge sun cue, & sightline to primary burnable target
        val observerY = if (data.terrain.isEmpty()) bottom else y(data.terrain[0])
        val observer = Offset(x(0.0), observerY.coerceIn(top + 10.dp.toPx(), bottom - 5.dp.toPx()))

        primaryBurnableFocus(data)?.let { focus ->
            val targetPt = Offset(x(focus.distanceKM), y(focus.baseASL))
            drawLine(
                Color(0xFFFC9E47).copy(alpha = 0.65f),
                observer,
                targetPt,
                strokeWidth = 1.3.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(5.dp.toPx(), 4.dp.toPx())),
            )
            drawCircle(Color(0xFFFA732E).copy(alpha = 0.28f), radius = 8.dp.toPx(), center = targetPt)
            drawCircle(Color(0xFFFF8F38), radius = 4.2.dp.toPx(), center = targetPt)
            drawCircle(Color.White.copy(alpha = 0.95f), radius = 4.2.dp.toPx(), center = targetPt, style = Stroke(width = 1.3.dp.toPx()))

            val baseLabel = litBaseFormatter(focus.baseASL / 1000.0)
            val tMeasured = textMeasurer.measure(
                baseLabel,
                TextStyle(fontSize = 8.5.sp, fontWeight = FontWeight.SemiBold, color = Color.White),
            )
            val tW = tMeasured.size.width + 10.dp.toPx()
            val tH = tMeasured.size.height + 4.dp.toPx()
            val tX = (targetPt.x - tW * 0.5f).coerceIn(left + 6.dp.toPx(), max(left + 6.dp.toPx(), right - tW - 6.dp.toPx()))
            val tY = if (targetPt.y - tH - 10.dp.toPx() >= top + 6.dp.toPx()) {
                targetPt.y - tH - 10.dp.toPx()
            } else {
                min(bottom - tH - 6.dp.toPx(), targetPt.y + 10.dp.toPx())
            }
            val tRect = Rect(tX, tY, tX + tW, tY + tH)
            val tShape = Path().apply {
                addRoundRect(RoundRect(tRect, CornerRadius(tH * 0.5f, tH * 0.5f)))
            }
            drawPath(tShape, Color(0xFFEB5C29).copy(alpha = 0.90f))
            drawText(tMeasured, topLeft = Offset(tX + 5.dp.toPx(), tY + 2.dp.toPx()))
        }

        drawCircle(Color(0xFFF57A3D).copy(alpha = 0.28f), radius = 8.dp.toPx(), center = observer)
        drawCircle(Color(0xFFF57A3D), radius = 4.2.dp.toPx(), center = observer)
        drawCircle(Color.White.copy(alpha = 0.92f), radius = 4.2.dp.toPx(), center = observer, style = Stroke(width = 1.3.dp.toPx()))
        val obsMeasured = textMeasurer.measure(
            observerLabel,
            TextStyle(fontSize = labelSp, fontWeight = FontWeight.SemiBold, color = primaryText),
        )
        drawText(
            obsMeasured,
            topLeft = Offset(
                observer.x + 9.dp.toPx(),
                (observer.y - obsMeasured.size.height - 4.dp.toPx()).coerceAtLeast(top + 4.dp.toPx()),
            ),
        )

        val sunMeasured = textMeasurer.measure(
            sunCueText,
            TextStyle(fontSize = 9.5.sp, fontWeight = FontWeight.SemiBold, color = Color(0xFFFAB857)),
        )
        drawText(
            sunMeasured,
            topLeft = Offset(right - sunMeasured.size.width - 8.dp.toPx(), top + 6.dp.toPx()),
        )

        // Layer 8 — Vertical scrub crosshair
        if (scrubDistanceKM != null) {
            val hx = x(scrubDistanceKM)
            drawLine(
                primaryText.copy(alpha = 0.45f),
                Offset(hx, top),
                Offset(hx, bottom),
                strokeWidth = 1.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 3.dp.toPx())),
            )
        }
    }

    // Crisp rounded plot border
    drawPath(
        plotPath,
        secondaryText.copy(alpha = if (dark) 0.28f else 0.22f),
        style = Stroke(width = 0.9.dp.toPx()),
    )

    // Outside axis labels: Altitude (left)
    var altLabel = 0.0
    while (altLabel <= maxHeight + 1.0) {
        val ly = y(altLabel)
        val measured = textMeasurer.measure("${(altLabel / 1000.0).roundToInt()}km", labelStyle)
        drawText(
            measured,
            topLeft = Offset((left - measured.size.width - 5.dp.toPx()).coerceAtLeast(0f), ly - measured.size.height / 2f),
        )
        altLabel += 3000.0
    }

    // Outside axis labels: Distance (bottom, skip 0km)
    val distLabelStep = when {
        maxDistance <= 120.0 -> 25.0
        maxDistance <= 250.0 -> 50.0
        else -> 100.0
    }
    var dLabel = distLabelStep
    while (dLabel <= maxDistance + 0.1) {
        val lx = x(dLabel)
        val measured = textMeasurer.measure("${dLabel.roundToInt()}km", labelStyle)
        val textX = if (dLabel >= maxDistance - distLabelStep * 0.25) {
            (lx - measured.size.width).coerceAtLeast(left)
        } else {
            (lx - measured.size.width / 2f).coerceIn(left, right - measured.size.width)
        }
        drawText(measured, topLeft = Offset(textX, bottom + 4.dp.toPx()))
        dLabel += distLabelStep
    }

    // Outside axis labels: Isobars (right margin when ON)
    if (showIsobars) {
        val isoStyle = labelStyle.copy(fontSize = max(8.5f, labelSp.value - 0.5f).sp)
        var lastLabelY = Float.NEGATIVE_INFINITY
        val minSpacing = 13.dp.toPx()
        for (l in data.levels.indices) {
            val lastH = data.distances.indices.mapNotNull { gridScalar(data.heights, it, l) }.lastOrNull() ?: continue
            val lineY = y(lastH)
            if (lineY < top + 4.dp.toPx() || lineY > bottom - 4.dp.toPx()) continue
            if (abs(lineY - lastLabelY) < minSpacing) continue
            lastLabelY = lineY
            val measured = textMeasurer.measure("${data.levels[l]}hPa", isoStyle)
            drawText(measured, topLeft = Offset(right + 5.dp.toPx(), lineY - measured.size.height / 2f))
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CrossSectionLegendV3() {
    val secondary = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f)
    FlowRow(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(
                Modifier
                    .size(width = 13.dp, height = 8.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(Brush.verticalGradient(listOf(Color(0xFFB8D6F0), Color(0xFFF55D28)))),
            )
            Text(stringResource(R.string.legend_v3_fire_base), fontSize = 9.5.sp, color = secondary)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(3.dp)) {
            Box(Modifier.size(width = 6.dp, height = 7.dp).background(Color(0xFFB8D9F5), CircleShape))
            Box(Modifier.size(width = 6.dp, height = 7.dp).background(Color(0xFF7ABDC7), CircleShape))
            Box(Modifier.size(width = 6.dp, height = 7.dp).background(Color(0xFF758291), CircleShape))
            Text(stringResource(R.string.legend_v3_cloud_strata), fontSize = 9.5.sp, color = secondary)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(
                Modifier
                    .size(width = 13.dp, height = 8.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(Brush.verticalGradient(listOf(Color(0xFFFAA642).copy(alpha = 0.40f), Color(0xFF1F2E4D).copy(alpha = 0.48f)))),
            )
            Text(stringResource(R.string.legend_v3_corridor), fontSize = 9.5.sp, color = secondary)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(Modifier.size(width = 13.dp, height = 2.dp).background(Color(0xFFEB9630)))
            Text(stringResource(R.string.legend_v3_sunlit_line), fontSize = 9.5.sp, color = secondary)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(Modifier.size(width = 13.dp, height = 2.dp).background(Color(0xFFE09433).copy(alpha = 0.85f)))
            Text(stringResource(R.string.legend_v3_occluded), fontSize = 9.5.sp, color = secondary)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(Modifier.size(width = 13.dp, height = 5.dp).background(Color(0xFF697573).copy(alpha = 0.72f)))
            Text(stringResource(R.string.legend_v3_terrain_sight), fontSize = 9.5.sp, color = secondary)
        }
    }
}

@Composable
private fun CompactGenusSummaryStrip(proxy: JSONObject) {
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
    val confText = proxy.optNum("confidence")?.takeIf { it.isFinite() }?.let { " ${(it * 100).roundToInt()}%" } ?: ""
    val primaryText = when {
        label != "unknown" && label.isNotEmpty() -> "${genusDisplayName(label)}$confText"
        compatibleNames.isNotEmpty() -> stringResource(R.string.cloud_genus_compatible_line_fmt, compatibleNames)
        else -> stringResource(R.string.cloud_genus_undetermined)
    }

    Surface(
        Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(7.dp),
        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.055f),
    ) {
        Row(
            Modifier.padding(horizontal = 9.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Icon(Icons.Filled.Cloud, contentDescription = null, modifier = Modifier.size(13.dp), tint = coolAccentColor())
            Text(stringResource(R.string.genus_proxy_label), fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
            Text(primaryText, fontSize = 10.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
            if (candidatesText.isNotEmpty()) {
                Text(
                    "· ${stringResource(R.string.cloud_genus_candidates_fmt, candidatesText)}",
                    fontSize = 9.5.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
            }
        }
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
        if (!isStepValid(step)) continue
        val quality = step.optNum("instant_quality_index") ?: continue
        val time = parseLocalTime(step.optString("valid_time_local")) ?: continue
        points.add(time.toEpochSecond(java.time.ZoneOffset.UTC) / 60 to quality)
    }
    return points.sortedBy { it.first }
}
