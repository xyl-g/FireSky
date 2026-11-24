package com.firesky.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudQueue
import androidx.compose.material.icons.filled.Folder
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(stringResource(R.string.settings_title), fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Text(
                when (state.settingsCategory) {
                    "数据" -> stringResource(R.string.settings_data_subtitle)
                    "高级" -> stringResource(R.string.settings_advanced_subtitle)
                    else -> stringResource(R.string.settings_general_subtitle)
                },
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        }
        CapsuleCategoryBar(
            options = SettingsCategories,
            titles = listOf(
                stringResource(R.string.settings_general),
                stringResource(R.string.settings_data),
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
            "数据" -> {
                var sourcesY by remember { mutableIntStateOf(0) }
                var camsY by remember { mutableIntStateOf(0) }
                var syncY by remember { mutableIntStateOf(0) }
                var retentionY by remember { mutableIntStateOf(0) }

                Row(
                    Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    AnchorPillChip(stringResource(R.string.anchor_sources)) {
                        scope.launch { scrollState.animateScrollTo(sourcesY) }
                    }
                    AnchorPillChip(stringResource(R.string.anchor_cams)) {
                        scope.launch { scrollState.animateScrollTo(camsY) }
                    }
                    AnchorPillChip(stringResource(R.string.anchor_sync)) {
                        scope.launch { scrollState.animateScrollTo(syncY) }
                    }
                    AnchorPillChip(stringResource(R.string.anchor_retention)) {
                        scope.launch { scrollState.animateScrollTo(retentionY) }
                    }
                }
                Box(Modifier.onGloballyPositioned { sourcesY = it.positionInParent().y.roundToInt() }) {
                    DataSourceSection(store, state)
                }
                Box(Modifier.onGloballyPositioned { camsY = it.positionInParent().y.roundToInt() }) {
                    CamsSection(store, state)
                }
                Box(Modifier.onGloballyPositioned { syncY = it.positionInParent().y.roundToInt() }) {
                    DataSyncSection(store, state)
                }
                Box(Modifier.onGloballyPositioned { retentionY = it.positionInParent().y.roundToInt() }) {
                    RetentionSection(store, state)
                }
            }
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
private fun RadioRow(label: String, selected: Boolean, onSelect: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 44.dp)
            .selectable(selected = selected, role = Role.RadioButton, onClick = onSelect),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null)
        Spacer(Modifier.width(8.dp))
        Text(label, fontSize = 14.sp)
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
                    Text(stringResource(R.string.runtime_manage_data), fontSize = 12.sp)
                    Spacer(Modifier.width(4.dp))
                    Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = null, modifier = Modifier.size(14.dp))
                }
            }
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusSummaryBadge(
                        title = stringResource(if (state.isRemoteMode) R.string.runtime_remote_mac else R.string.runtime_engine),
                        value = if (state.online) stringResource(R.string.engine_connected) else stringResource(R.string.engine_offline),
                        healthy = state.online,
                        modifier = Modifier.weight(1f),
                    )
                    StatusSummaryBadge(
                        title = stringResource(R.string.runtime_data_sync),
                        value = if (state.dataSyncConfigured || store.camsConfigured) stringResource(R.string.runtime_configured)
                        else stringResource(R.string.runtime_on_demand),
                        healthy = state.dataSyncConfigured || store.camsConfigured,
                        modifier = Modifier.weight(1f),
                    )
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusSummaryBadge(
                        title = stringResource(R.string.runtime_cams),
                        value = if (store.camsConfigured) stringResource(R.string.runtime_configured)
                        else stringResource(R.string.runtime_not_configured),
                        healthy = store.camsConfigured,
                        modifier = Modifier.weight(1f),
                    )
                    StatusSummaryBadge(
                        title = stringResource(R.string.runtime_retention),
                        value = if (state.retentionConfigured) stringResource(R.string.runtime_active)
                        else stringResource(R.string.retention_unlimited),
                        healthy = state.retentionConfigured,
                        modifier = Modifier.weight(1f),
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

@Composable
private fun DataSourceSection(store: WorkbenchStore, state: WorkbenchState) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionCaption(stringResource(R.string.section_datasource))
        Section(
            title = stringResource(R.string.section_datasource),
            icon = Icons.Filled.CloudQueue,
            iconTint = coolAccentColor(),
        ) {
            Column {
                RadioRow(stringResource(R.string.src_ecmwf), state.ecmwfSource == "ecmwf") {
                    store.setEcmwfSource("ecmwf")
                }
                RadioRow(stringResource(R.string.src_aws), state.ecmwfSource == "aws") {
                    store.setEcmwfSource("aws")
                }
                RadioRow(stringResource(R.string.src_google), state.ecmwfSource == "google") {
                    store.setEcmwfSource("google")
                }
            }
            Text(
                stringResource(R.string.datasource_note),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        }
    }
}

@Composable
private fun CamsSection(store: WorkbenchStore, state: WorkbenchState) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionCaption(stringResource(R.string.cams_section))
        Section(
            title = stringResource(R.string.cams_section),
            icon = Icons.Filled.Key,
            iconTint = MaterialTheme.colorScheme.primary,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Icon(
                    Icons.Filled.CheckCircle,
                    contentDescription = null,
                    Modifier.size(18.dp),
                    tint = if (store.camsConfigured) Color(0xFF3B9C57)
                    else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f),
                )
                Text(store.camsConfigurationDescription, fontSize = 12.sp, modifier = Modifier.weight(1f))
                TextButton(onClick = { store.refreshCAMSConfiguration() }, enabled = state.online) {
                    Text(stringResource(R.string.recheck), fontSize = 11.sp)
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = state.camsKeyDraft,
                    onValueChange = store::setCAMSKeyDraft,
                    enabled = !state.isRemoteMode,
                    label = { Text(stringResource(R.string.cams_token_label), fontSize = 11.sp) },
                    singleLine = true,
                    shape = SoftInputShape,
                    colors = softInputColors(),
                    visualTransformation = PasswordVisualTransformation(),
                    textStyle = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.weight(1f),
                )
                if (state.savingCAMS) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                }
                Button(
                    onClick = { store.saveCAMSKey() },
                    enabled = !state.isRemoteMode && state.online && store.canSaveCAMS && !state.savingCAMS &&
                        state.camsKeyDraft.trim().isNotEmpty(),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.primary,
                        contentColor = actionForeground(),
                    ),
                    shape = RoundedCornerShape(12.dp),
                ) { Text(stringResource(R.string.save_key), fontSize = 12.sp) }
            }
            Text(
                stringResource(if (state.isRemoteMode) R.string.cams_remote_readonly_note else R.string.cams_note),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            if (!state.isRemoteMode && state.camsConfiguration != null && !store.canSaveCAMS) {
                Text(
                    stringResource(R.string.cams_env_override),
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            }
            if (state.camsMessage.isNotEmpty()) {
                Text(
                    state.camsMessage,
                    fontSize = 12.sp,
                    color = if (state.camsSaveFailed) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            }
            TextButton(onClick = { store.setShowCAMSTutorial(!state.showCAMSTutorial) }) {
                Text(
                    if (state.showCAMSTutorial) stringResource(R.string.cams_guide_hide)
                    else stringResource(R.string.cams_guide),
                    fontSize = 12.sp,
                )
            }
            if (state.showCAMSTutorial) {
                WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        TutorialStep("1", stringResource(R.string.cams_tut1_title), stringResource(R.string.cams_tut1_detail))
                        TutorialStep("2", stringResource(R.string.cams_tut2_title), stringResource(R.string.cams_tut2_detail))
                        TutorialStep("3", stringResource(R.string.cams_tut3_title), stringResource(R.string.cams_tut3_detail))
                        TutorialStep("4", stringResource(R.string.cams_tut4_title), stringResource(R.string.cams_tut4_detail))
                        Text(
                            stringResource(R.string.cams_check_note),
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                        )
                    }
                }
            }
        }
    }
}

/** 后台同步设置（5–1440 分钟周期，立即同步传 force=true）。 */
@Composable
private fun DataSyncSection(store: WorkbenchStore, state: WorkbenchState) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionCaption(stringResource(R.string.sync_section))
        Section(
            title = stringResource(R.string.sync_section),
            icon = Icons.Filled.Sync,
            iconTint = coolAccentColor(),
        ) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.sync_enable), fontSize = 13.sp, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
                Switch(checked = state.dataSyncEnabled, onCheckedChange = store::setDataSyncEnabled)
            }
            if (state.dataSyncEnabled && !state.dataSyncRetentionConfigured) {
                Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Icon(Icons.Filled.Warning, contentDescription = null, modifier = Modifier.size(15.dp), tint = Color(0xFFD98F24))
                    Text(stringResource(R.string.sync_retention_warning), fontSize = 11.sp, color = Color(0xFFD98F24))
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                SourceToggleChip("CAMS", state.dataSyncCAMS, state.dataSyncEnabled, store::setDataSyncCAMS, Modifier.weight(1f))
                SourceToggleChip("GFS", state.dataSyncGFS, state.dataSyncEnabled, store::setDataSyncGFS, Modifier.weight(1f))
                SourceToggleChip("EC", state.dataSyncEC, state.dataSyncEnabled, store::setDataSyncEC, Modifier.weight(1f))
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                NumericSettingField(
                    label = stringResource(R.string.sync_interval_label),
                    value = state.dataSyncInterval,
                    suffix = stringResource(R.string.sync_interval_unit),
                    placeholder = "15",
                    onValueChange = store::setDataSyncInterval,
                    modifier = Modifier.weight(1f),
                )
                NumericSettingField(
                    label = stringResource(R.string.sync_horizon_label),
                    value = state.dataSyncLeadHorizon,
                    suffix = stringResource(R.string.sync_horizon_unit),
                    placeholder = "72",
                    onValueChange = store::setDataSyncLeadHorizon,
                    modifier = Modifier.weight(1f),
                )
            }
            Text(
                stringResource(R.string.sync_note),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            if (state.dataSyncMessage.isNotEmpty()) {
                Text(
                    state.dataSyncMessage,
                    fontSize = 11.sp,
                    color = if (state.dataSyncSaveFailed) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.7f),
                )
            } else if (state.dataSyncConfigured) {
                Text(
                    stringResource(if (state.dataSyncEnabled) R.string.sync_configured_on else R.string.sync_configured_off),
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = { store.runDataSyncNow() },
                    enabled = state.online && !state.busy && !state.dataSyncRunning,
                    modifier = Modifier.weight(1f).height(42.dp),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Icon(Icons.Filled.Sync, contentDescription = null, modifier = Modifier.size(15.dp))
                    Spacer(Modifier.width(5.dp))
                    Text(stringResource(R.string.sync_now), fontSize = 12.sp)
                }
                Button(
                    onClick = { store.saveDataSyncSettings() },
                    enabled = state.online && !state.savingDataSync && !state.busy,
                    modifier = Modifier.weight(1f).height(42.dp),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text(stringResource(R.string.sync_save), fontSize = 12.sp)
                }
            }
        }
    }
}

@Composable
private fun SourceToggleChip(
    label: String,
    checked: Boolean,
    enabled: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        Checkbox(checked = checked, onCheckedChange = onCheckedChange, enabled = enabled)
        Text(label, fontSize = 12.sp)
    }
}

/** 存储与保留策略（含「立即按策略清理」调用 apply_retention_settings）。 */
@Composable
private fun RetentionSection(store: WorkbenchStore, state: WorkbenchState) {
    val activeRulesCount = listOf(
        state.retentionGFS, state.retentionECMWF, state.retentionCAMS,
        state.retentionSatelliteDays, state.retentionReportDays, state.retentionMapDays,
    ).count { it.trim().isNotEmpty() }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionCaption(stringResource(R.string.retention_section))
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
            Text(
                stringResource(R.string.retention_note),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            val unlimited = stringResource(R.string.retention_unlimited)
            val cyclesUnit = stringResource(R.string.retention_unit_cycles)
            val daysUnit = stringResource(R.string.retention_unit_days)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumericSettingField(stringResource(R.string.retention_gfs), state.retentionGFS, cyclesUnit, unlimited, store::setRetentionGFS, Modifier.weight(1f))
                NumericSettingField(stringResource(R.string.retention_ec), state.retentionECMWF, cyclesUnit, unlimited, store::setRetentionECMWF, Modifier.weight(1f))
                NumericSettingField(stringResource(R.string.retention_cams), state.retentionCAMS, cyclesUnit, unlimited, store::setRetentionCAMS, Modifier.weight(1f))
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                NumericSettingField(stringResource(R.string.retention_sat), state.retentionSatelliteDays, daysUnit, unlimited, store::setRetentionSatelliteDays, Modifier.weight(1f))
                NumericSettingField(stringResource(R.string.retention_reports), state.retentionReportDays, daysUnit, unlimited, store::setRetentionReportDays, Modifier.weight(1f))
                NumericSettingField(stringResource(R.string.retention_maps), state.retentionMapDays, daysUnit, unlimited, store::setRetentionMapDays, Modifier.weight(1f))
            }
            if (state.retentionMessage.isNotEmpty()) {
                Text(
                    state.retentionMessage,
                    fontSize = 11.sp,
                    color = if (state.retentionSaveFailed) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.7f),
                )
            } else if (state.retentionConfigured) {
                Text(
                    stringResource(R.string.retention_configured_note),
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = { store.applyRetentionNow() },
                    enabled = state.online && state.retentionConfigured && !state.busy && !state.savingRetention && !state.applyingRetention,
                    modifier = Modifier.weight(1f).height(42.dp),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    if (state.applyingRetention) {
                        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.5.dp)
                        Spacer(Modifier.width(5.dp))
                    } else {
                        Icon(Icons.Filled.AutoAwesome, contentDescription = null, modifier = Modifier.size(15.dp))
                        Spacer(Modifier.width(5.dp))
                    }
                    Text(stringResource(R.string.retention_clean_now), fontSize = 12.sp)
                }
                Button(
                    onClick = { store.saveRetentionSettings() },
                    enabled = state.online && !state.savingRetention && !state.applyingRetention && !state.busy,
                    modifier = Modifier.weight(1f).height(42.dp),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text(stringResource(R.string.retention_save), fontSize = 12.sp)
                }
            }
        }
    }
}

@Composable
private fun NumericSettingField(
    label: String,
    value: String,
    suffix: String,
    placeholder: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label, fontSize = 10.sp, maxLines = 1) },
        placeholder = { Text(placeholder, fontSize = 11.sp) },
        suffix = { Text(suffix, fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f)) },
        singleLine = true,
        shape = SoftInputShape,
        colors = softInputColors(),
        textStyle = MaterialTheme.typography.bodySmall.merge(TextStyle(fontFeatureSettings = "tnum")),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = modifier,
    )
}

@Composable
private fun TutorialStep(number: String, title: String, detail: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(
            number,
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.primary,
        )
        Column {
            Text(title, fontSize = 12.sp, fontWeight = FontWeight.Medium)
            Text(detail, fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        }
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
                            if (state.isRemoteMode) "REMOTE" else state.transportMode.uppercase(),
                            fontSize = 12.sp,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Spacer(Modifier.weight(1f))
                        OutlinedButton(
                            onClick = { store.connect() },
                            enabled = !state.busy,
                            shape = RoundedCornerShape(12.dp),
                        ) {
                            Icon(Icons.Filled.Refresh, contentDescription = null, modifier = Modifier.size(14.dp))
                            Spacer(Modifier.width(4.dp))
                            Text(stringResource(R.string.reconnect), fontSize = 11.sp)
                        }
                    }
                }
                IndentedDivider()
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        stringResource(R.string.transport_label),
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium,
                    )
                    Segmented(
                        options = listOf("ffi", "mock"),
                        titles = listOf(stringResource(R.string.transport_ffi), stringResource(R.string.transport_mock)),
                        selection = state.transportMode,
                        onSelect = { store.setTransportMode(it) },
                        accent = true,
                    )
                    Text(
                        stringResource(R.string.engine_note),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                }
                IndentedDivider()
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    IconBadge(Icons.Filled.Folder, tint = coolAccentColor())
                    Text(stringResource(R.string.data_dir_note), fontSize = 11.sp)
                }
            }
        }
    }
}

@Composable
private fun RemoteServerSection(store: WorkbenchStore, state: WorkbenchState) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionCaption(stringResource(R.string.remote_server_title))
        Section(
            title = stringResource(R.string.remote_server_title),
            icon = Icons.Filled.CloudQueue,
            iconTint = coolAccentColor(),
            trailing = {
                if (state.isRemoteMode && state.online) {
                    SoftPillChip(
                        text = stringResource(R.string.remote_connected_badge),
                        tint = Color(0xFF3B9C57),
                        leadingDotColor = Color(0xFF3B9C57),
                    )
                }
            },
        ) {
            Segmented(
                options = listOf("local", "remote"),
                titles = listOf(
                    stringResource(R.string.remote_mode_local),
                    stringResource(R.string.remote_mode_remote),
                ),
                selection = state.connectionModeDraft,
                onSelect = { mode ->
                    if (mode == "local") store.switchToLocalEngine()
                    else store.setConnectionModeDraft("remote")
                },
                accent = true,
            )
            OutlinedTextField(
                value = state.remoteServerUrl,
                onValueChange = store::setRemoteServerUrl,
                label = { Text(stringResource(R.string.remote_url_label), fontSize = 11.sp) },
                placeholder = { Text(stringResource(R.string.remote_url_placeholder), fontSize = 12.sp) },
                singleLine = true,
                shape = SoftInputShape,
                colors = softInputColors(),
                textStyle = MaterialTheme.typography.bodySmall,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = state.remoteTokenDraft,
                    onValueChange = store::setRemoteTokenDraft,
                    label = { Text(stringResource(R.string.remote_token_label), fontSize = 11.sp) },
                    placeholder = {
                        Text(
                            stringResource(
                                if (state.remoteTokenSaved) R.string.remote_token_saved_hint
                                else R.string.remote_token_hint,
                            ),
                            fontSize = 11.sp,
                        )
                    },
                    singleLine = true,
                    shape = SoftInputShape,
                    colors = softInputColors(),
                    visualTransformation = PasswordVisualTransformation(),
                    textStyle = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.weight(1f),
                )
                if (state.remoteTokenSaved) {
                    TextButton(onClick = { store.clearRemoteToken() }) {
                        Text(stringResource(R.string.remote_clear_token), fontSize = 11.sp)
                    }
                }
            }
            Text(
                stringResource(R.string.remote_pairing_hint),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            if (state.remoteTestMessage.isNotEmpty()) {
                Text(
                    state.remoteTestMessage,
                    fontSize = 11.sp,
                    color = if (state.remoteTestOk) Color(0xFF3B9C57) else MaterialTheme.colorScheme.error,
                )
            }
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedButton(
                    onClick = { store.testRemoteServer() },
                    enabled = !state.remoteTesting && state.remoteServerUrl.trim().isNotEmpty(),
                    modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                    shape = RoundedCornerShape(14.dp),
                ) {
                    if (state.remoteTesting) {
                        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.5.dp)
                        Spacer(Modifier.width(6.dp))
                    }
                    Text(stringResource(R.string.remote_test_connection), fontSize = 13.sp, maxLines = 1)
                }
                Box(Modifier.weight(1f)) {
                    AccentButton(
                        title = stringResource(R.string.remote_save_and_connect),
                        enabled = !state.remoteTesting && state.remoteServerUrl.trim().isNotEmpty(),
                    ) {
                        store.saveAndConnectRemoteServer()
                    }
                }
            }
        }
    }
}

private fun onlineColor(state: WorkbenchState): Color =
    if (state.online) Color(0xFF3B9C57) else Color(0xFFC4574A)
