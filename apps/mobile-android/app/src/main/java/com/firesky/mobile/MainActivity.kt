package com.firesky.mobile

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
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

    private val store: WorkbenchStore by viewModels()

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(L10n.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            WorkbenchApp(store)
        }
    }
}
