package com.firesky.mobile.store

import org.json.JSONObject
import java.util.Locale

data class DownloadProbeRow(
    val source: String,
    val title: String,
    val state: String = "waiting",
    val detail: String = "尚未探测",
    val host: String = "",
    val bytesReceived: Long = 0,
    val elapsedMs: Long = 0,
) {
    val active: Boolean get() = state == "testing"
    val passed: Boolean get() = state == "passed"

    val measurement: String
        get() = "${formatBytes(bytesReceived)} · ${"%.1f".format(Locale.ROOT, elapsedMs / 1000.0)} 秒"

    companion object {
        private val sources = setOf("gfs", "ec", "cams")

        fun initial(aerosolSource: String = "cams"): List<DownloadProbeRow> = listOf(
            DownloadProbeRow("gfs", "GFS · NOAA NOMADS"),
            DownloadProbeRow("ec", "EC · ECMWF Open Data"),
            DownloadProbeRow(
                "cams",
                if (aerosolSource == "geos-fp") "GEOS-FP · NASA OPeNDAP" else "CAMS · Copernicus ADS",
            ),
        )

        fun fromJson(value: JSONObject?): DownloadProbeRow? {
            value ?: return null
            val source = value.optString("source")
            if (source !in sources) return null
            return DownloadProbeRow(
                source = source,
                title = value.optString("title").ifEmpty { source.uppercase(Locale.ROOT) },
                state = value.optString("state", "error"),
                detail = value.optString("detail"),
                host = value.optString("host"),
                bytesReceived = value.optLong("bytes_received", 0).coerceAtLeast(0),
                elapsedMs = value.optLong("elapsed_ms", 0).coerceAtLeast(0),
            )
        }

        private fun formatBytes(bytes: Long): String = when {
            bytes < 1024 -> "$bytes B"
            bytes < 1024 * 1024 -> "%.1f KB".format(Locale.ROOT, bytes / 1024.0)
            bytes < 1024L * 1024 * 1024 -> "%.1f MB".format(Locale.ROOT, bytes / (1024.0 * 1024))
            else -> "%.1f GB".format(Locale.ROOT, bytes / (1024.0 * 1024 * 1024))
        }
    }
}
