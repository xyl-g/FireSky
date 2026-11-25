import Foundation
import UIKit

extension Store {
    // MARK: - 启动恢复最新结果

    func restoreLatestForecastResult() async {
        guard online, !busy, !showPreflight, session == nil, result == nil else { return }
        let revision = generation
        do {
            let history = try await engine.call("task_history", ["limit": 20])
            guard generation == revision, !busy, !showPreflight, session == nil, result == nil,
                  history["schema"] as? String == "firesky-task-history-v1",
                  let tasks = history["tasks"] as? [JSONObject] else { return }
            guard let item = tasks.first(where: { task in
                guard task["method"] as? String == "forecast",
                      task["status"] as? String == "done",
                      let taskID = task["task_id"] as? String, !taskID.isEmpty,
                      let retry = task["retry_request"] as? JSONObject,
                      let kind = retry["kind"] as? String,
                      ["point", "regional", "nowcast"].contains(kind),
                      retry["payload"] is JSONObject,
                      let reference = task["result_ref"] as? JSONObject,
                      let snapshot = reference["snapshot"] as? JSONObject,
                      snapshot["contract"] as? String == "firesky-result-snapshot-v1",
                      snapshot["sha256"] is String else { return false }
                return true
            }), let taskID = item["task_id"] as? String else { return }
            let retry = item["retry_request"] as? JSONObject
            let targetKind = WorkbenchMode(rawValue: retry?["kind"] as? String ?? "point") ?? .point
            let response = try await engine.call("result_restore", ["task_id": taskID])
            guard generation == revision, !busy, !showPreflight, session == nil, result == nil else { return }
            guard Self.isValidRestoredResult(response, kind: targetKind) else { return }
            let restoredPayload = retry?["payload"] as? JSONObject
            if let restoredPayload {
                applyRestoredPayload(restoredPayload, kind: targetKind)
            }
            adoptResult(response, kind: targetKind, params: restoredPayload)
            workbenchMode = targetKind.rawValue
            applyRestoredModelSelection(response, payload: restoredPayload, kind: targetKind)
            status = "已恢复历史结果"
            clearError()
        } catch {
            guard generation == revision, !Task.isCancelled else { return }
            self.error = "历史结果恢复失败：\(friendlyEngineMessage(error.localizedDescription))"
            self.errorPage = .point
        }
    }

    static func isValidRestoredResult(_ response: JSONObject, kind: WorkbenchMode) -> Bool {
        if kind == .nowcast {
            return response["nowcast"] is JSONObject || response["steps"] is [JSONObject]
        }
        return response["model_results"] is JSONObject
    }

    func applyRestoredModelSelection(_ response: JSONObject, payload: JSONObject?, kind: WorkbenchMode) {
        if kind == .point {
            selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
        } else if kind == .regional {
            if let model = payload?["model_source"] as? String, ["gfs", "ec"].contains(model) {
                regionalModel = model
            } else {
                regionalModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
            }
            selectedRegionalCoordinate = regionalForecast.model(regionalModel).peak?.coordinate
        }
    }

    func applyRestoredPayload(_ payload: JSONObject, kind: WorkbenchMode = .point) {
        if let value = payload["name"] as? String { name = value }
        if let value = (payload["lat"] as? NSNumber)?.doubleValue { lat = value }
        if let value = (payload["lon"] as? NSNumber)?.doubleValue { lon = value }
        if let value = (payload["elevation_m"] as? NSNumber)?.doubleValue { elevation = value }
        if let value = payload["timezone_name"] as? String { timezone = value }
        if let value = payload["target_date"] as? String,
           let day = ObservationDate.date(value, timezone: timezone) {
            date = day
        }
        if let value = payload["phenomenon"] as? String { phenomenon = value }
        if let value = payload["ecmwf_source"] as? String { ecmwfSource = value }
        if kind == .regional {
            if let model = payload["model_source"] as? String, ["gfs", "ec"].contains(model) {
                regionalModel = model
            }
            let regions = (payload["regions"] as? [String])?.filter { $0 != "custom" } ?? []
            if !regions.isEmpty {
                selectedRegions = Set(regions)
            }
        }
        metadataReady = true
        syncDateTab()
    }

    // MARK: - 库存与任务历史

    func loadInventory() async {
        guard online else { return }
        do {
            let response = try await engine.call("inventory")
            inventory = response["datasets"] as? [JSONObject] ?? []
            if let wrapped = response["categories"] as? JSONObject,
               let rows = wrapped["categories"] as? [JSONObject] {
                inventoryCategories = rows
            } else {
                inventoryCategories = response["categories"] as? [JSONObject] ?? []
            }
        } catch { fail(error, page: .data) }
    }

    func loadTaskHistory(older: Bool = false) {
        guard online, !taskHistoryLoading else { return }
        if older && (taskHistoryCursor == nil || !taskHistoryHasMore) { return }
        Task { [weak self] in
            guard let self else { return }
            taskHistoryLoading = true
            taskHistoryError = ""
            defer { taskHistoryLoading = false }
            do {
                var params: JSONObject = ["limit": 100]
                if older, let cursor = taskHistoryCursor {
                    params["before"] = cursor
                }
                let response = try await engine.call("task_history", params)
                guard response["schema"] as? String == "firesky-task-history-v1",
                      let tasks = response["tasks"] as? [JSONObject] else {
                    throw EngineError(message: "任务历史协议不匹配，请重新连接服务器。")
                }
                let parsed = tasks.compactMap(MobileTaskHistoryItem.parse)
                if Set(parsed.map(\.taskID)).count != parsed.count {
                    throw EngineError(message: "任务历史包含重复记录。")
                }
                taskHistory = older ? taskHistory + parsed : parsed
                taskHistoryTotal = (response["total"] as? NSNumber)?.intValue ?? parsed.count
                taskHistoryHasMore = response["has_more"] as? Bool ?? false
                taskHistoryCursor = response["next_cursor"] as? String
                taskHistoryLoaded = true
                if let selectedTaskID, !parsed.contains(where: { $0.taskID == selectedTaskID }) {
                    clearTaskSelection()
                }
            } catch is CancellationError {
            } catch {
                taskHistoryError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    func selectTask(_ taskID: String) {
        if selectedTaskID == taskID {
            clearTaskSelection()
            return
        }
        guard taskHistory.contains(where: { $0.taskID == taskID }) else { return }
        selectedTaskID = taskID
        taskEvents = []
        taskEventsError = ""
        taskEventsLoading = true
        if let item = taskHistory.first(where: { $0.taskID == taskID }) {
            loadTaskResult(for: item)
        } else {
            resetTaskResult()
        }
        Task { [weak self] in
            guard let self else { return }
            defer { taskEventsLoading = false }
            do {
                let response = try await engine.call("task_events", ["task_id": taskID, "after_seq": 0])
                guard response["task_id"] as? String == taskID,
                      let values = response["events"] as? [JSONObject] else {
                    throw EngineError(message: "任务事件协议不匹配。")
                }
                var previous = 0
                let rows: [MobileTaskEventRow] = try values.compactMap { value in
                    let seq = (value["seq"] as? NSNumber)?.intValue ?? 0
                    guard seq > previous else { throw EngineError(message: "事件顺序无效。") }
                    previous = seq
                    guard let at = value["at_utc"] as? String, let event = value["event"] as? String else {
                        throw EngineError(message: "事件字段无效。")
                    }
                    let data = value["data"] as? JSONObject ?? [:]
                    return MobileTaskEventRow(
                        seq: seq, at: at, event: event,
                        stage: data["stage"] as? String ?? "",
                        message: data["message"] as? String ?? "",
                        percent: data["percent"] as? Double
                    )
                }
                taskEvents = rows
            } catch {
                taskEventsError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    func clearTaskSelection() {
        selectedTaskID = nil
        taskEvents = []
        taskEventsError = ""
        taskEventsLoading = false
        resetTaskResult()
    }

    private func loadTaskResult(for item: MobileTaskHistoryItem) {
        guard item.method == "forecast", item.status == "done" else {
            resetTaskResult()
            return
        }
        guard taskResultTaskID != item.taskID else { return }
        taskResultTaskID = item.taskID
        if let cached = taskResultCache[item.taskID] {
            taskResult = cached
            taskResultLoading = false
            taskResultError = ""
            return
        }
        taskResult = nil
        taskResultLoading = true
        taskResultError = ""
        let targetKind = WorkbenchMode(rawValue: item.retryKind ?? "point") ?? .point
        Task { [weak self] in
            guard let self else { return }
            defer { taskResultLoading = false }
            do {
                let response = try await engine.call("result_restore", ["task_id": item.taskID])
                guard Self.isValidRestoredResult(response, kind: targetKind) else {
                    throw EngineError(message: "预测详情快照不完整。")
                }
                taskResultCache[item.taskID] = response
                taskResult = response
            } catch {
                taskResultError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    private func resetTaskResult() {
        taskResultTaskID = nil
        taskResult = nil
        taskResultLoading = false
        taskResultError = ""
    }

    func setPendingForgetTask(_ taskID: String?) {
        pendingForgetTaskID = taskID
    }

    func forgetPendingTask() {
        let taskID = pendingForgetTaskID
        pendingForgetTaskID = nil
        guard let taskID, online, !busy,
              taskHistory.first(where: { $0.taskID == taskID })?.status != "running" else { return }
        Task { [weak self] in
            guard let self else { return }
            do {
                let response = try await engine.call("task_forget", ["task_id": taskID])
                guard response["forgotten"] as? Bool == true else {
                    throw EngineError(message: "任务记录没有被删除。")
                }
                if selectedTaskID == taskID { clearTaskSelection() }
                loadTaskHistory()
            } catch {
                taskHistoryError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    func restoreTaskResult(_ item: MobileTaskHistoryItem) {
        guard !busy, restoringTaskID == nil, item.canRestoreResult else { return }
        restoringTaskID = item.taskID
        let targetKind = WorkbenchMode(rawValue: item.retryKind ?? "point") ?? .point
        Task { [weak self] in
            guard let self else { return }
            defer { restoringTaskID = nil }
            do {
                let response = try await engine.call("result_restore", ["task_id": item.taskID])
                guard Self.isValidRestoredResult(response, kind: targetKind) else {
                    throw EngineError(message: "已发布结果格式无效。")
                }
                if let payload = item.retryPayload {
                    applyRestoredPayload(payload, kind: targetKind)
                }
                adoptResult(response, kind: targetKind, params: item.retryPayload)
                workbenchMode = targetKind.rawValue
                page = .point
                applyRestoredModelSelection(response, payload: item.retryPayload, kind: targetKind)
                status = "已恢复历史结果"
                clearError()
            } catch {
                taskHistoryError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    func restoreTaskParameters(_ item: MobileTaskHistoryItem) {
        guard !busy, restoringTaskID == nil, let payload = item.retryPayload, online else { return }
        let targetKind = WorkbenchMode(rawValue: item.retryKind ?? "point") ?? .point
        applyRestoredPayload(payload, kind: targetKind)
        workbenchMode = targetKind.rawValue
        page = .point
        beginCheck(for: targetKind)
    }

    // MARK: - 数据管理（手动删除 + SRTM 地形下载）

    func beginDataDelete(source: String, cycle: String?) {
        guard online, !busy, !dataDeleteRunning, !dataDeletePreviewing else { return }
        var scope: JSONObject = ["source": source]
        if let cycle { scope["cycle"] = cycle }
        pendingDataDelete = scope
        dataDeletePreview = nil
        dataDeleteMessage = ""
        dataDeleteFailed = false
        dataDeletePreviewing = true
        Task { [weak self] in
            guard let self else { return }
            defer { dataDeletePreviewing = false }
            do {
                dataDeletePreview = try await engine.call("data_delete_preview", ["scope": scope])
            } catch {
                pendingDataDelete = nil
                dataDeleteMessage = friendlyEngineMessage(error.localizedDescription)
                dataDeleteFailed = true
            }
        }
    }

    func cancelDataDelete() {
        pendingDataDelete = nil
        dataDeletePreview = nil
    }

    func executeDataDelete() {
        guard let scope = pendingDataDelete,
              let preview = dataDeletePreview,
              let previewID = preview["preview_id"] as? String,
              online, !dataDeleteRunning else { return }
        dataDeleteRunning = true
        Task { [weak self] in
            guard let self else { return }
            defer { dataDeleteRunning = false }
            do {
                let result = try await engine.call("data_delete_execute", [
                    "scope": scope, "preview_id": previewID, "confirm": true,
                ])
                pendingDataDelete = nil
                dataDeletePreview = nil
                let deleted = (result["deleted"] as? NSNumber)?.intValue ?? 0
                let skipped = (result["skipped"] as? [Any])?.count ?? 0
                let failed = (result["failed"] as? [Any])?.count ?? 0
                let mb = (result["freed_mb"] as? NSNumber)?.doubleValue ?? 0
                dataDeleteMessage = "已删除 \(deleted) 个文件，释放 \(String(format: "%.1f", mb)) MB"
                    + (skipped > 0 ? "；占用中跳过 \(skipped) 个" : "")
                    + (failed > 0 ? "；失败 \(failed) 个" : "")
                dataDeleteFailed = failed > 0
                await loadInventory()
            } catch {
                dataDeletePreview = nil
                dataDeleteMessage = friendlyEngineMessage(error.localizedDescription)
                dataDeleteFailed = true
            }
        }
    }

    func loadTerrain() {
        guard online, !terrainLoading, !terrainDownloading else { return }
        terrainTask?.cancel()
        terrainTask = Task { [weak self] in
            guard let self else { return }
            terrainLoading = true
            defer {
                terrainLoading = false
                terrainTask = nil
            }
            do {
                terrain = try await engine.call("terrain_inventory", ["lat": lat, "lon": lon])
            } catch is CancellationError {
            } catch {
                terrain = nil
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
        }
    }

    func loadTerrainEstimate() {
        guard online, !terrainDownloading, !terrainEstimateLoading else { return }
        terrainEstimateLoading = true
        var params: JSONObject = ["estimate_global": true, "lat": lat, "lon": lon]
        if let bounds = terrainRegion.bounds {
            params["bounds"] = bounds
        }
        Task { [weak self] in
            guard let self else { return }
            defer { terrainEstimateLoading = false }
            do {
                terrainEstimate = try await engine.call("terrain_inventory", params)
                showTerrainConfirm = terrainEstimate != nil
            } catch {
                terrainEstimate = nil
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
        }
    }

    func dismissTerrainConfirm() {
        showTerrainConfirm = false
    }

    func downloadTerrainRegion() {
        downloadTerrain(region: terrainRegion, tiles: nil)
    }

    func retryFailedTerrainTiles() {
        let region = terrainLastFailedRegion ?? terrainRegion
        let tiles = terrainLastFailedTiles.isEmpty ? nil : terrainLastFailedTiles
        downloadTerrain(region: region, tiles: tiles)
    }

    func downloadTerrain(region: TerrainRegion, tiles: [String]? = nil) {
        guard online, !busy, !terrainDownloading else { return }
        terrainDownloading = true
        terrainCancelling = false
        terrainMessage = ""
        terrainProgress = [:]
        showTerrainConfirm = false
        var params: JSONObject = ["mode": "global", "timeout_seconds": 300.0]
        if let tiles, !tiles.isEmpty {
            params["tiles"] = tiles
        } else if let bounds = region.bounds {
            params["bounds"] = bounds
        }
        terrainDownloadTask = Task { [weak self] in
            guard let self else { return }
            defer {
                terrainDownloading = false
                terrainCancelling = false
                terrainDownloadTask = nil
            }
            do {
                let result = try await engine.call("terrain_download", params, progress: { [weak self] event in
                    guard let self, let terrain = event["terrain"] as? JSONObject else { return }
                    self.terrainProgress = terrain
                })
                try Task.checkCancellation()
                let downloaded = (result["downloaded"] as? NSNumber)?.intValue ?? 0
                let absent = (result["absent"] as? NSNumber)?.intValue ?? 0
                let failed = (result["failed"] as? NSNumber)?.intValue ?? 0
                let failedTiles = result["failed_tiles"] as? [String] ?? []
                terrainLastFailedCount = failed
                terrainLastFailedRegion = failed > 0 ? region : nil
                terrainLastFailedTiles = failed > 0 ? failedTiles : []
                let gb = (result["received_gb"] as? NSNumber)?.doubleValue ?? 0
                let summary = "新增 \(downloaded) 块、缺失 \(absent) 块、失败 \(failed) 块，占用 \(String(format: "%.1f", gb)) GB。"
                switch result["status"] as? String ?? "" {
                case "cancelled":
                    terrainMessage = (result["stopped_reason"] as? String == "disk_space"
                        ? "\(region.rawValue)下载因磁盘空间不足停止：" : "\(region.rawValue)下载已停止：") + summary
                    terrainMessageFailed = result["stopped_reason"] as? String == "disk_space"
                default:
                    terrainMessage = (failed > 0 ? "\(region.rawValue)下载完成（部分失败）：" : "\(region.rawValue)下载完成：") + summary
                    terrainMessageFailed = failed > 0
                }
            } catch is CancellationError {
                terrainMessage = "已停止下载；已完成的瓦片保留，重新开始会跳过它们。"
                terrainMessageFailed = false
            } catch {
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
            loadTerrain()
        }
    }

    func downloadTerrainPoint() {
        guard online, !busy, !terrainDownloading else { return }
        terrainDownloading = true
        terrainCancelling = false
        terrainMessage = ""
        terrainProgress = [:]
        terrainDownloadTask = Task { [weak self] in
            guard let self else { return }
            defer {
                terrainDownloading = false
                terrainCancelling = false
                terrainDownloadTask = nil
            }
            do {
                let result = try await engine.call("terrain_download",
                                                   ["lat": lat, "lon": lon, "timeout_seconds": 600.0])
                try Task.checkCancellation()
                switch result["status"] as? String ?? "" {
                case "exists":
                    terrainMessage = "地形瓦片已存在，直接复用本地数据。"
                    terrainMessageFailed = false
                case "downloaded":
                    terrainMessage = "已下载地形瓦片 \(result["tile"] as? String ?? "")。"
                    terrainMessageFailed = false
                case "out_of_coverage":
                    terrainMessage = result["message"] as? String ?? "该位置不在 SRTM 覆盖范围内。"
                    terrainMessageFailed = true
                default:
                    terrainMessage = "下载未完成。"
                    terrainMessageFailed = true
                }
            } catch is CancellationError {
                terrainMessage = "已停止下载。"
                terrainMessageFailed = false
            } catch {
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
            loadTerrain()
        }
    }

    func cancelTerrainDownload() {
        guard terrainDownloading else { return }
        terrainCancelling = true
        terrainDownloadTask?.cancel()
    }

    // MARK: - 保留策略与后台同步

    func loadRetentionSettings() async {
        guard online else { return }
        do {
            let response = try await engine.call("retention_settings")
            let settings = response["settings"] as? JSONObject ?? [:]
            retentionGFS = retentionText(settings["gfs_cycles"])
            retentionECMWF = retentionText(settings["ecmwf_cycles"])
            retentionCAMS = retentionText(settings["cams_cycles"])
            retentionSatelliteDays = retentionText(settings["satellite_days"])
            retentionReportDays = retentionText(settings["report_days"])
            retentionMapDays = retentionText(settings["map_days"])
            retentionConfigured = response["configured"] as? Bool ?? false
            retentionMessage = ""
            retentionSaveFailed = false
        } catch {
            retentionMessage = friendlyEngineMessage(error.localizedDescription)
            retentionSaveFailed = true
        }
    }

    func saveRetentionSettings() {
        guard online, !busy, !savingRetention else { return }
        savingRetention = true
        retentionMessage = ""
        let payload: JSONObject = [
            "settings": [
                "gfs_cycles": retentionInteger(retentionGFS),
                "ecmwf_cycles": retentionInteger(retentionECMWF),
                "cams_cycles": retentionInteger(retentionCAMS),
                "satellite_days": retentionNumber(retentionSatelliteDays),
                "report_days": retentionNumber(retentionReportDays),
                "map_days": retentionNumber(retentionMapDays),
            ],
        ]
        Task {
            defer { savingRetention = false }
            do {
                let response = try await engine.call("save_retention_settings", payload)
                let settings = response["settings"] as? JSONObject ?? [:]
                retentionGFS = retentionText(settings["gfs_cycles"])
                retentionECMWF = retentionText(settings["ecmwf_cycles"])
                retentionCAMS = retentionText(settings["cams_cycles"])
                retentionSatelliteDays = retentionText(settings["satellite_days"])
                retentionReportDays = retentionText(settings["report_days"])
                retentionMapDays = retentionText(settings["map_days"])
                retentionConfigured = response["configured"] as? Bool ?? false
                retentionMessage = retentionConfigured
                    ? L10n.text("保留设置已保存；可点击「立即按策略清理」，或等待下一次成功准备后自动清理。",
                                "Retention policy saved; tap Clean up now or wait for the next prepare.")
                    : L10n.text("已清空保留设置。", "Retention policy cleared.")
                retentionSaveFailed = false
                await refreshDataSyncState()
            } catch {
                retentionMessage = friendlyEngineMessage(error.localizedDescription)
                retentionSaveFailed = true
            }
        }
    }

    func applyRetentionNow() {
        guard online, !busy, !savingRetention, !applyingRetention else { return }
        applyingRetention = true
        retentionMessage = ""
        retentionSaveFailed = false
        Task {
            defer { applyingRetention = false }
            do {
                let response = try await engine.call("apply_retention_settings")
                let configured = response["configured"] as? Bool ?? false
                guard configured, let result = response["result"] as? JSONObject else {
                    retentionMessage = L10n.text("尚未保存任何保留规则，未执行清理。",
                                                 "No retention rules saved; nothing to clean.")
                    retentionSaveFailed = false
                    return
                }
                let removed = (result["removed_count"] as? NSNumber)?.intValue ?? 0
                let freedMB = (result["freed_mb"] as? NSNumber)?.doubleValue ?? 0
                let deferred = (result["deferred_count"] as? NSNumber)?.intValue ?? 0
                let failed = (result["failed_count"] as? NSNumber)?.intValue ?? 0
                var parts: [String] = []
                if removed > 0 {
                    parts.append(L10n.text(
                        String(format: "已清理 %d 项旧数据，释放 %.1f MB", removed, freedMB),
                        String(format: "Cleaned %d old items, freed %.1f MB", removed, freedMB)
                    ))
                } else {
                    parts.append(L10n.text("当前没有超出保留策略的旧数据", "No old data exceeded the retention policy"))
                }
                if deferred > 0 {
                    parts.append(L10n.text("跳过 \(deferred) 项占用中文件", "skipped \(deferred) busy items"))
                }
                if failed > 0 {
                    parts.append(L10n.text("\(failed) 项清理失败", "\(failed) failed"))
                }
                retentionMessage = parts.joined(separator: " · ") + "。"
                retentionSaveFailed = failed > 0
                await loadInventory()
            } catch {
                retentionMessage = friendlyEngineMessage(error.localizedDescription)
                retentionSaveFailed = true
            }
        }
    }

    private func retentionText(_ value: Any?) -> String {
        guard let number = value as? NSNumber else { return "" }
        if number.doubleValue.rounded() == number.doubleValue {
            return String(number.intValue)
        }
        return String(format: "%.2f", number.doubleValue)
    }

    private func retentionInteger(_ value: String) -> Any {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? NSNull() : (Int(trimmed) ?? -1)
    }

    private func retentionNumber(_ value: String) -> Any {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? NSNull() : (Double(trimmed) ?? -1)
    }

    func loadDataSyncSettings() async {
        guard online else { return }
        do {
            let response = try await engine.call("data_sync_settings")
            applyDataSyncStatus(response)
        } catch {
            dataSyncMessage = friendlyEngineMessage(error.localizedDescription)
            dataSyncSaveFailed = true
        }
    }

    private func applyDataSyncStatus(_ response: JSONObject) {
        let settings = response["settings"] as? JSONObject ?? [:]
        let sources = settings["sources"] as? JSONObject ?? [:]
        dataSyncEnabled = settings["enabled"] as? Bool ?? false
        dataSyncCAMS = sources["cams"] as? Bool ?? false
        dataSyncGFS = sources["gfs"] as? Bool ?? false
        dataSyncEC = sources["ec"] as? Bool ?? false
        dataSyncInterval = dataSyncNumberText(settings["interval_minutes"])
        dataSyncLeadHorizon = dataSyncNumberText(settings["lead_horizon_hours"])
        dataSyncAnchor = settings["anchor_region"] as? String ?? ""
        dataSyncConfigured = response["configured"] as? Bool ?? false
        dataSyncRetentionConfigured = response["retention_configured"] as? Bool ?? false
        dataSyncState = (response["state"] as? JSONObject)?["sources"] as? JSONObject ?? [:]
        dataSyncLoaded = true
    }

    private func dataSyncNumberText(_ value: Any?) -> String {
        guard let number = value as? NSNumber else { return "" }
        return String(number.intValue)
    }

    private func dataSyncInteger(_ value: String) -> Any {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? NSNull() : (Int(trimmed) ?? -1)
    }

    func saveDataSyncSettings() {
        guard online, !busy, !savingDataSync else { return }
        savingDataSync = true
        dataSyncMessage = ""
        let payload: JSONObject = [
            "settings": [
                "enabled": dataSyncEnabled,
                "sources": ["cams": dataSyncCAMS, "gfs": dataSyncGFS, "ec": dataSyncEC],
                "interval_minutes": dataSyncInteger(dataSyncInterval),
                "anchor_region": (dataSyncAnchor.isEmpty ? NSNull() : dataSyncAnchor) as Any,
                "lead_horizon_hours": dataSyncInteger(dataSyncLeadHorizon),
                "ecmwf_source": ecmwfSource,
            ],
        ]
        Task {
            defer { savingDataSync = false }
            do {
                let response = try await engine.call("save_data_sync_settings", payload)
                applyDataSyncStatus(response)
                dataSyncMessage = dataSyncEnabled
                    ? L10n.text("后台同步已保存。", "Background sync saved.")
                    : L10n.text("后台同步已关闭。", "Background sync disabled.")
                dataSyncSaveFailed = false
            } catch {
                dataSyncMessage = friendlyEngineMessage(error.localizedDescription)
                dataSyncSaveFailed = true
            }
        }
    }

    private var dataSyncEnabledSources: [String] {
        [("cams", dataSyncCAMS), ("gfs", dataSyncGFS), ("ec", dataSyncEC)]
            .filter { $0.1 }.map { $0.0 }
    }

    func refreshDataSyncState() async {
        guard online, let response = try? await engine.call("data_sync_settings") else { return }
        dataSyncRetentionConfigured = response["retention_configured"] as? Bool ?? dataSyncRetentionConfigured
        dataSyncState = (response["state"] as? JSONObject)?["sources"] as? JSONObject ?? dataSyncState
    }

    private func runDataSyncRound(sources: [String], force: Bool = false) async -> (response: JSONObject?, error: Error?) {
        dataSyncRunning = true
        dataSyncProgress = [:]
        dataSyncActiveSource = ""
        defer {
            dataSyncRunning = false
            dataSyncActiveSource = ""
        }
        var response: JSONObject?
        var roundError: Error?
        var params: JSONObject = ["sources": sources]
        if force { params["force"] = true }
        do {
            response = try await engine.call("data_sync_run", params, progress: { [weak self] event in
                guard let self, !self.busy else { return }
                self.updateProgress(event)
            })
        } catch {
            roundError = error
        }
        await refreshDataSyncState()
        return (response, roundError)
    }

    func runDataSyncNow() {
        guard online, !busy, !probingDownloads, !dataSyncRunning, !savingDataSync else { return }
        let sources = dataSyncEnabledSources
        guard dataSyncEnabled, !sources.isEmpty else {
            dataSyncMessage = L10n.text("请先在设置中启用后台数据同步并至少选择一个数据源。",
                                        "Enable background sync and select at least one source first.")
            dataSyncSaveFailed = true
            return
        }
        status = "正在立即同步后台数据"
        Task {
            let round = await runDataSyncRound(sources: sources, force: true)
            guard !busy else { return }
            if let error = round.error {
                dataSyncMessage = friendlyEngineMessage(error.localizedDescription)
                dataSyncSaveFailed = true
                status = "立即同步未完成"
            } else {
                let summary = round.response.map(dataSyncRoundSummary) ?? "立即同步未完成，详见任务历史。"
                status = summary
                dataSyncMessage = summary
                dataSyncSaveFailed = summary.contains("失败") || summary.contains("未完成")
            }
        }
    }

    private func dataSyncRoundSummary(_ response: JSONObject) -> String {
        let labels = ["cams": "CAMS", "gfs": "GFS", "ec": "EC"]
        let names = ["cams", "gfs", "ec"]
        let parts = names.compactMap { name -> String? in
            guard let source = response["sources"] as? JSONObject, let row = source[name] as? JSONObject
            else { return nil }
            let status = row["status"] as? String ?? ""
            let text: String
            switch status {
            case "synced": text = "已同步"
            case "current": text = "已是最新"
            case "failed": text = "失败"
            case "cancelled": text = "已取消"
            case "in_progress": text = "后台同步中"
            default: text = status.isEmpty ? "无结果" : status
            }
            return "\(labels[name] ?? name) \(text)"
        }
        return parts.isEmpty ? "立即同步结束，没有可同步的数据源。" : "立即同步结束：" + parts.joined(separator: "、")
    }

    // MARK: - CAMS 与网络探测

    var camsConfigured: Bool { camsConfiguration?["configured"] as? Bool == true }
    var canSaveCAMS: Bool { camsConfiguration?["can_save"] as? Bool == true }
    var camsConfigurationDescription: String {
        guard let camsConfiguration else { return L10n.text("正在检查服务端配置", "Checking server configuration") }
        switch camsConfiguration["state"] as? String {
        case "configured": return L10n.text("已检测到 Key，下载时验证授权。", "Key detected; authorization verified on download.")
        case "wrong_endpoint": return L10n.text("接口地址不是 Copernicus ADS，请重新配置。", "Endpoint is not Copernicus ADS; please reconfigure.")
        case "tls_disabled": return L10n.text("配置关闭了 TLS 验证，请重新保存以启用安全连接。", "TLS verification disabled; save key again to enable.")
        case "unreadable": return L10n.text("配置文件无法读取或格式有误，请修正后重新检查。", "Configuration unreadable; please fix and re-check.")
        default: return L10n.text("尚未配置 CAMS Key，服务端缺失数据时无法下载。", "CAMS key not configured on server; cannot download missing data.")
        }
    }

    func refreshCAMSConfiguration() async {
        guard online, !savingCAMS else { return }
        do {
            camsConfiguration = try await engine.call("cams_credentials")
        } catch {
            camsMessage = "无法检查配置，请确认服务器已连接。"
            camsSaveFailed = true
        }
    }

    func saveCAMSKey() {
        if isRemoteMode {
            camsMessage = L10n.text(
                "远程模式下不支持通过网络修改服务端 CAMS Key，请在 Mac / Docker 服务端本地配置。",
                "Saving CAMS Key remotely is disabled for security; configure it locally on your server."
            )
            camsSaveFailed = true
            return
        }
        guard online, !busy, !savingCAMS, !probingDownloads, canSaveCAMS else { return }
        savingCAMS = true
        camsMessage = ""
        let key = camsKeyDraft
        Task {
            defer { savingCAMS = false }
            do {
                camsConfiguration = try await engine.call("save_cams_key", ["key": key])
                if camsKeyDraft == key { camsKeyDraft = "" }
                if let index = downloadProbeRows.firstIndex(where: { $0.source == "cams" }) {
                    downloadProbeRows[index] = DownloadProbeRow.initial[index]
                }
                downloadProbeDate = nil
                camsMessage = "已保存到服务端配置文件。"
                camsSaveFailed = false
            } catch {
                camsMessage = friendlyEngineMessage(error.localizedDescription)
                camsSaveFailed = true
            }
        }
    }

    func probeDownloads() {
        guard online, !busy, !savingCAMS, !probingDownloads else { return }
        probingDownloads = true
        cancellingDownloadProbe = false
        downloadProbeDate = nil
        downloadProbeError = ""
        downloadProbeRows = DownloadProbeRow.initial
        downloadProbeTask = Task { [self] in
            defer {
                probingDownloads = false
                cancellingDownloadProbe = false
                downloadProbeTask = nil
            }
            do {
                let response = try await engine.call("network_probe", ["ecmwf_source": ecmwfSource],
                                                     progress: { [weak self] event in
                    guard let self, !self.cancellingDownloadProbe,
                          let json = event["probe"] as? JSONObject,
                          let row = DownloadProbeRow(json),
                          let index = self.downloadProbeRows.firstIndex(where: { $0.id == row.id }) else { return }
                    self.downloadProbeRows[index] = row
                })
                try Task.checkCancellation()
                let rows = (response["datasets"] as? [JSONObject] ?? []).compactMap(DownloadProbeRow.init)
                guard Set(rows.map(\.source)) == Set(["gfs", "ec", "cams"]), rows.count == 3 else {
                    throw EngineError(message: "探测结果不完整，请重新探测。")
                }
                downloadProbeRows = rows
                downloadProbeDate = Date()
            } catch {
                let cancelled = Task.isCancelled
                for index in downloadProbeRows.indices where downloadProbeRows[index].active
                    || downloadProbeRows[index].state == "waiting" {
                    downloadProbeRows[index].state = cancelled ? "cancelled" : "error"
                    downloadProbeRows[index].detail = cancelled ? "已停止，尚未验证下载。" : "探测中断，尚未验证下载。"
                }
                downloadProbeDate = Date()
                downloadProbeError = cancelled
                    ? "探测已取消；已完成的结果保留。"
                    : friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    func cancelDownloadProbe() {
        guard probingDownloads else { return }
        cancellingDownloadProbe = true
        downloadProbeTask?.cancel()
    }

    func copyDownloadProbeReport() {
        let timestamp = downloadProbeDate?.formatted(date: .numeric, time: .standard) ?? "未完成"
        let report = ["FireSky 数据源下载探测 · \(timestamp)"] + downloadProbeRows.map {
            "\($0.title) [\($0.host)] · \($0.label)\n\($0.detail)\n\($0.measurement)"
        }
        UIPasteboard.general.string = report.joined(separator: "\n\n")
    }
}
