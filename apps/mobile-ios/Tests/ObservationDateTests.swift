import XCTest
@testable import FireSkyMobile

/// ObservationDate / PreparationActions 纯逻辑迁移验证。
final class ObservationDateTests: XCTestCase {
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
}
