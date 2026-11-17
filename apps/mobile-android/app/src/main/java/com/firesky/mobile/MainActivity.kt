package com.firesky.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import com.firesky.mobile.store.WorkbenchStore
import com.firesky.mobile.ui.WorkbenchApp

/**
 * FireSky Android 入口（MN2 骨架）。
 * 壳层对应 apps/macos FireSkyApp.swift + Workbench.swift 的移动适配：
 * 底部导航承载「工作台 / 设置」两个面，区域预测与实况按蓝图 v1 排除。
 */
class MainActivity : ComponentActivity() {

    private val store: WorkbenchStore by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            WorkbenchApp(store)
        }
    }
}
