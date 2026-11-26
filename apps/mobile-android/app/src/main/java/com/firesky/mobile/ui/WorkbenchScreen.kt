package com.firesky.mobile.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.CalendarToday
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.CloudQueue
import androidx.compose.material.icons.filled.ContentPaste
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.OpenInFull
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.SatelliteAlt
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material.icons.filled.WbTwilight
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.core.content.ContextCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.firesky.mobile.R
import com.firesky.mobile.store.UpdatePhase
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneOffset

/** 应用壳：底部导航（工作台 / 数据管理 / 任务历史 / 设置）+ 准备面板（preflight bottom sheet）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WorkbenchApp(store: WorkbenchStore) {
    val state by store.state.collectAsStateWithLifecycle()
    FireSkyTheme(state.appearance) {
        val dark = LocalFireSkyDark.current
        val navBg = if (dark) Color(0xFF0E1524).copy(alpha = 0.94f) else Color(0xFFF6F7FA).copy(alpha = 0.96f)
        val navDivider = if (dark) Color.White.copy(alpha = 0.08f) else Color.Black.copy(alpha = 0.07f)

        FireSkyBackdrop(Modifier.fillMaxSize()) {
            Scaffold(
                containerColor = Color.Transparent,
                contentColor = MaterialTheme.colorScheme.onBackground,
                topBar = {
                    if (state.page == "workbench") {
                        Box(
                            Modifier
                                .fillMaxWidth()
                                .statusBarsPadding()
                                .padding(horizontal = 16.dp, vertical = 8.dp),
                        ) {
                            HeaderLine(store, state)
                        }
                    }
                },
                bottomBar = {
                    Column(Modifier.background(navBg).navigationBarsPadding()) {
                        HorizontalDivider(thickness = 0.5.dp, color = navDivider)
                        if (state.page == "workbench" && !state.showPreflight && (state.serverConfigured || state.online)) {
                            Box(Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
                                RunButton(store, state)
                            }
                        }
                        NavigationBar(
                            containerColor = Color.Transparent,
                            tonalElevation = 0.dp,
                            windowInsets = WindowInsets(0.dp),
                        ) {
                            NavigationItem("workbench", stringResource(R.string.nav_point), Icons.Filled.Place, state.page) {
                                store.setPage("workbench")
                            }
                            NavigationItem("data", stringResource(R.string.nav_data), Icons.Filled.Storage, state.page) {
                                store.setPage("data")
                            }
                            NavigationItem("history", stringResource(R.string.nav_history), Icons.Filled.History, state.page) {
                                store.setPage("history")
                            }
                            NavigationItem(
                                "settings",
                                stringResource(R.string.nav_settings),
                                Icons.Filled.Settings,
                                state.page,
                                showBadge = state.updatePhase == UpdatePhase.AVAILABLE ||
                                    state.updatePhase == UpdatePhase.READY_TO_INSTALL,
                            ) {
                                store.setPage("settings")
                            }
                        }
                    }
                },
            ) { padding ->
                Box(Modifier.fillMaxSize().padding(padding)) {
                    when (state.page) {
                        "settings" -> SettingsScreen(store, state)
                        "data" -> DataScreen(store, state)
                        "history" -> HistoryScreen(store, state)
                        else -> WorkbenchScreen(store, state)
                    }
                    if (state.showPreflight) {
                        ModalBottomSheet(
                            onDismissRequest = {
                                // 语义对齐 iOS interactiveDismissDisabled(busy)：忙时禁止下滑关闭。
                                if (!state.checking && !state.busy) store.closePreflight()
                            },
                            containerColor = if (dark) Color(0xFF182132) else Color(0xFFFCFDFF),
                        ) {
                            PreflightSheet(store, state)
                        }
                    }
                    if (state.showCycleAgeConfirm) {
                        CycleAgeConfirmDialog(store, state)
                    }
                    if (state.showUpdateDialog) {
                        SoftwareUpdateDialog(store, state)
                    }
                }
            }
        }
    }
}

@Composable
private fun RowScope.NavigationItem(
    key: String,
    title: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    current: String,
    showBadge: Boolean = false,
    onClick: () -> Unit,
) {
    val selected = current == key
    NavigationBarItem(
        selected = selected,
        onClick = onClick,
        icon = {
            Box {
                Icon(icon, contentDescription = title)
                if (showBadge) {
                    Box(
                        Modifier
                            .align(Alignment.TopEnd)
                            .size(7.dp)
                            .background(MaterialTheme.colorScheme.primary, CircleShape),
                    )
                }
            }
        },
        label = {
            Text(
                title,
                fontSize = 11.sp,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        },
        colors = NavigationBarItemDefaults.colors(
            indicatorColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.14f),
            selectedIconColor = MaterialTheme.colorScheme.primary,
            selectedTextColor = MaterialTheme.colorScheme.primary,
            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.72f),
            unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.72f),
        ),
    )
}

// ---------------------------------------------------------------- 工作台主面

/** 工作台主面：远程服务器引导 + 三模式（单点预测 / 区域预测 / 卫星实况）切换 + 参数 → 运行 → 结果。 */
@Composable
fun WorkbenchScreen(store: WorkbenchStore, state: WorkbenchState) {
    val pageError = state.errorText("workbench")
    var showMapPicker by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp)
            .padding(top = 6.dp, bottom = 18.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (!state.serverConfigured && !state.online) {
            RemoteServerOnboardingCard(store, state)
            FirstRunCapabilitiesStrip()
        } else {
            if (!state.online) {
                OfflineBanner(state, onOpenSettings = {
                    store.setSettingsCategory("高级")
                    store.setPage("settings")
                }) { store.connect() }
            }
            if (state.online && pageError.isNotEmpty()) {
                DismissibleErrorBanner(pageError, onDismiss = { store.clearError() })
            }

            CapsuleCategoryBar(
                options = listOf("point", "regional", "nowcast"),
                titles = listOf(
                    stringResource(R.string.mode_point),
                    stringResource(R.string.mode_regional),
                    stringResource(R.string.mode_nowcast),
                ),
                icons = listOf(Icons.Filled.Place, Icons.Filled.Map, Icons.Filled.SatelliteAlt),
                selection = state.workbenchMode,
                onSelect = { store.setWorkbenchMode(it) },
            )

            if (state.workbenchMode == "nowcast") {
                NowcastGuidanceBanner()
            }

            if (state.workbenchMode == "regional") {
                RegionalControlsCard(store, state)
            } else {
                LocationCard(store, state, onOpenMapPicker = { showMapPicker = true })
            }

            ParametersCard(store, state, showCoordinatesEditor = state.workbenchMode != "regional")

            if (state.busy && !state.showPreflight) ProgressRow(store, state)

            when (state.workbenchMode) {
                "regional" -> RegionalResultSection(store, state)
                "nowcast" -> NowcastResultSection(store, state)
                else -> ResultSection(store, state)
            }
        }
    }

    if (showMapPicker) {
        MapPickerDialog(store, state, onDismiss = { showMapPicker = false })
    }
}

@Composable
private fun FirstRunCapabilitiesStrip() {
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                stringResource(R.string.onboarding_features_caption),
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
            )
            OnboardingFeatureRow(
                icon = Icons.Filled.WbTwilight,
                tint = MaterialTheme.colorScheme.primary,
                title = stringResource(R.string.empty_feature_models_title),
                detail = stringResource(R.string.empty_feature_models_desc),
            )
            HorizontalDivider(
                Modifier.padding(start = 40.dp),
                thickness = 0.5.dp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f),
            )
            OnboardingFeatureRow(
                icon = Icons.Filled.Map,
                tint = coolAccentColor(),
                title = stringResource(R.string.mode_regional),
                detail = stringResource(R.string.regional_empty_body),
            )
            HorizontalDivider(
                Modifier.padding(start = 40.dp),
                thickness = 0.5.dp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f),
            )
            OnboardingFeatureRow(
                icon = Icons.Filled.SatelliteAlt,
                tint = Teal,
                title = stringResource(R.string.mode_nowcast),
                detail = stringResource(R.string.nowcast_empty_body),
            )
        }
    }
}

@Composable
private fun OnboardingFeatureRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    tint: Color,
    title: String,
    detail: String,
) {
    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        IconBadge(icon, tint = tint)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Text(
                detail,
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
            )
        }
    }
}

@Composable
private fun RemoteServerOnboardingCard(store: WorkbenchStore, state: WorkbenchState) {
    WorkSurface(Modifier.fillMaxWidth(), emphasized = true) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Column(
                Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FireSkyMarkScene(size = 72.dp)
                Text(
                    stringResource(R.string.onboarding_connect_title),
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center,
                )
                Text(
                    stringResource(R.string.onboarding_connect_subtitle),
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.68f),
                    textAlign = TextAlign.Center,
                )
            }
            RemoteServerConnectionForm(store, state)
        }
    }
}

/** 共享远程服务器表单（首次引导卡与设置页共用）。 */
@Composable
fun RemoteServerConnectionForm(store: WorkbenchStore, state: WorkbenchState) {
    val labelColor = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.72f)
    val context = LocalContext.current
    val clipboardManager = LocalClipboardManager.current
    val qrPrompt = stringResource(R.string.remote_qr_prompt)
    val cameraDeniedMsg = stringResource(R.string.remote_camera_denied)
    val clipboardEmptyMsg = stringResource(R.string.remote_clipboard_empty)
    val pairingInvalidMsg = stringResource(R.string.remote_pairing_invalid)

    val scanLauncher = rememberLauncherForActivityResult(ScanContract()) { result ->
        val contents = result.contents?.trim().orEmpty()
        if (contents.isNotEmpty()) {
            store.applyPairingUri(contents, invalidMessage = pairingInvalidMsg)
        }
    }

    fun startQrScan() {
        val options = ScanOptions().apply {
            setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            setPrompt(qrPrompt)
            setBeepEnabled(false)
            setOrientationLocked(false)
        }
        scanLauncher.launch(options)
    }

    val cameraPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startQrScan()
        } else {
            store.reportRemotePairingError(cameraDeniedMsg)
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SecondaryButton(
                title = stringResource(R.string.remote_scan_qr),
                icon = Icons.Filled.QrCodeScanner,
                enabled = !state.remoteTesting,
                modifier = Modifier.weight(1f),
            ) {
                if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                    PackageManager.PERMISSION_GRANTED
                ) {
                    startQrScan()
                } else {
                    cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
                }
            }
            SecondaryButton(
                title = stringResource(R.string.remote_paste_pairing),
                icon = Icons.Filled.ContentPaste,
                enabled = !state.remoteTesting,
                modifier = Modifier.weight(1f),
            ) {
                val clip = clipboardManager.getText()?.text?.trim().orEmpty()
                if (clip.isEmpty()) {
                    store.reportRemotePairingError(clipboardEmptyMsg)
                } else {
                    store.applyPairingUri(clip, invalidMessage = pairingInvalidMsg)
                }
            }
        }
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(stringResource(R.string.remote_url_label), fontSize = 12.sp, fontWeight = FontWeight.Medium, color = labelColor)
            OutlinedTextField(
                value = state.remoteServerUrl,
                onValueChange = store::setRemoteServerUrl,
                placeholder = {
                    Text(
                        stringResource(R.string.remote_url_placeholder),
                        fontSize = 12.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                singleLine = true,
                shape = SoftInputShape,
                colors = softInputColors(),
                textStyle = MaterialTheme.typography.bodySmall,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(stringResource(R.string.remote_token_label), fontSize = 12.sp, fontWeight = FontWeight.Medium, color = labelColor)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = state.remoteTokenDraft,
                    onValueChange = store::setRemoteTokenDraft,
                    placeholder = {
                        Text(
                            stringResource(
                                if (state.remoteTokenSaved) R.string.remote_token_saved_hint
                                else R.string.remote_token_hint,
                            ),
                            fontSize = 12.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
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
        }
        WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 10.dp) {
            Text(
                stringResource(R.string.remote_pairing_hint),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.68f),
                modifier = Modifier.padding(11.dp),
            )
        }
        if (state.remoteTestMessage.isNotEmpty()) {
            Text(
                state.remoteTestMessage,
                fontSize = 12.sp,
                color = if (state.remoteTestOk) Color(0xFF2E8B72) else MaterialTheme.colorScheme.error,
            )
        }
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SecondaryButton(
                title = stringResource(R.string.remote_test_connection),
                enabled = !state.remoteTesting && state.remoteServerUrl.trim().isNotEmpty(),
                loading = state.remoteTesting,
                modifier = Modifier.weight(1f),
            ) {
                store.testRemoteServer()
            }
            AccentButton(
                title = stringResource(R.string.remote_save_and_connect),
                enabled = !state.remoteTesting && state.remoteServerUrl.trim().isNotEmpty(),
                modifier = Modifier.weight(1f),
            ) {
                store.saveAndConnectRemoteServer()
            }
        }
    }
}

@Composable
private fun HeaderLine(store: WorkbenchStore, state: WorkbenchState) {
    val loc = state.location
    val titleRes = when (state.workbenchMode) {
        "regional" -> R.string.mode_regional
        "nowcast" -> R.string.mode_nowcast
        else -> R.string.point_title
    }
    val subtitle = when (state.workbenchMode) {
        "regional" -> "${state.selectedRegionPreset.label} · ${state.regionalModel.uppercase()} · ${state.targetDate}"
        else -> "%s · %.4f° N, %.4f° E".format(loc.name, loc.lat, loc.lon)
    }
    val dark = LocalFireSkyDark.current
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        FireSkyMark(Modifier.size(34.dp))
        Column(Modifier.weight(1f)) {
            Text(
                stringResource(titleRes),
                fontSize = 19.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onBackground,
            )
            Text(
                subtitle,
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        val statusColor = when {
            state.online -> Color(0xFF2E8B72)
            state.serverConfigured -> Color(0xFFD98F24)
            dark -> Color(0xFF94A3B8)
            else -> Color(0xFF5A6B82)
        }
        Box(
            Modifier.clip(CircleShape).clickable {
                store.setSettingsCategory("高级")
                store.setPage("settings")
            },
        ) {
            SoftPillChip(
                text = stringResource(
                    if (state.online) R.string.remote_connected_badge
                    else if (state.serverConfigured) R.string.remote_mac_badge
                    else R.string.remote_unconfigured_badge,
                ),
                tint = statusColor,
                leadingDotColor = statusColor,
            )
        }
    }
}

@Composable
private fun OfflineBanner(
    state: WorkbenchState,
    onOpenSettings: () -> Unit,
    onReconnect: () -> Unit,
) {
    val dark = LocalFireSkyDark.current
    val errorColor = MaterialTheme.colorScheme.error
    val pillBg = if (dark) Color.White.copy(alpha = 0.06f) else Color.White.copy(alpha = 0.72f)
    val pillBorder = errorColor.copy(alpha = 0.28f)

    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        color = errorColor.copy(alpha = if (dark) 0.14f else 0.08f),
        border = BorderStroke(0.75.dp, errorColor.copy(alpha = 0.30f)),
    ) {
        Column(
            Modifier.padding(horizontal = 14.dp, vertical = 11.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                IconBadge(Icons.Filled.Warning, tint = errorColor)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(
                        stringResource(R.string.engine_offline),
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = errorColor,
                    )
                    if (state.error.isNotEmpty()) {
                        Text(
                            state.error,
                            fontSize = 11.sp,
                            lineHeight = 15.sp,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.70f),
                        )
                    }
                }
            }
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Surface(
                    onClick = onOpenSettings,
                    shape = CircleShape,
                    color = pillBg,
                    border = BorderStroke(0.75.dp, pillBorder),
                ) {
                    Text(
                        stringResource(R.string.nav_settings),
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                    )
                }
                Spacer(Modifier.width(8.dp))
                Surface(
                    onClick = onReconnect,
                    shape = CircleShape,
                    color = MaterialTheme.colorScheme.primary.copy(alpha = if (dark) 0.22f else 0.13f),
                    border = BorderStroke(0.75.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.35f)),
                ) {
                    Text(
                        stringResource(R.string.reconnect),
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------- 参数表单

/** 地图优先观测点卡（搜索 + GPS + 内嵌交互地图 + 全屏选点 + 收藏快捷切换）。 */
@Composable
private fun LocationCard(
    store: WorkbenchStore,
    state: WorkbenchState,
    onOpenMapPicker: () -> Unit,
) {
    val loc = state.location
    val dark = LocalFireSkyDark.current
    val groupedFill = if (dark) Color(0xFF202A3D) else Color(0xFFF3F6FA)
    val groupedBorder = if (dark) Color.White.copy(alpha = 0.09f) else Color(0xFFD8E1EA).copy(alpha = 0.85f)
    val overlayBg = if (dark) Color(0xFF121B2E).copy(alpha = 0.88f) else Color(0xFFFCFDFF).copy(alpha = 0.92f)
    val overlayBorder = if (dark) Color.White.copy(alpha = 0.12f) else Color(0xFFC9D5E1).copy(alpha = 0.78f)

    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                IconBadge(Icons.Filled.Place)
                Text(
                    stringResource(R.string.obs_point),
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                Surface(
                    onClick = onOpenMapPicker,
                    shape = CircleShape,
                    color = groupedFill,
                    border = BorderStroke(0.75.dp, groupedBorder),
                ) {
                    Row(
                        Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(5.dp),
                    ) {
                        Icon(
                            Icons.Filled.OpenInFull,
                            contentDescription = null,
                            modifier = Modifier.size(13.dp),
                            tint = coolAccentColor(),
                        )
                        Text(
                            stringResource(R.string.map_expand),
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
            }

            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = state.query,
                    onValueChange = store::setQuery,
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                    shape = RoundedCornerShape(14.dp),
                    colors = softInputColors(),
                    leadingIcon = {
                        Icon(
                            Icons.Filled.Search,
                            contentDescription = null,
                            modifier = Modifier.size(17.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                        )
                    },
                    trailingIcon = {
                        if (state.query.isNotEmpty()) {
                            IconButton(onClick = { store.setQuery("") }) {
                                Icon(
                                    Icons.Filled.Close,
                                    contentDescription = stringResource(R.string.dismiss),
                                    modifier = Modifier.size(16.dp),
                                )
                            }
                        } else {
                            IconButton(onClick = { store.search() }) {
                                Icon(
                                    Icons.Filled.Search,
                                    contentDescription = stringResource(R.string.cd_search),
                                    modifier = Modifier.size(17.dp),
                                    tint = MaterialTheme.colorScheme.primary,
                                )
                            }
                        }
                    },
                    placeholder = {
                        Text(
                            stringResource(R.string.search_placeholder),
                            fontSize = 12.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    },
                    textStyle = MaterialTheme.typography.bodySmall,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { store.search() }),
                )
                Surface(
                    onClick = { store.locate() },
                    enabled = !state.busy,
                    shape = RoundedCornerShape(14.dp),
                    color = groupedFill,
                    border = BorderStroke(0.75.dp, groupedBorder),
                    modifier = Modifier.size(48.dp),
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
            }

            Box(
                Modifier
                    .fillMaxWidth()
                    .height(228.dp)
                    .clip(RoundedCornerShape(18.dp))
                    .border(0.75.dp, groupedBorder, RoundedCornerShape(18.dp)),
            ) {
                PointLocationMap(
                    lat = loc.lat,
                    lon = loc.lon,
                    dark = dark,
                    zoom = 11,
                    modifier = Modifier.fillMaxSize(),
                    onSelectPoint = { tapLat, tapLon -> store.selectPoint(tapLat, tapLon) },
                )

                Surface(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .fillMaxWidth()
                        .padding(8.dp),
                    shape = RoundedCornerShape(14.dp),
                    color = overlayBg,
                    border = BorderStroke(0.75.dp, overlayBorder),
                ) {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Box(Modifier.size(7.dp).background(MaterialTheme.colorScheme.primary, CircleShape))
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(1.dp)) {
                            Text(
                                loc.name,
                                fontSize = 13.sp,
                                fontWeight = FontWeight.SemiBold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                "%.2f°N, %.2f°E · %s".format(
                                    loc.lat,
                                    loc.lon,
                                    if (loc.metadataReady) stringResource(R.string.elevation_m, loc.elevation)
                                    else stringResource(R.string.elevation_unconfirmed),
                                ),
                                fontSize = 10.sp,
                                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.68f),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                        Surface(
                            onClick = { store.starLocation() },
                            shape = CircleShape,
                            color = MaterialTheme.colorScheme.primary.copy(alpha = if (dark) 0.20f else 0.12f),
                            modifier = Modifier.size(32.dp),
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Icon(
                                    Icons.Filled.Star,
                                    contentDescription = stringResource(R.string.star_location),
                                    modifier = Modifier.size(16.dp),
                                    tint = MaterialTheme.colorScheme.primary,
                                )
                            }
                        }
                    }
                }
            }

            if (state.favorites.isNotEmpty()) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    state.favorites.forEachIndexed { index, favorite ->
                        val favName = favorite.optString("name", stringResource(R.string.favorite_name))
                        val isCurrent = favName == loc.name
                        Surface(
                            onClick = { store.applyFavorite(index) },
                            shape = CircleShape,
                            color = if (isCurrent) MaterialTheme.colorScheme.primary.copy(alpha = if (dark) 0.20f else 0.12f)
                            else groupedFill,
                            border = BorderStroke(
                                0.75.dp,
                                if (isCurrent) MaterialTheme.colorScheme.primary.copy(alpha = 0.35f) else groupedBorder,
                            ),
                        ) {
                            Row(
                                Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(5.dp),
                            ) {
                                Icon(
                                    Icons.Filled.Place,
                                    contentDescription = null,
                                    Modifier.size(12.dp),
                                    tint = if (isCurrent) MaterialTheme.colorScheme.primary
                                    else MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Text(
                                    favName,
                                    fontSize = 11.sp,
                                    fontWeight = if (isCurrent) FontWeight.SemiBold else FontWeight.Normal,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

/** 紧凑观测参数卡（日期与霞相紧凑排布，尽量减少首屏高度占用）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ParametersCard(
    store: WorkbenchStore,
    state: WorkbenchState,
    showCoordinatesEditor: Boolean = true,
) {
    var showDatePicker by remember { mutableStateOf(false) }
    val pickerState = androidx.compose.material3.rememberDatePickerState(
        initialSelectedDateMillis = state.targetDate.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
    )
    val dark = LocalFireSkyDark.current
    val groupedFill = if (dark) Color(0xFF202A3D) else Color(0xFFF3F6FA)
    val groupedBorder = if (dark) Color(0xFF50617B).copy(alpha = 0.45f) else Color(0xFFD8E1EA).copy(alpha = 0.85f)

    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    IconBadge(Icons.Filled.Tune, tint = coolAccentColor())
                    Text(stringResource(R.string.obs_params), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                }
                TextButton(
                    onClick = { store.resetParameters() },
                    modifier = Modifier.heightIn(min = 28.dp),
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp),
                ) {
                    Text(stringResource(R.string.reset), fontSize = 11.sp)
                }
            }
            Segmented(
                options = listOf("今天", "明天", "自定义"),
                titles = listOf(
                    stringResource(R.string.date_today),
                    stringResource(R.string.date_tomorrow),
                    stringResource(R.string.date_custom),
                ),
                selection = state.dateTab,
                onSelect = { store.chooseDate(it) },
            )
            if (state.dateTab == "自定义") {
                Surface(
                    onClick = { showDatePicker = true },
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp),
                    color = groupedFill,
                    border = BorderStroke(0.75.dp, groupedBorder),
                ) {
                    Row(
                        Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.Center,
                    ) {
                        Icon(
                            Icons.Filled.CalendarToday,
                            contentDescription = null,
                            modifier = Modifier.size(14.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                        Spacer(Modifier.width(7.dp))
                        Text(
                            state.targetDate.toString(),
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
            }
            Segmented(
                options = listOf("dawn", "dusk"),
                titles = listOf(
                    stringResource(R.string.phenomenon_dawn),
                    stringResource(R.string.phenomenon_dusk),
                ),
                selection = state.phenomenon,
                onSelect = { store.setPhenomenon(it) },
                accent = true,
            )
            if (showCoordinatesEditor) {
                TextButton(
                    onClick = { store.setEditingCoordinates(!state.editingCoordinates) },
                    modifier = Modifier.fillMaxWidth().heightIn(min = 32.dp),
                ) {
                    Text(
                        if (state.editingCoordinates) stringResource(R.string.collapse_coordinates)
                        else stringResource(R.string.edit_coordinates),
                        fontSize = 11.sp,
                    )
                }
                AnimatedVisibility(visible = state.editingCoordinates) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = state.location.name,
                        onValueChange = { value -> store.editLocation { it.copy(name = value) } },
                        label = { Text(stringResource(R.string.name_label)) },
                        singleLine = true,
                        shape = SoftInputShape,
                        colors = softInputColors(),
                        textStyle = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    NumberField(stringResource(R.string.lat_label), state.location.lat) { value ->
                        store.editLocation { it.copy(lat = value, metadataReady = false) }
                    }
                    NumberField(stringResource(R.string.lon_label), state.location.lon) { value ->
                        store.editLocation { it.copy(lon = value, metadataReady = false) }
                    }
                    NumberField(stringResource(R.string.elevation_label), state.location.elevation) { value ->
                        store.editLocation { it.copy(elevation = value) }
                    }
                    OutlinedTextField(
                        value = state.location.timezone,
                        onValueChange = { value -> store.editLocation { it.copy(timezone = value) } },
                        label = { Text(stringResource(R.string.tz_label)) },
                        singleLine = true,
                        shape = SoftInputShape,
                        colors = softInputColors(),
                        textStyle = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    TextButton(onClick = { store.lookupMetadata() }) { Text(stringResource(R.string.fetch_metadata), fontSize = 12.sp) }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        androidx.compose.material3.Checkbox(
                            checked = state.location.metadataReady,
                            onCheckedChange = { store.setMetadataReady(it) },
                        )
                        Text(stringResource(R.string.location_confirmed), fontSize = 11.sp)
                    }
                }
            }
            }
        }
    }
    if (showDatePicker) {
        DatePickerDialog(
            onDismissRequest = { showDatePicker = false },
            confirmButton = {
                TextButton(onClick = {
                    val millis = pickerState.selectedDateMillis
                    if (millis != null) {
                        store.setCustomDate(Instant.ofEpochMilli(millis).atZone(ZoneOffset.UTC).toLocalDate())
                    }
                    showDatePicker = false
                }) { Text(stringResource(R.string.ok)) }
            },
            dismissButton = { TextButton(onClick = { showDatePicker = false }) { Text(stringResource(R.string.cancel)) } },
        ) {
            DatePicker(state = pickerState)
        }
    }
}

@Composable
private fun NumberField(label: String, value: Double, onChange: (Double) -> Unit) {
    OutlinedTextField(
        value = if (value == 0.0) "0" else value.toString(),
        onValueChange = { text -> text.toDoubleOrNull()?.let(onChange) },
        label = { Text(label) },
        singleLine = true,
        shape = SoftInputShape,
        colors = softInputColors(),
        textStyle = MaterialTheme.typography.bodySmall,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        modifier = Modifier.fillMaxWidth(),
    )
}

// ---------------------------------------------------------------- 运行与进度

@Composable
private fun RunButton(store: WorkbenchStore, state: WorkbenchState) {
    val labelRes = when (state.workbenchMode) {
        "regional" -> R.string.start_regional_forecast
        "nowcast" -> R.string.start_nowcast_analysis
        else -> R.string.start_forecast
    }
    AccentButton(
        title = stringResource(labelRes),
        enabled = !state.busy && state.online,
        icon = Icons.Filled.AutoAwesome,
        indicatorDot = state.resultParametersStale,
    ) { store.beginCheck() }
}

@Composable
private fun ProgressRow(store: WorkbenchStore, state: WorkbenchState) {
    WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            LinearProgressIndicator(
                progress = { (state.percent / 100).toFloat() },
                modifier = Modifier.fillMaxWidth().clip(CircleShape),
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    state.status,
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    modifier = Modifier.weight(1f),
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                IconButton(onClick = { store.cancel() }, modifier = Modifier.size(28.dp)) {
                    Icon(Icons.Filled.Close, contentDescription = stringResource(R.string.cd_cancel_task), Modifier.size(14.dp))
                }
            }
        }
    }
}

// ---------------------------------------------------------------- 通用小件

/** WorkSegments 同构：紧凑流线型分段选择器（40dp 高，圆角 12dp 滑轨 + 9dp 悬浮胶囊）。 */
@Composable
fun Segmented(
    options: List<String>,
    titles: List<String>? = null,
    selection: String,
    onSelect: (String) -> Unit,
    accent: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val dark = LocalFireSkyDark.current
    val trackFill = if (dark) Color(0xFF161E2E) else Color(0xFFEAF0F6)
    val trackBorder = if (dark) Color(0xFF50617B).copy(alpha = 0.42f) else Color(0xFFD8E1EA).copy(alpha = 0.80f)
    val pillShape = RoundedCornerShape(9.dp)

    Row(
        modifier
            .fillMaxWidth()
            .heightIn(min = 40.dp)
            .selectableGroup()
            .background(trackFill, RoundedCornerShape(12.dp))
            .border(0.75.dp, trackBorder, RoundedCornerShape(12.dp))
            .padding(3.dp),
        horizontalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        options.forEachIndexed { index, option ->
            val selected = selection == option
            val pillFill = when {
                selected && accent && dark -> MaterialTheme.colorScheme.primary.copy(alpha = 0.22f)
                selected && dark -> Color.White.copy(alpha = 0.13f)
                selected -> Color.White.copy(alpha = 0.96f)
                else -> Color.Transparent
            }
            val pillShadow = if (selected && !dark) {
                Modifier.shadow(
                    elevation = 1.dp,
                    shape = pillShape,
                    clip = false,
                    ambientColor = Color(0xFF2A3C58).copy(alpha = 0.14f),
                    spotColor = Color(0xFF2A3C58).copy(alpha = 0.10f),
                )
            } else {
                Modifier
            }
            Box(
                Modifier
                    .weight(1f)
                    .heightIn(min = 34.dp)
                    .then(pillShadow)
                    .clip(pillShape)
                    .background(pillFill, pillShape)
                    .selectable(selected = selected, role = Role.Tab, onClick = { onSelect(option) })
                    .padding(horizontal = 6.dp, vertical = 7.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    titles?.getOrNull(index) ?: option,
                    fontSize = 12.sp,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                    color = when {
                        selected && accent -> MaterialTheme.colorScheme.primary
                        selected -> MaterialTheme.colorScheme.onSurface
                        else -> MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.72f)
                    },
                )
            }
        }
    }
}

@Composable
fun ChipText(text: String, modifier: Modifier = Modifier) {
    SoftPillChip(text = text, modifier = modifier)
}

/** 模型结果状态行（comparison.status 同构）。 */
@Composable
fun comparisonStatus(result: JSONObject?, model: String): String {
    if (result == null) return stringResource(R.string.awaiting_forecast)
    if (modelError(result, model) != null) return stringResource(R.string.status_incomplete)
    return if (modelReport(result, model) != null) stringResource(R.string.status_complete)
    else stringResource(R.string.status_no_result)
}

// ---------------------------------------------------------------- 准备面板（preflight）

@Composable
fun PreflightSheet(store: WorkbenchStore, state: WorkbenchState) {
    val maxHeight = LocalConfiguration.current.screenHeightDp.dp * 0.85f
    Column(Modifier.fillMaxWidth().heightIn(max = maxHeight).padding(horizontal = 20.dp).padding(bottom = 24.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.preflight_title), fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
                Text(
                    state.location.name,
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            }
            if (!state.busy) {
                IconButton(onClick = { store.refreshCheck() }) {
                    Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.cd_recheck))
                }
            }
        }
        Spacer(Modifier.height(16.dp))
        Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState())) {
            if (state.rows.isEmpty()) {
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 40.dp),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(12.dp))
                    Text(stringResource(R.string.checking_data), fontSize = 13.sp)
                }
            } else {
                WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 14.dp) {
                    Column(Modifier.padding(horizontal = 12.dp)) {
                        state.rows.forEachIndexed { index, row ->
                            Row(
                                Modifier.fillMaxWidth().padding(vertical = 10.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Box(
                                    Modifier.size(32.dp).background(
                                        MaterialTheme.colorScheme.primary.copy(alpha = 0.12f),
                                        RoundedCornerShape(8.dp),
                                    ),
                                    contentAlignment = Alignment.Center,
                                ) {
                                    Text(
                                        row.title.take(2),
                                        fontSize = 11.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        color = MaterialTheme.colorScheme.primary,
                                    )
                                }
                                Spacer(Modifier.width(12.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(row.title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                                    Text(
                                        row.detail,
                                        fontSize = 11.sp,
                                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                                    )
                                }
                                StatusChip(
                                    state = if (!row.ready) "pending" else if (row.old) "interrupted" else "done",
                                    title = statusText(row.status),
                                )
                            }
                            if (index < state.rows.size - 1) {
                                HorizontalDivider(
                                    Modifier.padding(start = 44.dp),
                                    thickness = 0.5.dp,
                                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.10f),
                                )
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
            Column {
                when {
                    state.checking -> {
                        LinearProgressIndicator(
                            progress = { (state.percent / 100).toFloat() },
                            modifier = Modifier.fillMaxWidth().clip(CircleShape),
                        )
                        Text(
                            state.status,
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                            modifier = Modifier.padding(top = 6.dp),
                        )
                    }
                    state.errorText("workbench").isNotEmpty() -> DismissibleErrorBanner(
                        message = state.errorText("workbench"),
                        onDismiss = { store.clearError() },
                    )
                    else -> Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            preflightSummary(state, state.status),
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Medium,
                            modifier = Modifier.weight(1f),
                        )
                        if (state.publicationsPending) {
                            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.5.dp)
                        }
                    }
                }
            }
        }
        Spacer(Modifier.height(14.dp))
        AccentButton(
            title = stringResource(if (state.ready) R.string.confirm_forecast else R.string.download_missing),
            enabled = state.session != null && !state.busy,
            icon = if (state.ready) Icons.Filled.AutoAwesome else Icons.Filled.CloudDownload,
        ) { if (state.ready) store.confirm() else store.prepare() }
        if (state.ready) {
            TextButton(
                onClick = { store.prepare() },
                enabled = state.session != null && !state.busy,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(stringResource(R.string.update_data))
            }
        }
        TextButton(onClick = { store.closePreflight() }, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.cancel))
        }
    }
}

@Composable
private fun preflightSummary(state: WorkbenchState, status: String): String {
    // map（inline）内调用 @Composable statusText 合法；joinToString 非内联不行。
    val missing = state.rows.filter { !it.ready }.map { statusText(it.title) }.joinToString("、")
    return if (!state.ready) {
        if (missing.isEmpty()) status else stringResource(R.string.preflight_need_download, missing)
    } else {
        stringResource(R.string.preflight_ready)
    }
}

/** 结构化数据周期龄确认弹窗（对标 macOS CycleAgeConfirmView）。 */
@Composable
private fun CycleAgeConfirmDialog(store: WorkbenchStore, state: WorkbenchState) {
    val flagged = state.flaggedCycleAgeRows
    val warm = MaterialTheme.colorScheme.primary
    AlertDialog(
        onDismissRequest = { store.dismissCycleAgeConfirm() },
        title = {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                IconBadge(Icons.Filled.Schedule, tint = warm, modifier = Modifier.size(38.dp))
                Text(stringResource(R.string.cycle_age_title), fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
            }
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    stringResource(R.string.cycle_age_body),
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.78f),
                )
                WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
                    Column {
                        flagged.forEachIndexed { index, row ->
                            Row(
                                Modifier
                                    .fillMaxWidth()
                                    .padding(horizontal = 12.dp, vertical = 10.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(10.dp),
                            ) {
                                Icon(
                                    Icons.Filled.Storage,
                                    contentDescription = null,
                                    modifier = Modifier.size(18.dp),
                                    tint = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f),
                                )
                                Column(Modifier.weight(1f)) {
                                    Text(row.title, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                                    Text(
                                        statusText(row.detail),
                                        fontSize = 11.sp,
                                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.65f),
                                    )
                                }
                                StatusChip(
                                    state = if (row.old) "interrupted" else "pending",
                                    title = statusText(row.status),
                                )
                            }
                            if (index < flagged.size - 1) {
                                HorizontalDivider(
                                    thickness = 0.5.dp,
                                    color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.45f),
                                )
                            }
                        }
                    }
                }
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = { store.updateToLatestFromCycleAge() },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                        shape = RoundedCornerShape(12.dp),
                    ) {
                        Icon(Icons.Filled.CloudDownload, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(stringResource(R.string.cycle_age_update_latest), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    }
                    OutlinedButton(
                        onClick = { store.confirmCycleAgeAndForecast() },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 42.dp),
                        shape = RoundedCornerShape(12.dp),
                    ) {
                        Text(stringResource(R.string.cycle_age_continue_cache), fontSize = 13.sp)
                    }
                }
            }
        },
        confirmButton = {},
        dismissButton = {
            TextButton(onClick = { store.dismissCycleAgeConfirm() }) {
                Text(stringResource(R.string.back))
            }
        },
    )
}

@Composable
private fun SoftwareUpdateDialog(store: WorkbenchStore, state: WorkbenchState) {
    val context = LocalContext.current
    val info = state.updateInfo
    val phase = state.updatePhase
    val busyTransfer = phase == UpdatePhase.DOWNLOADING || phase == UpdatePhase.VERIFYING
    val currentLabel = state.appVersion.ifEmpty { "—" }

    AlertDialog(
        onDismissRequest = {
            if (!busyTransfer) store.dismissUpdateDialog()
        },
        title = {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                IconBadge(Icons.Filled.CloudDownload, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(38.dp))
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(stringResource(R.string.update_dialog_title), fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        if (info != null) "v$currentLabel → v${info.version}" else "v$currentLabel",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.68f),
                    )
                }
            }
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                when (phase) {
                    UpdatePhase.CHECKING -> {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                            Text(stringResource(R.string.update_checking), fontSize = 13.sp)
                        }
                    }
                    UpdatePhase.CURRENT -> {
                        Text(stringResource(R.string.update_up_to_date), fontSize = 13.sp)
                    }
                    else -> {
                        if (info != null) {
                            val notesText = info.notes.trim().ifEmpty { stringResource(R.string.update_notes_empty) }
                            Box(
                                Modifier
                                    .fillMaxWidth()
                                    .heightIn(max = 220.dp)
                                    .verticalScroll(rememberScrollState()),
                            ) {
                                Text(
                                    notesText,
                                    fontSize = 12.sp,
                                    lineHeight = 18.sp,
                                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.82f),
                                )
                            }
                        }
                        if (busyTransfer) {
                            val total = state.updateTotalBytes
                            val received = state.updateReceivedBytes
                            if (phase == UpdatePhase.DOWNLOADING && total > 0L) {
                                val pct = (received.toFloat() / total.toFloat()).coerceIn(0f, 1f)
                                LinearProgressIndicator(progress = { pct }, modifier = Modifier.fillMaxWidth())
                                Text(
                                    "%.1f MB / %.1f MB".format(received / 1_048_576.0, total / 1_048_576.0),
                                    fontSize = 11.sp,
                                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.65f),
                                )
                            } else {
                                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                                Text(
                                    stringResource(
                                        if (phase == UpdatePhase.VERIFYING) R.string.update_verifying
                                        else R.string.update_downloading,
                                    ),
                                    fontSize = 11.sp,
                                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.65f),
                                )
                            }
                        }
                        if (state.updateMessage.isNotEmpty()) {
                            Text(
                                state.updateMessage,
                                fontSize = 12.sp,
                                color = if (phase == UpdatePhase.FAILED) MaterialTheme.colorScheme.error
                                else MaterialTheme.colorScheme.primary,
                            )
                        }
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            when (phase) {
                                UpdatePhase.READY_TO_INSTALL -> {
                                    Button(
                                        onClick = { store.installDownloadedApk(context) },
                                        modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                                        shape = RoundedCornerShape(12.dp),
                                    ) {
                                        Text(stringResource(R.string.update_install_now), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                                    }
                                }
                                UpdatePhase.AVAILABLE, UpdatePhase.FAILED -> {
                                    if (info != null) {
                                        Button(
                                            onClick = { store.downloadUpdateApk() },
                                            modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                                            shape = RoundedCornerShape(12.dp),
                                        ) {
                                            Icon(Icons.Filled.CloudDownload, contentDescription = null, modifier = Modifier.size(16.dp))
                                            Spacer(Modifier.width(6.dp))
                                            Text(stringResource(R.string.update_download_install), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                                        }
                                    } else {
                                        Button(
                                            onClick = { store.checkForUpdates(interactive = true) },
                                            modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                                            shape = RoundedCornerShape(12.dp),
                                        ) {
                                            Text(stringResource(R.string.update_check), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                                        }
                                    }
                                }
                                UpdatePhase.DOWNLOADING -> {
                                    OutlinedButton(
                                        onClick = { store.cancelUpdateDownload() },
                                        modifier = Modifier.fillMaxWidth().heightIn(min = 42.dp),
                                        shape = RoundedCornerShape(12.dp),
                                    ) {
                                        Text(stringResource(R.string.update_cancel_download), fontSize = 13.sp)
                                    }
                                }
                                else -> {}
                            }
                            if (info != null && phase != UpdatePhase.VERIFYING) {
                                OutlinedButton(
                                    onClick = { store.openUpdateInBrowser(context) },
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 42.dp),
                                    shape = RoundedCornerShape(12.dp),
                                ) {
                                    Text(stringResource(R.string.update_browser_download), fontSize = 13.sp)
                                }
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {},
        dismissButton = {
            if (!busyTransfer) {
                TextButton(onClick = { store.dismissUpdateDialog() }) {
                    Text(stringResource(if (phase == UpdatePhase.CURRENT) R.string.ok else R.string.update_later))
                }
            }
        },
    )
}
