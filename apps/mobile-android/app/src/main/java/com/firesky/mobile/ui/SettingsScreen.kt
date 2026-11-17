package com.firesky.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore

/** 设置页（DialogViews.swift SettingsView 同构：外观 / 数据源 / CAMS / 本地引擎）。 */
@Composable
fun SettingsScreen(store: WorkbenchStore, state: WorkbenchState) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("设置", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        AppearanceSection(store, state)
        DataSourceSection(store, state)
        CamsSection(store, state)
        EngineSection(store, state)
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
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = onSelect)
        Spacer(Modifier.width(8.dp))
        Text(label, fontSize = 13.sp)
    }
}

@Composable
private fun AppearanceSection(store: WorkbenchStore, state: WorkbenchState) {
    Section("外观") {
        RadioRow("跟随系统", state.appearance == "system") { store.setAppearance("system") }
        RadioRow("浅色", state.appearance == "light") { store.setAppearance("light") }
        RadioRow("深色", state.appearance == "dark") { store.setAppearance("dark") }
    }
}

@Composable
private fun DataSourceSection(store: WorkbenchStore, state: WorkbenchState) {
    Section("数据源") {
        RadioRow("ECMWF 官方", state.ecmwfSource == "ecmwf") { store.setEcmwfSource("ecmwf") }
        RadioRow("AWS 欧洲镜像", state.ecmwfSource == "aws") { store.setEcmwfSource("aws") }
        RadioRow("Google Cloud 镜像", state.ecmwfSource == "google") { store.setEcmwfSource("google") }
        Text(
            "EC 下载源会同时用于数据准备和后续预测。GFS 使用 NOAA NOMADS，CAMS 使用 Copernicus ADS。",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
    }
}

@Composable
private fun CamsSection(store: WorkbenchStore, state: WorkbenchState) {
    Section("CAMS · Copernicus ADS") {
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
                Text("重新检查", fontSize = 10.sp)
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = state.camsKeyDraft,
                onValueChange = store::setCAMSKeyDraft,
                label = { Text("粘贴 ADS Personal Access Token", fontSize = 11.sp) },
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
            ) { Text("保存 Key", fontSize = 11.sp) }
        }
        Text(
            "Key 仅保存在本机 SharedPreferences（v1 骨架）；正式版写入沙箱私有配置文件（0600 权限，契约同桌面 ~/.cdsapirc）。",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        if (state.camsConfiguration != null && !store.canSaveCAMS) {
            Text(
                "环境变量正在覆盖配置。修改 CDSAPI_KEY / CDSAPI_URL 后需重新启动应用。",
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
            Text(if (state.showCAMSTutorial) "收起配置教程" else "配置教程", fontSize = 11.sp)
        }
        if (state.showCAMSTutorial) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                TutorialStep("1", "注册并登录 ADS", "使用 ECMWF 账号登录 Atmosphere Data Store。")
                TutorialStep("2", "复制 Personal Access Token", "在官方 API 配置页复制 key 后面的 Token。")
                TutorialStep("3", "接受 CAMS 数据集条款", "在数据集 Download 页底部接受 Terms of Use。")
                TutorialStep("4", "返回 FireSky 保存 Key", "粘贴到上方输入框并保存，再到准备面板下载数据。")
                Text(
                    "这里只检查本地配置是否完整；Token 是否有效在实际下载时验证。",
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
    Section("本地引擎") {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.size(8.dp).background(onlineColor(state), CircleShape))
            Text(if (state.online) "引擎已连接" else "引擎未连接", fontSize = 12.sp)
            Spacer(Modifier.weight(1f))
            OutlinedButton(onClick = { store.connect() }, enabled = !state.busy) { Text("重新连接", fontSize = 11.sp) }
        }
        Text("传输通道", fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        Segmented(
            options = listOf("mock", "ffi"),
            titles = listOf("Mock（进程内模拟）", "FFI engine-mobile（预留）"),
            selection = state.transportMode,
            onSelect = { store.setTransportMode(it) },
        )
        Text(
            "Mock：桌面 JSON-lines 协议在进程内模拟，供骨架全链路演示；FFI：经 engine-mobile（MN2）的 libengine_mobile.so 调用进程内引擎，当前未接入，选择后连接会显式失败。",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
        Text("数据目录：App 沙箱 filesDir（Android 不共享桌面工作区路径）", fontSize = 11.sp)
        Text(
            "协议：desktop JSON-lines v1（protocol=1），帧契约与桌面 firesky-engine serve 一致。",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
    }
}

private fun onlineColor(state: WorkbenchState): Color =
    if (state.online) Color(0xFF3B9C57) else Color(0xFFC4574A)
