package com.firesky.mobile.ui

import androidx.annotation.StringRes
import com.firesky.mobile.R
import org.json.JSONObject
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * 结果展示格式化 —— 移植 Workbench.swift 的 format/percent/timeLabel/windowLabel/metricValue。
 * 输入为桌面 JSON-lines 协议的 report 对象（键名零改动）。
 */

fun JSONObject?.optNum(key: String): Double? =
    this?.optDouble(key)?.takeUnless { it.isNaN() }

fun JSONObject?.optNumAt(path: String, key: String): Double? =
    this?.optJSONObject(path)?.optNum(key)

/** 数值格式化（同 Swift format(_:digits:)，缺省 "—"）。 */
fun fmt(value: Double?, digits: Int = 2): String =
    value?.let { "%.${digits}f".format(it) } ?: "—"

/** 百分数（同 Swift percent(_:)，缺省 "--"）。 */
fun percent(value: Double?): String =
    value?.let { "%.0f%%".format(it * 100) } ?: "--"

/** ISO 本地时间 → "HH:mm"（同 Swift timeLabel：取第 11–15 位）。 */
fun timeLabel(value: String?): String =
    value?.takeIf { it.length >= 16 }?.substring(11, 16) ?: "—"

/** 推荐窗口（timing_window.start_time_local – end_time_local）。 */
fun windowLabel(report: JSONObject?): String {
    val window = report?.optJSONObject("timing_window") ?: return "--"
    return "${timeLabel(window.optString("start_time_local"))} – ${timeLabel(window.optString("end_time_local"))}"
}

/** 解析本地 ISO 时间（yyyy-MM-ddTHH:mm:ss）供曲线 x 轴使用。 */
fun parseLocalTime(value: String?): LocalDateTime? = runCatching {
    LocalDateTime.parse(value, DateTimeFormatter.ISO_LOCAL_DATE_TIME)
}.getOrNull()

/** 模型展示色标记：ec → teal，gfs → warm。 */
const val MODEL_GFS = "gfs"
const val MODEL_EC = "ec"
val COMPARISON_MODELS = listOf(MODEL_GFS, MODEL_EC)

/** 指标卡规格（同 Workbench.swift metricSpec：key/标题/说明）。
 *  标题与说明为字符串资源引用（v1.9.5 i18n），显示处 stringResource 解析。 */
data class MetricSpec(val key: String, @StringRes val titleRes: Int, @StringRes val captionRes: Int)

val METRIC_GROUPS: List<Pair<Int, List<MetricSpec>>> = listOf(
    R.string.metric_group_cloud to listOf(
        MetricSpec("burnable", R.string.metric_burnable, R.string.metric_burnable_caption),
        MetricSpec("material", R.string.metric_material, R.string.metric_material_caption),
        MetricSpec("brightness", R.string.metric_brightness, R.string.metric_brightness_caption),
    ),
    R.string.metric_group_optics to listOf(
        MetricSpec("aod", R.string.metric_aod, R.string.metric_aod_caption),
        MetricSpec("incoming", R.string.metric_incoming, R.string.metric_incoming_caption),
        MetricSpec("contrast", R.string.metric_contrast, R.string.metric_contrast_caption),
    ),
    R.string.metric_group_reliability to listOf(
        MetricSpec("duration", R.string.metric_duration, R.string.metric_duration_caption),
        MetricSpec("confidence", R.string.metric_confidence, R.string.metric_confidence_caption),
        MetricSpec("disagreement", R.string.metric_disagreement, R.string.metric_disagreement_caption),
    ),
)

/** 指标取值映射（同 Workbench.swift metricValue）。 */
fun metricValue(key: String, report: JSONObject?): String {
    if (report == null) return "--"
    val components = report.optJSONObject("quality_components") ?: JSONObject()
    return when (key) {
        "aod" -> fmt(components.optNum("quality_aod550") ?: report.optNum("quality_aod550"))
        "duration" -> fmt(report.optNum("duration_minutes") ?: components.optNum("duration_minutes"), 0)
        "confidence" -> percent(report.optNum("confidence_score"))
        "disagreement" -> fmt(report.optNum("model_disagreement_score"))
        "burnable" -> percent(components.optNum("burnable_cloud_coverage_effective")
            ?: report.optNum("burnable_cloud_coverage_effective"))
        "material" -> fmt(components.optNum("cloud_material"))
        "brightness" -> fmt(components.optNum("brightness_color"))
        "incoming" -> fmt(components.optNum("incoming_aod_illumination_factor"))
        "contrast" -> fmt(components.optNum("observer_aod_contrast_factor"))
        else -> "--"
    }
}

/** AOD 汇总（同 ForecastComparison.AODSummary 的最小闭包：取两模型 aerosol.aod550）。 */
data class AodSummary(val values: List<Double>, val sources: List<String>, val detail: String) {
    val available: Boolean get() = values.isNotEmpty()
    /** 来源类别：cams / other / mixed（显示处经资源映射）。 */
    val sourceKind: String
        get() = when {
            sources.isEmpty() || sources.all { it == "CAMS" } -> "cams"
            sources.all { it != "CAMS" } -> "other"
            else -> "mixed"
        }
    val value: String
        get() {
            val low = values.minOrNull() ?: return "--"
            val high = values.maxOrNull() ?: return "--"
            val first = "%.2f".format(low)
            val last = "%.2f".format(high)
            return if (first == last) first else "$first – $last"
        }
    val caption: String
        get() = if (values.toSet().size > 1) "事件时刻取值范围 · 未取平均" else "事件时刻 · 气溶胶光学厚度"
}

fun aodSummary(result: JSONObject?, modelReport: (String) -> JSONObject?): AodSummary {
    val values = mutableListOf<Double>()
    val sources = mutableListOf<String>()
    val details = mutableListOf<String>()
    for (model in COMPARISON_MODELS) {
        val report = modelReport(model) ?: continue
        val aerosol = report.optJSONObject("aerosol") ?: continue
        val number = aerosol.optNum("aod550") ?: continue
        if (!number.isFinite() || number < 0) continue
        val rawSource = aerosol.optString("source", "")
        val cams = rawSource.lowercase().startsWith("cams") || aerosol.optString("quality", "") == "cams"
        values.add(number)
        sources.add(if (cams) "CAMS" else "其他来源")
        details.add("${report.optString("anchor_local", "时刻未提供")} · %.4f".format(number) + " · $rawSource")
    }
    return AodSummary(values, sources, details.distinct().sorted().joinToString("\n").ifEmpty { "等待 CAMS 环境读取" })
}

/** 单模型报告读取（result.model_results[model]；model_errors 非空时视作未完成）。 */
fun modelReport(result: JSONObject?, model: String): JSONObject? {
    if (result == null) return null
    val error = result.optJSONObject("model_errors")?.optString(model, "")
    if (!error.isNullOrEmpty()) return null
    return result.optJSONObject("model_results")?.optJSONObject(model)
}

fun modelError(result: JSONObject?, model: String): String? {
    val error = result?.optJSONObject("model_errors")?.optString(model, "")
    return if (error.isNullOrEmpty()) null else error
}
