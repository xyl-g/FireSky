package com.firesky.mobile.ui

import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import com.firesky.mobile.R
import org.json.JSONObject
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.roundToInt

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
        MetricSpec("cloud_layer", R.string.metric_cloud_layer, R.string.metric_cloud_layer_caption),
    ),
    R.string.metric_group_optics to listOf(
        MetricSpec("aod", R.string.metric_aod, R.string.metric_aod_caption),
        MetricSpec("incoming", R.string.metric_incoming, R.string.metric_incoming_caption),
        MetricSpec("contrast", R.string.metric_contrast, R.string.metric_contrast_caption),
    ),
    R.string.metric_group_reliability to listOf(
        MetricSpec("duration", R.string.metric_duration, R.string.metric_duration_caption),
        MetricSpec("confidence", R.string.metric_confidence, R.string.metric_confidence_caption),
        MetricSpec("convective", R.string.metric_convective, R.string.metric_convective_caption),
    ),
)

/** 主要云层区间（km AGL，缺省 "—"）。 */
fun cloudLayerMetricValue(report: JSONObject?): String {
    if (report == null) return "—"
    val base = report.optNum("dominant_burnable_cloud_base_agl_mean_m") ?: return "—"
    val top = report.optNum("dominant_burnable_cloud_top_agl_mean_m") ?: return "—"
    if (top < base) return "—"
    return "%.1f–%.1f km".format(base / 1000.0, top / 1000.0)
}

/** 指标取值映射（同 Workbench.swift metricValue）。 */
fun metricValue(key: String, report: JSONObject?): String {
    if (report == null) return "--"
    val components = report.optJSONObject("quality_components") ?: JSONObject()
    return when (key) {
        "aod" -> fmt(components.optNum("quality_aod550") ?: report.optNum("quality_aod550"))
        "cloud_layer" -> cloudLayerMetricValue(report)
        "duration" -> fmt(report.optNum("duration_minutes") ?: components.optNum("duration_minutes"), 0)
        "confidence" -> percent(report.optNum("confidence_score"))
        "convective" -> fmt(report.optNum("convective_risk"))
        "disagreement" -> fmt(report.optNum("model_disagreement_score"))
        "burnable" -> percent(
            components.optNum("burnable_cloud_coverage_effective")
                ?: report.optNum("burnable_cloud_coverage_effective"),
        )
        "material" -> percent(components.optNum("cloud_material"))
        "brightness" -> percent(components.optNum("brightness_color"))
        "incoming" -> fmt(components.optNum("incoming_aod_illumination_factor"))
        "contrast" -> fmt(components.optNum("observer_aod_contrast_factor"))
        else -> "--"
    }
}

/** 起报周期标签（e.g. "2026-11-12 06:00Z"）。 */
fun cycleLabel(model: String, report: JSONObject?): String? {
    val cycles = report?.optJSONObject("model_cycles")
    val value = cycles?.optString(model, "")?.ifEmpty { null }
        ?: report?.optString("cycle_time_utc", "")?.ifEmpty { null }
        ?: return null
    if (value.length < 16) return null
    val text = value.substring(0, 16).replace("T", " ")
    return if (value.contains("+00:00") || value.endsWith("Z")) "${text}Z" else text
}

/** 云属代码 → 中文名（供非 Composable 场景与单元测试使用）。 */
fun genusChineseName(value: String): String = when (value) {
    "cirrus" -> "卷云"
    "cirrocumulus" -> "卷积云"
    "cirrostratus" -> "卷层云"
    "altocumulus" -> "高积云"
    "altostratus" -> "高层云"
    "nimbostratus" -> "雨层云"
    "stratocumulus" -> "层积云"
    "stratus" -> "层云"
    "cumulus" -> "积云"
    "cumulonimbus" -> "积雨云"
    else -> value.replace("_", " ")
}

/** 解析后的云属代理结构（纯函数，便于单测验证）。 */
data class CloudGenusBrief(
    val genusKey: String,
    val confidencePercent: Int?,
    val isCompatibleFallback: Boolean,
)

fun parseCloudGenusBrief(proxy: JSONObject?): CloudGenusBrief? {
    if (proxy == null) return null
    val label = proxy.optString("label", "unknown")
    val conf = proxy.optNum("confidence")?.takeIf { it.isFinite() }?.let { (it * 100.0).roundToInt() }
    if (label != "unknown" && label.isNotEmpty()) {
        return CloudGenusBrief(label, conf, isCompatibleFallback = false)
    }
    val compatible = proxy.optJSONArray("compatible_genera") ?: return null
    for (i in 0 until compatible.length()) {
        val first = compatible.optString(i)
        if (first.isNotEmpty()) {
            return CloudGenusBrief(first, confidencePercent = null, isCompatibleFallback = true)
        }
    }
    return null
}

/** 非 UI 场景下的中文云属代理摘要（同 macOS `cloudGenusBrief`）。 */
fun cloudGenusRawBrief(proxy: JSONObject?): String? {
    val parsed = parseCloudGenusBrief(proxy) ?: return null
    val name = genusChineseName(parsed.genusKey)
    return if (parsed.isCompatibleFallback) {
        "${name}等（代理）"
    } else if (parsed.confidencePercent != null) {
        "$name（代理 ${parsed.confidencePercent}%）"
    } else {
        "$name（代理）"
    }
}

@Composable
fun genusDisplayName(raw: String): String = when (raw) {
    "cirrus" -> stringResource(R.string.genus_cirrus)
    "cirrocumulus" -> stringResource(R.string.genus_cirrocumulus)
    "cirrostratus" -> stringResource(R.string.genus_cirrostratus)
    "altocumulus" -> stringResource(R.string.genus_altocumulus)
    "altostratus" -> stringResource(R.string.genus_altostratus)
    "nimbostratus" -> stringResource(R.string.genus_nimbostratus)
    "stratocumulus" -> stringResource(R.string.genus_stratocumulus)
    "stratus" -> stringResource(R.string.genus_stratus)
    "cumulus" -> stringResource(R.string.genus_cumulus)
    "cumulonimbus" -> stringResource(R.string.genus_cumulonimbus)
    else -> raw.replace("_", " ")
}

@Composable
fun cloudGenusText(proxy: JSONObject?): String? {
    val parsed = parseCloudGenusBrief(proxy) ?: return null
    val name = genusDisplayName(parsed.genusKey)
    return when {
        parsed.isCompatibleFallback -> stringResource(R.string.cloud_genus_compatible_fmt, name)
        parsed.confidencePercent != null -> stringResource(R.string.cloud_genus_proxy_fmt, name, parsed.confidencePercent)
        else -> stringResource(R.string.cloud_genus_proxy_no_conf_fmt, name)
    }
}

@Composable
fun familyDisplayName(value: String?): String = when (value) {
    "high" -> stringResource(R.string.family_high)
    "middle" -> stringResource(R.string.family_middle)
    "low" -> stringResource(R.string.family_low)
    else -> stringResource(R.string.family_none)
}

@Composable
fun phaseDisplayName(value: String?): String = when (value) {
    "ice" -> stringResource(R.string.phase_ice)
    "water" -> stringResource(R.string.phase_water)
    "mixed" -> stringResource(R.string.phase_mixed)
    else -> stringResource(R.string.phase_unknown)
}

@Composable
fun modelSourceNote(model: String): String =
    if (model == MODEL_EC) stringResource(R.string.model_source_ec)
    else stringResource(R.string.model_source_gfs)

/** F3：引擎内部状态码与协议级错误映射，抑制内部码（如 native-nowcast-map-publication-not-requested）。 */
fun friendlyEngineMessage(message: String): String {
    val text = message.trim()
    if (text.isEmpty() || text == "native-nowcast-map-publication-not-requested") return ""
    if (text.startsWith("another data sync round is active in another process")) {
        return "另一轮数据同步正在进行，本轮已让路；稍后会自动重试。"
    }
    return when (text) {
        "another prepare/forecast is active" -> "已有预测准备或计算正在进行，请等待完成或先取消，再发起本次操作。"
        "data sync skipped because another prepare/forecast is active" -> "预测准备或计算正在进行，本轮同步已让路；待当前操作完成后再同步。"
        "another data sync is active" -> "已有一轮数据同步正在进行，请稍候再试。"
        "network probe is already active" -> "网络探测正在进行，请稍候再试。"
        "desktop operation cancelled", "nowcast preparation cancelled", "nowcast operation cancelled" -> "操作已取消。"
        "data cycle is unverified or older than the latest release; explicit cycle-age confirmation is required" ->
            "数据周期未核验或不是最新发布，请在确认提示中选择「使用当前缓存继续」，或先更新数据。"
        "explicit confirmation is required before forecast" -> "请先在预检窗口确认后再开始预测。"
        "inputs are missing; prepare data before forecasting" -> "本次预测数据不完整，请先点击「下载缺失数据」补齐。"
        "strict input integrity check failed" -> "数据完整性校验未通过，请重新下载本次数据后再试。"
        "data sync is not enabled" -> "后台数据同步未启用，请先在设置中开启。"
        "native-report-has-no-map-cells" -> "本次结果未生成地图格网（单点报告不含区域地图）。"
        else -> {
            if (text.startsWith("data sync source ") && text.endsWith(" is not enabled in settings")) {
                val name = text.removePrefix("data sync source ").removeSuffix(" is not enabled in settings")
                "数据源 ${name.uppercase()} 未在设置中启用，请先勾选后再同步。"
            } else {
                message
            }
        }
    }
}

/** AOD 汇总（同 ForecastComparison.AODSummary 的最小闭包：取两模型 aerosol.aod550）。 */
data class AodSummary(val values: List<Double>, val sources: List<String>, val detail: String) {
    val available: Boolean get() = values.isNotEmpty()
    val sourceKind: String
        get() = when {
            sources.isEmpty() || sources.all { it == "CAMS" } -> "cams"
            sources.all { it == "GEOS-FP" } -> "geos-fp"
            sources.all { it != "CAMS" && it != "GEOS-FP" } -> "other"
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
        val quality = aerosol.optString("quality", "").lowercase()
        val label = when {
            rawSource.lowercase().startsWith("geos") || quality == "geos-fp" -> "GEOS-FP"
            rawSource.lowercase().startsWith("cams") || quality == "cams" -> "CAMS"
            else -> "其他来源"
        }
        values.add(number)
        sources.add(label)
        details.add("${report.optString("anchor_local", "时刻未提供")} · %.4f".format(number) + " · $rawSource")
    }
    return AodSummary(values, sources, details.distinct().sorted().joinToString("\n").ifEmpty { "等待气溶胶环境读取" })
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
    if (error.isNullOrEmpty()) return null
    val friendly = friendlyEngineMessage(error)
    return friendly.ifEmpty { null }
}
