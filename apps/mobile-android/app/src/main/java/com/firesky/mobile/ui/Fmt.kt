package com.firesky.mobile.ui

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

/** 现象中文名。 */
fun phenomenonLabel(phenomenon: String): String = if (phenomenon == "dawn") "朝霞" else "晚霞"

/** 模型展示色标记：ec → teal，gfs → warm。 */
const val MODEL_GFS = "gfs"
const val MODEL_EC = "ec"
val COMPARISON_MODELS = listOf(MODEL_GFS, MODEL_EC)

/** 指标卡规格（同 Workbench.swift metricSpec：key/标题/说明）。 */
data class MetricSpec(val key: String, val title: String, val caption: String)

val METRIC_GROUPS: List<Pair<String, List<MetricSpec>>> = listOf(
    "云况与色彩" to listOf(
        MetricSpec("burnable", "有效可烧云量", "前向可烧云占视野比例"),
        MetricSpec("material", "云质厚度", "云体材质负载"),
        MetricSpec("brightness", "亮度色彩", "霞光亮度与色彩评分"),
    ),
    "大气光学" to listOf(
        MetricSpec("aod", "AOD 550", "气溶胶光学厚度 · 通透度"),
        MetricSpec("incoming", "入射受光", "太阳入射照亮云体的因子"),
        MetricSpec("contrast", "返程对比度", "观测者视线大气对比因子"),
    ),
    "时效与可靠性" to listOf(
        MetricSpec("duration", "持续时长", "核心观赏窗口 · 分钟"),
        MetricSpec("confidence", "置信度", "引擎结果置信评分"),
        MetricSpec("disagreement", "模式分歧", "多模式结论分歧度"),
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
    val source: String
        get() = when {
            sources.isEmpty() || sources.all { it == "CAMS" } -> "CAMS"
            sources.all { it != "CAMS" } -> "其他来源"
            else -> "混合来源"
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
