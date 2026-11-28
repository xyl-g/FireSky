package com.firesky.mobile.ui

import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.ApplicationInfo
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.firesky.mobile.R
import com.firesky.mobile.store.RegionalCell
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max

/**
 * 移动端交互式 Leaflet 地图（单点点按选点 + 区域热力叠加）。
 */
@SuppressLint("SetJavaScriptEnabled", "ClickableViewAccessibility")
@Composable
fun PointLocationMap(
    lat: Double,
    lon: Double,
    dark: Boolean,
    zoom: Int = 11,
    modifier: Modifier = Modifier,
    onSelectPoint: (Double, Double) -> Unit,
) {
    val currentOnSelect by rememberUpdatedState(onSelectPoint)
    val currentDark by rememberUpdatedState(dark)
    val currentLat by rememberUpdatedState(lat)
    val currentLon by rememberUpdatedState(lon)
    val currentZoom by rememberUpdatedState(zoom)
    val mainHandler = remember { Handler(Looper.getMainLooper()) }
    var pageLoaded by remember { mutableStateOf(false) }
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    var lastTheme by remember { mutableStateOf<Boolean?>(null) }
    var lastCoord by remember { mutableStateOf<Pair<Double, Double>?>(null) }

    Box(modifier) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                ensureWebViewDebugging(ctx)
                WebView(ctx).apply {
                    layoutParams = ViewGroup.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT,
                    )
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.allowFileAccess = true
                    settings.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
                    settings.cacheMode = WebSettings.LOAD_DEFAULT
                    isVerticalScrollBarEnabled = false
                    isHorizontalScrollBarEnabled = false
                    setBackgroundColor(if (currentDark) 0xFF0E1728.toInt() else 0xFFE9EFF6.toInt())

                    setOnTouchListener { v, event ->
                        when (event.actionMasked) {
                            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN, MotionEvent.ACTION_MOVE ->
                                v.parent?.requestDisallowInterceptTouchEvent(true)
                            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL ->
                                v.parent?.requestDisallowInterceptTouchEvent(false)
                        }
                        false
                    }

                    addJavascriptInterface(
                        object {
                            @JavascriptInterface
                            fun onSelectPoint(tapLat: Double, tapLon: Double) {
                                mainHandler.post { currentOnSelect(tapLat, tapLon) }
                            }
                        },
                        "AndroidMapBridge",
                    )

                    installMapDiagnostics(this)
                    webViewClient = object : MapWebViewClient(ctx) {
                        override fun onPageFinished(view: WebView, url: String?) {
                            super.onPageFinished(view, url)
                            pageLoaded = true
                            lastTheme = currentDark
                            lastCoord = currentLat to currentLon
                            view.setBackgroundColor(
                                if (currentDark) 0xFF0E1728.toInt() else 0xFFE9EFF6.toInt(),
                            )
                            view.evaluateJavascript(
                                "window.fireSkyMap && window.fireSkyMap.setTheme($currentDark)",
                                null,
                            )
                            view.evaluateJavascript(
                                "window.fireSkyMap && window.fireSkyMap.setPoint($currentLat, $currentLon, $currentZoom, true)",
                                null,
                            )
                        }
                    }
                    loadUrl(MAP_ENTRY_URL)
                    webViewRef = this
                }
            },
            update = { view ->
                webViewRef = view
                if (!pageLoaded) return@AndroidView
                if (lastTheme != dark) {
                    lastTheme = dark
                    view.setBackgroundColor(if (dark) 0xFF0E1728.toInt() else 0xFFE9EFF6.toInt())
                    view.evaluateJavascript(
                        "window.fireSkyMap && window.fireSkyMap.setTheme($dark)",
                        null,
                    )
                }
                val coord = lat to lon
                if (lastCoord != coord) {
                    val recenter = lastCoord != null
                    lastCoord = coord
                    view.evaluateJavascript(
                        "window.fireSkyMap && window.fireSkyMap.setPoint($lat, $lon, $zoom, $recenter)",
                        null,
                    )
                }
            },
            onRelease = { view ->
                if (webViewRef === view) webViewRef = null
                view.stopLoading()
                view.destroy()
            },
        )

        MapZoomPill(
            dark = dark,
            onZoomIn = { webViewRef?.evaluateJavascript("window.fireSkyMap?.zoomBy(1)", null) },
            onZoomOut = { webViewRef?.evaluateJavascript("window.fireSkyMap?.zoomBy(-1)", null) },
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(10.dp),
        )
    }
}

@SuppressLint("SetJavaScriptEnabled", "ClickableViewAccessibility")
@Composable
fun RegionalLeafletMap(
    cells: List<RegionalCell>,
    cities: List<RegionalCell>,
    bounds: List<Double>,
    selectedCell: RegionalCell?,
    dark: Boolean,
    modifier: Modifier = Modifier,
    onTapLatLon: (Double, Double) -> Unit,
) {
    val currentOnTap by rememberUpdatedState(onTapLatLon)
    val currentDark by rememberUpdatedState(dark)
    val mainHandler = remember { Handler(Looper.getMainLooper()) }
    var pageLoaded by remember { mutableStateOf(false) }
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    var lastTheme by remember { mutableStateOf<Boolean?>(null) }
    var lastPayload by remember { mutableStateOf<String?>(null) }

    val payloadJson = remember(cells, cities, bounds, selectedCell) {
        val lats = cells.map { it.lat }.distinct().sorted()
        val lons = cells.map { it.lon }.distinct().sorted()
        val latStep = lats.zipWithNext { a, b -> b - a }.filter { it > 0.05 }.minOrNull() ?: 0.25
        val lonStep = lons.zipWithNext { a, b -> b - a }.filter { it > 0.05 }.minOrNull() ?: 0.25
        val boundsArr = JSONArray().apply {
            val resolved = if (bounds.size >= 4) bounds else listOf(73.0, 18.0, 136.0, 54.0)
            resolved.take(4).forEach { put(it) }
        }
        val cellsArr = JSONArray().apply {
            cells.forEach { c ->
                put(JSONObject().apply {
                    put("lat", c.lat)
                    put("lon", c.lon)
                    if (c.quality != null) put("q", c.quality)
                })
            }
        }
        val citiesArr = JSONArray().apply {
            cities.take(15).forEach { c ->
                put(JSONObject().apply {
                    put("lat", c.lat)
                    put("lon", c.lon)
                    if (c.quality != null) put("q", c.quality)
                })
            }
        }
        JSONObject().apply {
            put("bounds", boundsArr)
            put("latStep", max(0.25, latStep))
            put("lonStep", max(0.25, lonStep))
            put("cells", cellsArr)
            put("cities", citiesArr)
            selectedCell?.let { sel ->
                put("selected", JSONObject().put("lat", sel.lat).put("lon", sel.lon))
            }
        }.toString()
    }
    val currentPayload by rememberUpdatedState(payloadJson)

    Box(modifier) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                ensureWebViewDebugging(ctx)
                WebView(ctx).apply {
                    layoutParams = ViewGroup.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT,
                    )
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.allowFileAccess = true
                    settings.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
                    settings.cacheMode = WebSettings.LOAD_DEFAULT
                    isVerticalScrollBarEnabled = false
                    isHorizontalScrollBarEnabled = false
                    setBackgroundColor(if (currentDark) 0xFF0E1728.toInt() else 0xFFE9EFF6.toInt())

                    setOnTouchListener { v, event ->
                        when (event.actionMasked) {
                            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN, MotionEvent.ACTION_MOVE ->
                                v.parent?.requestDisallowInterceptTouchEvent(true)
                            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL ->
                                v.parent?.requestDisallowInterceptTouchEvent(false)
                        }
                        false
                    }

                    addJavascriptInterface(
                        object {
                            @JavascriptInterface
                            fun onSelectRegional(tapLat: Double, tapLon: Double) {
                                mainHandler.post { currentOnTap(tapLat, tapLon) }
                            }
                        },
                        "AndroidMapBridge",
                    )

                    installMapDiagnostics(this)
                    webViewClient = object : MapWebViewClient(ctx) {
                        override fun onPageFinished(view: WebView, url: String?) {
                            super.onPageFinished(view, url)
                            pageLoaded = true
                            lastTheme = currentDark
                            lastPayload = currentPayload
                            view.setBackgroundColor(
                                if (currentDark) 0xFF0E1728.toInt() else 0xFFE9EFF6.toInt(),
                            )
                            view.evaluateJavascript(
                                "window.fireSkyMap && window.fireSkyMap.setTheme($currentDark)",
                                null,
                            )
                            view.evaluateJavascript(
                                "window.fireSkyMap && window.fireSkyMap.setRegional(${JSONObject.quote(currentPayload)})",
                                null,
                            )
                        }
                    }
                    loadUrl("$MAP_ENTRY_URL?mode=regional")
                    webViewRef = this
                }
            },
            update = { view ->
                webViewRef = view
                if (!pageLoaded) return@AndroidView
                if (lastTheme != dark) {
                    lastTheme = dark
                    view.setBackgroundColor(if (dark) 0xFF0E1728.toInt() else 0xFFE9EFF6.toInt())
                    view.evaluateJavascript(
                        "window.fireSkyMap && window.fireSkyMap.setTheme($dark)",
                        null,
                    )
                }
                if (lastPayload != payloadJson) {
                    lastPayload = payloadJson
                    view.evaluateJavascript(
                        "window.fireSkyMap && window.fireSkyMap.setRegional(${JSONObject.quote(payloadJson)})",
                        null,
                    )
                }
            },
            onRelease = { view ->
                if (webViewRef === view) webViewRef = null
                view.stopLoading()
                view.destroy()
            },
        )

        MapZoomPill(
            dark = dark,
            onZoomIn = { webViewRef?.evaluateJavascript("window.fireSkyMap?.zoomBy(1)", null) },
            onZoomOut = { webViewRef?.evaluateJavascript("window.fireSkyMap?.zoomBy(-1)", null) },
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(10.dp),
        )
    }
}

private const val MAP_TAG = "FireSkyMap"
private const val MAP_ASSET_HOST = "appassets.androidplatform.net"
private const val MAP_ENTRY_URL = "https://$MAP_ASSET_HOST/assets/map/mobile_map.html"
private var webDebuggingConfigured = false

private fun ensureWebViewDebugging(context: Context) {
    if (webDebuggingConfigured) return
    webDebuggingConfigured = true
    if ((context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
        WebView.setWebContentsDebuggingEnabled(true)
    }
}

private fun installMapDiagnostics(webView: WebView) {
    webView.webChromeClient = object : android.webkit.WebChromeClient() {
        override fun onConsoleMessage(msg: android.webkit.ConsoleMessage): Boolean {
            android.util.Log.d(MAP_TAG, "console ${msg.messageLevel()} ${msg.sourceId()}:${msg.lineNumber()} ${msg.message()}")
            return true
        }
    }
}

private open class MapWebViewClient(private val context: Context) : WebViewClient() {
    override fun shouldInterceptRequest(
        view: WebView,
        request: WebResourceRequest,
    ): WebResourceResponse? {
        val url = request.url
        if (url.scheme != "https" || url.host != MAP_ASSET_HOST) return null
        val path = url.path ?: return null
        if (!path.startsWith("/assets/")) return null
        val relPath = path.removePrefix("/assets/")
        if (relPath.isEmpty() || relPath.contains("..")) return null
        val mime = when {
            relPath.endsWith(".html") -> "text/html"
            relPath.endsWith(".js") -> "application/javascript"
            relPath.endsWith(".css") -> "text/css"
            relPath.endsWith(".png") -> "image/png"
            relPath.endsWith(".svg") -> "image/svg+xml"
            else -> "application/octet-stream"
        }
        return runCatching {
            val stream = context.assets.open(relPath)
            WebResourceResponse(mime, "utf-8", 200, "OK", mapOf("Cache-Control" to "no-cache"), stream)
        }.getOrNull()
    }

    override fun onPageFinished(view: WebView, url: String?) {
        android.util.Log.d(MAP_TAG, "pageFinished $url size=${view.width}x${view.height}")
    }
    override fun onReceivedError(view: WebView, request: WebResourceRequest, error: android.webkit.WebResourceError) {
        android.util.Log.w(MAP_TAG, "error ${error.errorCode} ${error.description} ${request.url}")
    }
    override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, resp: android.webkit.WebResourceResponse) {
        android.util.Log.w(MAP_TAG, "http ${resp.statusCode} ${request.url}")
    }
}

@Composable
private fun MapZoomPill(
    dark: Boolean,
    onZoomIn: () -> Unit,
    onZoomOut: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val bg = if (dark) Color(0xFF161E2E).copy(alpha = 0.88f) else Color(0xFFFCFDFF).copy(alpha = 0.90f)
    val border = if (dark) Color.White.copy(alpha = 0.14f) else Color(0xFFC9D5E1).copy(alpha = 0.85f)
    val fg = if (dark) Color(0xFFE6EDF0) else Color(0xFF1B2224)

    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(10.dp),
        color = bg,
        border = BorderStroke(0.75.dp, border),
    ) {
        Column {
            IconButton(onClick = onZoomIn, modifier = Modifier.size(30.dp)) {
                Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(15.dp), tint = fg)
            }
            HorizontalDivider(
                Modifier.width(30.dp),
                thickness = 0.5.dp,
                color = border.copy(alpha = 0.6f),
            )
            IconButton(onClick = onZoomOut, modifier = Modifier.size(30.dp)) {
                Icon(Icons.Filled.Remove, contentDescription = null, modifier = Modifier.size(15.dp), tint = fg)
            }
        }
    }
}

/** 全屏地图选点弹层（对齐 iOS MapPickerSheet）。 */
@Composable
fun MapPickerDialog(
    store: WorkbenchStore,
    state: WorkbenchState,
    onDismiss: () -> Unit,
) {
    val dark = LocalFireSkyDark.current
    val loc = state.location
    val headerBg = if (dark) Color(0xFF111826).copy(alpha = 0.96f) else Color(0xFFF6F7FA).copy(alpha = 0.96f)
    val dividerColor = if (dark) Color.White.copy(alpha = 0.08f) else Color.Black.copy(alpha = 0.07f)

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        FireSkyBackdrop(Modifier.fillMaxSize()) {
            Column(Modifier.fillMaxSize()) {
                Surface(color = headerBg) {
                    Column(Modifier.fillMaxWidth().statusBarsPadding()) {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(
                                    stringResource(R.string.map_picker_title),
                                    fontSize = 18.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = MaterialTheme.colorScheme.onBackground,
                                )
                                Text(
                                    stringResource(R.string.map_tap_hint),
                                    fontSize = 11.sp,
                                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                                )
                            }
                            Surface(
                                onClick = onDismiss,
                                shape = CircleShape,
                                color = MaterialTheme.colorScheme.primary,
                            ) {
                                Text(
                                    stringResource(R.string.map_picker_done),
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    color = actionForeground(),
                                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                                )
                            }
                        }
                        HorizontalDivider(thickness = 0.5.dp, color = dividerColor)
                    }
                }

                Box(Modifier.weight(1f).fillMaxWidth()) {
                    PointLocationMap(
                        lat = loc.lat,
                        lon = loc.lon,
                        dark = dark,
                        zoom = 11,
                        modifier = Modifier.fillMaxSize(),
                        onSelectPoint = { tapLat, tapLon -> store.selectPoint(tapLat, tapLon) },
                    )

                    Surface(
                        onClick = { store.locate() },
                        enabled = !state.busy,
                        shape = CircleShape,
                        color = if (dark) Color(0xFF161E2E).copy(alpha = 0.90f) else Color(0xFFFCFDFF).copy(alpha = 0.92f),
                        border = BorderStroke(0.75.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f)),
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(end = 16.dp, bottom = 106.dp)
                            .size(44.dp),
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            Icon(
                                Icons.Filled.LocationOn,
                                contentDescription = stringResource(R.string.cd_locate),
                                tint = coolAccentColor(),
                                modifier = Modifier.size(20.dp),
                            )
                        }
                    }

                    WorkSurface(
                        Modifier
                            .align(Alignment.BottomCenter)
                            .fillMaxWidth()
                            .navigationBarsPadding()
                            .padding(horizontal = 14.dp, vertical = 14.dp),
                        cornerRadius = 18.dp,
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Box(Modifier.size(9.dp).background(MaterialTheme.colorScheme.primary, CircleShape))
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                Text(
                                    loc.name,
                                    fontSize = 14.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    "%.4f° N, %.4f° E · %s".format(
                                        loc.lat,
                                        loc.lon,
                                        if (loc.metadataReady) stringResource(R.string.elevation_m, loc.elevation)
                                        else stringResource(R.string.elevation_unconfirmed),
                                    ),
                                    fontSize = 11.sp,
                                    style = TextStyle(fontFeatureSettings = "tnum"),
                                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                            Surface(
                                onClick = { store.starLocation() },
                                shape = CircleShape,
                                color = MaterialTheme.colorScheme.primary.copy(alpha = if (dark) 0.16f else 0.10f),
                            ) {
                                Row(
                                    Modifier.padding(horizontal = 10.dp, vertical = 7.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                                ) {
                                    Icon(
                                        Icons.Filled.Star,
                                        contentDescription = null,
                                        tint = MaterialTheme.colorScheme.primary,
                                        modifier = Modifier.size(14.dp),
                                    )
                                    Text(
                                        stringResource(R.string.star_location),
                                        fontSize = 11.sp,
                                        fontWeight = FontWeight.Medium,
                                        color = MaterialTheme.colorScheme.primary,
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
