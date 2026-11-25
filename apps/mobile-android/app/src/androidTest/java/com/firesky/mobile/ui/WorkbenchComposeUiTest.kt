package com.firesky.mobile.ui

import android.app.Application
import android.content.Context
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.firesky.mobile.engine.MockTransport
import com.firesky.mobile.store.WorkbenchStore
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Jetpack Compose UI 端到端界面测试：
 * 1) 未配置服务器时显示「连接 FireSky 服务器」引导卡（URL / Token / 测试连接 / 保存并连接）
 * 2) 通过 MockTransport 连接后，验证工作台三模式切换（单点预测 / 区域预测 / 卫星实况）
 * 3) 验证设置页远程服务器连接卡片与外观主题切换（跟随系统 / 浅色 / 深色）
 */
@RunWith(AndroidJUnit4::class)
class WorkbenchComposeUiTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    private lateinit var app: Application

    @Before
    fun setUp() {
        app = ApplicationProvider.getApplicationContext()
        app.getSharedPreferences("firesky", Context.MODE_PRIVATE).edit().clear().commit()
    }

    @Test
    fun unconfiguredClientShowsOnboardingConnectionCard() {
        val store = WorkbenchStore(app)
        composeTestRule.setContent {
            WorkbenchApp(store)
        }

        composeTestRule.onNodeWithText("连接 FireSky 服务器").assertIsDisplayed()
        composeTestRule.onNodeWithText("服务器地址").assertIsDisplayed()
        composeTestRule.onNodeWithText("测试连接").assertIsDisplayed()
        composeTestRule.onNodeWithText("保存并连接").assertIsDisplayed()
    }

    @Test
    fun modeSwitchingDisplaysPointRegionalAndNowcastSections() {
        val store = WorkbenchStore.forTesting(app, MockTransport())
        composeTestRule.waitUntil(timeoutMillis = 5_000) {
            store.state.value.online
        }

        composeTestRule.setContent {
            WorkbenchApp(store)
        }

        // 默认单点预测
        composeTestRule.onNodeWithText("观测点").assertIsDisplayed()
        composeTestRule.onNodeWithText("开始预测").assertIsDisplayed()

        // 切换到区域预测
        composeTestRule.onNodeWithText("区域预测").performClick()
        composeTestRule.waitForIdle()
        assertEquals("regional", store.state.value.workbenchMode)
        composeTestRule.onNodeWithText("区域范围与模式").assertIsDisplayed()
        composeTestRule.onNodeWithText("开始区域预测").assertIsDisplayed()

        // 切换到卫星实况
        composeTestRule.onNodeWithText("卫星实况").performClick()
        composeTestRule.waitForIdle()
        assertEquals("nowcast", store.state.value.workbenchMode)
        composeTestRule.onNodeWithText("开始实况分析").assertIsDisplayed()
        composeTestRule.onAllNodesWithText(
            "卫星实况仅适用距朝霞/晚霞 4 小时以内的当前观测窗口；远期日期请改用「单点预测」或「区域预测」。",
        ).onFirst().assertIsDisplayed()
    }

    @Test
    fun settingsShowsRemoteServerCardAndSwitchesAppearance() {
        val store = WorkbenchStore.forTesting(app, MockTransport())
        composeTestRule.waitUntil(timeoutMillis = 5_000) {
            store.state.value.online
        }

        composeTestRule.setContent {
            WorkbenchApp(store)
        }

        // 进入设置页
        composeTestRule.onNodeWithText("设置").performClick()
        composeTestRule.waitForIdle()
        composeTestRule.onNodeWithText("运行状态摘要").assertIsDisplayed()

        // 切到深色外观
        composeTestRule.onNodeWithText("深色").performClick()
        composeTestRule.waitForIdle()
        assertEquals("dark", store.state.value.appearance)

        // 切到高级查看服务器连接卡
        composeTestRule.onNodeWithText("高级").performClick()
        composeTestRule.waitForIdle()
        composeTestRule.onAllNodesWithText("服务器连接（Mac / CF Tunnel / LAN）").onFirst().assertIsDisplayed()
        composeTestRule.onNodeWithText("保存并连接").assertIsDisplayed()
    }
}
