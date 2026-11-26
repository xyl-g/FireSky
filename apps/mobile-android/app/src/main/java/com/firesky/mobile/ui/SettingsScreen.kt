package com.firesky.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudQueue
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import org.json.JSONObject
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.R
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/** 设置分类（macOS SettingsCategory 同构）：通用 / 数据 / 高级。 */
private val SettingsCategories = listOf("通用", "数据", "高级")

/** 设置页（macOS DialogViews.swift / iOS SettingsView 同构）：顶部胶囊分类切换 + 运行状态摘要 + 图标行分组卡。 */
@Composable
fun SettingsScreen(store: WorkbenchStore, state: WorkbenchState) {
    val pageError = state.errorText("settings")
    val scrollState = rememberScrollState()
    val scope = rememberCoroutineScope()

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                stringResource(R.string.settings_title),
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onBackground,
            )
            Text(
                when (state.settingsCategory) {
                    "数据" -> stringResource(R.string.settings_data_subtitle)
                    "高级" -> stringResource(R.string.settings_advanced_subtitle)
                    else -> stringResource(R.string.settings_general_subtitle)
                },
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
            )
        }
        CapsuleCategoryBar(
            options = SettingsCategories,
            titles = listOf(
                stringResource(R.string.settings_general),
                stringResource(R.string.settings_server_status),
                stringResource(R.string.settings_advanced),
            ),
            icons = listOf(Icons.Filled.Tune, Icons.Filled.Storage, Icons.Filled.Memory),
            selection = state.settingsCategory,
            onSelect = { store.setSettingsCategory(it) },
        )
        if (pageError.isNotEmpty()) {
            DismissibleErrorBanner(pageError, onDismiss = { store.clearError() })
        }
        when (state.settingsCategory) {
            "数据" -> ServerStatusSection(store, state)
            "高级" -> EngineSection(store, state)
            else -> GeneralSection(store, state)
        }
    }
}

@Composable
private fun AnchorPillChip(label: String, onClick: () -> Unit) {
    val dark = LocalFireSkyDark.current
    val fill = if (dark) Color(0xFF202A3D) else Color(0xFFF3F6FA)
    val border = if (dark) Color(0xFF50617B).copy(alpha = 0.45f) else Color(0xFFD8E1EA).copy(alpha = 0.85f)
    Surface(
        onClick = onClick,
        shape = CircleShape,
        color = fill,
        border = BorderStroke(0.75.dp, border),
    ) {
        Text(
            text = label,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 13.dp, vertical = 7.dp),
        )
    }
}

@Composable
private fun SectionCaption(title: String) {
    Text(
        title,
        fontSize = 12.sp,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
        modifier = Modifier.padding(start = 4.dp),
    )
}

@Composable
private fun Section(
    title: String,
    icon: ImageVector? = null,
    iconTint: Color = MaterialTheme.colorScheme.primary,
    trailing: (@Composable () -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                if (icon != null) {
                    IconBadge(icon, tint = iconTint)
                }
                Text(title, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                if (trailing != null) trailing()
            }
            content()
        }
    }
}

@Composable
private fun SettingsIconRow(
    icon: ImageVector,
    tint: Color = MaterialTheme.colorScheme.primary,
    title: String,
    subtitle: String,
    control: @Composable () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            IconBadge(icon, tint = tint)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                Text(subtitle, fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
            }
        }
        control()
    }
}

@Composable
private fun IndentedDivider() {
    HorizontalDivider(
        modifier = Modifier.padding(start = 42.dp),
        thickness = 0.5.dp,
        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.10f),
    )
}

@Composable
private fun SelectableSourceRow(label: String, selected: Boolean, onSelect: () -> Unit) {
    val dark = LocalFireSkyDark.current
    val warm = MaterialTheme.colorScheme.primary
    val fill = when {
        selected && dark -> warm.copy(alpha = 0.14f)
        selected -> warm.copy(alpha = 0.08f)
        dark -> Color(0xFF202A3D)
        else -> Color(0xFFF3F6FA)
    }
    val border = when {
        selected -> warm.copy(alpha = if (dark) 0.48f else 0.36f)
        dark -> Color(0xFF50617B).copy(alpha = 0.38f)
        else -> Color(0xFFD8E1EA).copy(alpha = 0.85f)
    }
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 46.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(fill, RoundedCornerShape(12.dp))
            .border(0.75.dp, border, RoundedCornerShape(12.dp))
            .selectable(selected = selected, role = Role.RadioButton, onClick = onSelect)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (selected) {
            Icon(Icons.Filled.CheckCircle, contentDescription = null, modifier = Modifier.size(18.dp), tint = warm)
        } else {
            Box(
                Modifier
                    .size(18.dp)
                    .border(
                        1.5.dp,
                        MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.45f),
                        CircleShape,
                    ),
            )
        }
        Text(
            label,
            fontSize = 13.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            color = if (selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.85f),
        )
    }
}

/** 通用分类：运行状态摘要卡 + 外观（主题）+ 语言（响应式切换）。 */
@Composable
private fun GeneralSection(store: WorkbenchStore, state: WorkbenchState) {
    val context = LocalContext.current
    val currentLanguage = L10n.language(context)
    val appearanceLabel = when (state.appearance) {
        "light" -> stringResource(R.string.theme_light)
        "dark" -> stringResource(R.string.theme_dark)
        else -> stringResource(R.string.theme_system)
    }
    val languageSubtitle = when (currentLanguage) {
        "zh" -> stringResource(R.string.lang_chinese)
        "en" -> stringResource(R.string.lang_english)
        else -> stringResource(R.string.lang_system)
    }

    RuntimeSummaryCard(store, state)

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionCaption(stringResource(R.string.section_interface))
        WorkSurface(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                SettingsIconRow(
                    icon = Icons.Filled.Palette,
                    tint = MaterialTheme.colorScheme.primary,
                    title = stringResource(R.string.appearance_title),
                    subtitle = appearanceLabel,
                ) {
                    Segmented(
                        options = listOf("system", "light", "dark"),
                        titles = listOf(
                            stringResource(R.string.theme_system),
                            stringResource(R.string.theme_light),
                            stringResource(R.string.theme_dark),
                        ),
                        selection = state.appearance,
                        onSelect = store::setAppearance,
                        accent = true,
                    )
                }
                IndentedDivider()
                SettingsIconRow(
                    icon = Icons.Filled.Language,
                    tint = coolAccentColor(),
                    title = stringResource(R.string.language_label),
                    subtitle = languageSubtitle,
                ) {
                    Segmented(
                        options = listOf("system", "zh", "en"),
                        titles = listOf(
                            stringResource(R.string.lang_system),
                            stringResource(R.string.lang_chinese),
                            stringResource(R.string.lang_english),
                        ),
                        selection = currentLanguage,
                        onSelect = { L10n.setLanguage(context, it) },
                        accent = true,
                    )
                }
            }
        }
    }
}

@Composable
private fun RuntimeSummaryCard(store: WorkbenchStore, state: WorkbenchState) {
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    stringResource(R.string.runtime_summary_title),
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { store.setSettingsCategory("数据") }) {
                    Text(stringResource(R.string.runtime_view_server), fontSize = 12.sp)
                    Spacer(Modifier.width(4.dp))
                    Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = null, modifier = Modifier.size(14.dp))
                }
            }
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusSummaryBadge(
                        title = stringResource(R.string.runtime_remote_server),
                        value = when {
                            state.online -> stringResource(R.string.engine_connected)
                            state.serverConfigured -> stringResource(R.string.engine_offline)
                            else -> stringResource(R.string.runtime_not_configured)
                        },
                        healthy = state.online,
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(12.dp))
                            .clickable { store.setSettingsCategory("高级") },
                    )
                    StatusSummaryBadge(
                        title = stringResource(R.string.runtime_data_sync),
                        value = if (state.dataSyncConfigured || store.camsConfigured) stringResource(R.string.runtime_configured)
                        else stringResource(R.string.runtime_on_demand),
                        healthy = state.dataSyncConfigured || store.camsConfigured,
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(12.dp))
                            .clickable { store.setSettingsCategory("数据") },
                    )
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusSummaryBadge(
                        title = stringResource(R.string.runtime_cams),
                        value = if (store.camsConfigured) stringResource(R.string.runtime_configured)
                        else stringResource(R.string.runtime_not_configured),
                        healthy = store.camsConfigured,
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(12.dp))
                            .clickable { store.setSettingsCategory("数据") },
                    )
                    StatusSummaryBadge(
                        title = stringResource(R.string.runtime_retention),
                        value = if (state.retentionConfigured) stringResource(R.string.runtime_active)
                        else stringResource(R.string.retention_unlimited),
                        healthy = state.retentionConfigured,
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(12.dp))
                            .clickable { store.setSettingsCategory("数据") },
                    )
                }
            }
            if (state.remoteServerUrl.isNotBlank() || state.remoteWorkspace.isNotBlank()) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .clickable { store.setSettingsCategory("高级") }
                        .padding(vertical = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        state.remoteWorkspace.ifEmpty { state.remoteServerUrl },
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    Text(
                        stringResource(R.string.runtime_configure_server),
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
        }
    }
}

@Composable
private fun StatusSummaryBadge(title: String, value: String, healthy: Boolean, modifier: Modifier = Modifier) {
    val dotColor = if (healthy) Color(0xFF3B9C57) else Color(0xFFC98B2C)
    WorkSurface(modifier = modifier, grouped = true, cornerRadius = 12.dp) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                title,
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Box(Modifier.size(6.dp).background(dotColor, CircleShape))
                Text(
                    value,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

/** 服务端只读状态视图（CAMS 状态 + 后台同步运行状态 + 保留规则摘要；所有写操作留在桌面端）。 */
@Composable
private fun ServerStatusSection(store: WorkbenchStore, state: WorkbenchState) {
    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        ReadOnlyNoticeBanner(stringResource(R.string.readonly_settings_notice))
        CamsStatusCard(store, state)
        DataSyncReadOnlyCard(state)
        RetentionReadOnlyCard(state)
    }
}

@Composable
private fun CamsStatusCard(store: WorkbenchStore, state: WorkbenchState) {
    Section(
        title = stringResource(R.string.cams_section),
        icon = Icons.Filled.Key,
        iconTint = MaterialTheme.colorScheme.primary,
        trailing = {
            TextButton(onClick = { store.refreshCAMSConfiguration() }, enabled = state.online) {
                Text(stringResource(R.string.recheck), fontSize = 11.sp)
            }
        },
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Icon(
                Icons.Filled.CheckCircle,
                contentDescription = null,
                Modifier.size(18.dp),
                tint = if (store.camsConfigured) Color(0xFF2E8B72)
                else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f),
            )
            Text(camsStatusDescription(state.camsConfiguration), fontSize = 12.sp, modifier = Modifier.weight(1f))
        }
        Text(
            stringResource(R.string.cams_remote_readonly_note),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
    }
}

/** 只读后台同步状态卡。 */
@Composable
private fun DataSyncReadOnlyCard(state: WorkbenchState) {
    val enabledSources = listOfNotNull(
        if (state.dataSyncGFS) "GFS" else null,
        if (state.dataSyncEC) "EC" else null,
        if (state.dataSyncCAMS) "CAMS" else null,
    )
    Section(
        title = stringResource(R.string.sync_section),
        icon = Icons.Filled.Sync,
        iconTint = coolAccentColor(),
        trailing = {
            val statusColor = if (state.dataSyncEnabled) Color(0xFF2E8B72) else MaterialTheme.colorScheme.onSurfaceVariant
            SoftPillChip(
                text = stringResource(if (state.dataSyncEnabled) R.string.sync_status_enabled else R.string.sync_status_disabled),
                tint = statusColor,
                leadingDotColor = statusColor,
            )
        },
    ) {
        if (state.dataSyncEnabled && !state.dataSyncRetentionConfigured) {
            Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Icon(Icons.Filled.Warning, contentDescription = null, modifier = Modifier.size(15.dp), tint = Color(0xFFD98F24))
                Text(stringResource(R.string.sync_retention_warning), fontSize = 11.sp, color = Color(0xFFD98F24))
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ReadOnlyStatTile(
                label = stringResource(R.string.sync_interval_label),
                value = "${state.dataSyncInterval.ifEmpty { "15" }} ${stringResource(R.string.sync_interval_unit)}",
                modifier = Modifier.weight(1f),
            )
            ReadOnlyStatTile(
                label = stringResource(R.string.sync_horizon_label),
                value = "${state.dataSyncLeadHorizon.ifEmpty { "72" }} ${stringResource(R.string.sync_horizon_unit)}",
                modifier = Modifier.weight(1f),
            )
            ReadOnlyStatTile(
                label = stringResource(R.string.sync_sources_label),
                value = if (enabledSources.isEmpty()) "—" else enabledSources.joinToString("/"),
                modifier = Modifier.weight(1f),
            )
        }
        if (state.dataSyncLoaded) {
            HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f))
            Text(
                stringResource(R.string.sync_status_title),
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.7f),
            )
            listOf("gfs" to "GFS", "ec" to "EC", "cams" to "CAMS").forEach { (key, label) ->
                SyncSourceStatusRow(
                    label = label,
                    sourceState = state.dataSyncState.optJSONObject(key) ?: JSONObject(),
                )
            }
        }
    }
}

/** 只读保留策略摘要卡。 */
@Composable
private fun RetentionReadOnlyCard(state: WorkbenchState) {
    val activeRulesCount = listOf(
        state.retentionGFS, state.retentionECMWF, state.retentionCAMS,
        state.retentionSatelliteDays, state.retentionReportDays, state.retentionMapDays,
    ).count { it.trim().isNotEmpty() }
    val unlimited = stringResource(R.string.retention_unlimited)
    val cyclesUnit = stringResource(R.string.retention_unit_cycles)
    val daysUnit = stringResource(R.string.retention_unit_days)
    fun formatRule(raw: String, unit: String): String =
        raw.trim().takeIf { it.isNotEmpty() }?.let { "$it $unit" } ?: unlimited

    Section(
        title = stringResource(R.string.retention_section),
        icon = Icons.Filled.Storage,
        iconTint = MaterialTheme.colorScheme.primary,
        trailing = {
            if (activeRulesCount > 0) {
                SoftPillChip(
                    text = stringResource(R.string.retention_rules_active_fmt, activeRulesCount),
                    tint = MaterialTheme.colorScheme.primary,
                )
            }
        },
    ) {
        if (activeRulesCount == 0 && !state.retentionConfigured) {
            Text(
                stringResource(R.string.retention_unlimited_summary),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
            )
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ReadOnlyStatTile(stringResource(R.string.retention_gfs), formatRule(state.retentionGFS, cyclesUnit), Modifier.weight(1f))
                    ReadOnlyStatTile(stringResource(R.string.retention_ec), formatRule(state.retentionECMWF, cyclesUnit), Modifier.weight(1f))
                    ReadOnlyStatTile(stringResource(R.string.retention_cams), formatRule(state.retentionCAMS, cyclesUnit), Modifier.weight(1f))
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ReadOnlyStatTile(stringResource(R.string.retention_sat), formatRule(state.retentionSatelliteDays, daysUnit), Modifier.weight(1f))
                    ReadOnlyStatTile(stringResource(R.string.retention_reports), formatRule(state.retentionReportDays, daysUnit), Modifier.weight(1f))
                    ReadOnlyStatTile(stringResource(R.string.retention_maps), formatRule(state.retentionMapDays, daysUnit), Modifier.weight(1f))
                }
            }
        }
    }
}

@Composable
private fun ReadOnlyStatTile(label: String, value: String, modifier: Modifier = Modifier) {
    WorkSurface(modifier = modifier, grouped = true, cornerRadius = 12.dp) {
        Column(Modifier.padding(horizontal = 10.dp, vertical = 9.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                label,
                fontSize = 10.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.60f),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                value,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
                style = TextStyle(fontFeatureSettings = "tnum"),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun SyncSourceStatusRow(label: String, sourceState: JSONObject) {
    val result = sourceState.optJSONObject("last_result") ?: JSONObject()
    val status = result.optString("status")
    val cycle = sourceState.optString("last_cycle")
    val failures = sourceState.optInt("consecutive_failures", 0)
    val suspended = sourceState.optBoolean("suspended", false)
    val inProgress = sourceState.optBoolean("in_progress", false)
    val nextRetry = sourceState.optString("next_retry_at_utc")
    val (badge, badgeColor) = when {
        inProgress -> stringResource(R.string.sync_status_in_progress) to coolAccentColor()
        status == "synced" -> stringResource(R.string.sync_status_synced) to Color(0xFF2E8B72)
        status == "current" -> stringResource(R.string.sync_status_current) to Color(0xFF2E8B72)
        status == "failed" -> stringResource(R.string.sync_status_failed) to MaterialTheme.colorScheme.error
        status == "cancelled" -> stringResource(R.string.sync_status_cancelled) to MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
        else -> stringResource(R.string.sync_status_never) to MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
    }
    // 后台同步中时压制旧连续失败 / 退避冷却等过期告警信息，避免界面杂乱
    val notes = buildList {
        if (cycle.isNotEmpty()) add(stringResource(R.string.sync_last_cycle_fmt, cycle))
        if (!inProgress) {
            if (failures > 0) add(stringResource(R.string.sync_failures_fmt, failures))
            if (suspended) {
                if (nextRetry.isNotEmpty()) {
                    val formatted = runCatching {
                        val instant = java.time.Instant.parse(nextRetry)
                        java.time.format.DateTimeFormatter.ofPattern("MM-dd HH:mm")
                            .withZone(java.time.ZoneId.systemDefault())
                            .format(instant)
                    }.getOrDefault(nextRetry)
                    add(stringResource(R.string.sync_cooldown_retry_fmt, formatted))
                } else {
                    add(stringResource(R.string.sync_cooldown_manual_hint))
                }
            }
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(label, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.width(44.dp))
            if (inProgress) {
                Surface(
                    shape = CircleShape,
                    color = badgeColor.copy(alpha = 0.14f),
                    border = BorderStroke(0.75.dp, badgeColor.copy(alpha = 0.35f)),
                ) {
                    Row(
                        Modifier.padding(horizontal = 9.dp, vertical = 3.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(11.dp),
                            strokeWidth = 1.6.dp,
                            color = badgeColor,
                        )
                        Text(badge, fontSize = 11.sp, color = badgeColor, fontWeight = FontWeight.SemiBold)
                    }
                }
            } else {
                Text(badge, fontSize = 12.sp, color = badgeColor, fontWeight = FontWeight.Medium)
            }
        }
        if (notes.isNotEmpty()) {
            Text(
                notes.joinToString(" · "),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                modifier = Modifier.padding(start = 54.dp),
            )
        }
    }
}

@Composable
private fun camsStatusDescription(configuration: JSONObject?): String {
    configuration ?: return stringResource(R.string.cams_status_checking)
    return when (configuration.optString("state")) {
        "configured" -> stringResource(R.string.cams_status_configured)
        "wrong_endpoint" -> stringResource(R.string.cams_status_wrong_endpoint)
        "tls_disabled" -> stringResource(R.string.cams_status_tls_disabled)
        "unreadable" -> stringResource(R.string.cams_status_unreadable)
        else -> stringResource(R.string.cams_status_missing)
    }
}

@Composable
private fun EngineSection(store: WorkbenchStore, state: WorkbenchState) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        RemoteServerSection(store, state)

        SectionCaption(stringResource(R.string.section_engine))
        WorkSurface(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                SettingsIconRow(
                    icon = Icons.Filled.Memory,
                    tint = onlineColor(state),
                    title = if (state.online) stringResource(R.string.engine_connected) else stringResource(R.string.engine_offline),
                    subtitle = stringResource(R.string.protocol_note),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Box(Modifier.size(8.dp).background(onlineColor(state), CircleShape))
                        Text(
                            if (state.remoteWorkspace.isNotEmpty()) state.remoteWorkspace else "HTTP + SSE",
                            fontSize = 12.sp,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.weight(1f),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        SecondaryButton(
                            title = stringResource(R.string.reconnect),
                            enabled = !state.busy && state.serverConfigured,
                            icon = Icons.Filled.Refresh,
                            modifier = Modifier.width(126.dp),
                        ) { store.connect() }
                    }
                }
            }
        }
    }
}

@Composable
private fun RemoteServerSection(store: WorkbenchStore, state: WorkbenchState) {
    Section(
        title = stringResource(R.string.remote_server_title),
        icon = Icons.Filled.CloudQueue,
        iconTint = coolAccentColor(),
        trailing = {
            if (state.online) {
                SoftPillChip(
                    text = stringResource(R.string.remote_connected_badge),
                    tint = Color(0xFF2E8B72),
                    leadingDotColor = Color(0xFF2E8B72),
                )
            }
        },
    ) {
        RemoteServerConnectionForm(store, state)
    }
}

private fun onlineColor(state: WorkbenchState): Color =
    if (state.online) Color(0xFF3B9C57) else Color(0xFFC4574A)
