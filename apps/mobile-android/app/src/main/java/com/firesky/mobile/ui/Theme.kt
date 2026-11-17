package com.firesky.mobile.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * FireSky 视觉基底 —— 抄 Workbench.swift 的暖橙（warm）/青绿（teal）双色与深浅双主题。
 * warm：浅色 (0.74, 0.32, 0.24) / 深色 (1, 0.55, 0.38)；teal：(0.26, 0.56, 0.54)。
 */
val WarmLight = Color(0xFFBD523D)
val WarmDark = Color(0xFFFF8C61)
val Teal = Color(0xFF428F8A)

private val DarkScheme = darkColorScheme(
    primary = WarmDark,
    secondary = Teal,
    background = Color(0xFF121A1C),
    surface = Color(0xFF121A1C),
    surfaceVariant = Color(0xFF1A2526),
    onBackground = Color(0xFFE6EDF0),
    onSurface = Color(0xFFE6EDF0),
)

private val LightScheme = lightColorScheme(
    primary = WarmLight,
    secondary = Teal,
    background = Color(0xFFE3EDEF),
    surface = Color(0xFFE3EDEF),
    surfaceVariant = Color(0xFFEDF3F2),
    onBackground = Color(0xFF1B2224),
    onSurface = Color(0xFF1B2224),
)

@Composable
fun FireSkyTheme(appearance: String, content: @Composable () -> Unit) {
    val dark = when (appearance) {
        "dark" -> true
        "light" -> false
        else -> isSystemInDarkTheme()
    }
    MaterialTheme(colorScheme = if (dark) DarkScheme else LightScheme, content = content)
}

/** 玻璃卡片（WorkSurface 同构：圆角 8dp 半透明面 + 细描边）。 */
@Composable
fun WorkSurface(
    modifier: Modifier = Modifier,
    emphasized: Boolean = false,
    content: @Composable () -> Unit,
) {
    val dark = MaterialTheme.colorScheme.background.luminance() < 0.5f
    val fill = if (dark) {
        Color.White.copy(alpha = if (emphasized) 0.075f else 0.045f)
    } else {
        Color.White.copy(alpha = if (emphasized) 0.82f else 0.60f)
    }
    val border = if (dark) {
        Color.White.copy(alpha = if (emphasized) 0.16f else 0.09f)
    } else {
        Color.White.copy(alpha = 0.80f)
    }
    Surface(
        modifier = modifier,
        shape = androidx.compose.foundation.shape.RoundedCornerShape(8.dp),
        color = fill,
        border = androidx.compose.foundation.BorderStroke(1.dp, border),
    ) {
        content()
    }
}

/** 深浅判断的近似：以背景亮度折算（避免再引 isSystemInDarkTheme 与显式覆盖打架）。 */
internal fun Color.luminance(): Float = 0.299f * red + 0.587f * green + 0.114f * blue

/** 页面根底色。 */
@Composable
fun RootSurface(content: @Composable () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.background) {
        Box(Modifier.padding(0.dp)) { content() }
    }
}
