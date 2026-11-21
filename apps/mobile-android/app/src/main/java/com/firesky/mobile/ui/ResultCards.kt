package com.firesky.mobile.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.R
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import org.json.JSONArray
import org.json.JSONObject

/**
 * 结果卡片 —— WorkHeroView / WorkResultsView（point 作用域）同构：
 * 双模型 hero 卡 + 分析页签（概览/大气剖面/诊断与时间窗/逐时台账）。
 */
@Composable
fun ResultSection(store: WorkbenchStore, state: WorkbenchState) {
    val result = state.result
    val loc = state.location
    WorkSurface(Modifier.fillMaxWidth(), emphasized = true) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text(
                "${loc.name} · ${state.targetDate} " +
                    stringResource(
                        if (state.phenomenon == "dawn") R.string.phenomenon_dawn else R.string.phenomenon_dusk,
                    ),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            if (result == null) {
                Text(
                    stringResource(R.string.result_wait_hint),
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
                    modifier = Modifier.padding(vertical = 20.dp),
                )
            } else {
                Row(verticalAlignment = Alignment.Top) {
                    HeroSummary(result, MODEL_GFS, Modifier.weight(1f))
                    Box(
                        Modifier.width(1.dp).height(124.dp)
                            .background(MaterialTheme.colorScheme.onBackground.copy(alpha = 0.3f)),
                    )
                    HeroSummary(result, MODEL_EC, Modifier.weight(1f))
                }
            }
        }
    }
    if (result != null) {
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
                            comparisonStatus(result, state.selectedModel),
                            fontSize = 10.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                        )
                    }
                }
                when (state.resultTab) {
                    "概览" -> OverviewContent(result)
                    "大气剖面" -> ProfilePlaceholder()
                    "逐时台账" -> LedgerContent(result, state.selectedModel)
                    else -> DiagnosticContent(result, state.selectedModel)
                }
            }
        }
    }
}

@Composable
private fun HeroSummary(result: JSONObject, model: String, modifier: Modifier = Modifier) {
    val report = modelReport(result, model)
    val error = modelError(result, model)
    val color = if (model == MODEL_EC) MaterialTheme.colorScheme.secondary else MaterialTheme.colorScheme.primary
    Column(modifier.padding(horizontal = 9.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(6.dp).background(color, CircleShape))
            Spacer(Modifier.width(5.dp))
            Text(model.uppercase(), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.weight(1f))
            Text(
                comparisonStatus(result, model),
                fontSize = 9.sp,
                color = if (error == null) MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
                else MaterialTheme.colorScheme.error,
            )
        }
        Row(verticalAlignment = Alignment.Top) {
            Text(
                fmt(report.optNum("peak_quality_index") ?: report.optNum("quality_index")),
                fontSize = 30.sp,
                fontWeight = FontWeight.SemiBold,
                color = if (report == null) MaterialTheme.colorScheme.onBackground.copy(alpha = 0.4f) else color,
                maxLines = 1,
            )
            Spacer(Modifier.width(5.dp))
            Text("/ 2.5", fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        }
        Text(
            report?.optString("quality_level", "")?.ifEmpty { stringResource(R.string.quality_index_fallback) }
                ?: stringResource(R.string.quality_index_fallback),
            fontSize = 9.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        Column {
            Text(stringResource(R.string.best_time), fontSize = 9.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
            Text(
                timeLabel(report?.optString("best_time_local")),
                fontSize = 20.sp,
                fontWeight = FontWeight.SemiBold,
            )
        }
        Text(
            stringResource(R.string.recommended_window, windowLabel(report)),
            fontSize = 10.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        if (error != null) {
            Text(error, fontSize = 10.sp, color = MaterialTheme.colorScheme.error, maxLines = 2)
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
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterHorizontally)) {
        for (tab in listOf("概览", "大气剖面", "诊断与时间窗", "逐时台账")) {
            val selected = state.resultTab == tab
            Column(
                Modifier
                    .androidClickable { store.selectResultTab(tab) }
                    .padding(horizontal = 4.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    tabTitle(tab),
                    fontSize = 13.sp,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                    color = if (selected) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
                Spacer(Modifier.height(3.dp))
                Box(
                    Modifier
                        .fillMaxWidth()
                        .height(3.dp)
                        .background(
                            if (selected) MaterialTheme.colorScheme.primary else Color.Transparent,
                        ),
                )
            }
        }
    }
}

/** 概览：三组指标卡（GFS/EC 并排）+ AOD + 质量曲线。 */
@Composable
private fun OverviewContent(result: JSONObject) {
    val aod = aodSummary(result) { modelReport(result, it) }
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        for ((groupTitleRes, specs) in METRIC_GROUPS) {
            Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                Text(
                    stringResource(groupTitleRes),
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    for (spec in specs) {
                        MetricCard(spec, result, Modifier.weight(1f))
                    }
                }
            }
        }
        CurveCard(result)
    }
}

@Composable
private fun MetricCard(spec: MetricSpec, result: JSONObject, modifier: Modifier = Modifier) {
    WorkSurface(modifier) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Text(
                stringResource(spec.titleRes),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.8f),
            )
            if (spec.key == "aod") {
                val aod = aodSummary(result) { modelReport(result, it) }
                Text(
                    if (aod.sourceKind == "cams") "CAMS"
                    else stringResource(
                        if (aod.sourceKind == "mixed") R.string.aod_mixed_source else R.string.aod_other_source,
                    ),
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.secondary,
                )
                Text(
                    aod.value,
                    fontSize = 19.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = if (aod.available) MaterialTheme.colorScheme.onBackground
                    else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f),
                    maxLines = 1,
                )
            } else {
                for (model in COMPARISON_MODELS) {
                    val report = modelReport(result, model)
                    val color = if (model == MODEL_EC) MaterialTheme.colorScheme.secondary else MaterialTheme.colorScheme.primary
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(
                            model.uppercase(),
                            fontSize = 9.sp,
                            fontWeight = FontWeight.Medium,
                            color = color,
                        )
                        Text(
                            metricValue(spec.key, report),
                            fontSize = 17.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = if (report == null) MaterialTheme.colorScheme.onBackground.copy(alpha = 0.45f)
                            else MaterialTheme.colorScheme.onBackground,
                            maxLines = 1,
                        )
                    }
                }
            }
            Text(
                stringResource(spec.captionRes),
                fontSize = 9.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
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
                val color = if (model == MODEL_EC) MaterialTheme.colorScheme.secondary else MaterialTheme.colorScheme.primary
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    Box(Modifier.size(width = 14.dp, height = 2.dp).background(color))
                    Text(model.uppercase(), fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                    Spacer(Modifier.width(8.dp))
                }
            }
        }
        Box(Modifier.fillMaxWidth().height(164.dp)) {
            val warm = MaterialTheme.colorScheme.primary
            val teal = MaterialTheme.colorScheme.secondary
            val faint = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.18f)
            Canvas(Modifier.fillMaxWidth().height(164.dp)) {
                val allTimes = (gfsPoints + ecPoints).map { it.first }
                if (allTimes.isEmpty()) return@Canvas
                val minTime = allTimes.min()
                val maxTime = allTimes.max()
                val span = (maxTime - minTime).coerceAtLeast(1)
                fun y(value: Double): Float = size.height * (1f - (value / 2.5).toFloat())
                fun x(time: Long): Float = size.width * ((time - minTime).toFloat() / span)
                // 横向参考线 0 / 1.25 / 2.5
                for (level in listOf(0.0, 1.25, 2.5)) {
                    drawLine(faint, Offset(0f, y(level)), Offset(size.width, y(level)), strokeWidth = 1f)
                }
                drawSeries(gfsPoints, warm, dashed = false, ::x, ::y)
                drawSeries(ecPoints, teal, dashed = true, ::x, ::y)
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

private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawSeries(
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

/** 逐时台账（WorkLedgerView 同构）。 */
@Composable
private fun LedgerContent(result: JSONObject, model: String) {
    val report = modelReport(result, model)
    if (modelError(result, model) != null) {
        Text(
            stringResource(R.string.model_incomplete, model.uppercase(), modelError(result, model) ?: ""),
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
    Column {
        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
            Text(stringResource(R.string.col_time), Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
            Text(stringResource(R.string.col_quality), Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
            Text(stringResource(R.string.col_solar), Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.15f))
        val steps = stepsOf(report)
        steps.forEach { step ->
            Row(
                Modifier.fillMaxWidth().padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(timeLabel(step.optString("valid_time_local")), Modifier.weight(1f), fontSize = 11.sp)
                Text(fmt(step.optNum("instant_quality_index"), 3), Modifier.weight(1f), fontSize = 11.sp)
                Text(fmt(step.optNum("solar_elevation_deg")), Modifier.weight(1f), fontSize = 11.sp)
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f))
        }
        if (steps.isEmpty()) {
            Text(
                stringResource(R.string.no_ledger),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
                modifier = Modifier.padding(vertical = 24.dp),
            )
        }
    }
}

/** 诊断与时间窗（WorkDiagnosticView 同构，去掉桌面 HTML 报告入口）。 */
@Composable
private fun DiagnosticContent(result: JSONObject, model: String) {
    val report = modelReport(result, model)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.tab_diagnostic), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        if (report == null) {
            Text(
                if (modelError(result, model) != null)
                    stringResource(R.string.model_incomplete, model.uppercase(), modelError(result, model) ?: "")
                else stringResource(R.string.awaiting_forecast),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        } else {
            Text(report.optString("summary", ""), fontSize = 12.sp)
            Text(
                stringResource(R.string.cycle_line, report.optString("cycle_time_utc", "--")),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        }
    }
}

@Composable
private fun ProfilePlaceholder() {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.profile_title), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        Text(
            stringResource(R.string.profile_note),
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
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

private fun Modifier.androidClickable(onClick: () -> Unit): Modifier =
    this.clickable(onClick = onClick)
