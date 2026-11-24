package com.firesky.mobile.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Star
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
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.firesky.mobile.R
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
        androidx.compose.material3.Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            bottomBar = {
                Column(Modifier.background(MaterialTheme.colorScheme.surfaceVariant)) {
                if (state.page == "workbench" && !state.showPreflight) {
                    Box(Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) {
                        RunButton(store, state)
                    }
                }
                androidx.compose.material3.NavigationBar(containerColor = MaterialTheme.colorScheme.surfaceVariant) {
                    NavigationItem("workbench", stringResource(R.string.nav_point), Icons.Filled.Place, state.page) {
                        store.setPage("workbench")
                    }
                    NavigationItem("data", stringResource(R.string.nav_data), Icons.Filled.Storage, state.page) {
                        store.setPage("data")
                    }
                    NavigationItem("history", stringResource(R.string.nav_history), Icons.Filled.History, state.page) {
                        store.setPage("history")
                    }
                    NavigationItem("settings", stringResource(R.string.nav_settings), Icons.Filled.Settings, state.page) {
                        store.setPage("settings")
                    }
                }
                }
            },
        ) { padding ->
            Box(Modifier.fillMaxSize().background(pageBackground()).padding(padding)) {
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
                    ) {
                        PreflightSheet(store, state)
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
    onClick: () -> Unit,
) {
    NavigationBarItem(
        selected = current == key,
        onClick = onClick,
        icon = { Icon(icon, contentDescription = title) },
        label = { Text(title) },
    )
}

// ---------------------------------------------------------------- 工作台主面

/** 单点工作台（Workbench.swift workbench 区域同构）：参数表单 → 运行 → 进度 → 结果卡片。 */
@Composable
fun WorkbenchScreen(store: WorkbenchStore, state: WorkbenchState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        HeaderLine(state)
        if (!state.online) OfflineBanner(state) { store.connect() }
        LocationCard(store, state)
        ParametersCard(store, state)
        if (state.busy && !state.showPreflight) ProgressRow(store, state)
        ResultSection(store, state)
    }
}

@Composable
private fun HeaderLine(state: WorkbenchState) {
    val loc = state.location
    Column {
        Text(stringResource(R.string.point_title), fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Text(
            "%s · %.4f° N, %.4f° E".format(loc.name, loc.lat, loc.lon),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
    }
}

@Composable
private fun OfflineBanner(state: WorkbenchState, onReconnect: () -> Unit) {
    WorkSurface(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("⚠", color = MaterialTheme.colorScheme.error)
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.engine_offline), fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
                if (state.error.isNotEmpty()) {
                    Text(state.error, fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                }
            }
            TextButton(onClick = onReconnect) { Text(stringResource(R.string.reconnect)) }
        }
    }
}

// ---------------------------------------------------------------- 参数表单

/** 观测点卡（WorkLocationView 同构；地图选点延后至 openQuestions D1）。 */
@Composable
private fun LocationCard(store: WorkbenchStore, state: WorkbenchState) {
    val loc = state.location
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(stringResource(R.string.obs_point), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedTextField(
                    value = state.query,
                    onValueChange = store::setQuery,
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                    placeholder = { Text(stringResource(R.string.search_placeholder), fontSize = 12.sp) },
                    textStyle = MaterialTheme.typography.bodySmall,
                    trailingIcon = {
                        IconButton(onClick = { store.search() }) {
                            Icon(Icons.Filled.Search, contentDescription = stringResource(R.string.cd_search))
                        }
                    },
                )
                IconButton(onClick = { store.locate() }, enabled = !state.busy) {
                    Icon(Icons.Filled.LocationOn, contentDescription = stringResource(R.string.cd_locate))
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                Box(Modifier.size(8.dp).background(MaterialTheme.colorScheme.primary, CircleShape))
                Column {
                    Text(loc.name, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        "%.4f° N, %.4f° E".format(loc.lat, loc.lon),
                        fontSize = 10.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                ChipText(
                    if (loc.metadataReady) stringResource(R.string.elevation_m, loc.elevation)
                    else stringResource(R.string.elevation_unconfirmed),
                )
                ChipText(loc.timezone, modifier = Modifier.weight(1f, fill = false))
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                TextButton(onClick = { store.starLocation() }) {
                    Icon(Icons.Filled.Star, contentDescription = null, Modifier.size(14.dp))
                    Spacer(Modifier.width(4.dp))
                    Text(stringResource(R.string.star_location), fontSize = 10.sp)
                }
            }
            if (state.favorites.isNotEmpty()) {
                HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
                state.favorites.forEachIndexed { index, favorite ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clickable { store.applyFavorite(index) }
                            .padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(
                            Icons.Filled.Place,
                            contentDescription = null,
                            Modifier.size(14.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            favorite.optString("name", stringResource(R.string.favorite_name)),
                            fontSize = 12.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Spacer(Modifier.weight(1f))
                        Text(
                            "%.4f, %.4f".format(favorite.optDouble("lat", 0.0), favorite.optDouble("lon", 0.0)),
                            fontSize = 9.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f),
                        )
                    }
                }
            }
        }
    }
}

/** 观测参数卡（WorkParametersView 同构）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ParametersCard(store: WorkbenchStore, state: WorkbenchState) {
    var showDatePicker by remember { mutableStateOf(false) }
    val pickerState = androidx.compose.material3.rememberDatePickerState(
        initialSelectedDateMillis = state.targetDate.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
    )
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.obs_params), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                TextButton(onClick = { store.resetParameters() }) { Text(stringResource(R.string.reset), fontSize = 10.sp) }
            }
            Text(stringResource(R.string.target_date), fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
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
                OutlinedButton(
                    onClick = { showDatePicker = true },
                    modifier = Modifier.fillMaxWidth().height(40.dp),
                ) {
                    Text("📅 ${state.targetDate}", fontSize = 12.sp)
                }
            }
            Text(stringResource(R.string.phenomenon_label), fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
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
            TextButton(
                onClick = { store.setEditingCoordinates(!state.editingCoordinates) },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    if (state.editingCoordinates) stringResource(R.string.collapse_coordinates)
                    else stringResource(R.string.edit_coordinates),
                    fontSize = 10.sp,
                )
            }
            AnimatedVisibility(visible = state.editingCoordinates) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = state.location.name,
                        onValueChange = { value -> store.editLocation { it.copy(name = value) } },
                        label = { Text(stringResource(R.string.name_label)) },
                        singleLine = true,
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
                        textStyle = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    TextButton(onClick = { store.lookupMetadata() }) { Text(stringResource(R.string.fetch_metadata), fontSize = 11.sp) }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        androidx.compose.material3.Checkbox(
                            checked = state.location.metadataReady,
                            onCheckedChange = { store.setMetadataReady(it) },
                        )
                        Text(stringResource(R.string.location_confirmed), fontSize = 10.sp)
                    }
                }
            }
            if (state.error.isNotEmpty()) {
                Text(
                    state.error,
                    fontSize = 10.sp,
                    color = MaterialTheme.colorScheme.error,
                )
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
        textStyle = MaterialTheme.typography.bodySmall,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        modifier = Modifier.fillMaxWidth(),
    )
}

// ---------------------------------------------------------------- 运行与进度

@Composable
private fun RunButton(store: WorkbenchStore, state: WorkbenchState) {
    AccentButton(
        title = stringResource(R.string.start_forecast),
        enabled = !state.busy && state.online,
    ) { store.beginCheck() }
}

@Composable
private fun ProgressRow(store: WorkbenchStore, state: WorkbenchState) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        LinearProgressIndicator(
            progress = { (state.percent / 100).toFloat() },
            modifier = Modifier.fillMaxWidth(),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                state.status,
                fontSize = 9.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
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

// ---------------------------------------------------------------- 通用小件

/** WorkSegments 同构：分段选择器（accent 时选中项用主题色）。 */
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
    Row(
        modifier
            .fillMaxWidth()
            .selectableGroup()
            .background(
                if (dark) Color.White.copy(alpha = 0.08f) else Color.Black.copy(alpha = 0.05f),
                RoundedCornerShape(8.dp),
            )
            .padding(3.dp),
        horizontalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        options.forEachIndexed { index, option ->
            val selected = selection == option
            Box(
                Modifier
                    .weight(1f)
                    .background(
                        when {
                            selected && accent -> MaterialTheme.colorScheme.primary.copy(alpha = 0.16f)
                            selected -> if (dark) Color.White.copy(alpha = 0.10f) else Color.White.copy(alpha = 0.85f)
                            else -> Color.Transparent
                        },
                        RoundedCornerShape(6.dp),
                    )
                    .selectable(selected = selected, role = Role.Tab, onClick = { onSelect(option) })
                    .heightIn(min = 48.dp)
                    .padding(horizontal = 4.dp, vertical = 8.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    titles?.getOrNull(index) ?: option,
                    fontSize = 14.sp,
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                    color = when {
                        selected && accent -> MaterialTheme.colorScheme.primary
                        selected -> MaterialTheme.colorScheme.onBackground
                        else -> MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f)
                    },
                )
            }
        }
    }
}

@Composable
fun ChipText(text: String, modifier: Modifier = Modifier) {
    Box(
        modifier
            .background(
                MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f),
                RoundedCornerShape(6.dp),
            )
            .padding(horizontal = 8.dp, vertical = 5.dp),
    ) {
        Text(text, fontSize = 9.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.7f))
    }
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
            state.rows.forEach { row ->
                Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier.size(32.dp).background(
                            MaterialTheme.colorScheme.onBackground.copy(alpha = 0.10f),
                            RoundedCornerShape(6.dp),
                        ),
                        contentAlignment = Alignment.Center,
                    ) { Text(row.title.take(2), fontSize = 11.sp, fontWeight = FontWeight.SemiBold) }
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
                HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.10f))
            }
        }
        Spacer(Modifier.height(12.dp))
        Column {
            when {
                state.checking -> {
                    LinearProgressIndicator(
                        progress = { (state.percent / 100).toFloat() },
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        state.status,
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
                state.error.isNotEmpty() -> Text(
                    state.error,
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.error,
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
        ) { if (state.ready) store.confirm() else store.prepare() }
        if (state.ready) {
            TextButton(onClick = { store.prepare() }, enabled = state.session != null && !state.busy,
                modifier = Modifier.fillMaxWidth()) {
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
