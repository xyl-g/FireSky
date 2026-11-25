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
import com.firesky.mobile.R
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

        composeTestRule.onNodeWithText(app.getString(R.string.onboarding_connect_title)).assertIsDisplayed()
        composeTestRule.onNodeWithText(app.getString(R.string.remote_url_label)).assertIsDisplayed()
        composeTestRule.onNodeWithText(app.getString(R.string.remote_test_connection)).assertIsDisplayed()
        composeTestRule.onNodeWithText(app.getString(R.string.remote_save_and_connect)).assertIsDisplayed()
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
        composeTestRule.onNodeWithText(app.getString(R.string.obs_point)).assertIsDisplayed()
        composeTestRule.onNodeWithText(app.getString(R.string.start_forecast)).assertIsDisplayed()

        // 切换到区域预测
        composeTestRule.onNodeWithText(app.getString(R.string.mode_regional)).performClick()
        composeTestRule.waitForIdle()
        assertEquals("regional", store.state.value.workbenchMode)
        composeTestRule.onNodeWithText(app.getString(R.string.regional_scope_title)).assertIsDisplayed()
        composeTestRule.onNodeWithText(app.getString(R.string.start_regional_forecast)).assertIsDisplayed()

        // 切换到卫星实况
        composeTestRule.onNodeWithText(app.getString(R.string.mode_nowcast)).performClick()
        composeTestRule.waitForIdle()
        assertEquals("nowcast", store.state.value.workbenchMode)
        composeTestRule.onNodeWithText(app.getString(R.string.start_nowcast_analysis)).assertIsDisplayed()
        composeTestRule.onAllNodesWithText(
            app.getString(R.string.nowcast_window_guidance),
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
        composeTestRule.onNodeWithText(app.getString(R.string.nav_settings)).performClick()
        composeTestRule.waitForIdle()
        composeTestRule.onNodeWithText(app.getString(R.string.runtime_summary_title)).assertIsDisplayed()

        // 切到深色外观
        composeTestRule.onNodeWithText(app.getString(R.string.theme_dark)).performClick()
        composeTestRule.waitForIdle()
        assertEquals("dark", store.state.value.appearance)

        // 切到高级查看服务器连接卡
        composeTestRule.onNodeWithText(app.getString(R.string.settings_advanced)).performClick()
        composeTestRule.waitForIdle()
        composeTestRule.onAllNodesWithText(app.getString(R.string.remote_server_title)).onFirst().assertIsDisplayed()
        composeTestRule.onNodeWithText(app.getString(R.string.remote_save_and_connect)).assertIsDisplayed()
    }
}
