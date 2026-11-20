package com.firesky.mobile.store

import android.content.SharedPreferences
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 海拔来源三档（v1.1.1 任务书 §1）JVM 单测：
 * 持久化（SharedPreferences 读写/重启恢复/非法值归一）与
 * search/metadata 请求参数透传 elevation_source。
 * SharedPreferences/Editor 是接口，JVM 侧用内存实现替身，无需 Robolectric。
 */
class ElevationSourceSettingTest {

    /** 内存 SharedPreferences 替身：跨实例共享同一 map 即模拟 kill-restart。 */
    private class FakePrefs(private val values: MutableMap<String, Any?> = mutableMapOf()) :
        SharedPreferences {
        private val editor = object : SharedPreferences.Editor {
            private val pending = mutableMapOf<String, Any?>()
            private val removals = mutableSetOf<String>()
            private var clearAll = false
            override fun putString(key: String, value: String?) = apply { pending[key] = value }
            override fun putStringSet(key: String, value: MutableSet<String>?) =
                apply { pending[key] = value }
            override fun putInt(key: String, value: Int) = apply { pending[key] = value }
            override fun putLong(key: String, value: Long) = apply { pending[key] = value }
            override fun putFloat(key: String, value: Float) = apply { pending[key] = value }
            override fun putBoolean(key: String, value: Boolean) = apply { pending[key] = value }
            override fun remove(key: String) = apply { removals.add(key) }
            override fun clear() = apply { clearAll = true }
            override fun commit(): Boolean {
                apply()
                return true
            }
            override fun apply() {
                if (clearAll) values.clear()
                removals.forEach { values.remove(it) }
                values.putAll(pending)
            }
        }
        override fun getAll(): MutableMap<String, *> = values
        override fun getString(key: String, defValue: String?): String? =
            values[key] as? String ?: defValue
        override fun getStringSet(key: String, defValue: MutableSet<String>?) =
            @Suppress("UNCHECKED_CAST") (values[key] as? MutableSet<String> ?: defValue)
        override fun getInt(key: String, defValue: Int) = values[key] as? Int ?: defValue
        override fun getLong(key: String, defValue: Long) = values[key] as? Long ?: defValue
        override fun getFloat(key: String, defValue: Float) = values[key] as? Float ?: defValue
        override fun getBoolean(key: String, defValue: Boolean) = values[key] as? Boolean ?: defValue
        override fun contains(key: String) = values.containsKey(key)
        override fun edit(): SharedPreferences.Editor = editor
        override fun registerOnSharedPreferenceChangeListener(
            listener: SharedPreferences.OnSharedPreferenceChangeListener?,
        ) {}
        override fun unregisterOnSharedPreferenceChangeListener(
            listener: SharedPreferences.OnSharedPreferenceChangeListener?,
        ) {}
    }

    @Test
    fun defaultIsAutoWhenUnset() {
        assertEquals("auto", ElevationSourceSetting.load(FakePrefs()))
        assertEquals("auto", ElevationSourceSetting.normalize(null))
    }

    @Test
    fun persistRoundTripAcrossRestart() {
        for (value in listOf("auto", "http", "srtm")) {
            val backing = mutableMapOf<String, Any?>()
            val prefs = FakePrefs(backing)
            assertEquals(value, ElevationSourceSetting.save(prefs, value))
            assertEquals(value, prefs.getString(ElevationSourceSetting.KEY, null))
            // 新实例共享同一存储 = 进程重启后恢复。
            assertEquals(value, ElevationSourceSetting.load(FakePrefs(backing)))
        }
    }

    @Test
    fun invalidValuesNormalizeToAuto() {
        for (bad in listOf("bogus", "", "HTTP", "srtm3", "auto ")) {
            val prefs = FakePrefs()
            assertEquals("auto", ElevationSourceSetting.save(prefs, bad))
            assertEquals("auto", ElevationSourceSetting.load(prefs))
        }
        // 存储里被外部写坏的值同样归一。
        val prefs = FakePrefs(mutableMapOf(ElevationSourceSetting.KEY to "bogus"))
        assertEquals("auto", ElevationSourceSetting.load(prefs))
    }

    @Test
    fun searchParamsPassThroughElevationSource() {
        for (value in listOf("auto", "http", "srtm")) {
            val params = ElevationSourceSetting.searchParams("杭州", value)
            assertEquals("杭州", params.optString("query"))
            assertEquals(value, params.optString("elevation_source"))
        }
        // 非法档归一为 auto，绝不把非法值透传给引擎。
        assertEquals("auto", ElevationSourceSetting.searchParams("杭州", "bogus")
            .optString("elevation_source"))
    }

    @Test
    fun metadataParamsPassThroughElevationSource() {
        for (value in listOf("auto", "http", "srtm")) {
            val params = ElevationSourceSetting.metadataParams(30.2741, 120.1551, value)
            assertEquals(30.2741, params.optDouble("lat"), 0.0)
            assertEquals(120.1551, params.optDouble("lon"), 0.0)
            assertEquals(value, params.optString("elevation_source"))
        }
        assertEquals("auto", ElevationSourceSetting.metadataParams(0.0, 0.0, "bogus")
            .optString("elevation_source"))
    }

    @Test
    fun paramsArePlainJsonObjectsWithoutExtraKeys() {
        val search = ElevationSourceSetting.searchParams("北京", "srtm")
        assertEquals(setOf("query", "elevation_source"), search.keys().asSequence().toSet())
        val metadata = ElevationSourceSetting.metadataParams(39.9042, 116.4074, "http")
        assertEquals(setOf("lat", "lon", "elevation_source"), metadata.keys().asSequence().toSet())
        assertTrue(metadata.get("elevation_source") is String)
        assertFalse(search.has("config"))
        // 序列化后可被引擎 Params 反序列化口径消费（字段名 snake_case）。
        assertTrue(JSONObject(search.toString()).has("elevation_source"))
    }
}