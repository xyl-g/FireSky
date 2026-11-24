import SwiftUI

/// 任务历史页（对齐 macOS NativeTaskHistoryView 空态、筛选与任务详情）。
struct HistoryView: View {
    @EnvironmentObject private var store: Store
    @State private var kindFilter = "all"

    private var visibleHistory: [MobileTaskHistoryItem] {
        switch kindFilter {
        case "active": return store.taskHistory.filter { !$0.isBackgroundSync }
        case "sync": return store.taskHistory.filter(\.isBackgroundSync)
        default: return store.taskHistory
        }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if !store.taskHistory.isEmpty {
                    Picker(L10n.text("筛选", "Filter"), selection: $kindFilter) {
                        Text(L10n.text("全部", "All")).tag("all")
                        Text(L10n.text("主动任务", "Active")).tag("active")
                        Text(L10n.text("后台同步", "Sync")).tag("sync")
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                }

                Group {
                    if store.taskHistoryLoading && store.taskHistory.isEmpty {
                        ProgressView(L10n.text("正在读取任务记录…", "Loading task records…"))
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if visibleHistory.isEmpty {
                        emptyState(
                            title: store.taskHistory.isEmpty
                                ? L10n.text("暂无任务记录", "No task records")
                                : L10n.text("没有符合筛选的任务", "No tasks match the filter"),
                            message: store.online
                                ? (store.taskHistory.isEmpty
                                   ? L10n.text("完成一次数据检查或预测后，任务记录与可恢复快照会显示在这里。",
                                               "Task records and restorable snapshots will appear here after a check or forecast.")
                                   : L10n.text("当前筛选条件下没有记录，可切换到「全部」查看。",
                                               "No records match this filter. Switch back to All to view everything."))
                                : L10n.text("引擎未连接。", "Engine is offline.")
                        )
                    } else {
                        historyList
                    }
                }
            }
            .workPageBackground()
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
            Text(L10n.text("只删除状态和事件，不删除已发布报告或天气数据。",
                           "Only status and events are removed; published reports and weather data are kept."))
        }
    }

    /// 统一空态（对齐 macOS NativeTaskHistoryView.emptyState）：FireSkyMarkScene + 标题 + 说明。
    private func emptyState(title: String, message: String) -> some View {
        VStack(spacing: 12) {
            FireSkyMarkScene(size: 64)
            Text(title).font(.subheadline.weight(.medium))
            Text(message)
                .font(.caption).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
    }

    private var historyList: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if !store.taskHistoryError.isEmpty {
                    Text(store.taskHistoryError)
                        .foregroundStyle(.red)
                        .font(.callout)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                }

                LazyVStack(spacing: 0) {
                    ForEach(Array(visibleHistory.enumerated()), id: \.element.id) { index, item in
                        HistoryRow(item: item)
                        if index < visibleHistory.count - 1 {
                            Divider()
                                .opacity(0.45)
                                .padding(.leading, 46)
                        }
                    }
                }
                .fireSkySurface(role: .content, cornerRadius: 14)

                if store.taskHistoryHasMore {
                    Button {
                        store.loadTaskHistory(older: true)
                    } label: {
                        HStack(spacing: 8) {
                            if store.taskHistoryLoading { ProgressView().controlSize(.small) }
                            Text(L10n.loadOlder).font(.callout.weight(.medium))
                        }
                        .frame(maxWidth: .infinity, minHeight: 42)
                    }
                    .buttonStyle(.plain)
                    .fireSkySurface(role: .grouped, cornerRadius: 10)
                    .disabled(store.taskHistoryLoading)
                }

                if !store.online && store.taskHistoryError.isEmpty {
                    Label(L10n.text("引擎未连接，显示的是上次读取的记录。", "Engine offline; showing previously loaded records."),
                          systemImage: "wifi.slash")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 4)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
    }
}

private struct HistoryRow: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    let item: MobileTaskHistoryItem

    private var isSelected: Bool {
        store.selectedTaskID == item.taskID
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: statusSymbol)
                    .foregroundStyle(statusColor)
                    .frame(width: 22, height: 22)
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
                        .foregroundStyle(.secondary)
                        .frame(minWidth: 32, minHeight: 32)
                }
            }
            if isSelected {
                TaskDetail(item: item)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(isSelected ? warm.opacity(scheme == .dark ? 0.10 : 0.06) : Color.clear)
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

private struct TaskDetail: View {
    @EnvironmentObject private var store: Store
    let item: MobileTaskHistoryItem

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Divider()
            if item.method == "forecast" && item.status == "done" {
                ForecastResultCard(taskID: item.taskID)
            }
            Text(L10n.text("事件", "Events")).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
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
            Text(L10n.text("删除记录只删除状态和事件，不影响报告与天气数据。",
                           "Deleting removes only status and events; reports and weather data are unaffected."))
                .font(.caption2).foregroundStyle(.secondary)
        }
    }
}

private struct ForecastResultCard: View {
    @EnvironmentObject private var store: Store
    let taskID: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(L10n.text("预测详情", "Forecast details")).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if store.taskResultTaskID != taskID {
                Text("—").font(.caption).foregroundStyle(.secondary)
            } else if store.taskResultLoading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(L10n.text("正在读取预测详情…", "Loading forecast details…")).font(.caption).foregroundStyle(.secondary)
                }
            } else if !store.taskResultError.isEmpty {
                Text("\(L10n.text("预测详情不可用", "Forecast details unavailable"))：\(store.taskResultError)")
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
                    .foregroundStyle(modelColor(model))
                if let error, !error.isEmpty {
                    Text("\(L10n.t("未完成"))：\(error)").font(.caption2).foregroundStyle(.red)
                        .textSelection(.enabled).lineLimit(2)
                } else if report == nil {
                    Text(L10n.t("暂无结果")).font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text("\(format(report?["peak_quality_index"] ?? report?["quality_index"])) / 2.5")
                        .font(.caption.weight(.medium)).monospacedDigit()
                    Text(report?["quality_level"] as? String ?? "")
                        .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    Spacer(minLength: 0)
                    Text("\(L10n.text("最佳", "Best")) \(timeLabel(report?["best_time_local"] as? String))")
                        .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                    Text("\(L10n.text("窗口", "Window")) \(windowLabel(report))")
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
