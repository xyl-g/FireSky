package com.firesky.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.selection.selectable
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.R
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore

/** 设置分类（macOS SettingsCategory 同构）：通用 / 数据 / 高级。
 *  中文值同时是状态键（state.settingsCategory），标题经资源映射显示。 */
private val SettingsCategories = listOf("通用", "数据", "高级")

/** 设置页（DialogViews.swift SettingsView 同构，v1.6.0 拆分类）：切换分类保留
 *  各表单草稿（全部常驻 store）；数据相关（下载源 / CAMS）统一归「数据」。 */
@Composable
fun SettingsScreen(store: WorkbenchStore, state: WorkbenchState) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(stringResource(R.string.settings_title), fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Segmented(
            options = SettingsCategories,
            titles = listOf(
                stringResource(R.string.settings_general),
                stringResource(R.string.settings_data),
                stringResource(R.string.settings_advanced),
            ),
            selection = state.settingsCategory,
            onSelect = { store.setSettingsCategory(it) },
        )
        when (state.settingsCategory) {
            "数据" -> {
                DataSourceSection(store, state)
                CamsSection(store, state)
            }
            "高级" -> EngineSection(store, state)
            else -> GeneralSection(store, state)
        }
        if (state.error.isNotEmpty()) {
            Text(state.error, fontSize = 11.sp, color = MaterialTheme.colorScheme.error)
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(title, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            content()
        }
    }
}

@Composable
private fun RadioRow(label: String, selected: Boolean, onSelect: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 48.dp)
            .selectable(selected = selected, role = Role.RadioButton, onClick = onSelect),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null)
        Spacer(Modifier.width(8.dp))
        Text(label, fontSize = 15.sp)
    }
}

/** 通用分类：外观（主题）+ 语言（v1.9.5 i18n）。 */
@Composable
private fun GeneralSection(store: WorkbenchStore, state: WorkbenchState) {
    val context = LocalContext.current
    Section(stringResource(R.string.section_appearance)) {
        Segmented(
            options = listOf("system", "light", "dark"),
            titles = listOf(stringResource(R.string.theme_system), stringResource(R.string.theme_light), stringResource(R.string.theme_dark)),
            selection = state.appearance,
            onSelect = store::setAppearance,
            accent = true,
        )
    }
    Section(stringResource(R.string.language_label)) {
        val current = L10n.language(context)
        RadioRow(stringResource(R.string.lang_system), current == "system") {
            L10n.setLanguage(context, "system")
        }
        RadioRow(stringResource(R.string.lang_chinese), current == "zh") {
            L10n.setLanguage(context, "zh")
        }
        RadioRow(stringResource(R.string.lang_english), current == "en") {
            L10n.setLanguage(context, "en")
        }
    }
}

@Composable
private fun DataSourceSection(store: WorkbenchStore, state: WorkbenchState) {
    Section(stringResource(R.string.section_datasource)) {
        RadioRow(stringResource(R.string.src_ecmwf), state.ecmwfSource == "ecmwf") {
            store.setEcmwfSource("ecmwf")
        }
        RadioRow(stringResource(R.string.src_aws), state.ecmwfSource == "aws") {
            store.setEcmwfSource("aws")
        }
        RadioRow(stringResource(R.string.src_google), state.ecmwfSource == "google") {
            store.setEcmwfSource("google")
        }
        Text(
            stringResource(R.string.datasource_note),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
    }
}

@Composable
private fun CamsSection(store: WorkbenchStore, state: WorkbenchState) {
    Section(stringResource(R.string.cams_section)) {
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
                Text(stringResource(R.string.recheck), fontSize = 10.sp)
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = state.camsKeyDraft,
                onValueChange = store::setCAMSKeyDraft,
                label = { Text(stringResource(R.string.cams_token_label), fontSize = 11.sp) },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                textStyle = MaterialTheme.typography.bodySmall,
                modifier = Modifier.weight(1f),
            )
            if (state.savingCAMS) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            }
            Button(
                onClick = { store.saveCAMSKey() },
                enabled = state.online && store.canSaveCAMS && !state.savingCAMS &&
                    state.camsKeyDraft.trim().isNotEmpty(),
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = Color.White,
                ),
                shape = RoundedCornerShape(8.dp),
            ) { Text(stringResource(R.string.save_key), fontSize = 11.sp) }
        }
        Text(
            stringResource(R.string.cams_note),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        if (state.camsConfiguration != null && !store.canSaveCAMS) {
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
                fontSize = 11.sp,
            )
        }
        if (state.showCAMSTutorial) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
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

@Composable
private fun TutorialStep(number: String, title: String, detail: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(
            number,
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        Column {
            Text(title, fontSize = 12.sp, fontWeight = FontWeight.Medium)
            Text(detail, fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        }
    }
}

@Composable
private fun EngineSection(store: WorkbenchStore, state: WorkbenchState) {
    Section(stringResource(R.string.section_engine)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.size(8.dp).background(onlineColor(state), CircleShape))
            Text(
                if (state.online) stringResource(R.string.engine_connected)
                else stringResource(R.string.engine_offline),
                fontSize = 12.sp,
            )
            Spacer(Modifier.weight(1f))
            OutlinedButton(onClick = { store.connect() }, enabled = !state.busy) {
                Text(stringResource(R.string.reconnect), fontSize = 11.sp)
            }
        }
        Text(
            stringResource(R.string.transport_label),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        Segmented(
            options = listOf("mock", "ffi"),
            titles = listOf(stringResource(R.string.transport_mock), stringResource(R.string.transport_ffi)),
            selection = state.transportMode,
            onSelect = { store.setTransportMode(it) },
        )
        Text(
            stringResource(R.string.engine_note),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
        Text(stringResource(R.string.data_dir_note), fontSize = 11.sp)
        Text(
            stringResource(R.string.protocol_note),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
    }
}

private fun onlineColor(state: WorkbenchState): Color =
    if (state.online) Color(0xFF3B9C57) else Color(0xFFC4574A)
