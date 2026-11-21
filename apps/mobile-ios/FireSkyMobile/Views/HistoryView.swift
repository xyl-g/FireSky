import SwiftUI

/// 任务历史页（macOS NativeTaskHistoryView 同构）：列表 + 点击展开详情
/// （事件时间线），行内恢复结果 / 恢复参数重查 / 删除记录；删除只删状态与事件。
struct HistoryView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        NavigationStack {
            Group {
                if store.taskHistoryLoading && store.taskHistory.isEmpty {
                    ProgressView(L10n.text("正在读取任务记录…", "Loading task records…"))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if store.taskHistory.isEmpty {
                    VStack(spacing: 10) {
                        FireSkyMark()
                            .frame(width: 32, height: 32)
                            .opacity(0.24)
                        Text(L10n.text("暂无任务记录", "No task records")).font(.system(size: 13, weight: .medium))
                        Text(store.online
                             ? L10n.text("完成一次检查或预测后会显示在这里。", "Shown after a check or forecast completes.")
                             : L10n.text("引擎未连接。", "Engine is offline."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    historyList
                }
            }
            .navigationTitle(L10n.navHistory)
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
        .confirmationDialog(L10n.text("删除任务记录？", "Delete task record?"), isPresented: Binding(
            get: { store.pendingForgetTaskID != nil },
            set: { if !$0 { store.setPendingForgetTask(nil) } }
        )) {
            Button(L10n.text("删除记录", "Delete record"), role: .destructive) { store.forgetPendingTask() }
            Button(L10n.text("保留记录", "Keep record"), role: .cancel) { store.setPendingForgetTask(nil) }
        } message: {
            Text(L10n.text("只删除状态和事件，不删除已发布报告或天气数据。", "Only status and events are removed; published reports and weather data are kept."))
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
                        Text(L10n.loadOlder).font(.callout)
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
                        StatusChip(status: item.status, title: L10n.status(item.statusTitle))
                    }
                    if !item.contextTitle.isEmpty {
                        Text(item.contextTitle).font(.caption).foregroundStyle(.secondary)
                    }
                    Text(item.rowTimeLine).font(.caption2).foregroundStyle(.secondary)
                        .monospacedDigit()
                }
                Spacer(minLength: 4)
                // 方案 D9：动作收进「…」菜单，危险项红字隔离。
                Menu {
                    if item.canRestoreResult {
                        Button(L10n.text("恢复已发布结果", "Restore published result")) { store.restoreTaskResult(item) }
                            .disabled(store.busy || store.restoringTaskID != nil)
                    }
                    if item.retryPayload != nil {
                        Button(L10n.text("恢复参数并重新检查数据", "Restore parameters & re-check")) { store.restoreTaskParameters(item) }
                            .disabled(store.busy || item.status == "running" || store.restoringTaskID != nil)
                    }
                    Divider()
                    Button(role: .destructive) {
                        store.setPendingForgetTask(item.taskID)
                    } label: {
                        Label(L10n.text("删除任务记录", "Delete task record"), systemImage: "trash")
                    }
                    .disabled(store.busy || item.status == "running")
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                
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

/// 详情：预测详情（结果快照）+ 事件时间线 + 删除口径说明（同 macOS 详情区）。
private struct TaskDetail: View {
    @EnvironmentObject private var store: Store
    let item: MobileTaskHistoryItem

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Divider()
            if item.method == "forecast" && item.status == "done" {
                ForecastResultCard(taskID: item.taskID)
            }
            Text("事件").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if store.taskEventsLoading {
                ProgressView().controlSize(.small)
            } else if !store.taskEventsError.isEmpty {
                Text(store.taskEventsError).font(.caption).foregroundStyle(.red)
            } else if store.taskEvents.isEmpty {
                Text(L10n.text("没有已记录的事件。", "No recorded events.")).font(.caption).foregroundStyle(.secondary)
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
            Text(L10n.text("删除记录只删除状态和事件，不影响报告与天气数据。", "Deleting removes only status and events; reports and weather data are unaffected."))
                .font(.caption2).foregroundStyle(.secondary)
        }
    }
}

/// v1.9.5 预测详情卡（macOS forecastResultCard 同构）：读取不可变结果
/// 快照，呈现该次预测双模型的关键信息；快照过期时给出可读说明。
private struct ForecastResultCard: View {
    @EnvironmentObject private var store: Store
    let taskID: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("预测详情").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if store.taskResultTaskID != taskID {
                Text("—").font(.caption).foregroundStyle(.secondary)
            } else if store.taskResultLoading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在读取预测详情…").font(.caption).foregroundStyle(.secondary)
                }
            } else if !store.taskResultError.isEmpty {
                Text("预测详情不可用：\(store.taskResultError)")
                    .font(.caption).foregroundStyle(.secondary)
                    .textSelection(.enabled)
            } else if let result = store.taskResult {
                VStack(alignment: .leading, spacing: 9) {
                    ForEach(["gfs", "ec"], id: \.self) { model in
                        modelRow(result, model)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func modelRow(_ result: JSONObject, _ model: String) -> some View {
        let errors = result["model_errors"] as? JSONObject
        let error = errors?[model] as? String
        let report = (result["model_results"] as? JSONObject)?[model] as? JSONObject
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text(model.uppercased()).font(.caption.weight(.semibold))
                    .foregroundStyle(model == "ec" ? .teal : .orange)
                if let error, !error.isEmpty {
                    Text("未完成：\(error)").font(.caption2).foregroundStyle(.red)
                        .textSelection(.enabled).lineLimit(2)
                } else if report == nil {
                    Text("暂无结果").font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text("\(format(report?["peak_quality_index"] ?? report?["quality_index"])) / 2.5")
                        .font(.caption.weight(.medium)).monospacedDigit()
                    Text(report?["quality_level"] as? String ?? "")
                        .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    Spacer(minLength: 0)
                    Text("最佳 \(timeLabel(report?["best_time_local"] as? String))")
                        .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                    Text("窗口 \(windowLabel(report))")
                        .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                }
            }
            if let summary = report?["summary"] as? String, !summary.isEmpty {
                Text(summary).font(.caption2).foregroundStyle(.secondary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
