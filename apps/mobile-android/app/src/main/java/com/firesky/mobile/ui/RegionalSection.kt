package com.firesky.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.LocationCity
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.R
import com.firesky.mobile.store.RegionalCell
import com.firesky.mobile.store.RegionalModel
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import com.firesky.mobile.store.parseRegionalForecast
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sinh
import kotlin.math.sqrt
import kotlin.math.tan

/**
 * 区域预测模式（RegionalForecast.swift / RegionalMapLayers.swift / Workbench.swift 同构）：
 * 区域预设 + GFS/EC 切换 + Mercator 质量热力图 Canvas + 图例 + 网格详情弹窗 + 城市榜单。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RegionalControlsCard(store: WorkbenchStore, state: WorkbenchState) {
    var regionDropdownExpanded by remember { mutableStateOf(false) }
    val selectedPreset = state.selectedRegionPreset

    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                IconBadge(Icons.Filled.Public, tint = MaterialTheme.colorScheme.primary)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(1.dp)) {
                    Text(stringResource(R.string.regional_scope_title), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        stringResource(
                            R.string.regional_bounds_fmt,
                            selectedPreset.bounds.getOrElse(0) { 73.0 },
                            selectedPreset.bounds.getOrElse(2) { 136.0 },
                            selectedPreset.bounds.getOrElse(1) { 18.0 },
                            selectedPreset.bounds.getOrElse(3) { 54.0 },
                        ),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.60f),
                    )
                }
            }

            Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                Text(
                    stringResource(R.string.regional_preset_label),
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                )
                ExposedDropdownMenuBox(
                    expanded = regionDropdownExpanded,
                    onExpandedChange = { regionDropdownExpanded = it },
                ) {
                    OutlinedTextField(
                        value = selectedPreset.label,
                        onValueChange = {},
                        readOnly = true,
                        shape = SoftInputShape,
                        colors = softInputColors(),
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(regionDropdownExpanded) },
                        textStyle = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.menuAnchor().fillMaxWidth(),
                    )
                    ExposedDropdownMenu(
                        expanded = regionDropdownExpanded,
                        onDismissRequest = { regionDropdownExpanded = false },
                    ) {
                        state.availableRegions.forEach { preset ->
                            DropdownMenuItem(
                                text = { Text("${preset.label} (${preset.code})", fontSize = 13.sp) },
                                onClick = {
                                    store.selectRegionalPreset(preset.code)
                                    regionDropdownExpanded = false
                                },
                            )
                        }
                    }
                }
            }

            Text(
                stringResource(R.string.regional_model_label),
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
            )
            Segmented(
                options = listOf("gfs", "ec"),
                titles = listOf("GFS 0.25°", "EC Open Data"),
                selection = state.regionalModel,
                onSelect = { store.setRegionalModel(it) },
                accent = true,
            )
        }
    }
}

@Composable
fun RegionalResultSection(store: WorkbenchStore, state: WorkbenchState) {
    val activeResult = if (state.resultKind == "regional") state.result else null
    if (activeResult == null) {
        RegionalEmptyCard(state)
        return
    }

    val forecast = remember(activeResult) { parseRegionalForecast(activeResult) }
    val modelName = state.regionalModel
    val model = forecast.model(modelName)

    RegionalHeroSummaryCard(store, state, modelName, model)

    RegionalHeatmapCard(
        store = store,
        state = state,
        modelName = modelName,
        model = model,
        bounds = state.selectedRegionPreset.bounds,
    )

    state.selectedRegionalCell?.let { cell ->
        RegionalCellDetailCard(store, modelName, cell)
    }

    RegionalRankingCard(store, state, modelName, model)
}

@Composable
private fun RegionalEmptyCard(state: WorkbenchState) {
    val dark = LocalFireSkyDark.current
    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                IconBadge(Icons.Filled.Map, tint = coolAccentColor())
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(stringResource(R.string.regional_empty_title), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        stringResource(R.string.regional_empty_body),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    )
                }
            }
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(210.dp)
                    .clip(RoundedCornerShape(16.dp))
                    .border(0.75.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.45f), RoundedCornerShape(16.dp)),
            ) {
                RegionalLeafletMap(
                    cells = emptyList(),
                    cities = emptyList(),
                    bounds = state.selectedRegionPreset.bounds,
                    selectedCell = null,
                    dark = dark,
                    modifier = Modifier.fillMaxSize(),
                    onTapLatLon = { _, _ -> },
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RegionalHeroSummaryCard(
    store: WorkbenchStore,
    state: WorkbenchState,
    modelName: String,
    model: RegionalModel,
) {
    val accent = if (modelName == "ec") coolAccentColor() else MaterialTheme.colorScheme.primary
    val tnumStyle = TextStyle(fontFeatureSettings = "tnum")

    WorkSurface(Modifier.fillMaxWidth(), emphasized = true) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "${state.resultTitle} " + stringResource(
                        if (state.resultPhenomenon == "dawn") R.string.phenomenon_dawn else R.string.phenomenon_dusk,
                    ),
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                    modifier = Modifier.weight(1f),
                )
                SoftPillChip(text = "${modelName.uppercase()} · ${statusText(model.status)}", tint = accent)
            }

            if (state.resultParametersStale) {
                Surface(
                    Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(10.dp),
                    color = MaterialTheme.colorScheme.primary.copy(alpha = 0.10f),
                ) {
                    Row(
                        Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            stringResource(R.string.stale_result_banner),
                            fontSize = 11.sp,
                            modifier = Modifier.weight(1f),
                        )
                        TextButton(onClick = { store.beginCheck() }, enabled = !state.busy && state.online) {
                            Text(stringResource(R.string.rerun_with_new_params), fontSize = 11.sp)
                        }
                    }
                }
            }

            if (model.error != null) {
                Text(model.error, fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
            }

            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(stringResource(R.string.regional_peak_label), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text(
                            fmt(model.peak?.quality),
                            fontSize = 34.sp,
                            fontWeight = FontWeight.Bold,
                            style = tnumStyle,
                            color = if (model.peak?.quality != null) accent else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.45f),
                        )
                        Spacer(Modifier.width(4.dp))
                        Text("/ 2.5", fontSize = 12.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f), modifier = Modifier.padding(bottom = 5.dp))
                    }
                    model.peak?.let { peak ->
                        Text(
                            "${peak.label} · ${timeLabel(peak.bestTime)}",
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.72f),
                        )
                    }
                }
                Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        stringResource(R.string.regional_valid_high_fmt, model.validCount, model.highCount),
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        style = tnumStyle,
                    )
                    Text(
                        if (model.missingCount > 0) stringResource(R.string.regional_missing_fmt, model.missingCount)
                        else stringResource(R.string.regional_no_missing),
                        fontSize = 11.sp,
                        color = if (model.missingCount > 0) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                }
            }

            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                model.precision?.let { ChipText(it) }
                model.cycle?.let { ChipText(stringResource(R.string.cycle_chip_fmt, it.take(16).replace("T", " "))) }
                if (model.cacheStatus == "hit") {
                    ChipText(stringResource(R.string.regional_cache_hit))
                }
            }
        }
    }
}

@Composable
private fun RegionalHeatmapCard(
    store: WorkbenchStore,
    state: WorkbenchState,
    modelName: String,
    model: RegionalModel,
    bounds: List<Double>,
) {
    val dark = LocalFireSkyDark.current
    val west = bounds.getOrElse(0) { 73.0 }
    val south = bounds.getOrElse(1) { 18.0 }
    val east = bounds.getOrElse(2) { 136.0 }
    val north = bounds.getOrElse(3) { 54.0 }
    val mapBg = if (dark) Color(0xFF0E1728) else Color(0xFFE9EFF6)
    val gridLineColor = if (dark) Color.White.copy(alpha = 0.09f) else Color(0xFF2A3C58).copy(alpha = 0.12f)
    val missingColor = if (dark) Color.White.copy(alpha = 0.08f) else Color.Black.copy(alpha = 0.06f)
    val selectedColor = if (dark) Color(0xFFFFE082) else Color(0xFF1A237E)

    val allCells = model.cells
    val minLat = allCells.minOfOrNull { it.lat }?.let { min(it - 0.5, south) } ?: south
    val maxLat = allCells.maxOfOrNull { it.lat }?.let { max(it + 0.5, north) } ?: north
    val minLon = allCells.minOfOrNull { it.lon }?.let { min(it - 0.5, west) } ?: west
    val maxLon = allCells.maxOfOrNull { it.lon }?.let { max(it + 0.5, east) } ?: east

    val yMin = mercatorY(maxLat.coerceIn(-85.0, 85.0))
    val yMax = mercatorY(minLat.coerceIn(-85.0, 85.0))
    val ySpan = (yMax - yMin).coerceAtLeast(1e-6)
    val xSpan = (maxLon - minLon).coerceAtLeast(1e-6)

    // 计算网格实际步长用于渲染矩形块大小
    val latStep = remember(allCells) {
        val lats = allCells.map { it.lat }.distinct().sorted()
        lats.zipWithNext { a, b -> b - a }.filter { it > 0.05 }.minOrNull() ?: max(0.25, model.spacingDeg)
    }
    val lonStep = remember(allCells) {
        val lons = allCells.map { it.lon }.distinct().sorted()
        lons.zipWithNext { a, b -> b - a }.filter { it > 0.05 }.minOrNull() ?: max(0.25, model.spacingDeg)
    }

    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                IconBadge(Icons.Filled.Map, tint = coolAccentColor())
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(stringResource(R.string.regional_heatmap_title), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    Text(stringResource(R.string.regional_heatmap_hint), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f))
                }
                Text(modelName.uppercase(), fontSize = 11.sp, fontWeight = FontWeight.Bold, color = coolAccentColor())
            }

            Box(
                Modifier
                    .fillMaxWidth()
                    .height(260.dp)
                    .clip(RoundedCornerShape(16.dp))
                    .background(mapBg)
                    .border(0.75.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.45f), RoundedCornerShape(16.dp)),
            ) {
                RegionalLeafletMap(
                    cells = allCells,
                    cities = model.cityRanking,
                    bounds = bounds,
                    selectedCell = state.selectedRegionalCell,
                    dark = dark,
                    modifier = Modifier.fillMaxSize(),
                    onTapLatLon = { tapLat, tapLon ->
                        val nearest = (allCells + model.cityRanking).minByOrNull { c ->
                            val dLat = c.lat - tapLat
                            val dLon = c.lon - tapLon
                            dLat * dLat + dLon * dLon
                        }
                        if (nearest != null) {
                            val threshold = max(1.5, max(latStep, lonStep) * 1.4)
                            if (abs(nearest.lat - tapLat) <= threshold && abs(nearest.lon - tapLon) <= threshold) {
                                store.selectRegionalCell(nearest)
                            }
                        }
                    },
                )
            }

            RegionalLegendRow()
        }
    }
}

@Composable
private fun RegionalLegendRow() {
    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(stringResource(R.string.regional_legend_quality), fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Row(Modifier.fillMaxWidth().height(7.dp).clip(CircleShape)) {
                for (step in 0..24) {
                    val q = step / 24.0 * 2.5
                    Box(Modifier.weight(1f).height(7.dp).background(regionalDuskColor(q)))
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                for (label in listOf("0", "0.5", "1.0", "1.5", "2.0", "2.5")) {
                    Text(label, fontSize = 9.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f))
                }
            }
        }
    }
}

@Composable
private fun RegionalCellDetailCard(store: WorkbenchStore, modelName: String, cell: RegionalCell) {
    val accent = if (modelName == "ec") coolAccentColor() else MaterialTheme.colorScheme.primary
    val tnumStyle = TextStyle(fontFeatureSettings = "tnum")

    WorkSurface(Modifier.fillMaxWidth(), grouped = true) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(cell.label, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    Text(cell.coordinateLabel, fontSize = 11.sp, style = tnumStyle, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                }
                IconButton(onClick = { store.selectRegionalCell(null) }, modifier = Modifier.size(28.dp)) {
                    Icon(Icons.Filled.Close, contentDescription = stringResource(R.string.dismiss), Modifier.size(16.dp))
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                WorkSurface(Modifier.weight(1f), cornerRadius = 10.dp) {
                    Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(stringResource(R.string.regional_cell_peak), fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                        Text(fmt(cell.peakQuality ?: cell.quality, 2), fontSize = 18.sp, fontWeight = FontWeight.Bold, style = tnumStyle, color = accent)
                    }
                }
                WorkSurface(Modifier.weight(1f), cornerRadius = 10.dp) {
                    Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(stringResource(R.string.best_time), fontSize = 10.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                        Text(timeLabel(cell.bestTime), fontSize = 18.sp, fontWeight = FontWeight.Bold, style = tnumStyle)
                    }
                }
            }
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                DetailRow(stringResource(R.string.regional_cell_family), cell.pressureFamily?.let { familyDisplayName(it) } ?: stringResource(R.string.not_provided))
                DetailRow(stringResource(R.string.metric_burnable), cell.cloudCoverageEffective?.let { "%.1f%%".format(it * 100) } ?: stringResource(R.string.not_computed))
                DetailRow(stringResource(R.string.metric_duration), cell.durationMinutes?.let { "%.0f min".format(it) } ?: stringResource(R.string.not_computed))
                DetailRow(stringResource(R.string.metric_aod), cell.qualityAod?.let { "%.3f".format(it) } ?: stringResource(R.string.not_computed))
                DetailRow(stringResource(R.string.metric_confidence), cell.confidenceScore?.let { "${(it * 100).roundToInt()}%" } ?: stringResource(R.string.not_provided))
            }
            TextButton(
                onClick = { store.openRegionalCellInPointForecast(cell, modelName) },
                modifier = Modifier.align(Alignment.End),
            ) {
                Icon(Icons.AutoMirrored.Filled.OpenInNew, contentDescription = null, modifier = Modifier.size(15.dp))
                Spacer(Modifier.width(5.dp))
                Text(stringResource(R.string.regional_open_in_point), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }
        }
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f))
        Spacer(Modifier.weight(1f))
        Text(value, fontSize = 11.sp, fontWeight = FontWeight.Medium, style = TextStyle(fontFeatureSettings = "tnum"))
    }
}

@Composable
private fun RegionalRankingCard(
    store: WorkbenchStore,
    state: WorkbenchState,
    modelName: String,
    model: RegionalModel,
) {
    val q = state.regionalQuery.trim()
    val rows = remember(model, state.regionalInspectorMode, q) {
        val base = if (state.regionalInspectorMode == "cities") model.cityRanking else model.ranked
        val filtered = if (q.isEmpty()) base else base.filter { it.label.contains(q, ignoreCase = true) || it.coordinateLabel.contains(q, ignoreCase = true) }
        filtered.take(30)
    }
    val accent = if (modelName == "ec") coolAccentColor() else MaterialTheme.colorScheme.primary

    WorkSurface(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                IconBadge(Icons.Filled.LocationCity, tint = accent)
                Text(
                    stringResource(
                        if (state.regionalInspectorMode == "cities") R.string.regional_city_ranking
                        else R.string.regional_grid_ranking,
                    ),
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                Text(modelName.uppercase(), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f))
            }

            Segmented(
                options = listOf("cities", "grid"),
                titles = listOf(stringResource(R.string.regional_tab_cities), stringResource(R.string.regional_tab_grid)),
                selection = state.regionalInspectorMode,
                onSelect = { store.setRegionalInspectorMode(it) },
            )

            OutlinedTextField(
                value = state.regionalQuery,
                onValueChange = store::setRegionalQuery,
                singleLine = true,
                shape = SoftInputShape,
                colors = softInputColors(),
                leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null, modifier = Modifier.size(16.dp)) },
                placeholder = {
                    Text(
                        stringResource(
                            if (state.regionalInspectorMode == "cities") R.string.regional_search_city
                            else R.string.regional_search_grid,
                        ),
                        fontSize = 12.sp,
                    )
                },
                textStyle = MaterialTheme.typography.bodySmall,
                modifier = Modifier.fillMaxWidth(),
            )

            if (rows.isEmpty()) {
                val msg = if (state.regionalInspectorMode == "cities" && model.cityRankingStatus == "unavailable") {
                    model.cityRankingReason ?: stringResource(R.string.regional_no_cities)
                } else {
                    stringResource(R.string.regional_no_matching_cells)
                }
                Text(msg, fontSize = 12.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f), modifier = Modifier.padding(vertical = 12.dp))
            } else {
                rows.forEachIndexed { index, cell ->
                    val selected = state.selectedRegionalCell?.key == cell.key
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .background(if (selected) accent.copy(alpha = 0.12f) else Color.Transparent)
                            .clickable { store.selectRegionalCell(cell) }
                            .padding(horizontal = 8.dp, vertical = 9.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text(
                            "${index + 1}",
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Bold,
                            color = if (index < 3) accent else MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
                            modifier = Modifier.width(24.dp),
                            style = TextStyle(fontFeatureSettings = "tnum"),
                        )
                        Column(Modifier.weight(1f)) {
                            Text(cell.label, fontSize = 13.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(
                                "${timeLabel(cell.bestTime)} · ${cell.level}",
                                fontSize = 11.sp,
                                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.58f),
                            )
                        }
                        Text(
                            fmt(cell.quality, 2),
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Bold,
                            color = cell.quality?.let { regionalDuskColor(it).copy(alpha = 1f) } ?: MaterialTheme.colorScheme.onBackground.copy(alpha = 0.45f),
                            style = TextStyle(fontFeatureSettings = "tnum"),
                        )
                    }
                    if (index < rows.size - 1) {
                        HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f))
                    }
                }
            }
        }
    }
}

private fun mercatorY(latDeg: Double): Double {
    val rad = latDeg * PI / 180.0
    return ln(tan(PI / 4.0 + rad / 2.0))
}

private fun inverseMercatorY(y: Double): Double =
    (2.0 * atan(kotlin.math.exp(y)) - PI / 2.0) * 180.0 / PI

/** 匹配 RegionalMapLayers.swift RegionalPalette 的晚霞色阶（0–2.5）。 */
private fun regionalDuskColor(quality: Double): Color {
    val t = (quality / 2.5).coerceIn(0.0, 1.0)
    val stops = listOf(
        0.0 to floatArrayOf(253f, 230f, 220f),
        0.2 to floatArrayOf(251f, 176f, 122f),
        0.45 to floatArrayOf(245f, 124f, 102f),
        0.7 to floatArrayOf(237f, 91f, 130f),
        1.0 to floatArrayOf(201f, 60f, 145f),
    )
    for (i in 1 until stops.size) {
        val (rightAt, rightRgb) = stops[i]
        if (t <= rightAt) {
            val (leftAt, leftRgb) = stops[i - 1]
            val f = ((t - leftAt) / (rightAt - leftAt)).toFloat()
            val r = ((leftRgb[0] + (rightRgb[0] - leftRgb[0]) * f) / 255f).coerceIn(0f, 1f)
            val g = ((leftRgb[1] + (rightRgb[1] - leftRgb[1]) * f) / 255f).coerceIn(0f, 1f)
            val b = ((leftRgb[2] + (rightRgb[2] - leftRgb[2]) * f) / 255f).coerceIn(0f, 1f)
            val alpha = if (t <= 0.001) 0.10f else ((42.0 + 190.0 * sqrt(t)) / 255.0).toFloat().coerceIn(0.15f, 0.92f)
            return Color(r, g, b, alpha)
        }
    }
    return Color(201f / 255f, 60f / 255f, 145f / 255f, 0.91f)
}
