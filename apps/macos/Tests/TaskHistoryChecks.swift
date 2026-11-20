import Foundation

struct TaskHistoryChecks {
    static func run() {
        var row: [String: Any] = [
            "task_id": String(repeating: "a", count: 64),
            "method": "forecast", "status": "done", "event_seq": 3,
            "created_at_utc": "2026-11-12T12:27:33.706Z",
            "updated_at_utc": "2026-11-12T12:27:42.600Z",
            "retry_request": ["kind": "point", "payload": ["target_date": "2026-11-09"]],
            "result_ref": ["snapshot": ["contract": "firesky-result-snapshot-v1",
                                         "sha256": String(repeating: "b", count: 64)]],
        ]
        let item = NativeTaskHistoryItem(row)
        precondition(item != nil && item!.canRestoreResult)
        precondition(item!.retryRequest?.kind == .point)
        // UX #13：带类别的行标题保留「单点 · 预测计算」。
        precondition(item!.rowTitle == "单点 · 预测计算")
        // UX #4：历史时间戳本地化（小数秒 UTC → 本机时区展示）。
        precondition(historyTimeText("2026-11-12T12:27:33.706Z",
                                     timeZone: TimeZone(identifier: "Asia/Shanghai")!) == "11-12 20:27")
        // UX #13：data_sync 映射为「后台同步」，无参数时给出轮次说明，标题去重。
        let syncRow: [String: Any] = [
            "task_id": String(repeating: "c", count: 64),
            "method": "data_sync", "status": "done", "event_seq": 1,
            "created_at_utc": "2026-11-12T12:27:33.706Z",
            "updated_at_utc": "2026-11-12T12:27:42.600Z",
        ]
        let syncItem = NativeTaskHistoryItem(syncRow)
        precondition(syncItem != nil)
        precondition(syncItem!.methodTitle == "后台同步")
        precondition(syncItem!.isBackgroundSync)
        precondition(syncItem!.rowTitle == "后台同步")
        precondition(syncItem!.contextTitle == "后台数据同步轮次")
        precondition(!item!.isBackgroundSync)
        row["created_at_utc"] = "2026-11-12T12:27:33Z"
        row["updated_at_utc"] = "2026-11-12T20:27:42.600123+08:00"
        precondition(NativeTaskHistoryItem(row) != nil)
        row["created_at_utc"] = "invalid"
        precondition(NativeTaskHistoryItem(row) == nil)
    }
}
