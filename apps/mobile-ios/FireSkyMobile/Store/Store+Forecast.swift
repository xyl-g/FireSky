import Foundation
import SwiftUI

extension Store {
    var targetDateLabel: String { ObservationDate.label(date, timezone: timezone) }

    var availableRegions: [(code: String, label: String, bounds: [Double]?)] {
        if !regionDefinitions.isEmpty {
            return regionDefinitions.compactMap { item in
                guard let code = item["code"] as? String, !code.isEmpty else { return nil }
                let label = item["label"] as? String ?? code
                let bounds = (item["bounds"] as? [Any])?.compactMap { ($0 as? NSNumber)?.doubleValue }
                return (code, label, bounds?.count == 4 ? bounds : nil)
            }
        }
        return [
            ("china", L10n.text("中国", "China"), [73.0, 18.0, 135.0, 54.0]),
            ("east-asia", L10n.text("东亚", "East Asia"), [100.0, 20.0, 146.0, 54.0]),
        ]
    }

    var regionalTitle: String {
        let map = Dictionary(uniqueKeysWithValues: availableRegions.map { ($0.code, $0.label) })
        let labels = selectedRegions.sorted().map { map[$0] ?? $0 }
        return labels.isEmpty ? L10n.text("中国", "China") : labels.joined(separator: " · ")
    }

    var selectedRegionBounds: [Double]? {
        let areas = availableRegions
            .filter { selectedRegions.contains($0.code) }
            .compactMap(\.bounds)
        guard !areas.isEmpty else { return [73.0, 18.0, 135.0, 54.0] }
        let west = areas.map { $0[0] }.min() ?? 73.0
        let south = areas.map { $0[1] }.min() ?? 18.0
        let east = areas.map { $0[2] }.max() ?? 135.0
        let north = areas.map { $0[3] }.max() ?? 54.0
        return [west, south, east, north]
    }

    func selectRegionalPreset(_ code: String) {
        selectedRegions = [code]
        selectedRegionalCoordinate = nil
    }

    func switchWorkbenchMode(_ mode: WorkbenchMode) {
        guard workbenchMode != mode.rawValue else { return }
        workbenchMode = mode.rawValue
        clearError()
        if let cached = resultsByMode[mode] {
            resultParams = cached.params
            result = cached.result
            resultKind = mode
            if mode == .regional {
                regionalCache = RegionalForecast(result: cached.result)
            }
        }
    }

    func payload(for mode: WorkbenchMode? = nil) -> JSONObject {
        let targetMode = mode ?? activeWorkbenchMode
        var value: JSONObject = [
            "name": name, "lat": lat, "lon": lon, "elevation_m": elevation,
            "timezone_name": timezone, "target_date": targetDateLabel,
            "phenomenon": phenomenon, "model_source": "both", "workers": 1,
            "forecast_mode": "model", "satellite_source": "off",
            "ecmwf_source": ecmwfSource,
            "aerosol_source": aerosolSource,
            "elevation_source": elevationSource,
        ]
        if targetMode == .nowcast {
            value["model_source"] = "satellite"
            value["forecast_mode"] = "nowcast"
            value["satellite_source"] = "auto"
            value["radius_km"] = 900.0
        } else if targetMode == .regional {
            let regions = selectedRegions.isEmpty ? ["china"] : selectedRegions.sorted()
            value["model_source"] = regionalModel
            value["regions"] = regions
            value["spacing_deg"] = 0.25
        }
        return value
    }

    func beginCheck(for mode: WorkbenchMode? = nil) {
        guard !busy, online else { return }
        let targetMode = mode ?? activeWorkbenchMode
        if workbenchMode != targetMode.rawValue {
            workbenchMode = targetMode.rawValue
        }
        if targetMode != .regional && !metadataReady {
            error = "请先获取海拔与时区，或手动确认位置数据。"
            errorPage = .point
            return
        }
        if targetMode == .regional && selectedRegions.isEmpty {
            selectedRegions = ["china"]
        }
        generation = UUID()
        let revision = generation
        requestKind = targetMode
        requestPayload = payload(for: targetMode)
        session = nil
        rows = []
        ready = false
        clearError()
        showPreflight = true
        check(revision: revision)
    }

    func refreshCheck() {
        guard !busy else { return }
        dismissToken()
        generation = UUID()
        check(revision: generation)
    }

    private func check(revision: UUID) {
        busy = true
        checking = true
        percent = 0
        status = "正在检查本地数据"
        operation = Task {
            defer { if generation == revision { busy = false; checking = false } }
            do {
                let response = try await engine.call("check", [
                    "kind": requestKind.rawValue, "payload": requestPayload,
                ])
                guard generation == revision, showPreflight else {
                    if let token = response["session"] as? String {
                        _ = try? await engine.call("dismiss", ["session": token])
                    }
                    return
                }
                session = response["session"] as? String
                applyReport(response)
                verifyPublications(revision: revision)
            } catch { if generation == revision { fail(error, page: .point) } }
        }
    }

    private func applyReport(_ response: JSONObject) {
        let report = response["report"] as? JSONObject ?? [:]
        ready = report["ready"] as? Bool ?? false
        rows = (report["datasets"] as? [JSONObject] ?? []).map {
            let verified = $0["verified"] as? Bool ?? false
            let latest = $0["is_latest"] as? Bool ?? false
            return DataRow(
                source: $0["source"] as? String ?? "",
                ready: $0["ready"] as? Bool ?? false,
                old: !latest,
                verified: verified,
                latest: latest,
                missing: $0["missing_count"] as? Int ?? 0,
                policy: $0["policy"] as? String ?? "",
                aerosolSource: $0["aerosol_source"] as? String ?? ""
            )
        }
        status = ready ? "本次数据已下载" : "缺少 " + rows.filter { !$0.ready }.map(\.title).joined(separator: "、")
    }

    private func verifyPublications(revision: UUID) {
        guard let session else { return }
        publicationsPending = true
        publicationTask = Task {
            defer { if generation == revision { publicationsPending = false } }
            do {
                let response = try await engine.call("publications", ["session": session])
                guard generation == revision, showPreflight else { return }
                applyReport(response)
            } catch { /* A publication outage must not invalidate local availability. */ }
        }
    }

    func prepare() {
        guard let session, !busy else { return }
        publicationTask?.cancel()
        busy = true
        checking = true
        ready = false
        percent = 0
        clearError()
        status = "正在准备数据"
        let revision = generation
        operation = Task {
            defer { if generation == revision { busy = false; checking = false } }
            do {
                let response = try await engine.call("prepare", ["session": session], progress: updateProgress)
                try Task.checkCancellation()
                guard generation == revision, showPreflight else { return }
                applyReport(response)
            } catch { if generation == revision { fail(error, page: .point) } }
        }
    }

    var needsCycleAgeConfirmation: Bool {
        ready && rows.contains { $0.ready && (!$0.verified || !$0.latest) }
    }

    func confirm() {
        guard session != nil, ready, !busy else { return }
        if needsCycleAgeConfirmation {
            showCycleAgeConfirm = true
            return
        }
        runForecast(cycleAgeConfirmed: false)
    }

    func confirmCycleAgeAndForecast() {
        showCycleAgeConfirm = false
        runForecast(cycleAgeConfirmed: true)
    }

    private func runForecast(cycleAgeConfirmed: Bool) {
        guard let session, ready, !busy else { return }
        publicationTask?.cancel()
        showPreflight = false
        busy = true
        checking = false
        clearError()
        percent = 0
        status = requestKind == .nowcast ? "正在进行实况分析" : "正在预测"
        let revision = generation
        let kind = requestKind
        operation = Task {
            defer { if generation == revision { busy = false } }
            do {
                var params: JSONObject = [
                    "session": session, "confirmed": true,
                ]
                if cycleAgeConfirmed { params["cycle_age_confirmed"] = true }
                let response = try await engine.call("forecast", params, progress: updateProgress)
                try Task.checkCancellation()
                guard generation == revision else { return }
                adoptResult(response, kind: kind, params: requestPayload)
                if kind == .point {
                    selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
                } else if kind == .regional {
                    if let model = requestPayload["model_source"] as? String, ["gfs", "ec"].contains(model) {
                        regionalModel = model
                    } else {
                        regionalModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
                    }
                    selectedRegionalCoordinate = regionalForecast.model(regionalModel).peak?.coordinate
                } else if kind == .nowcast {
                    nowcastResultTab = "概览"
                }
                self.session = nil
                status = kind == .nowcast ? "分析完成" : "预测完成"
            } catch { if generation == revision { self.session = nil; ready = false; fail(error, page: .point) } }
        }
    }

    func updateProgress(_ event: JSONObject) {
        percent = min(100, max(0, (event["percent"] as? NSNumber)?.doubleValue ?? 0))
        let source = event["source"] as? String ?? ""
        let message = event["message"] as? String ?? ""
        if dataSyncRunning, !source.isEmpty {
            dataSyncActiveSource = source
            dataSyncProgress[source] = ["percent": percent, "message": message]
        }
        status = message.isEmpty
            ? (checking ? "正在准备 \(source) 数据" : "正在计算 \(source)")
            : message
    }

    func dismissToken() {
        if let session {
            Task { _ = try? await engine.call("dismiss", ["session": session]) }
        }
        session = nil
    }

    func closePreflight() {
        generation = UUID()
        publicationTask?.cancel()
        operation?.cancel()
        dismissToken()
        showPreflight = false
        showCycleAgeConfirm = false
        checking = false
        publicationsPending = false
        busy = false
        ready = false
        clearError()
        status = "已取消"
    }

    func cancel() {
        operation?.cancel()
        status = "正在取消，等待当前阶段结束"
    }

    // MARK: - 地点搜索与收藏

    func search() {
        let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !searching else { return }
        searching = true
        if !busy { status = "正在查找位置" }
        let source = elevationSource
        searchTask = Task {
            defer { searching = false }
            do {
                let point = try await engine.call(
                    "search", ElevationSourceSetting.searchParams(query: text, source: source))
                try Task.checkCancellation()
                applyLocation(point)
                query = ""
                clearError()
            } catch { fail(error, page: .point) }
        }
    }

    func selectPoint(lat: Double, lon: Double) {
        self.lat = lat
        self.lon = lon
        self.name = String(format: "地图选点 %.2f, %.2f", lat, lon)
        metadataReady = false
        lookupMetadata()
    }

    func lookupMetadata() {
        let revision = UUID()
        metadataGeneration = revision
        let a = lat, b = lon
        let source = elevationSource
        metadataReady = false
        if !busy { status = "正在获取海拔与时区" }
        Task {
            do {
                let point = try await engine.call(
                    "metadata", ElevationSourceSetting.metadataParams(lat: a, lon: b, source: source))
                guard metadataGeneration == revision, a == lat, b == lon,
                      source == elevationSource else { return }
                elevation = (point["elevation_m"] as? NSNumber)?.doubleValue ?? 0
                timezone = point["timezone_name"] as? String ?? timezone
                elevationSourceActual = point["source"] as? String ?? ""
                metadataReady = true
                if !busy { status = "位置已就绪" }
            } catch {
                guard metadataGeneration == revision else { return }
                self.error = friendlyEngineMessage(error.localizedDescription)
                self.errorPage = .point
                if !busy { status = "海拔未获取，可手动填写" }
            }
        }
    }

    func chooseDate(_ tab: String) {
        dateTab = tab
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        if tab == "今天" { date = Date() }
        if tab == "明天" { date = calendar.date(byAdding: .day, value: 1, to: Date()) ?? Date() }
    }

    func syncDateTab() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        if calendar.isDateInToday(date) {
            dateTab = "今天"
        } else if calendar.isDateInTomorrow(date) {
            dateTab = "明天"
        } else {
            dateTab = "自定义"
        }
    }

    func starLocation() {
        guard metadataReady else {
            error = "请先确认海拔与时区。"
            errorPage = .point
            return
        }
        favorites.removeAll { ($0["lat"] as? Double) == lat && ($0["lon"] as? Double) == lon }
        favorites.append(["name": name, "lat": lat, "lon": lon,
                          "elevation_m": elevation, "timezone_name": timezone])
        UserDefaults.standard.set(favorites, forKey: "favorites")
    }

    func removeFavorite(_ favorite: JSONObject) {
        let favLat = (favorite["lat"] as? NSNumber)?.doubleValue
        let favLon = (favorite["lon"] as? NSNumber)?.doubleValue
        favorites.removeAll {
            ($0["lat"] as? NSNumber)?.doubleValue == favLat
                && ($0["lon"] as? NSNumber)?.doubleValue == favLon
        }
        UserDefaults.standard.set(favorites, forKey: "favorites")
    }

    // MARK: - 结果访问与参数追踪

    var modelResult: JSONObject? {
        guard let result else { return nil }
        if let models = result["model_results"] as? JSONObject {
            return models[selectedModel] as? JSONObject
        }
        return result
    }

    var pointComparison: ForecastComparison {
        let active = resultKind == .point ? result : resultsByMode[.point]?.result
        return ForecastComparison(result: active)
    }

    var regionalForecast: RegionalForecast {
        if resultKind == .regional {
            return regionalCache
        }
        if let cached = resultsByMode[.regional]?.result {
            return RegionalForecast(result: cached)
        }
        return RegionalForecast(result: nil)
    }

    var nowcastReport: JSONObject? {
        if resultKind == .nowcast { return result }
        return resultsByMode[.nowcast]?.result
    }

    func adoptResult(_ response: JSONObject, kind: WorkbenchMode, params: JSONObject?) {
        resultsByMode[kind] = (response, params)
        resultParams = params
        result = response
        resultKind = kind
        if kind == .regional {
            regionalCache = RegionalForecast(result: response)
        }
    }

    func openRegionalCellInPointForecast(_ cell: RegionalCell, model: String) {
        selectPoint(lat: cell.coordinate.lat, lon: cell.coordinate.lon)
        name = cell.label
        selectedModel = model
        switchWorkbenchMode(.point)
    }

    var resultParametersStale: Bool {
        guard page == .point else { return false }
        let mode = activeWorkbenchMode
        guard let entry = (resultKind == mode ? (result != nil ? (result: result!, params: resultParams) : nil) : resultsByMode[mode]),
              let params = entry.params else { return false }
        func sameString(_ key: String, _ current: String) -> Bool {
            guard let saved = params[key] as? String else { return true }
            return saved == current
        }
        func sameNumber(_ key: String, _ current: Double) -> Bool {
            guard let saved = (params[key] as? NSNumber)?.doubleValue else { return true }
            return abs(saved - current) < 1e-9
        }
        if !sameString("target_date", targetDateLabel) { return true }
        if !sameString("phenomenon", phenomenon) { return true }
        if mode == .regional {
            if !sameString("ecmwf_source", ecmwfSource) { return true }
            if !sameString("aerosol_source", aerosolSource) { return true }
            if !sameString("model_source", regionalModel) { return true }
            if let savedRegions = params["regions"] as? [String] {
                let current = selectedRegions.isEmpty ? ["china"] : selectedRegions.sorted()
                if Set(savedRegions) != Set(current) { return true }
            }
            return false
        }
        if mode == .point && !sameString("ecmwf_source", ecmwfSource) { return true }
        if !sameString("aerosol_source", aerosolSource) { return true }
        if !sameNumber("lat", lat) || !sameNumber("lon", lon) { return true }
        if !sameNumber("elevation_m", elevation) { return true }
        if !sameString("timezone_name", timezone) { return true }
        return false
    }

    var resultTitle: String {
        let mode = activeWorkbenchMode
        let params = (resultKind == mode ? resultParams : resultsByMode[mode]?.params)
        if mode == .regional {
            let dateText = params?["target_date"] as? String ?? targetDateLabel
            return "\(regionalTitle) · \(dateText)"
        }
        if let params {
            let titleName = params["name"] as? String ?? name
            let dateText = params["target_date"] as? String ?? targetDateLabel
            return "\(titleName) · \(dateText)"
        }
        return "\(name) · \(targetDateLabel)"
    }

    var resultPhenomenonLabel: String {
        let mode = activeWorkbenchMode
        let params = (resultKind == mode ? resultParams : resultsByMode[mode]?.params)
        let value = params?["phenomenon"] as? String ?? phenomenon
        return value == "dawn" ? "朝霞" : "晚霞"
    }

    var resultParamsSummary: String {
        let mode = activeWorkbenchMode
        guard let params = (resultKind == mode ? resultParams : resultsByMode[mode]?.params) else { return "" }
        var parts: [String] = []
        if let value = params["name"] as? String { parts.append("地点：\(value)") }
        if let regions = params["regions"] as? [String], !regions.isEmpty {
            parts.append("区域：\(regions.joined(separator: ", "))")
        }
        if let lat = (params["lat"] as? NSNumber)?.doubleValue,
           let lon = (params["lon"] as? NSNumber)?.doubleValue {
            parts.append(String(format: "坐标：%.4f, %.4f", lat, lon))
        }
        if let value = params["target_date"] as? String { parts.append("日期：\(value)") }
        if let value = params["phenomenon"] as? String {
            parts.append("现象：\(value == "dawn" ? "朝霞" : "晚霞")")
        }
        return "结果原始参数 — " + parts.joined(separator: "；")
    }
}
