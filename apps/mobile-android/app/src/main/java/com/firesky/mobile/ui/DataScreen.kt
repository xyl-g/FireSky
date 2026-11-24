package com.firesky.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material.icons.filled.CloudQueue
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Inventory2
import androidx.compose.material.icons.filled.Landscape
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.SatelliteAlt
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.R
import com.firesky.mobile.store.TerrainRegion
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import org.json.JSONObject

/** 数据管理页（macOS InventoryView + TerrainPanelView 同构）：数据目录、
 *  库存（按周期/整源手动删除）、预测结果与附加缓存类目、地形区域下载。 */
@Composable
fun DataScreen(store: WorkbenchStore, state: WorkbenchState) {
    val pageError = state.errorText("data")
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(stringResource(R.string.data_title), fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Text(
                stringResource(R.string.data_dir_line),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        }
        if (!state.online) {
            WorkSurface(Modifier.fillMaxWidth()) {
                Text(
                    stringResource(R.string.data_offline),
                    Modifier.padding(16.dp),
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        } else {
            if (pageError.isNotEmpty()) {
                DismissibleErrorBanner(pageError, onDismiss = { store.clearError() })
            }
            if (state.dataDeleteMessage.isNotEmpty()) {
                val msgColor = if (state.dataDeleteFailed) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.78f)
                Surface(
                    Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp),
                    color = msgColor.copy(alpha = 0.09f),
                    border = BorderStroke(0.75.dp, msgColor.copy(alpha = 0.28f)),
                ) {
                    Text(
                        state.dataDeleteMessage,
                        Modifier.padding(12.dp),
                        fontSize = 12.sp,
                        color = msgColor,
                    )
                }
            }
            TerrainCard(store, state)
            InventoryCard(store, state)
            AuxiliaryCategoriesCard(state)
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
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                IconBadge(Icons.Filled.Storage, tint = coolAccentColor())
                Text(stringResource(R.string.local_data), fontSize = 14.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                IconButton(onClick = { store.loadInventory() }) {
                    Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.cd_refresh_inventory), Modifier.size(16.dp))
                }
            }
            val datasets = state.inventory
            if (datasets.length() == 0) {
                Column(
                    Modifier.fillMaxWidth().padding(vertical = 16.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    FireSkyMarkScene(size = 64.dp)
                    Text(stringResource(R.string.inventory_empty_title), fontSize = 13.sp, fontWeight = FontWeight.Medium)
                    Text(
                        stringResource(R.string.inventory_empty_body),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                }
            }
            (0 until datasets.length()).forEach { index ->
                val dataset = datasets.optJSONObject(index) ?: return@forEach
                DatasetRows(store, state, dataset)
                if (index < datasets.length() - 1) {
                    HorizontalDivider(
                        thickness = 0.5.dp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.10f),
                    )
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
    val numStyle = TextStyle(fontFeatureSettings = "tnum")
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.weight(1f))
            Text(
                stringResource(R.string.files_count, dataset.optInt("total_file_count", 0)),
                fontSize = 11.sp,
                style = numStyle,
            )
            Spacer(Modifier.width(6.dp))
            Text(
                "%.2f GB".format((dataset.optDouble("total_storage_mb").takeUnless { it.isNaN() } ?: 0.0) / 1024),
                fontSize = 11.sp,
                style = numStyle,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
            IconButton(onClick = { store.beginDataDelete(source, null) }, enabled = !state.busy) {
                Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.cd_clear_source, title), Modifier.size(16.dp))
            }
        }
        Row(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
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
            WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
                Column(Modifier.padding(horizontal = 12.dp, vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    val keys = cycles.keys().asSequence().sortedDescending().toList()
                    keys.forEach { key ->
                        val item = cycles.optJSONObject(key) ?: JSONObject()
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Text(key, fontSize = 12.sp, style = numStyle)
                            Spacer(Modifier.weight(1f))
                            Text(
                                stringResource(R.string.files_count, item.optInt("files", 0)),
                                fontSize = 11.sp,
                                style = numStyle,
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
    }
}

/** 预测结果与附加缓存类目（对应 macOS intermediateCachesSection）。 */
@Composable
private fun AuxiliaryCategoriesCard(state: WorkbenchState) {
    if (state.inventoryCategories.isEmpty()) return
    val numStyle = TextStyle(fontFeatureSettings = "tnum")
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                IconBadge(Icons.Filled.Inventory2, tint = MaterialTheme.colorScheme.primary)
                Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text(stringResource(R.string.aux_categories_title), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        stringResource(R.string.aux_categories_note),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                    )
                }
            }
            HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.10f))
            state.inventoryCategories.forEachIndexed { index, item ->
                val key = item.optString("category")
                val files = item.optInt("file_count", 0)
                val bytes = item.optLong("bytes", 0L)
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    IconBadge(categoryIcon(key), tint = coolAccentColor())
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(categoryTitle(key), fontSize = 12.sp, fontWeight = FontWeight.Medium)
                        val sub = categorySubtitle(key)
                        if (sub.isNotEmpty()) {
                            Text(sub, fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f))
                        }
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        Text(
                            humanBytes(bytes),
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Medium,
                            style = numStyle,
                        )
                        Text(
                            stringResource(R.string.files_count, files),
                            fontSize = 10.sp,
                            style = numStyle,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f),
                        )
                    }
                }
                if (index < state.inventoryCategories.size - 1) {
                    HorizontalDivider(
                        Modifier.padding(start = 40.dp),
                        thickness = 0.5.dp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f),
                    )
                }
            }
        }
    }
}

private fun categoryIcon(key: String): ImageVector = when (key) {
    "point-forecasts" -> Icons.Filled.Place
    "regional-forecasts" -> Icons.Filled.Map
    "map-frames" -> Icons.Filled.Layers
    "reports" -> Icons.Filled.Description
    "satellite" -> Icons.Filled.SatelliteAlt
    "srtm" -> Icons.Filled.Landscape
    else -> Icons.Filled.Folder
}

@Composable
private fun categoryTitle(key: String): String = when (key) {
    "point-forecasts" -> stringResource(R.string.cat_point_forecasts)
    "regional-forecasts" -> stringResource(R.string.cat_regional_forecasts)
    "map-frames" -> stringResource(R.string.cat_map_frames)
    "reports" -> stringResource(R.string.cat_reports)
    "satellite" -> stringResource(R.string.cat_satellite)
    "srtm" -> stringResource(R.string.cat_srtm)
    else -> key
}

@Composable
private fun categorySubtitle(key: String): String = when (key) {
    "point-forecasts" -> stringResource(R.string.cat_point_forecasts_sub)
    "regional-forecasts" -> stringResource(R.string.cat_regional_forecasts_sub)
    "map-frames" -> stringResource(R.string.cat_map_frames_sub)
    "reports" -> stringResource(R.string.cat_reports_sub)
    "satellite" -> stringResource(R.string.cat_satellite_sub)
    "srtm" -> stringResource(R.string.cat_srtm_sub)
    else -> ""
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
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                shape = RoundedCornerShape(12.dp),
            ) { Text(stringResource(R.string.delete)) }
        },
        dismissButton = {
            TextButton(onClick = { store.cancelDataDelete() }) { Text(stringResource(R.string.cancel)) }
        },
    )
}

// ---------------------------------------------------------------- 地形区域下载

/** 地形卡（macOS TerrainPanelView 同构）：区域选择 → 估算确认 → 批量下载 + 失败瓦片重试。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TerrainCard(store: WorkbenchStore, state: WorkbenchState) {
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                IconBadge(Icons.Filled.Landscape, tint = MaterialTheme.colorScheme.primary)
                Column(Modifier.weight(1f)) {
                    Text(stringResource(R.string.terrain_title), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    Text(terrainSubtitle(state), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                }
                if (state.terrainLoading || state.terrainEstimateLoading) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                }
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
                        enabled = state.online && !state.busy && !state.terrainEstimateLoading,
                        modifier = Modifier.weight(1f).height(42.dp),
                        shape = RoundedCornerShape(12.dp),
                    ) { Text(stringResource(R.string.download_region), fontSize = 12.sp) }
                    OutlinedButton(
                        onClick = { store.downloadTerrainPoint() },
                        enabled = state.online && !state.busy,
                        modifier = Modifier.height(42.dp),
                        shape = RoundedCornerShape(12.dp),
                    ) { Text(stringResource(R.string.point_only), fontSize = 12.sp) }
                }
                if (state.terrainLastFailedCount > 0) {
                    OutlinedButton(
                        onClick = { store.retryFailedTerrainTiles() },
                        enabled = state.online && !state.busy,
                        modifier = Modifier.fillMaxWidth().height(42.dp),
                        shape = RoundedCornerShape(12.dp),
                    ) {
                        Icon(Icons.Filled.CloudQueue, contentDescription = null, modifier = Modifier.size(15.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(stringResource(R.string.terrain_retry_failed_fmt, state.terrainLastFailedCount), fontSize = 12.sp)
                    }
                }
            } else {
                Button(
                    onClick = { store.cancelTerrainDownload() },
                    enabled = !state.terrainCancelling,
                    modifier = Modifier.fillMaxWidth().height(42.dp),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text(
                        if (state.terrainCancelling) stringResource(R.string.stopping)
                        else stringResource(R.string.cancel_download),
                        fontSize = 12.sp,
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
            modifier = Modifier.fillMaxWidth().clip(CircleShape),
        )
        Text(
            stringResource(
                R.string.terrain_progress_fmt,
                percent, progress.optInt("downloaded", 0), candidates,
                progress.optInt("existing", 0), progress.optInt("absent", 0),
                progress.optInt("failed", 0),
            ) + (progress.optString("current_tile").takeIf { it.isNotEmpty() }
                ?.let { stringResource(R.string.terrain_progress_current, it) } ?: ""),
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        val received = progress.optLong("received_bytes", 0)
        if (received > 0) {
            Text(
                humanBytes(received),
                fontSize = 11.sp,
                style = TextStyle(fontFeatureSettings = "tnum"),
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
            shape = SoftInputShape,
            colors = softInputColors(),
            label = { Text(stringResource(R.string.region_label), fontSize = 10.sp) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            textStyle = MaterialTheme.typography.bodySmall,
            modifier = Modifier.menuAnchor().width(130.dp),
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            TerrainRegion.labels.forEach { label ->
                DropdownMenuItem(text = { Text(label, fontSize = 12.sp) }, onClick = {
                    onSelect(label)
                    expanded = false
                })
            }
        }
    }
}

/** 区域下载确认（非破坏性，跳过已有瓦片且可随时取消续传）。 */
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
            Button(
                onClick = { store.downloadTerrainRegion() },
                shape = RoundedCornerShape(12.dp),
            ) { Text(stringResource(R.string.start_download)) }
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
