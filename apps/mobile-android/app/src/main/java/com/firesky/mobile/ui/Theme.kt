package com.firesky.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.PauseCircle
import androidx.compose.material.icons.filled.Download
import androidx.compose.material3.Icon
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Native appearance tokens, aligned with macOS and iOS FSDesign.
 */
val WarmLight = Color(0xFFD25A36)
val WarmDark = Color(0xFFFF9B6A)
val Teal = Color(0xFF428F8A)

internal val LocalFireSkyDark = staticCompositionLocalOf { false }

@Composable
fun accentGradient() = Brush.linearGradient(
    colors = if (LocalFireSkyDark.current)
        listOf(Color(0xFFFF9B6A), Color(0xFFFFC273))
    else listOf(Color(0xFFB84B2E), Color(0xFF9B401F)),
)

@Composable
fun accentGradientBrush(): Brush = accentGradient()

@Composable
fun actionForeground(): Color = if (LocalFireSkyDark.current) Color(0xFF241810) else Color.White

@Composable
fun AccentButton(title: String, enabled: Boolean = true, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
            .background(
                if (enabled) accentGradientBrush()
                else androidx.compose.ui.graphics.SolidColor(MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f)),
                RoundedCornerShape(8.dp),
            ),
        colors = ButtonDefaults.buttonColors(
            containerColor = Color.Transparent,
            contentColor = actionForeground(),
            disabledContainerColor = Color.Transparent,
            disabledContentColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
        ),
        shape = RoundedCornerShape(8.dp),
    ) {
        Text(title, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center)
    }
}

@Composable
fun pageBackground(): Brush = Brush.linearGradient(
    colors = if (LocalFireSkyDark.current)
        listOf(Color(0xFF0B1222), Color(0xFF121B2E), Color(0xFF080E18))
    else listOf(Color(0xFFEEF3F8), Color(0xFFF8F5F0), Color(0xFFEBF1F6)),
)

enum class ChipState { Done, Failed, Interrupted, Pending }

@Composable
fun chipColors(state: ChipState): Pair<Color, Color> {
    val dark = LocalFireSkyDark.current
    val (fg, bgAlpha) = when (state) {
        ChipState.Done -> if (dark) Color(0xFF63D6AB) to 0.15f else Color(0xFF1F6E5A) to 0.13f
        ChipState.Failed -> if (dark) Color(0xFFFF8A7A) to 0.15f else Color(0xFFB83E38) to 0.13f
        ChipState.Interrupted -> if (dark) Color(0xFFFFC273) to 0.15f else Color(0xFF8A6321) to 0.13f
        ChipState.Pending -> if (dark) Color(0xFFD5DEEB) to 0.15f else Color(0xFF455364) to 0.13f
    }
    val bg = fg.copy(alpha = bgAlpha)
    return fg to bg
}

private val DarkScheme = darkColorScheme(
    primary = WarmDark,
    onPrimary = Color(0xFF241810),
    primaryContainer = Color(0xFF50352D),
    onPrimaryContainer = WarmDark,
    secondary = Teal,
    background = Color(0xFF0B1222),
    surface = Color(0xFF1B2435),
    surfaceVariant = Color(0xFF232E40),
    onSurfaceVariant = Color(0xFFD5DEEB),
    outline = Color(0xFF8292A8),
    outlineVariant = Color(0xFF4B5D78),
    error = Color(0xFFFF8A7A),
    onBackground = Color(0xFFE6EDF0),
    onSurface = Color(0xFFE6EDF0),
)

private val LightScheme = lightColorScheme(
    primary = WarmLight,
    onPrimary = Color(0xFF241810),
    primaryContainer = Color(0xFFF7E1D8),
    onPrimaryContainer = Color(0xFF9B401F),
    secondary = Teal,
    background = Color(0xFFEEF3F8),
    surface = Color(0xFFFBFCFE),
    surfaceVariant = Color(0xFFF2F6FA),
    onSurfaceVariant = Color(0xFF455364),
    outline = Color(0xFF687A90),
    outlineVariant = Color(0xFFC9D5E1),
    error = Color(0xFFB83E38),
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
    CompositionLocalProvider(LocalFireSkyDark provides dark) {
        MaterialTheme(colorScheme = if (dark) DarkScheme else LightScheme, content = content)
    }
}

/** Stable data surface; reserve platform glass for navigation and overlays. */
@Composable
fun WorkSurface(
    modifier: Modifier = Modifier,
    emphasized: Boolean = false,
    content: @Composable () -> Unit,
) {
    val fill = MaterialTheme.colorScheme.surface
    val border = if (emphasized) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant
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

/** 状态徽标（DESIGN.md 方案 4.1）：语义色底 + 图标 + 文字，圆角 6。 */
@Composable
fun StatusChip(state: String, title: String) {
    val chipState = when (state) {
        "done" -> ChipState.Done
        "failed" -> ChipState.Failed
        "interrupted" -> ChipState.Interrupted
        else -> ChipState.Pending
    }
    val (fg, bg) = chipColors(chipState)
    val icon = when (state) {
        "done" -> Icons.Filled.CheckCircle
        "failed" -> Icons.Filled.Error
        "interrupted" -> Icons.Filled.PauseCircle
        "running" -> Icons.Filled.Schedule
        else -> Icons.Filled.Download
    }
    Row(
        Modifier
            .background(bg, RoundedCornerShape(6.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(14.dp), tint = fg)
        Text(title, fontSize = 12.sp, fontWeight = FontWeight.Medium, color = fg)
    }
}
