import Foundation
import MapKit
import CoreGraphics

@main
enum PreparationActionsChecks {
    static func main() {
        // Fixtures assert Chinese UI strings deterministically regardless of CI runner OS locale.
        L10n.language = .chinese
        switch CommandLine.arguments.last {
        case "diagnostic-values":
            let parsed = try! JSONSerialization.jsonObject(with: Data(
                #"{"zero":0,"one":1,"false":false,"true":true,"missing":null,"ratio":0.75}"#.utf8
            )) as! [String: Any]
            precondition(ReportDiagnosticValue.text(parsed["zero"]) == "0.00")
            precondition(ReportDiagnosticValue.text(parsed["one"], percent: true) == "100%")
            precondition(ReportDiagnosticValue.text(parsed["false"]) == "否")
            precondition(ReportDiagnosticValue.text(parsed["true"]) == "是")
            precondition(ReportDiagnosticValue.text(parsed["missing"]) == "—")
            precondition(ReportDiagnosticValue.text(nil) == "—")
            precondition(ReportDiagnosticValue.text(Double.nan) == "—")
            precondition(ReportDiagnosticValue.text(parsed["ratio"], percent: true) == "75%")
            precondition(ReportDiagnosticValue.text(1, digits: 0, suffix: " m") == "1 m")
        case "download-probe":
            precondition(DownloadProbeRow.initial.map(\.source) == ["gfs", "ec", "cams"])
            precondition(DownloadProbeRow.initial.allSatisfy { !$0.passed && !$0.active })
            for state in ["waiting", "testing", "timeout", "unauthorized", "cancelled", "not_configured", "future-state"] {
                let row = DownloadProbeRow(["source": "cams", "state": state])!
                precondition(!row.passed && row.active == (state == "testing"))
                precondition(row.label != "下载验证通过")
            }
            let passed = DownloadProbeRow(["source": "gfs", "state": "passed",
                                          "bytes_received": 2048, "elapsed_ms": 1500])!
            precondition(passed.passed && passed.label == "下载验证通过")
            precondition(passed.bytes == 2048 && passed.elapsedMS == 1500)
            precondition(DownloadProbeRow(["source": "unknown", "state": "passed"]) == nil)
        case "missing":
            let actions = PreparationActions(ready: false, busy: false, hasSession: true)
            precondition(actions.primary == .download)
            precondition(actions.canDownload && !actions.canForecast)
        case "ready":
            let actions = PreparationActions(ready: true, busy: false, hasSession: true)
            precondition(actions.primary == .forecast)
            precondition(actions.canForecast && actions.canDownload)
        case "blocked":
            for ready in [false, true] {
                for (busy, hasSession) in [(true, true), (false, false), (true, false)] {
                    let actions = PreparationActions(ready: ready, busy: busy, hasSession: hasSession)
                    precondition(!actions.canDownload && !actions.canForecast)
                }
            }
        case "copy":
            let missing = DataRow(source: "gfs", ready: false, old: false, verified: false, latest: false, missing: 4)
            precondition(missing.title == "GFS" && missing.detail == "本次预测数据不完整")
            let unchecked = DataRow(source: "ecmwf", ready: true, old: false, verified: false, latest: true, missing: 0)
            precondition(unchecked.title == "EC" && unchecked.status == "待核验" && unchecked.detail == "更新待核验")
            let old = DataRow(source: "cams", ready: true, old: true, verified: true, latest: false, missing: 0)
            precondition(old.status == "可更新")
            let verified = DataRow(source: "cams", ready: true, old: false, verified: true, latest: true, missing: 0)
            precondition(verified.detail == "发布周期已核验")
            let satellite = DataRow(source: "hsd", ready: false, old: false, verified: false, latest: false, missing: 0)
            precondition(satellite.title == "卫星帧" && satellite.detail == "尚未下载本次预测数据")
            let unverifiedOld = DataRow(source: "gfs", ready: true, old: true, verified: false, latest: false, missing: 0)
            precondition(unverifiedOld.detail == "非最新发布周期")
        case "profile-query":
            let source: [String: Any] = [
                "contract": "rust-desktop-profile-source-v1",
                "available": true,
                "query_method": "profile_query",
                "model_source": "gfs",
                "profile_kind": "model-pressure-cross-section",
                "cycle_utc": "2026-11-09T00:00:00Z",
                "result_id": String(repeating: "a", count: 64),
                "source_identity": [
                    "bytes": 512,
                    "sha256": String(repeating: "b", count: 64),
                ],
            ]
            let report: [String: Any] = ["profile_source": source]
            let request = ProfileQueryBuilder.request(
                report: report, model: "gfs", lat: 30.25, lon: 120.25,
                localTime: "2026-11-09T17:10:00+08:00"
            )
            precondition(request != nil)
            let value = request!
            var snapped = report
            snapped["cross_section"] = ["observer": ["lat": 30.25, "lon": 120.25]]
            let snappedRequest = ProfileQueryBuilder.pointRequest(
                report: snapped, model: "gfs", lat: 30.2741, lon: 120.1551,
                localTime: "2026-11-09T17:10:00+08:00")
            precondition(snappedRequest?.lat == 30.25 && snappedRequest?.lon == 120.25)
            let exactRequest = ProfileQueryBuilder.pointRequest(
                report: report, model: "gfs", lat: 30.2741, lon: 120.1551,
                localTime: "2026-11-09T17:10:00+08:00")
            precondition(exactRequest?.lat == 30.2741 && exactRequest?.lon == 120.1551)
            snapped["cross_section"] = ["observer": ["lat": 100.0, "lon": 120.25]]
            precondition(ProfileQueryBuilder.pointRequest(
                report: snapped, model: "gfs", lat: 30.2741, lon: 120.1551,
                localTime: "2026-11-09T17:10:00+08:00") == nil)
            precondition(value.validTimeUTC == "2026-11-09T09:10:00.000Z")
            precondition(value.cycleUTC == "2026-11-09T00:00:00.000Z")
            let precise = ProfileQueryBuilder.request(
                report: report, model: "gfs", lat: value.lat, lon: value.lon,
                localTime: "2026-11-09T17:16:36.377757+08:00")
            precondition(precise?.validTimeUTC == "2026-11-09T09:16:36.377757Z")
            precondition((value.params["source_identity"] as? [String: Any])?["bytes"] as? Int64 == 512)
            let response: [String: Any] = [
                "contract": "rust-desktop-profile-query-v1",
                "result_id": value.resultID,
                "source_identity": ["bytes": value.sourceBytes, "sha256": value.sourceSHA256],
                "model_source": value.model,
                "lat": value.lat,
                "lon": value.lon,
                "valid_time_utc": value.validTimeUTC,
            ]
            precondition(ProfileQueryBuilder.responseMatches(response, request: value))
            precondition(ProfileQueryBuilder.sameUTC(response["valid_time_utc"] as! String,
                                                    "2026-11-09T17:10:00+08:00"))
            precondition(ProfileQueryBuilder.request(
                report: report, model: "ec", lat: value.lat, lon: value.lon,
                localTime: "2026-11-09T17:10:00+08:00"
            ) == nil)
            var invalid = report
            invalid["profile_source"] = ["contract": "rust-desktop-profile-source-v1"]
            precondition(ProfileQueryBuilder.request(
                report: invalid, model: "gfs", lat: value.lat, lon: value.lon,
                localTime: "2026-11-09T17:10:00+08:00"
            ) == nil)
            var satSource = source
            satSource["model_source"] = "satellite"
            satSource["profile_kind"] = "satellite-step-solar-path-diagnostic"
            let satReport: [String: Any] = ["profile_source": satSource]
            let satRequest = ProfileQueryBuilder.request(
                report: satReport, model: "satellite", lat: 30.25, lon: 120.25,
                localTime: "2026-11-09T17:00:00+08:00")
            precondition(satRequest != nil)
            precondition(satRequest!.model == "satellite")
            precondition(satRequest!.validTimeUTC == "2026-11-09T09:00:00.000Z")
            let satResponse: [String: Any] = [
                "contract": "rust-desktop-profile-query-v1",
                "result_id": satRequest!.resultID,
                "source_identity": ["bytes": satRequest!.sourceBytes, "sha256": satRequest!.sourceSHA256],
                "model_source": "satellite",
                "lat": satRequest!.lat,
                "lon": satRequest!.lon,
                "valid_time_utc": satRequest!.validTimeUTC,
            ]
            precondition(ProfileQueryBuilder.responseMatches(satResponse, request: satRequest!))
            precondition(ProfileQueryBuilder.request(
                report: satReport, model: "gfs", lat: 30.25, lon: 120.25,
                localTime: "2026-11-09T17:00:00+08:00") == nil)
            var wrongKind = source
            wrongKind["profile_kind"] = "satellite-step-solar-path-diagnostic"
            precondition(ProfileQueryBuilder.request(
                report: ["profile_source": wrongKind], model: "gfs", lat: 30.25, lon: 120.25,
                localTime: "2026-11-09T17:00:00+08:00") == nil)
        case "history-time":
            // UX #4：历史时间戳本地化（小数秒/整秒/带偏移/非法值兜底）。
            let shanghai = TimeZone(identifier: "Asia/Shanghai")!
            precondition(historyTimeText("2026-11-20T02:40:01.023Z", timeZone: shanghai) == "11-20 10:40")
            precondition(historyTimeText("2026-11-20T02:40:01Z", timeZone: shanghai) == "11-20 10:40")
            precondition(historyTimeText("2026-11-20T10:40:01+08:00", timeZone: shanghai) == "11-20 10:40")
            precondition(historyTimeText("not-a-timestamp", timeZone: shanghai) == "not-a-timestamp")
            precondition(historyTimeText("", timeZone: shanghai) == "")
            let utc = TimeZone(identifier: "UTC")!
            precondition(historyTimeText("2026-11-20T02:40:01.023Z", timeZone: utc) == "11-20 02:40")
        case "date":
            let parser = ISO8601DateFormatter()
            let midnight = parser.date(from: "2026-11-08T16:30:00Z")!
            precondition(ObservationDate.label(midnight, timezone: "Asia/Shanghai") == "2026-11-09")
            precondition(ObservationDate.label(midnight, timezone: "America/Los_Angeles") == "2026-11-08")
            let dst = parser.date(from: "2026-11-01T08:30:00Z")!
            precondition(ObservationDate.label(dst, timezone: "America/Los_Angeles") == "2026-11-01")
        case "calendar":
            let parser = ISO8601DateFormatter()
            for (stamp, timezone, expectedTitle, expectedFirst) in [
                ("2026-11-08T16:30:00Z", "Asia/Shanghai", "2026年11月", "2026-10-26"),
                ("2024-02-10T00:00:00Z", "Asia/Shanghai", "2024年2月", "2024-01-29"),
                ("2026-11-01T08:30:00Z", "America/Los_Angeles", "2026年11月", "2026-10-26"),
            ] {
                let month = ObservationMonth(containing: parser.date(from: stamp)!, timezone: timezone)
                precondition(month.title == expectedTitle && month.days.count == 42)
                precondition(Set(month.days.map { ObservationDate.label($0, timezone: timezone) }).count == 42)
                precondition(ObservationDate.label(month.days[0], timezone: timezone) == expectedFirst)
                precondition(month.calendar.component(.weekday, from: month.days[0]) == 2)
                let next = ObservationMonth(containing: month.moved(1), timezone: timezone)
                precondition(ObservationDate.label(next.moved(-1), timezone: timezone) == ObservationDate.label(month.start, timezone: timezone))
            }
            let leap = ObservationMonth(containing: parser.date(from: "2024-02-10T00:00:00Z")!, timezone: "Asia/Shanghai")
            precondition(leap.days.contains { ObservationDate.label($0, timezone: "Asia/Shanghai") == "2024-02-29" })
        case "comparison-empty":
            let empty = ForecastComparison(result: nil)
            for model in ForecastComparison.models {
                precondition(empty.status(model) == "等待预测")
                precondition(empty.report(model) == nil && empty.peak(model) == nil)
            }
            precondition(empty.curve(timezone: "Asia/Shanghai").isEmpty)
        case "comparison-pair":
            let pair = ForecastComparison(result: ["model_results": [
                "gfs": ["peak_quality_index": 0.0, "duration_minutes": 12],
                "ec": ["quality_index": 1.7, "duration_minutes": 34],
            ]])
            precondition(pair.peak("gfs") == 0 && pair.peak("ec") == 1.7)
            precondition((pair.report("gfs")?["duration_minutes"] as? NSNumber)?.doubleValue == 12)
            precondition((pair.report("ec")?["duration_minutes"] as? NSNumber)?.doubleValue == 34)
            precondition(pair.status("gfs") == "预测完成" && pair.status("ec") == "预测完成")
        case "comparison-partial":
            let partial = ForecastComparison(result: [
                "model_results": ["gfs": ["quality_index": 0.8]],
                "model_errors": ["ec": "EC test failure"],
            ])
            precondition(partial.peak("gfs") == 0.8 && partial.peak("ec") == nil)
            precondition(partial.report("ec") == nil && partial.status("ec") == "未完成")
            let absent = ForecastComparison(result: ["model_results": ["ec": ["quality_index": 1.0]]])
            precondition(absent.status("gfs") == "暂无结果" && absent.report("gfs") == nil)
            let conflict = ForecastComparison(result: [
                "model_results": ["ec": ["quality_index": 1.7]], "model_errors": ["ec": "test failure"],
            ])
            precondition(conflict.peak("ec") == nil && conflict.report("ec") == nil)
        case "comparison-curve":
            let pair = ForecastComparison(result: ["model_results": [
                "gfs": ["steps": [
                    ["valid_time_utc": "2026-11-09T09:10:00Z", "instant_quality_index": 0.9],
                    ["valid_time_utc": "2026-11-09T09:00:00Z", "instant_quality_index": 0.4],
                ]],
                "ec": ["steps": [
                    ["valid_time_local": "2026-11-09T17:00:00+08:00", "instant_quality_index": 1.4],
                    ["valid_time_local": "2026-11-09T17:20:00", "instant_quality_index": 1.1],
                ]],
            ]])
            let curve = pair.curve(timezone: "Asia/Shanghai")
            precondition(curve.count == 4)
            precondition(Set(curve.map(\.id)).count == 4)
            let gfs = curve.first { $0.model == "gfs" }!
            let ec = curve.first { $0.model == "ec" }!
            precondition(gfs.time == ec.time && gfs.quality == 0.4 && ec.quality == 1.4)
            precondition(curve.map(\.time) == curve.map(\.time).sorted())
            precondition(ObservationDate.timeLabel(curve.last!.time, timezone: "Asia/Shanghai") == "17:20")
        case "comparison-invalid":
            let model = ForecastComparison(result: ["model_results": ["gfs": [
                "peak_quality_index": Double.nan,
                "steps": [
                    ["valid_time_utc": "2026-11-09T09:00:00Z", "instant_quality_index": 0.4],
                    ["valid_time_utc": "2026-11-09T09:05:00Z", "instant_quality_index": 0.8, "valid": false],
                    ["valid_time_utc": "2026-11-09T09:07:00Z", "instant_quality_index": 0.0, "valid": true, "selected_regime": "invalid"],
                    ["valid_time_utc": "2026-11-09T09:10:00Z", "instant_quality_index": 1.2],
                    ["valid_time_utc": "2026-11-09T09:10:00Z", "instant_quality_index": 1.2],
                    ["valid_time_utc": "2026-11-09T09:15:00Z", "instant_quality_index": Double.infinity],
                    ["valid_time_utc": "invalid timestamp", "instant_quality_index": 1.0],
                ],
            ]]])
            let curve = model.curve(timezone: "Asia/Shanghai")
            precondition(model.peak("gfs") == nil && curve.count == 2)
            precondition(curve[0].series != curve[1].series)
            precondition(curve.allSatisfy { $0.model == "gfs" && $0.quality.isFinite })
            precondition(!ForecastComparison.isStepValid(["valid": true, "selected_regime": "invalid"]))
        case "aod-shared":
            let pair = ForecastComparison(result: ["model_results": [
                "gfs": ["aerosol": ["aod550": 0.21, "source": "cams-ads corridor"], "quality_components": ["quality_aod550": 9]],
                "ec": ["aerosol": ["aod550": 0.21, "quality": "cams"]],
            ]])
            precondition(pair.aod.value == "0.21" && pair.aod.source == "CAMS")
            precondition(pair.aod.available && !pair.aod.caption.contains("范围"))
            precondition(!pair.aod.value.contains("9"))
        case "aod-range":
            let pair = ForecastComparison(result: ["model_results": [
                "gfs": ["aerosol": ["aod550": 0.23, "quality": "cams"]],
                "ec": ["aerosol": ["aod550": 0.21, "quality": "cams"]],
            ]])
            precondition(pair.aod.value == "0.21 – 0.23" && pair.aod.source == "CAMS")
            precondition(pair.aod.caption.contains("未取平均"))
        case "aod-partial":
            let pair = ForecastComparison(result: [
                "model_results": [
                    "gfs": ["aerosol": ["aod550": 0, "quality": "cams"]],
                    "ec": ["aerosol": ["aod550": 9, "quality": "cams"]],
                ], "model_errors": ["ec": "failed"],
            ])
            precondition(pair.aod.value == "0.00" && pair.aod.values.count == 1)
            let missing = ForecastComparison(result: ["model_results": [
                "gfs": ["aerosol": ["aod550": Double.nan]],
                "ec": ["aerosol": ["aod550": -1]],
            ]])
            precondition(!missing.aod.available && missing.aod.value == "—")
            // 硬规则 2：无读数时不得冒充来源或暗示存在观测。
            precondition(missing.aod.source == "暂无来源")
            precondition(missing.aod.caption == "暂无可用 AOD 观测")
        case "aod-source":
            let other = ForecastComparison(result: ["model_results": [
                "gfs": ["aerosol": ["aod550": 0.21, "source": "default-heuristic"]],
            ]])
            precondition(other.aod.source == "其他来源")
            precondition(ForecastComparison(result: nil).aod.source == "暂无来源")
        case "nowcast-curve":
            let report: [String: Any] = ["steps": [
                ["valid_time_utc": "2026-11-09T09:00:00Z", "instant_quality_index": 0.4],
                ["valid_time_utc": "2026-11-09T09:05:00Z", "instant_quality_index": 0.8, "valid": false],
                ["valid_time_utc": "2026-11-09T09:20:00Z", "instant_quality_index": 1.2],
            ]]
            let curve = ForecastComparison.curve(report: report, model: "nowcast", timezone: "Asia/Shanghai")
            precondition(curve.count == 2 && curve.allSatisfy { $0.model == "nowcast" })
            precondition(curve[1].time.timeIntervalSince(curve[0].time) == 1200)
            precondition(curve[0].series != curve[1].series)
            precondition(ForecastComparison.curve(report: nil, model: "nowcast", timezone: "UTC").isEmpty)
        case "nowcast-aod":
            let reading = AODSummary.reading(reports: [
                ["aerosol": ["aod550": 0.18, "source": "cams-ads"], "quality_components": ["quality_aod550": 9]],
            ])
            precondition(reading.value == "0.18" && reading.source == "CAMS")
            precondition(reading.values.count == 1)
            precondition(!AODSummary.reading(reports: []).available)
        case "map-zoom":
            let current = CGRect(x: 10000, y: 20000, width: 10000, height: 6000)
            let anchor = CGPoint(x: 12500, y: 23000)
            for precise in [false, true] {
                let zoomed = MapZoom.rect(current, anchor: anchor, delta: 1, precise: precise, worldSize: 268435456)!
                precondition(zoomed.width < current.width && zoomed.height < current.height)
                precondition(abs((anchor.x - zoomed.minX) / zoomed.width - 0.25) < 0.000001)
                precondition(abs((anchor.y - zoomed.minY) / zoomed.height - 0.5) < 0.000001)
                let restored = MapZoom.rect(zoomed, anchor: anchor, delta: -1, precise: precise, worldSize: 268435456)!
                precondition(abs(restored.width - current.width) < 0.000001)
                precondition(abs(restored.minX - current.minX) < 0.000001)
            }
        case "map-zoom-limits":
            let world = 268435456.0
            var rect = CGRect(x: 10000, y: 20000, width: 10000, height: 6000)
            for _ in 0..<100 {
                rect = MapZoom.rect(rect, anchor: CGPoint(x: rect.midX, y: rect.midY),
                                    delta: 100000, precise: false, worldSize: world)!
            }
            precondition(abs(rect.height - 256) < 0.000001)
            for _ in 0..<100 {
                rect = MapZoom.rect(rect, anchor: CGPoint(x: rect.midX, y: rect.midY),
                                    delta: -100000, precise: false, worldSize: world)!
            }
            precondition(rect.width <= world && rect.height <= world)
            precondition(rect.minY >= 0 && rect.maxY <= world)
            for delta in [0, Double.nan, Double.infinity] {
                precondition(MapZoom.rect(rect, anchor: .zero, delta: delta, precise: false, worldSize: world) == nil)
            }
            precondition(MapZoom.rect(.zero, anchor: .zero, delta: 1, precise: false, worldSize: world) == nil)
        case "nowcast-metrics":
            let presentation = NowcastPresentation(report: [
                "confidence_score": 0.99,
                "quality_components": ["burnable_cloud_coverage_effective": 0.88],
                "nowcast": ["near_cloud_ratio": 0.32, "confidence": 0.72, "satellite_low_cloud_state": "detected",
                            "convective_score_ceiling": 0.64,
                            "satellite_motion_quality": "strong", "geometric_path_blocking_risk": 0,
                            "calibrated_sample_coverage_ratio": Double.nan],
            ])
            precondition(presentation.value("near_cloud_ratio") == "32%")
            precondition(presentation.value("confidence") == "72%")
            // 后端合同枚举 detected|clear|unknown 的中文标签（见 engine low_state）。
            precondition(presentation.value("satellite_low_cloud_state") == "检测到低云")
            precondition(NowcastPresentation(report: ["nowcast": ["satellite_low_cloud_state": "clear"]])
                .value("satellite_low_cloud_state") == "无低云")
            precondition(presentation.value("convective_score_ceiling") == "64%")
            precondition(presentation.value("satellite_motion_quality") == "可靠")
            precondition(presentation.value("geometric_path_blocking_risk") == "0%")
            precondition(presentation.value("far_cloud_ratio") == "—")
            precondition(presentation.value("calibrated_sample_coverage_ratio") == "—")
            precondition(!NowcastPresentation.metrics.contains { $0.0 == "disagreement" })
            precondition(NowcastPresentation(report: ["confidence_score": 0.99]).value("confidence") == "—")
        case "nowcast-profile":
            let presentation = NowcastPresentation(report: ["nowcast": ["satellite_vertical_profile": [
                "status": "available", "samples": [
                    ["distance_km": 20, "status": "retrieved", "levels": [
                        ["height_m": 1000, "status": "retrieved", "hsd_posterior_percent": 70],
                        ["height_m": 3000, "status": "unknown", "cloud_probability_percent": 90],
                        ["status": "retrieved", "hsd_posterior_percent": 30],
                    ]],
                    ["distance_km": 40, "status": "unknown", "levels": [
                        ["height_m": 1000, "hsd_posterior_percent": 80],
                    ]],
                ],
            ]]])
            let cells = presentation.profileCells
            precondition(cells.count == 3 && cells[0].probability == 70)
            precondition(cells[1].probability == nil && cells[2].probability == nil)
            precondition(cells.allSatisfy { $0.endKM > $0.startKM && $0.topKM > $0.bottomKM })
            precondition(NowcastPresentation(report: nil).profileCells.isEmpty)
            precondition(NowcastPresentation(report: ["nowcast": ["satellite_vertical_profile": [
                "status": "unavailable", "samples": [["distance_km": 20]],
            ]]]).profileCells.isEmpty)
            let fan: [[String: Any]] = [240.0, 255.0, 270.0].map { bearing in
                ["distance_km": 20, "bearing_deg": bearing, "status": "retrieved",
                 "levels": [["height_m": 1000, "hsd_posterior_percent": 70]]]
            }
            let alongPath = NowcastPresentation(report: [
                "cross_section": ["solar_azimuth_deg": 240.0],
                "nowcast": ["satellite_vertical_profile": ["status": "available", "samples": fan]],
            ])
            precondition(alongPath.profileCells.count == 1)
            let ambiguous = NowcastPresentation(report: [
                "nowcast": ["satellite_vertical_profile": ["status": "available", "samples": fan]],
            ])
            precondition(ambiguous.profileCells.isEmpty)
            precondition(ambiguous.profileUnavailableReason.contains("缺少太阳方位"))
            let bestStep = NowcastPresentation(report: [
                "best_time_local": "2026-11-09T17:10:00+08:00",
                "steps": [
                    ["valid_time_local": "2026-11-09T17:05:00+08:00", "solar_azimuth_deg": 270.0],
                    ["valid_time_local": "2026-11-09T17:10:00+08:00", "solar_azimuth_deg": 253.0],
                ],
                "nowcast": ["satellite_vertical_profile": ["status": "available", "samples": fan]],
            ])
            precondition(bestStep.profileBearing == 255 && bestStep.profileCells.count == 1)
            let unburnable = NowcastPresentation(report: [
                "best_time_local": NSNull(),
                "steps": [
                    ["valid": false, "solar_azimuth_deg": 240.0],
                    ["valid": true, "physically_burnable": false, "solar_azimuth_deg": 269.0],
                ],
                "nowcast": ["satellite_vertical_profile": ["status": "available", "samples": fan]],
            ])
            precondition(unburnable.profileBearing == 270 && unburnable.profileCells.count == 1)
            precondition(unburnable.profileBearingCaption.contains("诊断采样方位"))
            let northFan = NowcastPresentation(report: [
                "cross_section": ["solar_azimuth_deg": 359.0],
                "nowcast": ["satellite_vertical_profile": [
                    "samples": [["bearing_deg": 1.0], ["bearing_deg": 340.0]],
                ]],
            ])
            precondition(northFan.profileBearing == 1)
        case "regional-empty":
            let value = RegionalForecast(result: nil)
            precondition(!value.model("gfs").available && !value.model("ec").available)
            precondition(value.model("gfs").peak == nil)
        case "regional-models":
            let value = RegionalForecast(result: ["model_results": [
                "gfs": ["points": [["lat": 30.0, "lon": 120.0, "peak_quality_index": 0.0]],
                        "timing": ["native_timings_ms": ["assessment": 0.0, "invalid": Double.nan]],
                        "city_ranking": ["status": "ready", "items": [
                            ["name": "杭州", "lat": 30.2741, "lon": 120.1551,
                             "peak_quality_index": 2.0, "best_time_local": "2026-11-09T17:10:00+08:00"],
                            ["name": "宁波", "lat": 29.8683, "lon": 121.5440,
                             "peak_quality_index": 1.5, "best_time_local": "2026-11-09T17:05:00+08:00"],
                        ]]],
                "ec": ["points": [["lat": 30.0, "lon": 120.0, "peak_quality_index": 2.0]]],
            ]])
            precondition(value.model("gfs").peak?.quality == 0)
            precondition(value.model("gfs").nativeTimingsMS == ["assessment": 0])
            precondition(value.model("ec").peak?.quality == 2)
            precondition(value.model("gfs").cityRanking.count == 2)
            precondition(value.model("gfs").cityRanking.first?.label == "杭州")
            let partial = RegionalForecast(result: ["model_results": [
                "gfs": ["points": [["lat": 30.0, "lon": 120.0, "peak_quality_index": 0.0]]],
            ], "model_errors": ["ec": "unavailable"]])
            precondition(partial.model("gfs").available)
            precondition(!partial.model("ec").available && partial.model("ec").cells.isEmpty)
        case "regional-missing":
            let value = RegionalModel(report: [
                "point_count": 5, "error_count": 1,
                "points": [
                    ["lat": 30.0, "lon": 120.0, "quality_index": 0.0],
                    ["lat": 30.25, "lon": 120.0, "quality_index": 1.7],
                    ["lat": 30.5, "lon": 120.0, "quality_index": Double.nan],
                    ["lat": 30.75, "lon": 120.0, "quality_index": 4.0],
                    ["lat": 31.0, "lon": 120.0, "quality_index": 2.0],
                    ["lat": 99.0, "lon": 120.0, "quality_index": 2.0],
                ], "errors": [["lat": 31.0, "lon": 120.0, "error": "missing"]],
            ])
            precondition(value.cells.count == 5 && value.validCount == 2 && value.missingCount == 3)
            precondition(value.highCount == 1)
            precondition(value.ranked.first?.coordinate.lat == 30.25)
            precondition(value.hit(lat: 30.11, lon: 120.1) == GridCoordinate(lat: 30, lon: 120))
            precondition(value.hit(lat: 32, lon: 120) == nil)
            let shifted = RegionalModel(report: ["points": [
                ["lat": 30.1, "lon": 120.1, "quality_index": 1.0],
            ]])
            precondition(shifted.hit(lat: 30.2, lon: 120.2) == GridCoordinate(lat: 30.1, lon: 120.1))
            precondition(shifted.hit(lat: 30.3, lon: 120.1) == nil)
            let wrapped = RegionalModel(report: ["points": [
                ["lat": 10.0, "lon": 179.75, "quality_index": 1.0],
                ["lat": 10.0, "lon": -179.75, "quality_index": 1.2],
            ]])
            precondition(wrapped.hit(lat: 10.0, lon: -179.8) == GridCoordinate(lat: 10, lon: -179.75))
            precondition(wrapped.hit(lat: 10.0, lon: 179.8) == GridCoordinate(lat: 10, lon: 179.75))
        case "regional-grid":
            let bounds = GridBounds.drawn(from: GridCoordinate(lat: 30.13, lon: 120.07)!,
                                          to: GridCoordinate(lat: 29.9, lon: 119.8)!)!
            precondition(bounds.west == 119.75 && bounds.east == 120.25)
            precondition(bounds.south == 29.75 && bounds.north == 30.25)
            precondition(bounds.columns == 3 && bounds.rows == 3)
            let wrapped = GridBounds([170, 0, -170, 1])!
            precondition(wrapped.longitudeSpan == 20 && wrapped.columns == 81)
            let drawnWrapped = GridBounds.drawn(from: GridCoordinate(lat: 1, lon: 179.8)!,
                                                to: GridCoordinate(lat: -1, lon: -179.8)!)!
            precondition(drawnWrapped.west == 179.75 && drawnWrapped.east == -179.75)
            precondition(GridBounds([0, 0, 0, 1]) == nil)
            precondition(GridBounds([180, 0, -180, 1]) == nil)
            precondition(GridBounds([0, 0, Double.infinity, 1]) == nil)
            precondition(GridBounds.drawn(from: GridCoordinate(lat: 30, lon: 120)!,
                                          to: GridCoordinate(lat: 30, lon: 120)!) == nil)
        case "regional-camera":
            let camera = RegionalCamera(lat: 30, lon: 120, distance: 100_000)
            precondition(camera.matches(RegionalCamera(lat: 30, lon: 120, distance: 100_001)))
            precondition(!camera.matches(RegionalCamera(lat: 30.1, lon: 120, distance: 100_000)))
        case "regional-layers":
            let cells: [RegionalCell] = (0..<10_000).map { index in
                let lat = 10.0 + Double(index / 100) * 0.25
                let lon = 100.0 + Double(index % 100) * 0.25
                let coordinate = GridCoordinate(lat: lat, lon: lon)!
                let quality: Double? = index == 0 ? nil : Double(index % 26) / 10.0
                return RegionalCell(coordinate: coordinate, name: "", quality: quality,
                                    level: "", bestTime: nil, error: nil)
            }
            let layers = try! RegionalMapLayers.build(areas: [[100, 10, 124.75, 34.75]], cells: cells)
            precondition(layers.overlays.count <= 28)
            let polygons = layers.overlays.compactMap { $0 as? MKMultiPolygon }
            // 覆盖边界常显（regional-10，方案 :78）：末位 MKMultiPolygon 恒为外框
            // 描边（1 个多边形），其余与 10_000 格点一一对应（9_999 bins + 1 缺测）。
            precondition(polygons.last?.polygons.count == 1)
            precondition(polygons.dropLast().reduce(0) { $0 + $1.polygons.count } == 10_000)
            let smoothLayers = try! RegionalMapLayers.build(areas: [[100, 10, 124.75, 34.75]], cells: cells, smooth: true)
            precondition(smoothLayers.overlays.contains { $0 is RegionalSmoothOverlay })
            let preview = try! RegionalMapLayers.build(areas: [[119.75, 29.75, 120.25, 30.25]],
                                                       cells: [], showGrid: true)
            precondition(preview.overlays.compactMap { $0 as? MKMultiPolyline }.first?.polylines.count == 8)
            let wrappedPreview = try! RegionalMapLayers.build(areas: [[170, 0, -170, 1]],
                                                              cells: [], showGrid: true)
            precondition(wrappedPreview.overlays.compactMap { $0 as? MKMultiPolygon }.first?.polygons.count == 2)
            precondition(wrappedPreview.overlays.compactMap { $0 as? MKMultiPolyline }.first?.polylines.count == 96)
            let wrappedRegion = RegionalMapLayers.fitRegion(areas: [[170, 0, -170, 1]])!
            precondition(abs(abs(wrappedRegion.center.longitude) - 180) < 0.000001)
            precondition(wrappedRegion.span.longitudeDelta < 30)
            precondition(RegionalPalette.fill(0) == RegionalFill(red: 253, green: 230, blue: 220, alpha: 12))
            precondition(RegionalPalette.fill(0.5) == RegionalFill(red: 251, green: 176, blue: 122, alpha: 127))
            precondition(RegionalPalette.fill(1.125) == RegionalFill(red: 245, green: 124, blue: 102, alpha: 169))
            precondition(RegionalPalette.fill(2.5) == RegionalFill(red: 201, green: 60, blue: 145, alpha: 232))
            let cell = RegionalMapLayers.polygon(west: 119.875, south: 29.875, east: 120.125, north: 30.125)
            precondition(abs(cell.boundingMapRect.width / MKMapRect.world.width * 360 - 0.25) < 0.000001)
        case "regional-web-palette":
            let colors = (0...250).map { index -> [Int] in
                let fill = RegionalPalette.fill(Double(index) / 100)
                return [fill.red, fill.green, fill.blue, fill.alpha]
            }
            let bytes = try! JSONSerialization.data(withJSONObject: colors)
            print(String(data: bytes, encoding: .utf8)!)
        default:
            preconditionFailure("Unknown preparation check")
        }
    }
}
