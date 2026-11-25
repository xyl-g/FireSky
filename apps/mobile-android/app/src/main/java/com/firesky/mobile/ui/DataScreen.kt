package com.firesky.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
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
        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                stringResource(R.string.data_title),
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onBackground,
            )
            Text(
                stringResource(R.string.data_dir_line),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
            )
        }
        if (!state.online) {
            WorkSurface(Modifier.fillMaxWidth()) {
                Column(
                    Modifier.fillMaxWidth().padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    FireSkyMarkScene(size = 64.dp)
                    Text(
                        stringResource(R.string.engine_offline),
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        stringResource(R.string.data_offline),
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(2.dp))
                    AccentButton(stringResource(R.string.go_configure_server)) {
                        store.setSettingsCategory("高级")
                        store.setPage("settings")
                    }
                }
            }
        } else {
            if (pageError.isNotEmpty()) {
                DismissibleErrorBanner(pageError, onDismiss = { store.clearError() })
            }
            ReadOnlyNoticeBanner(stringResource(R.string.readonly_data_notice))
            TerrainCard(store, state)
            InventoryCard(store, state)
            AuxiliaryCategoriesCard(state)
        }
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
                DatasetRows(dataset)
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
private fun DatasetRows(dataset: JSONObject) {
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
            Spacer(Modifier.width(8.dp))
            Text(
                "%.2f GB".format((dataset.optDouble("total_storage_mb").takeUnless { it.isNaN() } ?: 0.0) / 1024),
                fontSize = 11.sp,
                style = numStyle,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
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
                Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
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

// ---------------------------------------------------------------- 地形状态（只读）

/** 只读地形状态卡：展示当前观测点与服务端本地瓦片覆盖摘要。 */
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
                if (state.terrainLoading) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                } else {
                    IconButton(onClick = { store.loadTerrain() }) {
                        Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.cd_refresh_terrain), Modifier.size(16.dp))
                    }
                }
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
        }
    }
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
