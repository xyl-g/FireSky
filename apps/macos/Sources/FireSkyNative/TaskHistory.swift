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
        case .point: "单点"
        case .regional: "区域"
        case .nowcast: "实况"
        default: methodTitle
        }
    }

    var methodTitle: String {
        switch method {
        case "check": "数据检查"
        case "prepare": "数据准备"
        case "forecast": "预测计算"
        case "publications": "发布核验"
        case "network_probe": "下载探测"
        case "data_sync": "后台同步"
        case "terrain_download": "地形下载"
        default: "任务"
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
        case "running": cancelRequested ? "取消中" : "运行中"
        case "done": "已完成"
        case "failed": "失败"
        case "cancelled": "已取消"
        case "interrupted": "已中断"
        default: status
        }
    }

    var contextTitle: String {
        guard let payload = retryRequest?.payload else {
            // UX #13：后台同步没有地点参数，给出明确说明而不是留白。
            return isBackgroundSync ? "后台数据同步轮次" : ""
        }
        let name = payload["name"] as? String
            ?? (payload["regions"] as? [String])?.joined(separator: "、")
            ?? ""
        let date = payload["target_date"] as? String ?? ""
        let phenomenon: String
        switch payload["phenomenon"] as? String {
        case "dawn": phenomenon = "朝霞"
        case "dusk": phenomenon = "晚霞"
        default: phenomenon = ""
        }
        let text = [name, date, phenomenon].filter { !$0.isEmpty }.joined(separator: " · ")
        if text.isEmpty && isBackgroundSync { return "后台数据同步轮次" }
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
        case "running": "开始"
        case "progress": "进度"
        case "cancel_requested": "请求取消"
        case "done": "完成"
        case "failed": "失败"
        case "cancelled": "已取消"
        case "interrupted": "已中断"
        default: "状态更新"
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

/// 任务历史独立侧栏页面：左侧列表 + 右侧详情区，支持类型/状态筛选。
struct NativeTaskHistoryView: View {
    @EnvironmentObject private var store: Store
    /// UX #13：后台同步轮次默认折叠。
    @State private var showSyncRounds = false
    /// 任务类型筛选：all / 主动任务 / 后台同步。
    @State private var kindFilter = "all"

    private var visibleHistory: [NativeTaskHistoryItem] {
        switch kindFilter {
        case "active": store.taskHistory.filter { !$0.isBackgroundSync }
        case "sync": store.taskHistory.filter(\.isBackgroundSync)
        default: showSyncRounds ? store.taskHistory : store.taskHistory.filter { !$0.isBackgroundSync }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            HStack(alignment: .top, spacing: 0) {
                listColumn
                if store.selectedTaskHistoryID != nil {
                    Divider()
                    detailColumn
                }
            }
        }
        .task { store.loadTaskHistory() }
        .confirmationDialog("删除任务记录？", isPresented: Binding(
            get: { store.pendingForgetTaskID != nil },
            set: { if !$0 { store.pendingForgetTaskID = nil } }
        )) {
            Button("删除记录", role: .destructive) {
                if let taskID = store.pendingForgetTaskID {
                    store.forgetTaskHistory(taskID)
                }
                store.pendingForgetTaskID = nil
            }
            Button("保留记录", role: .cancel) { store.pendingForgetTaskID = nil }
        } message: {
            Text("只删除状态和事件，不删除已发布报告或天气数据。")
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
                 ? "最近 \(store.taskHistory.count) 项 · 共 \(store.taskHistoryTotal) 项"
                 : "记录数据检查、准备、预测和下载探测任务")
                .font(.caption).foregroundStyle(.secondary)
            if store.taskHistoryIssues > 0 {
                Text("\(store.taskHistoryIssues) 项无法核验")
                    .font(.caption).foregroundStyle(.orange)
            }
            Spacer()
            // 筛选：主动任务 / 后台同步轮次。
            Picker("", selection: $kindFilter) {
                Text("全部").tag("all")
                Text("主动任务").tag("active")
                Text("后台同步").tag("sync")
            }.pickerStyle(.segmented).frame(width: 220)
                .controlSize(.small)
                .accessibilityLabel("任务类型筛选")
            Button { store.loadTaskHistory() } label: {
                Image(systemName: "arrow.clockwise")
            }.help("刷新任务历史")
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
                ProgressView("正在读取任务记录…").frame(maxWidth: .infinity, minHeight: 140)
            } else if visibleHistory.isEmpty {
                ContentUnavailableView(
                    store.taskHistory.isEmpty ? "暂无任务记录" : "没有符合筛选的任务",
                    systemImage: "clock.arrow.circlepath",
                    description: Text(store.online
                        ? (store.taskHistory.isEmpty ? "完成一次检查或预测后会显示在这里。" : "换个筛选条件试试。")
                        : "引擎未连接。"))
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
                                        Text("加载更早任务").font(.callout)
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
                Label("引擎未连接，显示的是上次读取的记录。", systemImage: "wifi.slash")
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
                        }.help("关闭详情").buttonStyle(.borderless)
                    }
                    summaryCard(selected)
                    Divider()
                    eventsCard(selected)
                }.padding(20).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }.background(Color(nsColor: .controlBackgroundColor).opacity(0.5))
            .frame(width: 360)
    }

    /// 摘要卡：状态、原始 UTC、进度、恢复说明；动作按钮集中在列表行。
    private func summaryCard(_ item: NativeTaskHistoryItem) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            summaryRow("状态", item.statusTitle + (item.status == "interrupted" ? "（不会自动重跑）" : ""))
            summaryRow("创建时间", historyTimeText(item.createdAtUTC),
                       help: "UTC：\(item.createdAtUTC)")
            summaryRow("更新时间", historyTimeText(item.updatedAtUTC),
                       help: "UTC：\(item.updatedAtUTC)")
            if item.status == "running",
               let percent = (item.progress["percent"] as? NSNumber)?.doubleValue {
                ProgressView(value: percent, total: 100)
                summaryRow("进度", String(format: "%.0f%%", percent))
            }
            if item.cancelRequested {
                summaryRow("取消", "已请求取消，等待任务停止")
            }
            if item.canRestoreResult {
                summaryRow("恢复", "可从已发布快照恢复本任务结果")
            }
            if item.retryRequest != nil {
                summaryRow("重试", "可恢复参数并重新检查数据")
            }
            summaryRow("删除记录", "只删除状态和事件，不影响报告与天气数据")
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

    /// 事件时间线：与旧版一致，带截断提示与逐条 UTC help。
    private func eventsCard(_ item: NativeTaskHistoryItem) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("事件").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if store.taskHistoryEventsLoading {
                ProgressView().controlSize(.small)
            } else if !store.taskHistoryEventsError.isEmpty {
                Text(store.taskHistoryEventsError).font(.caption).foregroundStyle(.red)
            } else if store.taskHistoryEvents.isEmpty {
                Text("没有已记录的事件。").font(.caption).foregroundStyle(.secondary)
            } else {
                if store.taskHistoryEventsTruncated {
                    Text("较早事件已超出保留上限。").font(.caption).foregroundStyle(.secondary)
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
                        StatusChip(status: item.status, title: item.statusTitle)
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
                        if item.status == "interrupted" { Text("不会自动重跑") }
                    }.font(.caption2).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                // 方案 D9：动作收进「…」菜单，命中区 36、危险项红字隔离。
                Menu {
                    if item.canRestoreResult {
                        Button("恢复已发布结果") { store.restoreTaskResult(item) }
                            .disabled(store.busy || store.restoringTaskHistoryID != nil)
                    }
                    if item.retryRequest != nil {
                        Button("恢复参数并重新检查数据") { store.restoreTaskParameters(item) }
                            .disabled(store.busy || item.status == "running" || store.restoringTaskHistoryID != nil)
                    }
                    Divider()
                    Button(role: .destructive) { store.pendingForgetTaskID = item.taskID } label: {
                        Label("删除任务记录", systemImage: "trash")
                    }
                    .disabled(store.busy || item.status == "running" || store.forgettingTaskHistoryID != nil)
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .help("任务动作")
                .disabled(store.forgettingTaskHistoryID != nil)
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            .contentShape(Rectangle())
            .background(store.selectedTaskHistoryID == item.taskID
                        ? Color(nsColor: .selectedContentBackgroundColor).opacity(0.18) : Color.clear)
        }.buttonStyle(.plain)
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
