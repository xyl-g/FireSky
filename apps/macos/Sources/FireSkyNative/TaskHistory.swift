import SwiftUI

private func taskTimestamp(_ value: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: value) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.date(from: value)
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
        default: "任务"
        }
    }

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
        guard let payload = retryRequest?.payload else { return "" }
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
        return [name, date, phenomenon].filter { !$0.isEmpty }.joined(separator: " · ")
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

struct NativeTaskHistoryView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("任务历史").font(.title.weight(.semibold))
                    Text(store.taskHistoryLoaded
                         ? "显示最近 \(store.taskHistory.count) 项，共 \(store.taskHistoryTotal) 项"
                         : "记录数据检查、准备、预测和下载探测任务")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button { store.loadTaskHistory() } label: {
                    Image(systemName: "arrow.clockwise")
                }.help("刷新任务历史")
                    .disabled(!store.online || store.taskHistoryLoading)
            }
            if !store.taskHistoryError.isEmpty {
                Text(store.taskHistoryError).font(.callout).foregroundStyle(.red)
                    .textSelection(.enabled)
            }
            if store.taskHistoryLoading && store.taskHistory.isEmpty {
                ProgressView("正在读取任务记录…").frame(maxWidth: .infinity, minHeight: 140)
            } else if store.taskHistory.isEmpty {
                ContentUnavailableView("暂无任务记录", systemImage: "clock.arrow.circlepath",
                                       description: Text(store.online ? "完成一次检查或预测后会显示在这里。" : "引擎未连接。"))
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(store.taskHistory) { item in
                            historyRow(item)
                            Divider()
                        }
                    }
                }
            }
            if store.taskHistoryIssues > 0 {
                Text("\(store.taskHistoryIssues) 项记录无法核验，未将未知状态标为中断。")
                    .font(.caption).foregroundStyle(.orange)
            }
            if let selected = store.taskHistory.first(where: { $0.taskID == store.selectedTaskHistoryID }) {
                eventsView(selected)
            }
        }
        .padding(30)
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

    private func historyRow(_ item: NativeTaskHistoryItem) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: statusSymbol(item.status))
                .foregroundStyle(statusColor(item.status))
                .frame(width: 22, height: 22)
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 7) {
                    Text("\(item.kindTitle) · \(item.methodTitle)")
                        .font(.callout.weight(.medium))
                    Text(item.statusTitle)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(statusColor(item.status))
                }
                if !item.contextTitle.isEmpty {
                    Text(item.contextTitle).font(.caption).foregroundStyle(.secondary)
                }
                HStack(spacing: 10) {
                    Text(item.createdAtUTC).monospacedDigit()
                    if item.status == "running",
                       let percent = (item.progress["percent"] as? NSNumber)?.doubleValue {
                        Text(String(format: "%.0f%%", percent)).monospacedDigit()
                    }
                    if item.status == "interrupted" { Text("不会自动重跑") }
                }.font(.caption2).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            HStack(spacing: 8) {
                Button {
                    store.selectTaskHistory(item.taskID)
                } label: {
                    Image(systemName: store.selectedTaskHistoryID == item.taskID
                          ? "list.bullet.rectangle.fill" : "list.bullet.rectangle")
                }.help("查看任务事件").buttonStyle(.borderless)
                if item.canRestoreResult {
                    Button { store.restoreTaskResult(item) } label: {
                        Image(systemName: "arrow.uturn.left.circle")
                    }.help("恢复已发布结果").buttonStyle(.borderless)
                        .disabled(store.busy || store.restoringTaskHistoryID != nil)
                }
                if item.retryRequest != nil {
                    Button { store.restoreTaskParameters(item) } label: {
                        Image(systemName: "arrow.clockwise.circle")
                    }.help("恢复参数并重新检查数据").buttonStyle(.borderless)
                        .disabled(store.busy || item.status == "running" || store.restoringTaskHistoryID != nil)
                }
                Button { store.pendingForgetTaskID = item.taskID } label: {
                    Image(systemName: "trash")
                }.help("删除任务记录").buttonStyle(.borderless)
                    .disabled(store.busy || item.status == "running" || store.forgettingTaskHistoryID != nil)
            }
        }.padding(.vertical, 12)
    }

    private func eventsView(_ item: NativeTaskHistoryItem) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("\(item.kindTitle) · \(item.methodTitle)事件")
                    .font(.headline)
                Spacer()
                Button { store.selectTaskHistory(item.taskID) } label: {
                    Image(systemName: "xmark")
                }.help("关闭任务事件").buttonStyle(.borderless)
            }
            if store.taskHistoryEventsLoading {
                ProgressView().controlSize(.small)
            } else if !store.taskHistoryEventsError.isEmpty {
                Text(store.taskHistoryEventsError).font(.caption).foregroundStyle(.red)
            } else {
                if store.taskHistoryEventsTruncated {
                    Text("较早事件已超出保留上限。").font(.caption).foregroundStyle(.secondary)
                }
                ForEach(store.taskHistoryEvents) { event in
                    HStack(alignment: .firstTextBaseline, spacing: 9) {
                        Text(event.atUTC).font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                        Text(event.title).font(.caption.weight(.medium))
                        Text(event.detail).font(.caption2).foregroundStyle(.secondary)
                        Spacer()
                    }.padding(.vertical, 3)
                }
            }
        }
        .padding(.top, 10)
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
