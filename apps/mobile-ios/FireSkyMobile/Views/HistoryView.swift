import SwiftUI

/// 任务历史页（macOS NativeTaskHistoryView 同构）：列表 + 点击展开详情
/// （事件时间线），行内恢复结果 / 恢复参数重查 / 删除记录；删除只删状态与事件。
struct HistoryView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        NavigationStack {
            Group {
                if store.taskHistoryLoading && store.taskHistory.isEmpty {
                    ProgressView("正在读取任务记录…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if store.taskHistory.isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: "clock.arrow.circlepath")
                            .font(.largeTitle).foregroundStyle(.secondary)
                        Text("暂无任务记录").font(.headline)
                        Text(store.online
                             ? "完成一次检查或预测后会显示在这里。"
                             : "引擎未连接。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    historyList
                }
            }
            .navigationTitle("任务历史")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        store.loadTaskHistory()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(!store.online || store.taskHistoryLoading)
                }
            }
        }
        .task(id: store.online) { store.loadTaskHistory() }
        .confirmationDialog("删除任务记录？", isPresented: Binding(
            get: { store.pendingForgetTaskID != nil },
            set: { if !$0 { store.setPendingForgetTask(nil) } }
        )) {
            Button("删除记录", role: .destructive) { store.forgetPendingTask() }
            Button("保留记录", role: .cancel) { store.setPendingForgetTask(nil) }
        } message: {
            Text("只删除状态和事件，不删除已发布报告或天气数据。")
        }
    }

    private var historyList: some View {
        List {
            if !store.taskHistoryError.isEmpty {
                Text(store.taskHistoryError).foregroundStyle(.red)
                    .font(.callout)
            }
            ForEach(store.taskHistory) { item in
                HistoryRow(item: item)
            }
            if store.taskHistoryHasMore {
                Button {
                    store.loadTaskHistory(older: true)
                } label: {
                    HStack(spacing: 8) {
                        if store.taskHistoryLoading { ProgressView() }
                        Text("加载更早任务").font(.callout)
                    }
                }
                .disabled(store.taskHistoryLoading)
                .frame(maxWidth: .infinity, alignment: .center)
            }
        }
        .listStyle(.plain)
    }
}

private struct HistoryRow: View {
    @EnvironmentObject private var store: Store
    let item: MobileTaskHistoryItem

    private var isSelected: Bool {
        store.selectedTaskID == item.taskID
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: statusSymbol)
                    .foregroundStyle(statusColor)
                    .frame(width: 22)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 7) {
                        Text(item.rowTitle).font(.callout.weight(.medium))
                        Text(item.statusTitle)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(statusColor)
                    }
                    if !item.contextTitle.isEmpty {
                        Text(item.contextTitle).font(.caption).foregroundStyle(.secondary)
                    }
                    Text(item.rowTimeLine).font(.caption2).foregroundStyle(.secondary)
                        .monospacedDigit()
                }
                Spacer(minLength: 4)
                HStack(spacing: 6) {
                    if item.canRestoreResult {
                        Button {
                            store.restoreTaskResult(item)
                        } label: {
                            Image(systemName: "arrow.uturn.left.circle")
                        }
                        .disabled(store.busy || store.restoringTaskID != nil)
                    }
                    if item.retryPayload != nil {
                        Button {
                            store.restoreTaskParameters(item)
                        } label: {
                            Image(systemName: "arrow.clockwise.circle")
                        }
                        .disabled(store.busy || item.status == "running" || store.restoringTaskID != nil)
                    }
                    Button(role: .destructive) {
                        store.setPendingForgetTask(item.taskID)
                    } label: {
                        Image(systemName: "trash")
                    }
                    .disabled(store.busy || item.status == "running")
                }
                .buttonStyle(.borderless)
            }
            if isSelected {
                TaskDetail(item: item)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { store.selectTask(item.taskID) }
    }

    private var statusSymbol: String {
        switch item.status {
        case "done": "checkmark.circle.fill"
        case "failed": "exclamationmark.circle.fill"
        case "cancelled": "stop.circle"
        case "interrupted": "pause.circle"
        default: "clock"
        }
    }

    private var statusColor: Color {
        switch item.status {
        case "done": .green
        case "failed": .red
        case "interrupted": .orange
        default: .secondary
        }
    }
}

/// 详情：事件时间线 + 删除口径说明（同 macOS 详情区）。
private struct TaskDetail: View {
    @EnvironmentObject private var store: Store
    let item: MobileTaskHistoryItem

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Divider()
            Text("事件").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if store.taskEventsLoading {
                ProgressView().controlSize(.small)
            } else if !store.taskEventsError.isEmpty {
                Text(store.taskEventsError).font(.caption).foregroundStyle(.red)
            } else if store.taskEvents.isEmpty {
                Text("没有已记录的事件。").font(.caption).foregroundStyle(.secondary)
            } else {
                ForEach(store.taskEvents) { event in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(event.localTime)
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                        Text(event.title).font(.caption.weight(.medium))
                        Text(event.detail).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            Text("删除记录只删除状态和事件，不影响报告与天气数据。")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }
}
