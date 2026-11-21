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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.R
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
        Text(stringResource(R.string.data_title), fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Text(
            stringResource(R.string.data_dir_line),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        if (!state.online) {
            WorkSurface(Modifier.fillMaxWidth()) {
                Text(
                    stringResource(R.string.data_offline),
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
                Text(stringResource(R.string.local_data), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                if (state.inventory.length() == 0 && state.error.isEmpty()) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                }
                IconButton(onClick = { store.loadInventory() }) {
                    Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.cd_refresh_inventory), Modifier.size(16.dp))
                }
            }
            val datasets = state.inventory
            if (datasets.length() == 0) {
                Text(
                    if (state.error.isEmpty()) stringResource(R.string.no_inventory) else state.error,
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
                stringResource(R.string.files_count, dataset.optInt("total_file_count", 0)),
                fontSize = 11.sp,
            )
            Text(
                "%.2f GB".format((dataset.optDouble("total_storage_mb").takeUnless { it.isNaN() } ?: 0.0) / 1024),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            IconButton(onClick = { store.beginDataDelete(source, null) }, enabled = !state.busy) {
                Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.cd_clear_source, title), Modifier.size(16.dp))
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
                stringResource(R.string.cycles_count, cycles.length()),
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
                        stringResource(R.string.files_count, item.optInt("files", 0)),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                    IconButton(
                        onClick = { store.beginDataDelete(source, key) },
                        enabled = !state.busy,
                        modifier = Modifier.size(30.dp),
                    ) {
                        Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.cd_delete_cycle, key), Modifier.size(14.dp))
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
        title = {
            Text(
                if (cycle == null) stringResource(R.string.clear_source_title, title)
                else stringResource(R.string.delete_cycle_title),
                fontSize = 16.sp,
            )
        },
        text = {
            Text(
                (if (cycle == null) {
                    stringResource(R.string.clear_source_body, title, files, mb)
                } else {
                    stringResource(R.string.delete_cycle_body, title, cycle, files, mb)
                }) + if (protected > 0) stringResource(R.string.delete_protected_note, protected) else "",
                fontSize = 13.sp,
            )
        },
        confirmButton = {
            Button(
                onClick = { store.executeDataDelete() },
                enabled = !state.dataDeleteRunning,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFB5482F)),
            ) { Text(stringResource(R.string.delete)) }
        },
        dismissButton = {
            TextButton(onClick = { store.cancelDataDelete() }) { Text(stringResource(R.string.cancel)) }
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
                    Text(stringResource(R.string.terrain_title), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
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
                        Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.cd_refresh_terrain), Modifier.size(16.dp))
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
                    ) { Text(stringResource(R.string.download_region), fontSize = 11.sp) }
                    OutlinedButton(
                        onClick = { store.downloadTerrainPoint() },
                        enabled = state.online,
                        modifier = Modifier.height(40.dp),
                    ) { Text(stringResource(R.string.point_only), fontSize = 11.sp) }
                }
            } else {
                Button(
                    onClick = { store.cancelTerrainDownload() },
                    enabled = !state.terrainCancelling,
                    modifier = Modifier.fillMaxWidth().height(40.dp),
                    shape = RoundedCornerShape(8.dp),
                ) {
                    Text(
                        if (state.terrainCancelling) stringResource(R.string.stopping)
                        else stringResource(R.string.cancel_download),
                        fontSize = 11.sp,
                    )
                }
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
            stringResource(
                R.string.terrain_progress_fmt,
                percent, progress.optInt("downloaded", 0), candidates,
                progress.optInt("existing", 0), progress.optInt("absent", 0),
                progress.optInt("failed", 0),
            ) + (progress.optString("current_tile").takeIf { it.isNotEmpty() }
                ?.let { stringResource(R.string.terrain_progress_current, it) } ?: ""),
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
            label = { Text(stringResource(R.string.region_label), fontSize = 10.sp) },
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
        title = { Text(stringResource(R.string.terrain_confirm_title, state.terrainRegion), fontSize = 16.sp) },
        text = {
            Text(
                stringResource(R.string.terrain_confirm_body, state.terrainRegion, candidates, existing) +
                    (estimatedGb?.let { stringResource(R.string.terrain_estimate, it) }
                        ?: stringResource(R.string.terrain_no_estimate)),
                fontSize = 13.sp,
            )
        },
        confirmButton = {
            Button(onClick = { store.downloadTerrainRegion() }) { Text(stringResource(R.string.start_download)) }
        },
        dismissButton = {
            TextButton(onClick = { store.dismissTerrainConfirm() }) { Text(stringResource(R.string.cancel)) }
        },
    )
}

@Composable
private fun terrainSubtitle(state: WorkbenchState): String {
    val terrain = state.terrain ?: return stringResource(R.string.terrain_default_subtitle)
    if (terrain.optBoolean("coverage_ok", false).not() && terrain.has("coverage_ok")) {
        return stringResource(R.string.terrain_out_of_coverage)
    }
    val tile = terrain.optJSONObject("tile") ?: return stringResource(R.string.terrain_point_subtitle)
    val loc = state.location
    return stringResource(
        R.string.terrain_target_line,
        loc.name, loc.lat, loc.lon,
        tile.optString("name"),
        if (tile.optBoolean("exists", false)) stringResource(R.string.tile_exists)
        else stringResource(R.string.tile_missing),
    )
}

@Composable
private fun localTilesLine(state: WorkbenchState) {
    val count = state.terrain?.optInt("tile_count", 0) ?: 0
    if (count > 8) {
        Text(
            stringResource(R.string.local_tiles_count, count),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
    } else {
        val tiles = state.terrain?.optJSONArray("tiles")
        if (tiles != null && tiles.length() > 0) {
            val names = (0 until tiles.length()).mapNotNull { tiles.optJSONObject(it)?.optString("name") }
            if (names.isNotEmpty()) {
                Text(
                    stringResource(R.string.local_tiles_list, names.joinToString("、")),
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
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
