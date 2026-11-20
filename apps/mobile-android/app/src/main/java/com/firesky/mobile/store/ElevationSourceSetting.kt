package com.firesky.mobile.store

import android.content.SharedPreferences
import org.json.JSONObject

/**
 * 海拔来源三档设置（v1.1.1 任务书 §1 契约）：
 * auto=HTTP 优先、SRTM 兜底（默认）；http=仅 open-meteo，失败显式报错；
 * srtm=跳过 HTTP，本地时区+SRTM 瓦片（缓存优先，缺瓦片按下载开关决定）。
 *
 * 持久化走 SharedPreferences（同 appearance/ecmwfSource）；非法/缺省值一律
 * 归一为 auto。search/metadata 请求参数在此统一构造，保证所有调用点透传
 * elevation_source（引擎侧 desktop_dispatch 对非法值报 invalid elevation_source）。
 */
object ElevationSourceSetting {
    const val KEY = "elevationSource"
    const val AUTO = "auto"
    const val HTTP = "http"
    const val SRTM = "srtm"

    fun normalize(value: String?): String = when (value) {
        HTTP -> HTTP
        SRTM -> SRTM
        else -> AUTO
    }

    fun load(prefs: SharedPreferences): String = normalize(prefs.getString(KEY, null))

    /** 持久化并返回归一后的取值（UI 状态以此为准）。 */
    fun save(prefs: SharedPreferences, value: String): String {
        val normalized = normalize(value)
        prefs.edit().putString(KEY, normalized).apply()
        return normalized
    }

    fun searchParams(query: String, elevationSource: String): JSONObject =
        JSONObject()
            .put("query", query)
            .put("elevation_source", normalize(elevationSource))

    fun metadataParams(lat: Double, lon: Double, elevationSource: String): JSONObject =
        JSONObject()
            .put("lat", lat)
            .put("lon", lon)
            .put("elevation_source", normalize(elevationSource))
}