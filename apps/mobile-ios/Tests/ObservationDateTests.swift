import XCTest
@testable import FireSkyMobile

/// ObservationDate / PreparationActions 纯逻辑迁移验证。
final class ObservationDateTests: XCTestCase {
    func testEnglishSettingsTranslations() {
        let saved = L10n.language
        defer { L10n.language = saved }
        L10n.language = .english
        XCTAssertEqual(L10n.t("同步"), "Sync")
        XCTAssertEqual(L10n.t("卫星数据"), "Satellite data")
        XCTAssertEqual(L10n.t("数据已齐，可更新 %@"), "Data ready — can update %@")
        XCTAssertEqual(L10n.navSettings, "Settings")
    }

    func testLabelFormatting() throws {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let date = try XCTUnwrap(formatter.date(from: "2026-11-12T15:46:40Z"))
        XCTAssertEqual(ObservationDate.label(date, timezone: "Asia/Shanghai"), "2026-11-12")
        XCTAssertEqual(ObservationDate.timeLabel(date, timezone: "Asia/Shanghai"), "23:46")
        XCTAssertEqual(ObservationDate.timeLabel(date, timezone: "UTC"), "15:46")
    }

    func testObservationMonthGrid() throws {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let date = try XCTUnwrap(formatter.date(from: "2026-11-12T00:00:00Z"))
        let month = ObservationMonth(containing: date, timezone: "Asia/Shanghai")
        XCTAssertEqual(month.title, "2026年11月")
        XCTAssertEqual(month.days.count, 42)
    }

    func testPreparationActions() {
        let download = PreparationActions(ready: false, busy: false, hasSession: true)
        XCTAssertEqual(download.primary, .download)
        XCTAssertTrue(download.canDownload)
        XCTAssertFalse(download.canForecast)
        let forecast = PreparationActions(ready: true, busy: false, hasSession: true)
        XCTAssertEqual(forecast.primary, .forecast)
        XCTAssertTrue(forecast.canForecast)
        XCTAssertFalse(PreparationActions(ready: true, busy: true, hasSession: true).canForecast)
    }

    func testCloudGenusBriefFormatting() {
        let saved = L10n.language
        defer { L10n.language = saved }
        L10n.language = .chinese

        XCTAssertEqual(
            cloudGenusBrief(["label": "cirrus", "confidence": 0.72]),
            "卷云（代理 72%）"
        )
        XCTAssertEqual(
            cloudGenusBrief(["label": "unknown", "compatible_genera": ["altocumulus", "altostratus"]]),
            "高积云等（代理）"
        )
        XCTAssertNil(cloudGenusBrief(["label": "unknown", "compatible_genera": []]))

        L10n.language = .english
        XCTAssertEqual(
            cloudGenusBrief(["label": "cirrus", "confidence": 0.72]),
            "Cirrus (proxy 72%)"
        )
    }

    func testHourlyLedgerFilteringAndToggleTitles() {
        let saved = L10n.language
        defer { L10n.language = saved }
        L10n.language = .chinese

        let steps: [JSONObject] = [
            ["valid_time_local": "2026-11-24T17:30:00", "valid": false],
            ["valid_time_local": "2026-11-24T17:40:00", "valid": true, "instant_quality_index": 1.2],
            ["valid_time_local": "2026-11-24T17:50:00", "instant_quality_index": 1.5],
            ["valid_time_local": "2026-11-24T18:00:00", "valid": true, "selected_regime": "invalid"],
            ["valid_time_local": "2026-11-24T18:10:00", "valid": false],
        ]
        XCTAssertFalse(ForecastComparison.isStepValid(steps[0]))
        XCTAssertTrue(ForecastComparison.isStepValid(steps[1]))
        XCTAssertTrue(ForecastComparison.isStepValid(steps[2]))
        XCTAssertFalse(ForecastComparison.isStepValid(steps[3]))
        XCTAssertEqual(WorkLedgerView.hiddenCount(in: steps), 3)
        XCTAssertEqual(WorkLedgerView.filteredSteps(from: steps, showInvalid: false).count, 2)
        XCTAssertEqual(WorkLedgerView.filteredSteps(from: steps, showInvalid: true).count, 5)
        XCTAssertEqual(WorkLedgerView.toggleButtonTitle(hiddenCount: 3, showInvalid: false), "显示 3 个未判断时次")
        XCTAssertEqual(WorkLedgerView.toggleButtonTitle(hiddenCount: 3, showInvalid: true), "隐藏未判断时次")
    }

    func testFriendlyEngineMessageSuppressesInternalNowcastReason() {
        XCTAssertEqual(friendlyEngineMessage("native-nowcast-map-publication-not-requested"), "")
    }
}
