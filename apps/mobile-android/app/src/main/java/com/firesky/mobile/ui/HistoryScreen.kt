package com.firesky.mobile.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.PauseCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.StopCircle
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.R
import com.firesky.mobile.store.TaskHistoryFilter
import com.firesky.mobile.store.TaskHistoryItem
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import com.firesky.mobile.store.filterTaskHistory
import org.json.JSONObject

/** 任务历史页（macOS NativeTaskHistoryView 同构）：列表 + 详情（事件时间线），
 *  行内恢复结果 / 恢复参数重查 / 删除记录；删除只删状态与事件。 */
@Composable
fun HistoryScreen(store: WorkbenchStore, state: WorkbenchState) {
    var historyFilter by rememberSaveable { mutableStateOf(TaskHistoryFilter.ALL) }
    val visibleTasks = filterTaskHistory(state.taskHistory, historyFilter)
    Column(
        Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                stringResource(R.string.history_title),
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onBackground,
            )
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (state.taskHistoryLoaded) stringResource(R.string.history_summary_fmt, state.taskHistory.size, state.taskHistoryTotal)
                    else stringResource(R.string.history_subtitle),
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                    modifier = Modifier.weight(1f),
                )
                IconButton(
                    onClick = { store.loadTaskHistory() },
                    enabled = state.online && !state.taskHistoryLoading,
                ) {
                    Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.cd_refresh_history), Modifier.size(16.dp))
                }
            }
        }
        val historyError = state.taskHistoryError.ifEmpty { state.errorText("history") }
        if (historyError.isNotEmpty()) {
            DismissibleErrorBanner(historyError, onDismiss = { store.clearError() })
        }
        if (state.taskHistory.isNotEmpty()) {
            Segmented(
                options = TaskHistoryFilter.entries.map { it.value },
                titles = listOf(
                    stringResource(R.string.history_filter_all),
                    stringResource(R.string.history_filter_active),
                    stringResource(R.string.history_filter_sync),
                ),
                selection = historyFilter.value,
                onSelect = { value ->
                    historyFilter = TaskHistoryFilter.entries.firstOrNull { it.value == value }
                        ?: TaskHistoryFilter.ALL
                },
                accent = true,
            )
        }
        when {
            state.taskHistoryLoading && state.taskHistory.isEmpty() -> {
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 40.dp),
                    horizontalArrangement = Arrangement.Center,
                ) {
                    CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(12.dp))
                    Text(stringResource(R.string.loading_tasks), fontSize = 13.sp)
                }
            }
            state.taskHistory.isEmpty() -> {
                WorkSurface(Modifier.fillMaxWidth()) {
                    Column(
                        Modifier.fillMaxWidth().padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        FireSkyMarkScene(size = 64.dp)
                        Text(stringResource(R.string.no_tasks), fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                        Text(
                            if (state.online) stringResource(R.string.no_tasks_online)
                            else stringResource(R.string.no_tasks_offline),
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                            textAlign = TextAlign.Center,
                        )
                        if (!state.online) {
                            Spacer(Modifier.height(2.dp))
                            SecondaryButton(stringResource(R.string.go_configure_server)) {
                                store.setSettingsCategory("高级")
                                store.setPage("settings")
                            }
                        }
                    }
                }
            }
            visibleTasks.isEmpty() -> {
                WorkSurface(Modifier.fillMaxWidth()) {
                    Column(
                        Modifier.fillMaxWidth().padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        FireSkyMarkScene(size = 56.dp)
                        Text(stringResource(R.string.history_filter_empty), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                        Text(
                            stringResource(R.string.history_filter_empty_hint),
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f),
                            textAlign = TextAlign.Center,
                        )
                    }
                }
            }
            else -> LazyColumn(
                verticalArrangement = Arrangement.spacedBy(10.dp),
                contentPadding = PaddingValues(bottom = 16.dp),
            ) {
                // 顶部品牌空态由上方 isEmpty 分支处理；列表分支内为悬浮任务卡片。
                visibleTasks.forEach { item ->
                    item("row-${item.taskId}") {
                        HistoryRow(store, state, item)
                    }
                }
                if (state.taskHistoryHasMore) {
                    item("load-more") {
                        WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 12.dp) {
                            Row(
                                Modifier.fillMaxWidth().clickable {
                                    store.loadTaskHistory(older = true)
                                }.padding(vertical = 12.dp),
                                horizontalArrangement = Arrangement.Center,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                if (state.taskHistoryLoading) {
                                    CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.5.dp)
                                    Spacer(Modifier.width(8.dp))
                                }
                                Text(
                                    stringResource(R.string.load_older),
                                    fontSize = 12.sp,
                                    color = MaterialTheme.colorScheme.primary,
                                    fontWeight = FontWeight.SemiBold,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun HistoryRow(store: WorkbenchStore, state: WorkbenchState, item: TaskHistoryItem) {
    val selected = state.selectedTaskId == item.taskId
    WorkSurface(
        modifier = Modifier.fillMaxWidth().testTag("history-task-${item.taskId}"),
        emphasized = selected,
        cornerRadius = 14.dp,
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .clickable { store.selectTask(item.taskId) }
                .padding(horizontal = 14.dp, vertical = 12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                IconBadge(statusIcon(item.status), tint = statusColor(item.status))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(7.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(localizedTaskTitle(item), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                        StatusChip(item.status, statusText(item.statusTitle))
                    }
                    if (item.contextTitle.isNotEmpty()) {
                        Text(item.contextTitle, fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.65f))
                    }
                    Text(
                        item.rowTimeLine,
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.52f),
                    )
                }
                if (item.canRestoreResult || item.retryPayload != null) {
                    var menuExpanded by remember { mutableStateOf(false) }
                    Box {
                        IconButton(
                            onClick = { menuExpanded = true },
                            enabled = !state.busy,
                            modifier = Modifier.size(30.dp),
                        ) {
                            Icon(Icons.Filled.MoreHoriz, contentDescription = stringResource(R.string.cd_task_actions), Modifier.size(16.dp))
                        }
                        DropdownMenu(
                            expanded = menuExpanded,
                            onDismissRequest = { menuExpanded = false },
                        ) {
                            if (item.canRestoreResult) {
                                DropdownMenuItem(
                                    text = { Text(stringResource(R.string.restore_result), fontSize = 12.sp) },
                                    onClick = {
                                        menuExpanded = false
                                        store.restoreTaskResult(item)
                                    },
                                    enabled = !state.busy && state.restoringTaskId == null,
                                )
                            }
                            if (item.retryPayload != null) {
                                DropdownMenuItem(
                                    text = { Text(stringResource(R.string.restore_params), fontSize = 12.sp) },
                                    onClick = {
                                        menuExpanded = false
                                        store.restoreTaskParameters(item)
                                    },
                                    enabled = !state.busy && item.status != "running" && state.restoringTaskId == null,
                                )
                            }
                        }
                    }
                }
            }
            if (selected) {
                TaskDetail(state, item)
            }
        }
    }
}

/** 详情：预测详情（结果快照）+ 事件时间线（同 macOS 详情区）。 */
@Composable
private fun TaskDetail(state: WorkbenchState, item: TaskHistoryItem) {
    Column(Modifier.padding(top = 10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.10f))
        if (item.method == "forecast" && item.status == "done") {
            ForecastResultCard(state, item.taskId)
        }
        Text(stringResource(R.string.events_title), fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        if (state.taskEventsLoading) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.5.dp)
                Text(stringResource(R.string.loading_events), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
            }
        } else if (state.taskEventsError.isNotEmpty()) {
            Text(state.taskEventsError, fontSize = 11.sp, color = MaterialTheme.colorScheme.error)
        } else if (state.taskEvents.isEmpty()) {
            Text(stringResource(R.string.no_events), fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        } else {
            WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 10.dp) {
                Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    state.taskEvents.forEach { event ->
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                event.localTime,
                                fontSize = 11.sp,
                                style = TextStyle(fontFeatureSettings = "tnum"),
                                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f),
                            )
                            Spacer(Modifier.width(8.dp))
                            Text(event.title, fontSize = 11.sp, fontWeight = FontWeight.Medium)
                            Spacer(Modifier.width(6.dp))
                            Text(
                                event.detail,
                                fontSize = 11.sp,
                                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.62f),
                                modifier = Modifier.weight(1f),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun localizedTaskTitle(item: TaskHistoryItem): String {
    val methodResource = when (item.method) {
        "check" -> R.string.history_method_check
        "prepare" -> R.string.history_method_prepare
        "forecast" -> R.string.history_method_forecast
        "publications" -> R.string.history_method_publications
        "network_probe" -> R.string.history_method_network_probe
        "data_sync" -> R.string.history_method_data_sync
        "terrain_download" -> R.string.history_method_terrain_download
        else -> R.string.history_method_task
    }
    val method = stringResource(methodResource)
    val kindResource = when (item.retryPayload?.optString("kind")) {
        "point" -> R.string.history_kind_point
        "regional" -> R.string.history_kind_regional
        "nowcast" -> R.string.history_kind_nowcast
        else -> null
    } ?: return method
    return stringResource(R.string.history_task_title_fmt, stringResource(kindResource), method)
}

private fun statusIcon(status: String) = when (status) {
    "done" -> Icons.Filled.CheckCircle
    "failed" -> Icons.Filled.Error
    "cancelled" -> Icons.Filled.StopCircle
    "interrupted" -> Icons.Filled.PauseCircle
    else -> Icons.Filled.Schedule
}

/** v1.9.5 预测详情卡（macOS forecastResultCard 同构）：读取不可变结果
 *  快照，呈现该次预测双模型的关键信息；快照过期时给出可读说明。 */
@Composable
private fun ForecastResultCard(state: WorkbenchState, taskId: String) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            stringResource(R.string.result_detail_title),
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
        )
        when {
            state.taskResultTaskId != taskId -> {}
            state.taskResultLoading -> {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.5.dp)
                    Text(
                        stringResource(R.string.result_detail_loading),
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                    )
                }
            }
            state.taskResultError.isNotEmpty() -> {
                Text(
                    stringResource(R.string.result_detail_unavailable, state.taskResultError),
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            }
            state.taskResult != null -> {
                WorkSurface(Modifier.fillMaxWidth(), grouped = true, cornerRadius = 10.dp) {
                    Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        val taskResult = state.taskResult!!
                        if (taskResult.has("nowcast") && !taskResult.has("model_results")) {
                            ForecastResultModelRow("NOWCAST", taskResult, "")
                        } else {
                            val results = taskResult.optJSONObject("model_results") ?: JSONObject()
                            val errors = taskResult.optJSONObject("model_errors") ?: JSONObject()
                            for (model in COMPARISON_MODELS) {
                                ForecastResultModelRow(model, results.optJSONObject(model), errors.optString(model))
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ForecastResultModelRow(model: String, report: JSONObject?, error: String) {
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(7.dp)) {
            Text(model.uppercase(), fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
            if (error.isNotEmpty()) {
                Text(
                    stringResource(R.string.model_incomplete, model.uppercase(), error),
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.error,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            } else if (report == null) {
                Text(
                    stringResource(R.string.status_no_result),
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            } else {
                Text(
                    "${fmt(report.optNum("peak_quality_index") ?: report.optNum("quality_index"))} / 2.5" +
                        (report.optString("quality_level").takeIf { it.isNotEmpty() }?.let { " · $it" } ?: ""),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Medium,
                    style = TextStyle(fontFeatureSettings = "tnum"),
                )
                Spacer(Modifier.weight(1f))
                Text(
                    "${stringResource(R.string.best_time)} ${timeLabel(report.optString("best_time_local"))}",
                    fontSize = 11.sp,
                    style = TextStyle(fontFeatureSettings = "tnum"),
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
                Text(
                    stringResource(R.string.recommended_window, windowLabel(report)),
                    fontSize = 11.sp,
                    style = TextStyle(fontFeatureSettings = "tnum"),
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                )
            }
        }
        val summary = report?.optString("summary").orEmpty()
        if (report != null && summary.isNotEmpty()) {
            Text(
                summary,
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
            )
        }
    }
}

@Composable
private fun statusColor(status: String) = when (status) {
    "done" -> Color(0xFF3B9C57)
    "failed" -> Color(0xFFC4574A)
    "interrupted" -> Color(0xFFC9A23C)
    else -> MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f)
}
