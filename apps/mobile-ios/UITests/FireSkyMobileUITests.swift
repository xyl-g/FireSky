import XCTest

/// UI 冒烟：MockTransport 通道下（-UITestMockMode）驱动 check→prepare→forecast 全链路。
final class FireSkyMobileUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testAppearanceSwitching() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestMockMode", "-appLanguage", "zh"]
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
        app.launchArguments = ["-UITestMockMode", "-appLanguage", "en",
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
        app.launchArguments.append("-UITestMockMode")
        app.launchArguments += ["-appLanguage", "zh"]
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

    func testReadOnlyTabsAndServerStatus() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestMockMode", "-appLanguage", "zh"]
        app.launch()

        let later = app.buttons["稍后"]
        if later.waitForExistence(timeout: 10) {
            later.tap()
        }

        let wbShot = XCTAttachment(screenshot: app.screenshot())
        wbShot.name = "ios-workbench-light"
        wbShot.lifetime = .keepAlways
        add(wbShot)

        // 数据页：只读视图，不含删除按钮
        app.tabBars.buttons.element(boundBy: 1).tap()
        let readOnlyData = app.staticTexts.matching(
            NSPredicate(format: "label BEGINSWITH %@", "只读视图")
        ).firstMatch
        XCTAssertTrue(readOnlyData.waitForExistence(timeout: 10), "数据页缺少只读提示")
        XCTAssertFalse(app.buttons["删除"].exists)
        XCTAssertFalse(app.buttons["下载区域地形"].exists)
        let dataShot = XCTAttachment(screenshot: app.screenshot())
        dataShot.name = "ios-data-readonly"
        dataShot.lifetime = .keepAlways
        add(dataShot)

        // 历史页：无删除任务提示
        app.tabBars.buttons.element(boundBy: 2).tap()
        sleep(1)
        let historyShot = XCTAttachment(screenshot: app.screenshot())
        historyShot.name = "ios-history-readonly"
        historyShot.lifetime = .keepAlways
        add(historyShot)

        // 设置 -> 服务端页：只读状态，无远端写操作
        app.tabBars.buttons.element(boundBy: 3).tap()
        let serverTab = app.buttons["服务端"]
        XCTAssertTrue(serverTab.waitForExistence(timeout: 10), "未找到服务端分类标签")
        serverTab.tap()
        let readOnlyServer = app.staticTexts.matching(
            NSPredicate(format: "label BEGINSWITH %@", "只读状态")
        ).firstMatch
        XCTAssertTrue(readOnlyServer.waitForExistence(timeout: 10), "服务端页缺少只读提示")
        XCTAssertFalse(app.buttons["保存 Key"].exists)
        XCTAssertFalse(app.buttons["立即同步"].exists)
        XCTAssertFalse(app.buttons["保存保留设置"].exists)
        let serverShot = XCTAttachment(screenshot: app.screenshot())
        serverShot.name = "ios-settings-server-readonly"
        serverShot.lifetime = .keepAlways
        add(serverShot)
    }
}
