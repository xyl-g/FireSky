package com.firesky.mobile

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import com.firesky.mobile.engine.MockTransport
import com.firesky.mobile.store.AuditLaunchOptions
import com.firesky.mobile.store.WorkbenchStore
import com.firesky.mobile.ui.L10n
import com.firesky.mobile.ui.WorkbenchApp

/**
 * FireSky Android 入口（MN2 骨架）。
 * 壳层对应 apps/macos FireSkyApp.swift + Workbench.swift 的移动适配：
 * 底部导航承载「工作台 / 数据管理 / 任务历史 / 设置」四面。
 * 多语言（v1.9.5）：attachBaseContext 按 L10n 偏好包裹 locale。
 */
class MainActivity : ComponentActivity() {

    private val store: WorkbenchStore by lazy {
        val options = parseAuditLaunchOptions(intent)
        val factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T {
                return if (options != null && options.mockMode) {
                    WorkbenchStore.forAudit(application, MockTransport(), options) as T
                } else {
                    WorkbenchStore(application) as T
                }
            }
        }
        ViewModelProvider(this, factory)[WorkbenchStore::class.java]
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(L10n.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val activeStore = store
        setContent {
            WorkbenchApp(activeStore)
        }
    }

    private fun parseAuditLaunchOptions(intent: Intent?): AuditLaunchOptions? {
        if (intent == null) return null
        val mockMode = intent.getBooleanExtra("mock_mode", false)
        val initialPage = intent.getStringExtra("initial_page")
        val initialMode = intent.getStringExtra("initial_mode")
        val initialTab = intent.getStringExtra("initial_tab")
        val openDiagnostics = intent.getBooleanExtra("open_diagnostics", false)
        val appearance = intent.getStringExtra("appearance")
        val hasAny = mockMode ||
            initialPage != null ||
            initialMode != null ||
            initialTab != null ||
            openDiagnostics ||
            appearance != null
        if (!hasAny) return null
        return AuditLaunchOptions(
            mockMode = mockMode,
            initialPage = initialPage,
            initialMode = initialMode,
            initialTab = initialTab,
            openDiagnostics = openDiagnostics,
            appearance = appearance,
        )
    }
}
