import SwiftUI

/// Tolerates both plain and fractional-second ISO8601 stamps: engine task
/// events carry `.300Z` while some receipts use whole seconds.
func taskTimestamp(_ value: String) -> Date? {
    ObservationDate.parseISO(value)
}

struct NativeRetryRequest {
    let kind: Page
    let payload: JSONObject
}

struct NativeTaskHistoryItem: Identifiable {
    let taskID: String
    let method: String
    let status: String
    let createdAtUTC: String
    let updatedAtUTC: String
    let cancelRequested: Bool
    let eventSequence: Int
    let retryRequest: NativeRetryRequest?
    let resultReference: JSONObject?
    let progress: JSONObject

    var id: String { taskID }

    init?(_ value: JSONObject) {
        guard let taskID = value["task_id"] as? String,
              taskID.count == 64,
              taskID.allSatisfy({ $0.isHexDigit }),
              let method = value["method"] as? String,
              let status = value["status"] as? String,
              ["running", "done", "failed", "cancelled", "interrupted"].contains(status),
              let createdAtUTC = value["created_at_utc"] as? String,
              taskTimestamp(createdAtUTC) != nil,
              let updatedAtUTC = value["updated_at_utc"] as? String,
              taskTimestamp(updatedAtUTC) != nil else {
            return nil
        }
        let eventSequence = (value["event_seq"] as? NSNumber)?.intValue ?? 0
        guard eventSequence >= 1 else { return nil }
        let retryObject = value["retry_request"] as? JSONObject
        let retryKind = retryObject?["kind"] as? String
        let retryPayload = retryObject?["payload"] as? JSONObject
        let retry: NativeRetryRequest?
        if let retryKind, let retryPayload,
           let kind = Page(rawValue: retryKind),
           [.point, .regional, .nowcast].contains(kind) {
            retry = NativeRetryRequest(kind: kind, payload: retryPayload)
        } else {
            retry = nil
        }
        self.taskID = taskID
        self.method = method
        self.status = status
        self.createdAtUTC = createdAtUTC
        self.updatedAtUTC = updatedAtUTC
        self.cancelRequested = value["cancel_requested"] as? Bool ?? false
        self.eventSequence = eventSequence
        self.retryRequest = retry
        self.resultReference = value["result_ref"] as? JSONObject
        self.progress = value["progress"] as? JSONObject ?? [:]
    }

    var kindTitle: String {
        switch retryRequest?.kind {
        case .point: L10n.text("单点", "Point")
        case .regional: L10n.text("区域", "Regional")
        case .nowcast: L10n.text("实况", "Nowcast")
        default: methodTitle
        }
    }

    var methodTitle: String {
        switch method {
        case "check": L10n.text("数据检查", "Data check")
        case "prepare": L10n.text("数据准备", "Data preparation")
        case "forecast": L10n.text("预测计算", "Forecast")
        case "publications": L10n.text("发布核验", "Publication check")
        case "network_probe": L10n.text("下载探测", "Download probe")
        case "data_sync": L10n.text("后台同步", "Background sync")
        case "terrain_download": L10n.text("地形下载", "Terrain download")
        default: L10n.text("任务", "Task")
        }
    }

    /// UX #13：行标题去重（无重试类别时 kindTitle 与 methodTitle 相同）。
    var rowTitle: String {
        kindTitle == methodTitle ? methodTitle : "\(kindTitle) · \(methodTitle)"
    }

    /// UX #13：后台同步轮次默认折叠，避免淹没用户主动发起的任务。
    var isBackgroundSync: Bool { method == "data_sync" }

    var statusTitle: String {
        switch status {
        case "running": L10n.status(cancelRequested ? "取消中" : "运行中")
        case "done": L10n.status("已完成")
        case "failed": L10n.status("失败")
        case "cancelled": L10n.status("已取消")
        case "interrupted": L10n.status("已中断")
        default: status
        }
    }

    var contextTitle: String {
        guard let payload = retryRequest?.payload else {
            // UX #13：后台同步没有地点参数，给出明确说明而不是留白。
            return isBackgroundSync ? L10n.text("后台数据同步轮次", "Background sync round") : ""
        }
        let name = payload["name"] as? String
            ?? (payload["regions"] as? [String])?.joined(separator: "、")
            ?? ""
        let date = payload["target_date"] as? String ?? ""
        let phenomenon: String
        switch payload["phenomenon"] as? String {
        case "dawn": phenomenon = L10n.t("朝霞")
        case "dusk": phenomenon = L10n.t("晚霞")
        default: phenomenon = ""
        }
        let text = [name, date, phenomenon].filter { !$0.isEmpty }.joined(separator: " · ")
        if text.isEmpty && isBackgroundSync { return L10n.text("后台数据同步轮次", "Background sync round") }
        return text
    }

    var canRestoreResult: Bool {
        guard status == "done", let snapshot = resultReference?["snapshot"] as? JSONObject else { return false }
        return snapshot["contract"] as? String == "firesky-result-snapshot-v1"
            && snapshot["sha256"] is String
    }
}

struct NativeTaskEvent: Identifiable {
    let sequence: Int
    let atUTC: String
    let event: String
    let data: JSONObject

    var id: Int { sequence }

    var title: String {
        switch event {
        case "running": L10n.text("开始", "Started")
        case "progress": L10n.text("进度", "Progress")
        case "cancel_requested": L10n.text("请求取消", "Cancel requested")
        case "done": L10n.text("完成", "Done")
        case "failed": L10n.status("失败")
        case "cancelled": L10n.status("已取消")
        case "interrupted": L10n.status("已中断")
        default: L10n.text("状态更新", "Status update")
        }
    }

    var detail: String {
        var values: [String] = []
        for key in ["stage", "source"] {
            if let value = data[key] as? String, !value.isEmpty {
                values.append(String(value.prefix(128)))
            }
        }
        if let percent = (data["percent"] as? NSNumber)?.doubleValue, percent.isFinite {
            values.append(String(format: "%.0f%%", percent))
        }
        return values.joined(separator: " · ")
    }
}

/// 历史页筛选状态。CommandLineTools 环境无 SwiftUIMacros，使用普通 ObservableObject。
private final class HistoryFilterState: ObservableObject {
    @Published var showSyncRounds = false
    @Published var kindFilter = "all"
}

private final class HistoryRestoreConfirmation: ObservableObject {
    @Published var taskID: String?
}

/// 任务历史独立侧栏页面：左侧列表 + 右侧详情区，支持类型/状态筛选。
struct NativeTaskHistoryView: View {
    @EnvironmentObject private var store: Store
    @StateObject private var filters = HistoryFilterState()
    @StateObject private var restoreConfirmation = HistoryRestoreConfirmation()

    private var visibleHistory: [NativeTaskHistoryItem] {
        switch filters.kindFilter {
        case "active": store.taskHistory.filter { !$0.isBackgroundSync }
        case "sync": store.taskHistory.filter(\.isBackgroundSync)
        default: filters.showSyncRounds ? store.taskHistory : store.taskHistory.filter { !$0.isBackgroundSync }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            HStack(alignment: .top, spacing: 0) {
                listColumn
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                if store.selectedTaskHistoryID != nil {
                    Divider()
                    detailColumn
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task { store.loadTaskHistory() }
        .confirmationDialog(L10n.text("删除任务记录？", "Delete task record?"), isPresented: Binding(
            get: { store.pendingForgetTaskID != nil },
            set: { if !$0 { store.pendingForgetTaskID = nil } }
        )) {
            Button(L10n.text("删除记录", "Delete record"), role: .destructive) {
                if let taskID = store.pendingForgetTaskID {
                    store.forgetTaskHistory(taskID)
                }
                store.pendingForgetTaskID = nil
            }
            Button(L10n.text("保留记录", "Keep record"), role: .cancel) { store.pendingForgetTaskID = nil }
        } message: {
            Text(L10n.text("只删除状态和事件，不删除已发布报告或天气数据。", "Only status and events are removed; published reports and weather data are kept."))
        }
        .confirmationDialog(L10n.text("查看该次结果？", "View this result?"), isPresented: Binding(
            get: { restoreConfirmation.taskID != nil },
            set: { if !$0 { restoreConfirmation.taskID = nil } }
        )) {
            Button(L10n.text("恢复并查看", "Restore and view")) {
                if let taskID = restoreConfirmation.taskID,
                   let item = store.taskHistory.first(where: { $0.taskID == taskID }) {
                    store.restoreTaskResult(item)
                }
                restoreConfirmation.taskID = nil
            }
            .disabled(store.busy || store.restoringTaskHistoryID != nil)
            Button(L10n.text("取消", "Cancel"), role: .cancel) { restoreConfirmation.taskID = nil }
        } message: {
            Text(L10n.text("工作台会切到该次已发布结果并回填其原始参数；当前未保存的输入会被替换。恢复结果不会重新检查数据。", "The workbench switches to this published result and restores its original parameters; unsaved inputs will be replaced. No data check is re-run."))
        }
    }

    /// 统一空态（方案 4.6）：品牌小图 24% + 标题 + 说明，四端同构。
    private func emptyState(title: String, message: String) -> some View {
        VStack(spacing: 10) {
            FireSkyMarkScene(size: 64)
            Text(title).font(.system(size: 13, weight: .medium))
            Text(message).font(.caption).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text(store.taskHistoryLoaded
                 ? L10n.text("最近 \(store.taskHistory.count) 项 · 共 \(store.taskHistoryTotal) 项", "Recent \(store.taskHistory.count) · total \(store.taskHistoryTotal)")
                 : L10n.text("记录数据检查、准备、预测和下载探测任务", "Records of checks, preparations, forecasts and probes"))
                .font(.caption).foregroundStyle(.secondary)
            if store.taskHistoryIssues > 0 {
                Text("\(store.taskHistoryIssues) 项无法核验")
                    .font(.caption).foregroundStyle(.orange)
            }
            Spacer()
            // 筛选：主动任务 / 后台同步轮次。
            Picker("", selection: $filters.kindFilter) {
                Text(L10n.text("全部", "All")).tag("all")
                Text(L10n.text("主动任务", "Active")).tag("active")
                Text(L10n.text("后台同步", "Sync")).tag("sync")
            }.pickerStyle(.segmented).frame(width: 220)
                .controlSize(.small)
                .accessibilityLabel("任务类型筛选")
            Button { store.loadTaskHistory() } label: {
                Image(systemName: "arrow.clockwise")
            }.help(L10n.text("刷新任务历史", "Refresh task history"))
                .accessibilityLabel(L10n.text("刷新任务历史", "Refresh task history"))
                .disabled(!store.online || store.taskHistoryLoading)
        }.padding(.horizontal, 20).padding(.vertical, 10)
    }

    private var listColumn: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !store.taskHistoryError.isEmpty {
                Text(store.taskHistoryError).font(.callout).foregroundStyle(.red)
                    .textSelection(.enabled).padding(20)
            }
            if store.taskHistoryLoading && store.taskHistory.isEmpty {
                ProgressView(L10n.text("正在读取任务记录…", "Loading task records…")).frame(maxWidth: .infinity, minHeight: 140)
            } else if visibleHistory.isEmpty {
                emptyState(
                    title: store.taskHistory.isEmpty
                        ? L10n.text("暂无任务记录", "No task records")
                        : L10n.text("没有符合筛选的任务", "No tasks match the filter"),
                    message: store.online
                        ? (store.taskHistory.isEmpty
                           ? L10n.text("完成一次数据检查或预测后，任务记录与可恢复快照会显示在这里。", "Task records and restorable snapshots will appear here after a check or forecast.")
                           : L10n.text("当前筛选条件下没有记录，可切换到「全部」查看。", "No records match this filter. Switch back to All to view everything."))
                        : L10n.text("引擎未连接。", "Engine is offline.")
                )
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            ForEach(visibleHistory) { item in
                                historyRow(item)
                                    .id(item.taskID)
                                Divider().padding(.leading, 52)
                            }
                            if store.taskHistoryHasMore {
                                Button {
                                    store.loadTaskHistory(older: true)
                                } label: {
                                    HStack(spacing: 8) {
                                        if store.taskHistoryLoading {
                                            ProgressView().controlSize(.small)
                                        }
                                        Text(L10n.loadOlder).font(.callout)
                                    }
                                }
                                .buttonStyle(.borderless)
                                .disabled(store.taskHistoryLoading)
                                .frame(maxWidth: .infinity, alignment: .center)
                                .padding(.vertical, 12)
                            }
                        }
                    }
                    .onChange(of: store.selectedTaskHistoryID) { _, selected in
                        if let selected { proxy.scrollTo(selected, anchor: .center) }
                    }
                }
            }
            if !store.online && store.taskHistoryError.isEmpty {
                Label(L10n.text("引擎未连接，显示的是上次读取的记录。", "Engine offline; showing previously loaded records."), systemImage: "wifi.slash")
                    .font(.caption).foregroundStyle(.secondary).padding(20)
            }
        }.frame(minWidth: 420)
    }

    /// 右侧详情区：任务摘要 + 事件时间线；固定宽度，随选中项刷新。
    private var detailColumn: some View {
        Group {
            if let selected = store.taskHistory.first(where: { $0.taskID == store.selectedTaskHistoryID }) {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(selected.rowTitle).font(.headline)
                            if !selected.contextTitle.isEmpty {
                                Text(selected.contextTitle).font(.callout).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Button { store.clearTaskHistorySelection() } label: {
                            Image(systemName: "xmark")
                        }.help(L10n.text("关闭详情", "Close details"))
                            .accessibilityLabel(L10n.text("关闭详情", "Close details"))
                            .buttonStyle(.borderless)
                    }
                    summaryCard(selected)
                    if selected.canRestoreResult || fullReportPath(for: selected) != nil {
                        VStack(alignment: .leading, spacing: 7) {
                            HStack(spacing: 8) {
                                if selected.canRestoreResult {
                                    Button {
                                        restoreConfirmation.taskID = selected.taskID
                                    } label: {
                                        Label(L10n.text("查看该次结果", "View this result"),
                                              systemImage: "arrow.right.circle.fill")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(.borderedProminent)
                                    .controlSize(.small)
                                    .disabled(store.busy || store.restoringTaskHistoryID != nil)
                                    .help(L10n.text("从已发布快照恢复结果，不修改当前参数、不重新检查数据。", "Restore the published result without changing current parameters or re-checking data."))
                                }
                                if let path = fullReportPath(for: selected) {
                                    Button {
                                        openFullReport(path)
                                    } label: {
                                        Label(L10n.text("完整报告", "Full report"), systemImage: "doc.text")
                                    }
                                    .buttonStyle(.borderless)
                                    .controlSize(.small)
                                    .help(L10n.text("打开该次预测的 HTML 报告。", "Open this forecast's HTML report."))
                                }
                            }
                            Text(L10n.text("恢复结果只回到已发布快照；「恢复参数并重新检查数据」仍在任务动作菜单中。", "Restoring returns to the published snapshot; restore-and-re-check stays in task actions."))
                                .font(.caption2).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if selected.method == "forecast", selected.status == "done" {
                        Divider()
                        forecastResultCard(selected)
                    }
                    Divider()
                    eventsCard(selected)
                }.padding(20).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }.background(Color(nsColor: .controlBackgroundColor).opacity(0.5))
            .frame(width: 360)
    }

    /// 已选任务的 HTML 报告路径只从已加载快照读取，不在 SwiftUI 重新计算。
    private func fullReportPath(for item: NativeTaskHistoryItem) -> String? {
        guard store.taskResultTaskID == item.taskID,
              let models = store.taskResult?["model_results"] as? JSONObject else { return nil }
        return ["gfs", "ec"].compactMap { model in
            (models[model] as? JSONObject)?["html_report_path"] as? String
        }.first { !$0.isEmpty }
    }

    private func openFullReport(_ path: String) {
        let url = URL(fileURLWithPath: path, relativeTo: URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath))
        NSWorkspace.shared.open(url)
    }

    /// 摘要卡：状态、原始 UTC、进度、恢复说明；动作按钮集中在列表行。
    private func summaryCard(_ item: NativeTaskHistoryItem) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            summaryRow(L10n.text("状态", "Status"), L10n.status(item.statusTitle) + (item.status == "interrupted" ? L10n.text("（不会自动重跑）", " (will not re-run)") : ""))
            summaryRow(L10n.text("创建时间", "Created"), historyTimeText(item.createdAtUTC),
                       help: "UTC：\(item.createdAtUTC)")
            summaryRow(L10n.text("更新时间", "Updated"), historyTimeText(item.updatedAtUTC),
                       help: "UTC：\(item.updatedAtUTC)")
            if item.status == "running",
               let percent = (item.progress["percent"] as? NSNumber)?.doubleValue {
                ProgressView(value: percent, total: 100)
                summaryRow(L10n.text("进度", "Progress"), String(format: "%.0f%%", percent))
            }
            if item.cancelRequested {
                summaryRow(L10n.text("取消", "Cancel"), L10n.text("已请求取消，等待任务停止", "Cancellation requested; waiting for the task to stop"))
            }
            if item.canRestoreResult {
                summaryRow(L10n.text("恢复", "Restore"), L10n.text("可从已发布快照恢复本任务结果", "Result can be restored from the published snapshot"))
            }
            if item.retryRequest != nil {
                summaryRow(L10n.text("重试", "Retry"), L10n.text("可恢复参数并重新检查数据", "Parameters can be restored and re-checked"))
            }
            summaryRow(L10n.text("删除记录", "Delete record"), L10n.text("只删除状态和事件，不影响报告与天气数据", "Only status and events are removed; reports and weather data are unaffected"))
        }.padding(12)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
    }

    private func summaryRow(_ title: String, _ value: String, help: String? = nil) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.caption).foregroundStyle(.secondary).frame(width: 64, alignment: .leading)
            Text(value).font(.caption).textSelection(.enabled)
            Spacer(minLength: 0)
        }.help(help ?? value)
    }

    /// v1.9.5 预测详情卡：选中已完成的预测任务时读取不可变结果快照，
    /// 呈现该次预测的关键信息（双模型指数/档位/最佳时刻/窗口 + 摘要）；
    /// 快照过期或清理时给出可读说明而不是空白。
    @ViewBuilder
    private func forecastResultCard(_ item: NativeTaskHistoryItem) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L10n.text("预测详情", "Forecast details")).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if store.taskResultTaskID != item.taskID {
                Text("—").font(.caption).foregroundStyle(.secondary)
            } else {
                switch store.taskResultState {
                case .idle, .loading:
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(L10n.text("正在读取预测详情…", "Loading forecast details…")).font(.caption).foregroundStyle(.secondary)
                    }
                case .unavailable(let message):
                    Text(L10n.text("预测详情不可用", "Forecast details unavailable") + "：\(message)")
                        .font(.caption).foregroundStyle(.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                case .loaded:
                    if let result = store.taskResult {
                        forecastResultContent(result)
                    }
                }
            }
        }.padding(12)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder
    private func forecastResultContent(_ result: JSONObject) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(["gfs", "ec"], id: \.self) { model in
                forecastModelRow(result, model)
            }
        }
    }

    @ViewBuilder
    private func forecastModelRow(_ result: JSONObject, _ model: String) -> some View {
        let errors = result["model_errors"] as? JSONObject
        let error = errors?[model] as? String
        let report = (result["model_results"] as? JSONObject)?[model] as? JSONObject
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text(model.uppercased()).font(.caption.weight(.semibold))
                    .foregroundStyle(model == "ec" ? Color.teal : Color.orange)
                if let error, !error.isEmpty {
                    Text(L10n.text("未完成", "Incomplete") + "：\(error)").font(.caption2).foregroundStyle(.red)
                        .textSelection(.enabled).lineLimit(2)
                } else if report == nil {
                    Text(L10n.status("暂无结果")).font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text("\(historyFormat(report?["peak_quality_index"] ?? report?["quality_index"])) / 2.5")
                        .font(.caption.weight(.medium)).monospacedDigit()
                    Text(report?["quality_level"] as? String ?? "")
                        .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    Spacer(minLength: 0)
                    Text(L10n.text("最佳", "Best") + " \(historyTime(report?["best_time_local"] as? String))")
                        .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                    Text(L10n.text("窗口", "Window") + " \(historyWindow(report))")
                        .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                }
            }
            if let summary = report?["summary"] as? String, !summary.isEmpty {
                Text(summary).font(.caption2).foregroundStyle(.secondary)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            // v1.9.7：从任务详情直达该次预测的完整 HTML 报告（与工作台
            // 诊断区同一 html_report_path 契约）。
            if let path = report?["html_report_path"] as? String, !path.isEmpty {
                Button {
                    let url = URL(fileURLWithPath: path, relativeTo: URL(
                        fileURLWithPath: FileManager.default.currentDirectoryPath))
                    NSWorkspace.shared.open(url)
                } label: {
                    Label(L10n.text("完整报告", "Full report"), systemImage: "doc.text")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
            }
        }
    }

    // 快照格式化助手（Workbench.swift 的 format/timeLabel 为文件私有，此处同义复制）。
    private func historyFormat(_ value: Any?, digits: Int = 2) -> String {
        guard let number = value as? NSNumber, number.doubleValue.isFinite else { return "—" }
        return String(format: "%.\(digits)f", number.doubleValue)
    }

    private func historyTime(_ value: String?) -> String {
        guard let value, value.count >= 16 else { return "—" }
        return String(value.dropFirst(11).prefix(5))
    }

    private func historyWindow(_ report: JSONObject?) -> String {
        guard let window = report?["timing_window"] as? JSONObject else { return "--" }
        return "\(historyTime(window["start_time_local"] as? String)) – \(historyTime(window["end_time_local"] as? String))"
    }

    /// 事件时间线：与旧版一致，带截断提示与逐条 UTC help。
    private func eventsCard(_ item: NativeTaskHistoryItem) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L10n.text("事件", "Events")).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if store.taskHistoryEventsLoading {
                ProgressView().controlSize(.small)
            } else if !store.taskHistoryEventsError.isEmpty {
                Text(store.taskHistoryEventsError).font(.caption).foregroundStyle(.red)
            } else if store.taskHistoryEvents.isEmpty {
                Text(L10n.text("没有已记录的事件。", "No recorded events.")).font(.caption).foregroundStyle(.secondary)
            } else {
                if store.taskHistoryEventsTruncated {
                    Text(L10n.text("较早事件已超出保留上限。", "Older events exceed the retention limit.")).font(.caption).foregroundStyle(.secondary)
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(store.taskHistoryEvents) { event in
                            HStack(alignment: .firstTextBaseline, spacing: 9) {
                                Text(historyTimeText(event.atUTC)).font(.caption2).monospacedDigit()
                                    .foregroundStyle(.secondary)
                                    .help("UTC：\(event.atUTC)")
                                Text(event.title).font(.caption.weight(.medium))
                                Text(event.detail).font(.caption2).foregroundStyle(.secondary)
                                Spacer()
                            }.padding(.vertical, 3)
                        }
                    }
                }
            }
        }.frame(maxHeight: .infinity, alignment: .top)
    }

    private func historyRow(_ item: NativeTaskHistoryItem) -> some View {
        Button {
            store.selectTaskHistory(item.taskID)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: statusSymbol(item.status))
                    .foregroundStyle(statusColor(item.status))
                    .frame(width: 22, height: 22)
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 7) {
                        Text(item.rowTitle)
                            .font(.callout.weight(.medium))
                        StatusChip(status: item.status, title: L10n.status(item.statusTitle))
                    }
                    if !item.contextTitle.isEmpty {
                        Text(item.contextTitle).font(.caption).foregroundStyle(.secondary)
                    }
                    HStack(spacing: 10) {
                        // UX #4：本地时区展示，原始 UTC 放进 help 便于排查。
                        Text(historyTimeText(item.createdAtUTC)).monospacedDigit()
                            .help("UTC：\(item.createdAtUTC)")
                        if item.status == "running",
                           let percent = (item.progress["percent"] as? NSNumber)?.doubleValue {
                            Text(String(format: "%.0f%%", percent)).monospacedDigit()
                        }
                        if item.status == "interrupted" { Text(L10n.text("不会自动重跑", "Will not re-run")) }
                    }.font(.caption2).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                // 方案 D9：动作收进「…」菜单，命中区 36、危险项红字隔离。
                Menu {
                    if item.canRestoreResult {
                        Button(L10n.text("查看该次结果", "View this result")) {
                            restoreConfirmation.taskID = item.taskID
                        }
                        .disabled(store.busy || store.restoringTaskHistoryID != nil)
                        .help(L10n.text("从已发布快照恢复结果，不修改当前参数、不重新检查数据。", "Restore the published result without changing current parameters or re-checking data."))
                    }
                    if item.retryRequest != nil {
                        Button(L10n.text("恢复参数并重新检查数据", "Restore parameters & re-check")) { store.restoreTaskParameters(item) }
                            .disabled(store.busy || item.status == "running" || store.restoringTaskHistoryID != nil)
                            .help(L10n.text("回到预检并回填该次参数，不会直接恢复结果。", "Return to preflight with these parameters; the result is not restored."))
                    }
                    Divider()
                    Button(role: .destructive) { store.pendingForgetTaskID = item.taskID } label: {
                        Label(L10n.text("删除任务记录", "Delete task record"), systemImage: "trash")
                    }
                    .disabled(store.busy || item.status == "running" || store.forgettingTaskHistoryID != nil)
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .help(L10n.text("任务动作", "Task actions"))
                .accessibilityLabel(L10n.text("任务动作", "Task actions"))
                .disabled(store.forgettingTaskHistoryID != nil)
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            .contentShape(Rectangle())
            .background(store.selectedTaskHistoryID == item.taskID
                        ? Color(nsColor: .selectedContentBackgroundColor).opacity(0.18) : Color.clear)
        }.buttonStyle(.plain)
            .accessibilityAddTraits(store.selectedTaskHistoryID == item.taskID ? .isSelected : [])
    }

    private func statusSymbol(_ status: String) -> String {
        switch status {
        case "done": "checkmark.circle.fill"
        case "failed": "exclamationmark.circle.fill"
        case "cancelled": "stop.circle"
        case "interrupted": "pause.circle"
        default: "clock"
        }
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "done": .green
        case "failed": .red
        case "interrupted": .orange
        default: .secondary
        }
    }
}
