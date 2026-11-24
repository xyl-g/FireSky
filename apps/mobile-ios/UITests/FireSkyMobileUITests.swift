import XCTest

/// UI 冒烟：MockTransport 通道下驱动 check→prepare→forecast 全链路。
/// 对应 MN1 验收「模拟器内 hello→check→prepare→forecast 全链路」的 UI 面。
final class FireSkyMobileUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testAppearanceSwitching() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-engineTransport", "mock", "-appLanguage", "zh"]
        app.launch()
        let later = app.buttons["稍后"]
        if later.waitForExistence(timeout: 10) { later.tap() }
        app.tabBars.buttons["设置"].tap()
        let appearance = app.segmentedControls["appearance-picker"]
        XCTAssertTrue(appearance.waitForExistence(timeout: 10))
        for title in ["浅色", "深色", "跟随系统"] {
            let option = appearance.buttons[title]
            option.tap()
            XCTAssertTrue(option.isSelected)
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = "appearance-\(title)"
            attachment.lifetime = .keepAlways
            add(attachment)
        }
    }

    func testLargeTextPreparation() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-engineTransport", "mock", "-appLanguage", "en",
                               "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        let later = app.buttons["稍后"]
        if later.waitForExistence(timeout: 10) { later.tap() }
        let start = app.buttons["Start Forecast"]
        XCTAssertTrue(start.waitForExistence(timeout: 10))
        for _ in 0..<8 {
            if start.isHittable { break }
            app.swipeUp()
        }
        XCTAssertTrue(start.isHittable)
        start.tap()
        let download = app.buttons["Download missing data"]
        XCTAssertTrue(download.waitForExistence(timeout: 10))
        XCTAssertTrue(download.isHittable)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "preparation-large-text-en"
        attachment.lifetime = .keepAlways
        add(attachment)
        app.buttons["Cancel"].tap()
        XCTAssertFalse(download.exists)
    }

    func testPointForecastChain() throws {
        let app = XCUIApplication()
        // v1.1.0 起 Store 默认 ffi 真引擎；UI 冒烟仍走 Mock 链路（确定性数据、
        // 无网络依赖）。经 UserDefaults argument domain 显式固定 mock 通道。
        app.launchArguments += ["-engineTransport", "mock", "-appLanguage", "zh"]
        app.launch()

        // 首启 CAMS 提醒（Mock hello 返回未配置）→ 稍后
        let later = app.buttons["稍后"]
        if later.waitForExistence(timeout: 10) {
            later.tap()
        }

        // 单点工作台 → 开始预测
        let start = app.buttons["开始预测"]
        XCTAssertTrue(start.waitForExistence(timeout: 10), "未找到开始预测按钮")
        start.tap()

        // 预测准备 sheet：check 未就绪 → 下载缺失数据
        let download = app.buttons["下载缺失数据"]
        XCTAssertTrue(download.waitForExistence(timeout: 10), "预检弹层未出现或未提示下载")
        download.tap()

        // prepare 完成后主按钮切到「确定预测」（等它变为可用）
        let confirm = app.buttons["确定预测"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 10), "未找到确定预测按钮")
        let enabled = NSPredicate(format: "isEnabled == true")
        expectation(for: enabled, evaluatedWith: confirm)
        waitForExpectations(timeout: 20, handler: nil)
        confirm.tap()

        // forecast 完成 → hero 两侧模型状态为「预测完成」，sheet 关闭
        let done = app.staticTexts["预测完成"].firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 25), "预测完成后未出现结果摘要")
        XCTAssertFalse(app.buttons["确定预测"].exists, "预测完成 sheet 应已关闭")
    }
}
