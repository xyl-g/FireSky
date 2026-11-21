package com.firesky.mobile.ui

import android.app.Activity
import android.content.Context
import android.os.LocaleList
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import com.firesky.mobile.R
import java.util.Locale

/** 界面多语言（v1.9.5，macOS L10n.swift 同构）：跟随系统 / 中文 / English。
 *  偏好持久化 SharedPreferences；通过 attachBaseContext 包裹 locale 生效，
 *  切换后 recreate。未覆盖的字符串由 values/（中文默认）回落。 */
object L10n {
    private const val PREFS = "firesky"
    const val KEY_LANGUAGE = "appLanguage"

    /** 当前语言偏好：system / zh / en。 */
    fun language(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_LANGUAGE, "system") ?: "system"

    /** 写入偏好并重建 Activity（stringResource 随新 locale 重取）。 */
    fun setLanguage(context: Context, value: String) {
        if (language(context) == value) return
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_LANGUAGE, value).apply()
        (context as? Activity)?.recreate()
    }

    /** MainActivity.attachBaseContext 调用：zh/en 显式 locale，system 保持系统。 */
    fun wrap(base: Context): Context = when (language(base)) {
        "zh" -> localized(base, Locale.SIMPLIFIED_CHINESE)
        "en" -> localized(base, Locale.ENGLISH)
        else -> base
    }

    private fun localized(base: Context, locale: Locale): Context {
        val config = base.resources.configuration
        config.setLocales(LocaleList(locale))
        LocaleList.setDefault(LocaleList(locale))
        return base.createConfigurationContext(config)
    }
}

/** 引擎返回的中文状态 → 当前语言（macOS L10n.status 同构）；未覆盖回落原文。 */
@Composable
fun statusText(engineTitle: String): String = when (engineTitle) {
    "已完成" -> stringResource(R.string.st_completed)
    "需下载" -> stringResource(R.string.st_download_needed)
    "可更新" -> stringResource(R.string.st_update_available)
    "已下载" -> stringResource(R.string.st_downloaded)
    "运行中" -> stringResource(R.string.st_running)
    "取消中" -> stringResource(R.string.st_cancelling)
    "已取消" -> stringResource(R.string.st_cancelled)
    "失败" -> stringResource(R.string.st_failed)
    "已中断" -> stringResource(R.string.st_interrupted)
    "发布周期已核验" -> stringResource(R.string.st_pub_verified)
    "更新待核验" -> stringResource(R.string.st_update_pending)
    "有新发布的数据可更新" -> stringResource(R.string.st_newer_data)
    "非最新发布周期" -> stringResource(R.string.st_not_latest)
    "本次预测数据不完整" -> stringResource(R.string.st_data_incomplete)
    "尚未下载本次预测数据" -> stringResource(R.string.st_not_downloaded)
    else -> engineTitle
}
