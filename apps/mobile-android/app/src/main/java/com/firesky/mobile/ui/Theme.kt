package com.firesky.mobile.ui

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.PauseCircle
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextFieldColors
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import com.firesky.mobile.R
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin

/**
 * Native appearance tokens, aligned with macOS and iOS FSDesign.
 */
val WarmLight = Color(0xFFD25A36)
val WarmDark = Color(0xFFFF9B6A)
val CoolAccentLight = Color(0xFF4F76B8)
val CoolAccentDark = Color(0xFF8EB7FF)
val Teal = Color(0xFF428F8A)

val SoftInputShape = RoundedCornerShape(12.dp)

internal val LocalFireSkyDark = staticCompositionLocalOf { false }

@Composable
fun coolAccentColor(): Color = if (LocalFireSkyDark.current) CoolAccentDark else CoolAccentLight

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
fun AccentButton(
    title: String,
    enabled: Boolean = true,
    icon: ImageVector? = null,
    indicatorDot: Boolean = false,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val dark = LocalFireSkyDark.current
    val shape = RoundedCornerShape(14.dp)
    val shadowMod = if (enabled && !dark) {
        Modifier.shadow(
            elevation = 6.dp,
            shape = shape,
            clip = false,
            ambientColor = Color(0xFFB84B2E).copy(alpha = 0.30f),
            spotColor = Color(0xFFB84B2E).copy(alpha = 0.26f),
        )
    } else {
        Modifier
    }
    val disabledBg = if (dark) Color(0xFF283448) else Color(0xFFE2E8F0)
    val disabledFg = if (dark) Color(0xFF8899B2) else Color(0xFF8190A3)
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .then(shadowMod)
            .background(
                if (enabled) accentGradientBrush() else SolidColor(disabledBg),
                shape,
            ),
        colors = ButtonDefaults.buttonColors(
            containerColor = Color.Transparent,
            contentColor = actionForeground(),
            disabledContainerColor = Color.Transparent,
            disabledContentColor = disabledFg,
        ),
        elevation = ButtonDefaults.buttonElevation(0.dp, 0.dp, 0.dp, 0.dp, 0.dp),
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
        shape = shape,
    ) {
        if (icon != null) {
            Icon(
                icon,
                contentDescription = null,
                modifier = Modifier.size(17.dp),
            )
            Spacer(Modifier.width(8.dp))
        }
        Text(
            title,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        if (indicatorDot && enabled) {
            Spacer(Modifier.width(8.dp))
            Box(
                Modifier
                    .size(8.dp)
                    .background(Color(0xFFFFE082), CircleShape)
                    .border(1.dp, actionForeground().copy(alpha = 0.65f), CircleShape),
            )
        }
    }
}

/** 柔和色调次级操作按钮（对齐 AccentButton 高度与圆角）。 */
@Composable
fun SecondaryButton(
    title: String,
    enabled: Boolean = true,
    icon: ImageVector? = null,
    loading: Boolean = false,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val dark = LocalFireSkyDark.current
    val shape = RoundedCornerShape(14.dp)
    val bg = when {
        !enabled && dark -> Color(0xFF1D2638)
        !enabled -> Color(0xFFF1F5F9)
        dark -> Color(0xFF222D42)
        else -> Color(0xFFEEF3F8)
    }
    val fg = when {
        !enabled && dark -> Color(0xFF6B7C95)
        !enabled -> Color(0xFF94A3B8)
        dark -> Color(0xFFE6EDF0)
        else -> Color(0xFF233042)
    }
    val borderColor = when {
        !enabled && dark -> Color(0xFF394760).copy(alpha = 0.4f)
        !enabled -> Color(0xFFCBD5E1).copy(alpha = 0.55f)
        dark -> Color(0xFF50617B).copy(alpha = 0.55f)
        else -> Color(0xFFC9D5E1).copy(alpha = 0.85f)
    }
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp),
        shape = shape,
        border = BorderStroke(0.75.dp, borderColor),
        colors = ButtonDefaults.buttonColors(
            containerColor = bg,
            contentColor = fg,
            disabledContainerColor = bg,
            disabledContentColor = fg,
        ),
        elevation = ButtonDefaults.buttonElevation(0.dp, 0.dp, 0.dp, 0.dp, 0.dp),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 12.dp),
    ) {
        if (loading) {
            CircularProgressIndicator(
                modifier = Modifier.size(15.dp),
                strokeWidth = 1.75.dp,
                color = fg,
            )
            Spacer(Modifier.width(7.dp))
        } else if (icon != null) {
            Icon(icon, contentDescription = null, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(7.dp))
        }
        Text(
            title,
            fontSize = 14.sp,
            fontWeight = FontWeight.Medium,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
fun pageBackground(): Brush = Brush.linearGradient(
    colors = if (LocalFireSkyDark.current)
        listOf(Color(0xFF0B1222), Color(0xFF121B2E), Color(0xFF080E18))
    else listOf(Color(0xFFEEF3F8), Color(0xFFF8F5F0), Color(0xFFEBF1F6)),
)

/** Atmospheric dusk backdrop matching macOS FSDesign.pageBackground + warm sunset & cool twilight radial glows. */
@Composable
fun Modifier.fireSkyPageBackground(): Modifier {
    val dark = LocalFireSkyDark.current
    return this.drawBehind {
        val bgColors = if (dark) {
            listOf(Color(0xFF0B1222), Color(0xFF121B2E), Color(0xFF080E18))
        } else {
            listOf(Color(0xFFEEF3F8), Color(0xFFF8F5F0), Color(0xFFEBF1F6))
        }
        drawRect(
            brush = Brush.linearGradient(
                colors = bgColors,
                start = Offset.Zero,
                end = Offset(size.width, size.height),
            ),
        )
        val warmGlow = if (dark) Color(0xFF8A5546).copy(alpha = 0.20f) else Color(0xFFF4D2AE).copy(alpha = 0.32f)
        val coolGlow = if (dark) Color(0xFF2B558A).copy(alpha = 0.22f) else Color(0xFFCBDFF2).copy(alpha = 0.30f)
        val maxDim = max(size.width, size.height).coerceAtLeast(1f)
        drawRect(
            brush = Brush.radialGradient(
                colors = listOf(warmGlow, Color.Transparent),
                center = Offset(size.width * 0.92f, size.height * 0.06f),
                radius = maxDim * 0.72f,
            ),
        )
        drawRect(
            brush = Brush.radialGradient(
                colors = listOf(coolGlow, Color.Transparent),
                center = Offset(size.width * 0.08f, size.height * 0.92f),
                radius = maxDim * 0.68f,
            ),
        )
    }
}

@Composable
fun FireSkyBackdrop(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(modifier.fireSkyPageBackground()) {
        CompositionLocalProvider(LocalContentColor provides MaterialTheme.colorScheme.onBackground) {
            content()
        }
    }
}

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
    surface = Color(0xFF182132),
    surfaceVariant = Color(0xFF202A3D),
    onSurfaceVariant = Color(0xFFD5DEEB),
    outline = Color(0xFF8292A8),
    outlineVariant = Color(0xFF4B5D78),
    error = Color(0xFFFF8A7A),
    onBackground = Color(0xFFE6EDF0),
    onSurface = Color(0xFFE6EDF0),
)

private val LightScheme = lightColorScheme(
    primary = WarmLight,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFF7E1D8),
    onPrimaryContainer = Color(0xFF9B401F),
    secondary = Teal,
    background = Color(0xFFEEF3F8),
    surface = Color(0xFFFCFDFF),
    surfaceVariant = Color(0xFFF3F6FA),
    onSurfaceVariant = Color(0xFF455364),
    outline = Color(0xFF687A90),
    outlineVariant = Color(0xFFC9D5E1),
    error = Color(0xFFB83E38),
    onBackground = Color(0xFF1B2224),
    onSurface = Color(0xFF1B2224),
)

/** 按比例行高的 Material 3 字体排版，避免小字号继承固定 24sp 行高产生过疏双倍行距。 */
private val FireSkyTypography = Typography(
    titleLarge = TextStyle(fontSize = 22.sp, lineHeight = 1.28.em, fontWeight = FontWeight.Bold),
    titleMedium = TextStyle(fontSize = 15.sp, lineHeight = 1.35.em, fontWeight = FontWeight.SemiBold),
    titleSmall = TextStyle(fontSize = 13.sp, lineHeight = 1.35.em, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 1.42.em),
    bodyMedium = TextStyle(fontSize = 13.sp, lineHeight = 1.42.em),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 1.40.em),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 1.35.em, fontWeight = FontWeight.Medium),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 1.35.em, fontWeight = FontWeight.Medium),
    labelSmall = TextStyle(fontSize = 11.sp, lineHeight = 1.35.em, fontWeight = FontWeight.Medium),
)

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

@Composable
fun FireSkyTheme(appearance: String, content: @Composable () -> Unit) {
    val dark = when (appearance) {
        "dark" -> true
        "light" -> false
        else -> isSystemInDarkTheme()
    }
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = view.context.findActivity()?.window ?: return@SideEffect
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = !dark
                isAppearanceLightNavigationBars = !dark
            }
        }
    }
    CompositionLocalProvider(LocalFireSkyDark provides dark) {
        MaterialTheme(
            colorScheme = if (dark) DarkScheme else LightScheme,
            typography = FireSkyTypography,
            content = content,
        )
    }
}

/** Stable data surface; matches macOS FSDesign content / grouped cards with soft hairline border & light ambient shadow. */
@Composable
fun WorkSurface(
    modifier: Modifier = Modifier,
    emphasized: Boolean = false,
    grouped: Boolean = false,
    cornerRadius: Dp? = null,
    content: @Composable () -> Unit,
) {
    val dark = LocalFireSkyDark.current
    val radius = cornerRadius ?: if (grouped) 14.dp else 20.dp
    val shape = RoundedCornerShape(radius)
    val fill = if (grouped) {
        if (dark) Color(0xFF202A3D) else Color(0xFFF3F6FA)
    } else {
        if (dark) Color(0xFF182132) else Color(0xFFFCFDFF)
    }
    val borderColor = when {
        emphasized -> if (dark) Color(0xFFFF9B6A).copy(alpha = 0.50f) else Color(0xFFC45B38).copy(alpha = 0.36f)
        grouped -> if (dark) Color.White.copy(alpha = 0.08f) else Color(0xFFD8E1EA).copy(alpha = 0.78f)
        else -> if (dark) Color.White.copy(alpha = 0.09f) else Color(0xFFC9D5E1).copy(alpha = 0.60f)
    }
    val shadowModifier = if (dark || grouped) {
        Modifier
    } else {
        Modifier.shadow(
            elevation = 3.dp,
            shape = shape,
            clip = false,
            ambientColor = Color(0xFF2A3C58).copy(alpha = 0.14f),
            spotColor = Color(0xFF2A3C58).copy(alpha = 0.10f),
        )
    }
    Surface(
        modifier = modifier.then(shadowModifier),
        shape = shape,
        color = fill,
        border = BorderStroke(0.75.dp, borderColor),
    ) {
        if (emphasized) {
            val sheen = MaterialTheme.colorScheme.primary.copy(alpha = if (dark) 0.09f else 0.05f)
            Box(
                modifier = Modifier.drawBehind {
                    drawRect(
                        brush = Brush.linearGradient(
                            colors = listOf(sheen, Color.Transparent),
                            start = Offset.Zero,
                            end = Offset(size.width, size.height),
                        ),
                    )
                },
                propagateMinConstraints = true,
            ) {
                content()
            }
        } else {
            content()
        }
    }
}

/** Tinted 30dp squircle icon badge matching macOS & iOS Settings rows. */
@Composable
fun IconBadge(
    icon: ImageVector,
    tint: Color = MaterialTheme.colorScheme.primary,
    modifier: Modifier = Modifier,
) {
    val dark = LocalFireSkyDark.current
    Box(
        modifier = modifier
            .size(30.dp)
            .background(tint.copy(alpha = if (dark) 0.16f else 0.12f), RoundedCornerShape(8.dp)),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            icon,
            contentDescription = null,
            modifier = Modifier.size(16.dp),
            tint = tint,
        )
    }
}

/** Soft tonal capsule pill chip. */
@Composable
fun SoftPillChip(
    text: String,
    modifier: Modifier = Modifier,
    tint: Color? = null,
    leadingDotColor: Color? = null,
) {
    val dark = LocalFireSkyDark.current
    val resolvedTint = tint ?: if (dark) Color(0xFF9EB0C9) else Color(0xFF5D6D82)
    val bg = resolvedTint.copy(alpha = if (dark) 0.16f else 0.10f)
    val fg = tint ?: MaterialTheme.colorScheme.onSurfaceVariant
    val border = resolvedTint.copy(alpha = if (dark) 0.30f else 0.24f)
    Box(
        modifier = modifier
            .background(bg, CircleShape)
            .border(0.75.dp, border, CircleShape)
            .padding(horizontal = 10.dp, vertical = 4.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            if (leadingDotColor != null) {
                Box(Modifier.size(6.dp).background(leadingDotColor, CircleShape))
            }
            Text(
                text = text,
                fontSize = 11.sp,
                fontWeight = FontWeight.Medium,
                color = fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** Capsule top category switcher matching macOS & iOS Settings / Workbench bars. */
@Composable
fun CapsuleCategoryBar(
    options: List<String>,
    titles: List<String>,
    icons: List<ImageVector>,
    selection: String,
    onSelect: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val dark = LocalFireSkyDark.current
    val trackFill = if (dark) Color(0xFF161E2E) else Color(0xFFEAF0F6)
    val trackBorder = if (dark) Color(0xFF50617B).copy(alpha = 0.45f) else Color(0xFFD8E1EA).copy(alpha = 0.85f)
    val warm = MaterialTheme.colorScheme.primary

    Row(
        modifier = modifier
            .fillMaxWidth()
            .selectableGroup()
            .background(trackFill, CircleShape)
            .border(0.75.dp, trackBorder, CircleShape)
            .padding(4.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        options.forEachIndexed { index, option ->
            val selected = selection == option
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
                modifier = Modifier
                    .weight(1f)
                    .heightIn(min = 38.dp)
                    .then(pillShadow)
                    .clip(CircleShape)
                    .background(pillFill, CircleShape)
                    .selectable(selected = selected, role = Role.Tab, onClick = { onSelect(option) }),
                contentAlignment = Alignment.Center,
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    icons.getOrNull(index)?.let { icon ->
                        Icon(
                            icon,
                            contentDescription = null,
                            modifier = Modifier.size(15.dp),
                            tint = if (selected) warm
                            else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.72f),
                        )
                    }
                    Text(
                        text = titles.getOrNull(index) ?: option,
                        fontSize = 13.sp,
                        fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                        color = if (selected) MaterialTheme.colorScheme.onSurface
                        else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.72f),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
    }
}

/** Sleek soft-filled input colors replacing raw boxy Material 3 outlines. */
@Composable
fun softInputColors(): TextFieldColors {
    val dark = LocalFireSkyDark.current
    val fill = if (dark) Color(0xFF202A3D) else Color(0xFFF3F6FA)
    return OutlinedTextFieldDefaults.colors(
        focusedContainerColor = fill,
        unfocusedContainerColor = fill,
        disabledContainerColor = fill.copy(alpha = 0.6f),
        focusedBorderColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.75f),
        unfocusedBorderColor = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.35f),
        disabledBorderColor = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.22f),
    )
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

/** 状态徽标（DESIGN.md 方案 4.1）：语义色底 + 图标 + 文字。 */
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
            .background(bg, CircleShape)
            .border(0.75.dp, fg.copy(alpha = 0.28f), CircleShape)
            .padding(horizontal = 8.dp, vertical = 3.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(13.dp), tint = fg)
        Text(title, fontSize = 11.sp, fontWeight = FontWeight.Medium, color = fg)
    }
}

/**
 * 新版 FireSky 品牌标（对标 macOS BrandMark.swift）：
 * 深暮海军蓝→靛蓝→酒红圆角方标 + 发光日落焰云（右卷焰尾 + 底部火尾缺口）。
 */
@Composable
fun FireSkyMark(modifier: Modifier = Modifier) {
    Canvas(modifier = modifier) {
        if (size.width <= 0f || size.height <= 0f) return@Canvas
        withTransform({
            scale(scaleX = size.width / 64f, scaleY = size.height / 64f, pivot = Offset.Zero)
        }) {
            val tilePath = Path().apply {
                addRoundRect(
                    RoundRect(
                        rect = Rect(1f, 1f, 63f, 63f),
                        cornerRadius = CornerRadius(14.5f, 14.5f),
                    ),
                )
            }

            // 1. Deep twilight navy-to-burgundy squircle tile
            drawPath(
                path = tilePath,
                brush = Brush.verticalGradient(
                    0.0f to Color(0xFF0F294D),
                    0.5f to Color(0xFF242647),
                    1.0f to Color(0xFF5E1F36),
                    startY = 1f,
                    endY = 63f,
                ),
            )

            // Subtle inner twilight rim
            drawPath(
                path = tilePath,
                brush = Brush.verticalGradient(
                    colors = listOf(Color.White.copy(alpha = 0.16f), Color.White.copy(alpha = 0.03f)),
                    startY = 1f,
                    endY = 63f,
                ),
                style = Stroke(width = 0.8f),
            )

            // Soft warm sunset ambient glow behind the cloud
            clipPath(tilePath) {
                drawPath(
                    path = tilePath,
                    brush = Brush.radialGradient(
                        0.0f to Color(1.00f, 0.45f, 0.16f, 0.28f),
                        0.45f to Color(0.90f, 0.22f, 0.24f, 0.08f),
                        1.0f to Color.Transparent,
                        center = Offset(32f, 34f),
                        radius = 29f,
                    ),
                )
            }

            // 2. Sculpted outer sunset cloud silhouette
            val mainCloud = Path().apply {
                moveTo(17.5f, 45.2f)
                cubicTo(12.8f, 45.2f, 10.6f, 41.6f, 11.4f, 37.8f)
                cubicTo(11.9f, 34.4f, 14.0f, 32.3f, 16.5f, 31.8f)
                cubicTo(16.7f, 26.8f, 20.4f, 23.8f, 24.4f, 23.6f)
                cubicTo(27.2f, 15.6f, 39.2f, 15.6f, 42.4f, 24.8f)
                cubicTo(46.8f, 25.0f, 49.6f, 27.8f, 50.0f, 31.6f)
                cubicTo(52.4f, 32.4f, 53.6f, 35.4f, 52.8f, 38.4f)
                cubicTo(52.0f, 42.6f, 48.6f, 45.2f, 44.2f, 45.2f)
                lineTo(34.2f, 45.2f)
                cubicTo(31.4f, 45.4f, 29.5f, 46.8f, 27.8f, 48.0f)
                cubicTo(28.5f, 46.8f, 29.2f, 45.9f, 29.8f, 45.2f)
                lineTo(17.5f, 45.2f)
                close()
            }
            drawPath(
                path = mainCloud,
                brush = Brush.linearGradient(
                    0.0f to Color(1.00f, 0.78f, 0.16f),
                    0.48f to Color(0.99f, 0.49f, 0.13f),
                    1.0f to Color(0.85f, 0.16f, 0.21f),
                    start = Offset(32f, 17f),
                    end = Offset(28f, 47f),
                ),
            )

            // 3. Inner bright golden-orange upper body
            val innerGlow = Path().apply {
                moveTo(19.4f, 42.6f)
                cubicTo(15.6f, 41.2f, 14.6f, 37.2f, 16.4f, 34.6f)
                cubicTo(18.2f, 29.4f, 21.8f, 26.4f, 25.4f, 26.2f)
                cubicTo(28.0f, 19.4f, 38.6f, 19.6f, 41.4f, 27.6f)
                cubicTo(45.4f, 28.4f, 47.0f, 32.6f, 45.6f, 36.2f)
                cubicTo(43.2f, 40.4f, 37.6f, 43.0f, 31.8f, 43.2f)
                close()
            }
            drawPath(
                path = innerGlow,
                brush = Brush.radialGradient(
                    0.0f to Color(1.00f, 0.85f, 0.24f),
                    0.55f to Color(1.00f, 0.56f, 0.14f),
                    1.0f to Color(0.90f, 0.25f, 0.16f),
                    center = Offset(34f, 26f),
                    radius = 22f,
                ),
            )

            // 4. Lower-left sculpted deep-crimson cloud billow shadow
            val leftBillow = Path().apply {
                moveTo(11.6f, 38.6f)
                cubicTo(13.4f, 35.4f, 16.4f, 35.2f, 18.4f, 36.6f)
                cubicTo(21.8f, 38.6f, 25.0f, 43.8f, 29.8f, 45.2f)
                lineTo(17.5f, 45.2f)
                cubicTo(13.2f, 45.2f, 11.0f, 42.0f, 11.6f, 38.6f)
                close()
            }
            drawPath(
                path = leftBillow,
                brush = Brush.linearGradient(
                    colors = listOf(
                        Color(0.92f, 0.22f, 0.19f),
                        Color(0.64f, 0.09f, 0.22f),
                    ),
                    start = Offset(18f, 36f),
                    end = Offset(16f, 46f),
                ),
            )

            // 5. Right curling golden flame wisp sweeping to the bottom tail
            val rightWisp = Path().apply {
                moveTo(28.0f, 47.9f)
                cubicTo(31.2f, 42.4f, 34.4f, 39.4f, 39.2f, 36.8f)
                cubicTo(43.0f, 34.8f, 46.2f, 31.4f, 49.4f, 31.9f)
                cubicTo(52.2f, 32.6f, 53.6f, 35.6f, 52.7f, 38.4f)
                cubicTo(51.8f, 42.6f, 48.4f, 45.2f, 44.2f, 45.2f)
                lineTo(34.2f, 45.2f)
                cubicTo(31.6f, 45.4f, 29.8f, 46.8f, 28.0f, 47.9f)
                close()
            }
            drawPath(
                path = rightWisp,
                brush = Brush.linearGradient(
                    0.0f to Color(1.00f, 0.89f, 0.34f),
                    0.45f to Color(0.99f, 0.54f, 0.14f),
                    1.0f to Color(0.84f, 0.18f, 0.17f),
                    start = Offset(51f, 33f),
                    end = Offset(32f, 47f),
                ),
            )

            // 6. Crisp soft crest highlight on the right wisp tip
            val tipGlow = Path().apply {
                moveTo(42.0f, 38.2f)
                cubicTo(45.2f, 33.4f, 49.6f, 32.2f, 51.4f, 35.4f)
                cubicTo(52.6f, 38.8f, 48.8f, 42.0f, 44.6f, 42.6f)
                cubicTo(42.4f, 42.8f, 40.8f, 40.2f, 42.0f, 38.2f)
                close()
            }
            drawPath(
                path = tipGlow,
                brush = Brush.radialGradient(
                    0.0f to Color(1.00f, 0.94f, 0.52f, 0.88f),
                    1.0f to Color(1.00f, 0.68f, 0.20f, 0.0f),
                    center = Offset(49.2f, 35.6f),
                    radius = 8.5f,
                ),
            )
        }
    }
}

/** 品牌插画空态（对标 macOS FireSkyMarkScene）：品牌标 + 弧线轨道 + 三颗星点。 */
@Composable
fun FireSkyMarkScene(size: Dp = 96.dp, modifier: Modifier = Modifier) {
    val dark = LocalFireSkyDark.current
    val outerArcColor = if (dark) Color.White.copy(alpha = 0.14f) else Color(0xFF4F76B8).copy(alpha = 0.20f)
    val warmArcColor = Color(0xFFF49B6A).copy(alpha = if (dark) 0.35f else 0.45f)
    val starBaseColor = if (dark) Color.White.copy(alpha = 0.55f) else Color(0xFF4F76B8).copy(alpha = 0.55f)

    Box(modifier.size(size), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(size)) {
            val strokeWidth = 1.5.dp.toPx()
            val inset = strokeWidth / 2f
            val arcSize = Size(this.size.width - strokeWidth, this.size.height - strokeWidth)
            drawArc(
                color = outerArcColor,
                startAngle = -120f,
                sweepAngle = 259f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = arcSize,
                style = Stroke(width = strokeWidth, cap = StrokeCap.Round),
            )
            drawArc(
                color = warmArcColor,
                startAngle = 140f,
                sweepAngle = 162f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = arcSize,
                style = Stroke(width = strokeWidth, cap = StrokeCap.Round),
            )
            val radius = this.size.minDimension * 0.46f
            val center = Offset(this.size.width / 2f, this.size.height / 2f)
            for (index in 0..2) {
                val angle = (-120.0 + index * 55.0) * PI / 180.0
                val dotRadius = if (index == 2) 1.5.dp.toPx() else 2.25.dp.toPx()
                drawCircle(
                    color = if (index == 1) Color(0xFFF49B6A) else starBaseColor,
                    radius = dotRadius,
                    center = Offset(
                        center.x + (cos(angle) * radius).toFloat(),
                        center.y + (sin(angle) * radius).toFloat(),
                    ),
                )
            }
        }
        FireSkyMark(Modifier.size(size * 0.62f))
    }
}

/** 瘦客户端只读边界提示条（锁图标 + 柔和底色）。 */
@Composable
fun ReadOnlyNoticeBanner(text: String, modifier: Modifier = Modifier) {
    val dark = LocalFireSkyDark.current
    val tint = coolAccentColor()
    val bg = tint.copy(alpha = if (dark) 0.12f else 0.08f)
    val border = tint.copy(alpha = if (dark) 0.28f else 0.22f)
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        color = bg,
        border = BorderStroke(0.75.dp, border),
    ) {
        Row(
            Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(Icons.Filled.Lock, contentDescription = null, modifier = Modifier.size(15.dp), tint = tint)
            Text(
                text,
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.76f),
                modifier = Modifier.weight(1f),
            )
        }
    }
}

/** 按页面作用域隔离的可关闭错误条（支持复制与手动关闭）。 */
@Composable
fun DismissibleErrorBanner(
    message: String,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (message.isEmpty()) return
    val errorColor = MaterialTheme.colorScheme.error
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = errorColor.copy(alpha = 0.09f),
        border = BorderStroke(0.75.dp, errorColor.copy(alpha = 0.34f)),
    ) {
        Row(
            Modifier.padding(start = 12.dp, top = 8.dp, bottom = 8.dp, end = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(Icons.Filled.Error, contentDescription = null, Modifier.size(16.dp), tint = errorColor)
            SelectionContainer(Modifier.weight(1f)) {
                Text(message, fontSize = 12.sp, color = errorColor)
            }
            IconButton(onClick = onDismiss, modifier = Modifier.size(32.dp)) {
                Icon(
                    Icons.Filled.Close,
                    contentDescription = stringResource(R.string.dismiss),
                    modifier = Modifier.size(16.dp),
                    tint = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                )
            }
        }
    }
}
