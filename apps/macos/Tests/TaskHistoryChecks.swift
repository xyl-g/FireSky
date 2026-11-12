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
        row["created_at_utc"] = "2026-11-12T12:27:33Z"
        row["updated_at_utc"] = "2026-11-12T20:27:42.600123+08:00"
        precondition(NativeTaskHistoryItem(row) != nil)
        row["created_at_utc"] = "invalid"
        precondition(NativeTaskHistoryItem(row) == nil)
    }
}
