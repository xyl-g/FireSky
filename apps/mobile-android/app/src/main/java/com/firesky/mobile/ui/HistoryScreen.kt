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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.PauseCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Restore
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.StopCircle
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.firesky.mobile.store.TaskHistoryItem
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore

/** 任务历史页（macOS NativeTaskHistoryView 同构）：列表 + 详情（事件时间线），
 *  行内恢复结果 / 恢复参数重查 / 删除记录；删除只删状态与事件。 */
@Composable
fun HistoryScreen(store: WorkbenchStore, state: WorkbenchState) {
    Column(
        Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("任务历史", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (state.taskHistoryLoaded) "最近 ${state.taskHistory.size} 项 · 共 ${state.taskHistoryTotal} 项"
                else "记录数据检查、准备、预测和地形下载任务",
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                modifier = Modifier.weight(1f),
            )
            IconButton(
                onClick = { store.loadTaskHistory() },
                enabled = state.online && !state.taskHistoryLoading,
            ) {
                Icon(Icons.Filled.Refresh, contentDescription = "刷新任务历史", Modifier.size(16.dp))
            }
        }
        if (state.taskHistoryError.isNotEmpty()) {
            Text(state.taskHistoryError, fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
        }
        when {
            state.taskHistoryLoading && state.taskHistory.isEmpty() -> {
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 40.dp),
                    horizontalArrangement = Arrangement.Center,
                ) {
                    CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(12.dp))
                    Text("正在读取任务记录…", fontSize = 13.sp)
                }
            }
            state.taskHistory.isEmpty() -> {
                WorkSurface(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("暂无任务记录", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                        Text(
                            if (state.online) "完成一次检查或预测后会显示在这里。" else "引擎未连接。",
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                        )
                    }
                }
            }
            else -> LazyColumn(verticalArrangement = Arrangement.spacedBy(0.dp)) {
                state.taskHistory.forEach { item ->
                    item("row-${item.taskId}") {
                        HistoryRow(store, state, item)
                        HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.08f))
                    }
                }
                if (state.taskHistoryHasMore) {
                    item("load-more") {
                        Row(
                            Modifier.fillMaxWidth().clickable {
                                store.loadTaskHistory(older = true)
                            }.padding(vertical = 14.dp),
                            horizontalArrangement = Arrangement.Center,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            if (state.taskHistoryLoading) {
                                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.5.dp)
                                Spacer(Modifier.width(8.dp))
                            }
                            Text(
                                "加载更早任务",
                                fontSize = 12.sp,
                                color = MaterialTheme.colorScheme.primary,
                                fontWeight = FontWeight.Medium,
                            )
                        }
                    }
                }
            }
        }
    }
    if (state.pendingForgetTaskId != null) {
        AlertDialog(
            onDismissRequest = { store.setPendingForgetTask(null) },
            title = { Text("删除任务记录？", fontSize = 16.sp) },
            text = { Text("只删除状态和事件，不删除已发布报告或天气数据。", fontSize = 13.sp) },
            confirmButton = {
                Button(
                    onClick = { store.forgetPendingTask() },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFB5482F)),
                ) { Text("删除记录") }
            },
            dismissButton = {
                TextButton(onClick = { store.setPendingForgetTask(null) }) { Text("保留记录") }
            },
        )
    }
}

@Composable
private fun HistoryRow(store: WorkbenchStore, state: WorkbenchState, item: TaskHistoryItem) {
    val selected = state.selectedTaskId == item.taskId
    Column(
        Modifier
            .fillMaxWidth()
            .background(if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.10f) else Color.Transparent)
            .clickable { store.selectTask(item.taskId) }
            .padding(horizontal = 8.dp, vertical = 12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Icon(
                statusIcon(item.status),
                contentDescription = item.statusTitle,
                Modifier.size(20.dp),
                tint = statusColor(item.status),
            )
            Column(Modifier.weight(1f)) {
                Row(horizontalArrangement = Arrangement.spacedBy(7.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(item.rowTitle, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                    StatusChip(item.status, item.statusTitle)
                }
                if (item.contextTitle.isNotEmpty()) {
                    Text(item.contextTitle, fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
                }
                Text(
                    item.rowTimeLine,
                    fontSize = 10.sp,
                    color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f),
                )
            }
            Row {
                if (item.canRestoreResult) {
                    IconButton(
                        onClick = { store.restoreTaskResult(item) },
                        enabled = !state.busy && state.restoringTaskId == null,
                        modifier = Modifier.size(30.dp),
                    ) {
                        Icon(Icons.Filled.Restore, contentDescription = "恢复已发布结果", Modifier.size(16.dp))
                    }
                }
                if (item.retryPayload != null) {
                    IconButton(
                        onClick = { store.restoreTaskParameters(item) },
                        enabled = !state.busy && item.status != "running" && state.restoringTaskId == null,
                        modifier = Modifier.size(30.dp),
                    ) {
                        Icon(Icons.Filled.Refresh, contentDescription = "恢复参数并重新检查", Modifier.size(16.dp))
                    }
                }
                IconButton(
                    onClick = { store.setPendingForgetTask(item.taskId) },
                    enabled = !state.busy && item.status != "running",
                    modifier = Modifier.size(30.dp),
                ) {
                    Icon(Icons.Filled.Delete, contentDescription = "删除任务记录", Modifier.size(16.dp))
                }
            }
        }
        if (selected) {
            TaskDetail(store, state, item)
        }
    }
}

/** 详情：状态摘要 + 事件时间线（同 macOS 详情区）。 */
@Composable
private fun TaskDetail(store: WorkbenchStore, state: WorkbenchState, item: TaskHistoryItem) {
    Column(Modifier.padding(top = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        HorizontalDivider(color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.12f))
        Text("事件", fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        if (state.taskEventsLoading) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 1.5.dp)
                Text("正在读取事件…", fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
            }
        } else if (state.taskEventsError.isNotEmpty()) {
            Text(state.taskEventsError, fontSize = 11.sp, color = MaterialTheme.colorScheme.error)
        } else if (state.taskEvents.isEmpty()) {
            Text("没有已记录的事件。", fontSize = 11.sp, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f))
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                state.taskEvents.forEach { event ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(event.localTime, fontSize = 10.sp, fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f))
                        Spacer(Modifier.width(8.dp))
                        Text(event.title, fontSize = 11.sp, fontWeight = FontWeight.Medium)
                        Spacer(Modifier.width(6.dp))
                        Text(
                            event.detail,
                            fontSize = 10.sp,
                            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.6f),
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
            }
        }
        Text(
            "删除记录只删除状态和事件，不影响报告与天气数据。",
            fontSize = 10.sp,
            color = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.5f),
        )
    }
}

private fun statusIcon(status: String) = when (status) {
    "done" -> Icons.Filled.CheckCircle
    "failed" -> Icons.Filled.Error
    "cancelled" -> Icons.Filled.StopCircle
    "interrupted" -> Icons.Filled.PauseCircle
    else -> Icons.Filled.Schedule
}

@Composable
private fun statusColor(status: String) = when (status) {
    "done" -> Color(0xFF3B9C57)
    "failed" -> Color(0xFFC4574A)
    "interrupted" -> Color(0xFFC9A23C)
    else -> MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f)
}
