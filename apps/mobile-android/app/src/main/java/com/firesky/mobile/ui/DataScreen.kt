package com.firesky.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import org.json.JSONArray
import org.json.JSONObject

/** 数据管理页（macOS InventoryView + TerrainPanelView 同构）：数据目录、
 *  库存（按周期/整源手动删除）、地形区域下载。 */
@Composable
fun DataScreen(store: WorkbenchStore, state: WorkbenchState) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("数据管理", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Text(
            "数据目录：App 沙箱（cache/）· 已统计占用仅覆盖模式数据（GFS / EC / CAMS）",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        if (!state.online) {
            WorkSurface(Modifier.fillMaxWidth()) {
                Text(
                    "引擎未连接，无法读取库存与地形状态。",
                    Modifier.padding(15.dp),
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        } else {
            if (state.dataDeleteMessage.isNotEmpty()) {
                WorkSurface(Modifier.fillMaxWidth()) {
                    Text(
                        state.dataDeleteMessage,
                        Modifier.padding(12.dp),
                        fontSize = 12.sp,
                        color = if (state.dataDeleteFailed) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.7f),
                    )
                }
            }
            TerrainCard(store, state)
            InventoryCard(store, state)
        }
    }
    if (state.pendingDataDelete != null && state.dataDeletePreview != null) {
        DataDeleteDialog(store, state)
    }
}

// ---------------------------------------------------------------- 库存与删除

@Composable
private fun InventoryCard(store: WorkbenchStore, state: WorkbenchState) {
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("本地数据", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                if (state.inventory.length() == 0 && state.error.isEmpty()) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                }
                IconButton(onClick = { store.loadInventory() }) {
                    Icon(Icons.Filled.Refresh, contentDescription = "刷新本地清单", Modifier.size(16.dp))
                }
            }
            val datasets = state.inventory
            if (datasets.length() == 0) {
                Text(
                    if (state.error.isEmpty()) "尚未读取到本地数据。" else state.error,
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            }
            (0 until datasets.length()).forEach { index ->
                val dataset = datasets.optJSONObject(index) ?: return@forEach
                DatasetRows(store, state, dataset)
                if (index < datasets.length() - 1) {
                    HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.10f))
                }
            }
        }
    }
}

@Composable
private fun DatasetRows(store: WorkbenchStore, state: WorkbenchState, dataset: JSONObject) {
    val source = dataset.optString("source")
    val title = if (source == "ecmwf") "EC" else source.uppercase()
    val cycles = dataset.optJSONObject("cycles") ?: JSONObject()
    var expanded by remember(source) { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.weight(1f))
            Text(
                "${dataset.optInt("total_file_count", 0)} 个文件",
                fontSize = 11.sp,
            )
            Text(
                "%.2f GB".format((dataset.optDouble("total_storage_mb").takeUnless { it.isNaN() } ?: 0.0) / 1024),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            IconButton(onClick = { store.beginDataDelete(source, null) }, enabled = !state.busy) {
                Icon(Icons.Filled.Delete, contentDescription = "清空$title", Modifier.size(16.dp))
            }
        }
        Row(
            Modifier
                .fillMaxWidth()
                .clickable { expanded = !expanded }
                .padding(vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "${cycles.length()} 个周期",
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.7f),
            )
            Icon(
                if (expanded) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                contentDescription = null,
                Modifier.size(16.dp),
                tint = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        }
        if (expanded) {
            val keys = cycles.keys().asSequence().sortedDescending().toList()
            keys.forEach { key ->
                val item = cycles.optJSONObject(key) ?: JSONObject()
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text(key, fontSize = 12.sp, fontFamily = FontFamily.Monospace)
                    Spacer(Modifier.weight(1f))
                    Text(
                        "${item.optInt("files", 0)} 个文件",
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                    IconButton(
                        onClick = { store.beginDataDelete(source, key) },
                        enabled = !state.busy,
                        modifier = Modifier.size(30.dp),
                    ) {
                        Icon(Icons.Filled.Delete, contentDescription = "删除周期 $key", Modifier.size(14.dp))
                    }
                }
            }
        }
    }
}

/** 删除确认：具体范围 + 文件数 + 预计释放；占用中跳过；不影响报告与任务记录。 */
@Composable
private fun DataDeleteDialog(store: WorkbenchStore, state: WorkbenchState) {
    val scope = state.pendingDataDelete ?: return
    val preview = state.dataDeletePreview ?: return
    val source = scope.optString("source")
    val title = if (source == "ecmwf") "EC" else source.uppercase()
    val cycle = scope.optString("cycle", "").ifEmpty { null }
    val files = preview.optInt("file_count", 0)
    val mb = preview.optDouble("total_mb").takeUnless { it.isNaN() } ?: 0.0
    val protected = preview.optJSONArray("protected")?.length() ?: 0
    AlertDialog(
        onDismissRequest = { store.cancelDataDelete() },
        title = { Text(if (cycle == null) "清空$title？" else "删除周期？", fontSize = 16.sp) },
        text = {
            Text(
                if (cycle == null) {
                    "将删除 $title 的全部本地数据：共 $files 个文件，预计释放 %.1f MB。占用中的文件会跳过；报告与任务记录不受影响。".format(mb) +
                        (if (protected > 0) "当前有 $protected 个文件被下载任务占用，将跳过。" else "")
                } else {
                    "删除 $title 的周期 $cycle：共 $files 个文件，预计释放 %.1f MB。占用中的文件会跳过；报告与任务记录不受影响。".format(mb)
                },
                fontSize = 13.sp,
            )
        },
        confirmButton = {
            Button(
                onClick = { store.executeDataDelete() },
                enabled = !state.dataDeleteRunning,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFB5482F)),
            ) { Text("删除") }
        },
        dismissButton = {
            TextButton(onClick = { store.cancelDataDelete() }) { Text("取消") }
        },
    )
}

// ---------------------------------------------------------------- 地形区域下载

/** 地形卡（macOS TerrainPanelView 同构）：区域选择 → 估算确认 → 批量下载。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TerrainCard(store: WorkbenchStore, state: WorkbenchState) {
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("地形 SRTM", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    Text(terrainSubtitle(state), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                }
                if (state.terrainLoading) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            }
            if (state.terrainDownloading) {
                TerrainProgress(state)
            }
            if (state.terrainMessage.isNotEmpty()) {
                Text(
                    state.terrainMessage,
                    fontSize = 12.sp,
                    color = if (state.terrainMessageFailed) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.7f),
                )
            }
            localTilesLine(state)
            if (!state.terrainDownloading) {
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    RegionDropdown(state) { store.setTerrainRegion(it) }
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = { store.loadTerrain() }) {
                        Icon(Icons.Filled.Refresh, contentDescription = "刷新地形状态", Modifier.size(16.dp))
                    }
                }
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Button(
                        onClick = { store.loadTerrainEstimate() },
                        enabled = state.online,
                        modifier = Modifier.weight(1f).height(40.dp),
                        shape = RoundedCornerShape(8.dp),
                    ) { Text("下载区域地形", fontSize = 11.sp) }
                    OutlinedButton(
                        onClick = { store.downloadTerrainPoint() },
                        enabled = state.online,
                        modifier = Modifier.height(40.dp),
                    ) { Text("仅当前地点", fontSize = 11.sp) }
                }
            } else {
                Button(
                    onClick = { store.cancelTerrainDownload() },
                    enabled = !state.terrainCancelling,
                    modifier = Modifier.fillMaxWidth().height(40.dp),
                    shape = RoundedCornerShape(8.dp),
                ) { Text(if (state.terrainCancelling) "正在停止…" else "取消下载", fontSize = 11.sp) }
            }
        }
    }
    if (state.showTerrainConfirm) {
        TerrainConfirmDialog(store, state)
    }
}

/** 批量进度（macOS globalProgress 同构）。 */
@Composable
private fun TerrainProgress(state: WorkbenchState) {
    val progress = state.terrainProgress
    val candidates = progress.optInt("candidates", 0)
    val percent = progress.optDouble("percent").takeUnless { it.isNaN() } ?: 0.0
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        LinearProgressIndicator(
            progress = { (percent / 100).toFloat() },
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "%.0f%% · 已下载 ${progress.optInt("downloaded", 0)}/$candidates · " +
                "已有 ${progress.optInt("existing", 0)} · 缺失 ${progress.optInt("absent", 0)} · " +
                "失败 ${progress.optInt("failed", 0)}" +
                (progress.optString("current_tile").takeIf { it.isNotEmpty() }?.let { " · 当前 $it" } ?: ""),
            fontSize = 10.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        val received = progress.optLong("received_bytes", 0)
        if (received > 0) {
            Text(
                humanBytes(received),
                fontSize = 10.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun RegionDropdown(state: WorkbenchState, onSelect: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
        OutlinedTextField(
            value = state.terrainRegion,
            onValueChange = {},
            readOnly = true,
            label = { Text("区域", fontSize = 10.sp) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            textStyle = MaterialTheme.typography.bodySmall,
            modifier = Modifier.menuAnchor().width(120.dp),
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            com.firesky.mobile.store.TerrainRegion.labels.forEach { label ->
                DropdownMenuItem(text = { Text(label, fontSize = 12.sp) }, onClick = {
                    onSelect(label)
                    expanded = false
                })
            }
        }
    }
}

/** 区域下载确认：候选数 + 已有 + 抽样估算体积 + 续传说明。 */
@Composable
private fun TerrainConfirmDialog(store: WorkbenchStore, state: WorkbenchState) {
    val global = state.terrainEstimate?.optJSONObject("global") ?: JSONObject()
    val candidates = global.optInt("candidates", 0)
    val existing = global.optInt("existing", 0)
    val estimatedGb = global.optDouble("estimated_gb").takeUnless { it.isNaN() }
    AlertDialog(
        onDismissRequest = { store.dismissTerrainConfirm() },
        title = { Text("下载${state.terrainRegion}地形数据？", fontSize = 16.sp) },
        text = {
            Text(
                "将下载${state.terrainRegion}的 SRTM 瓦片：候选 $candidates 块，本地已有 $existing 块会自动跳过" +
                    "（海洋上的瓦片是很小的空值瓦片，同样会下载）。下载耗时较长且占用磁盘，可随时取消，" +
                    "重新开始会跳过已下载瓦片。" +
                    (estimatedGb?.let { String.format("按抽样估算体积约 %.0f GB（实际体积以运行时用量为准）。", it) }
                        ?: "暂时无法估算体积，开始后会显示实际用量。"),
                fontSize = 13.sp,
            )
        },
        confirmButton = {
            Button(onClick = { store.downloadTerrainRegion() }) { Text("开始下载") }
        },
        dismissButton = {
            TextButton(onClick = { store.dismissTerrainConfirm() }) { Text("取消") }
        },
    )
}

private fun terrainSubtitle(state: WorkbenchState): String {
    val terrain = state.terrain ?: return "全球瓦片下载后会覆盖任意地点，无需再按点下载。"
    if (terrain.optBoolean("coverage_ok", false).not() && terrain.has("coverage_ok")) {
        return "当前纬度不在 SRTM 覆盖范围（-56 ≤ 纬度 < 60）。"
    }
    val tile = terrain.optJSONObject("tile") ?: return "显示本地已有地形瓦片。"
    val loc = state.location
    return "目标 %s（%.4f° N, %.4f° E）· 瓦片 %s · %s".format(
        loc.name, loc.lat, loc.lon,
        tile.optString("name"),
        if (tile.optBoolean("exists", false)) "已有本地瓦片" else "未下载",
    )
}

@Composable
private fun localTilesLine(state: WorkbenchState) {
    val count = state.terrain?.optInt("tile_count", 0) ?: 0
    if (count > 8) {
        Text("本地已有 $count 块瓦片。", fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
    } else {
        val tiles = state.terrain?.optJSONArray("tiles")
        if (tiles != null && tiles.length() > 0) {
            val names = (0 until tiles.length()).mapNotNull { tiles.optJSONObject(it)?.optString("name") }
            if (names.isNotEmpty()) {
                Text("本地瓦片：${names.joinToString("、")}", fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
            }
        }
    }
}

private fun humanBytes(bytes: Long): String = when {
    bytes >= 1 shl 30 -> "%.1f GB".format(bytes.toDouble() / (1 shl 30))
    bytes >= 1 shl 20 -> "%.1f MB".format(bytes.toDouble() / (1 shl 20))
    bytes >= 1 shl 10 -> "%.1f KB".format(bytes.toDouble() / (1 shl 10))
    else -> "$bytes B"
}
