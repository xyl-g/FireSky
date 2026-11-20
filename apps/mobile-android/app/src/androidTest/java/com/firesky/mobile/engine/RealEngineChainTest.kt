package com.firesky.mobile.engine

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.firesky.mobile.store.WorkbenchState
import com.firesky.mobile.store.WorkbenchStore
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.MethodSorters
import java.io.File
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/**
 * MN2 验收（任务书 T1 §5.4）：Android 模拟器内真引擎全链
 * hello → check → prepare → forecast，镜像 iOS Tests/RealEngineChainTests.swift。
 *
 * 前置（宿主侧 harness 负责，见 output/ 下 T1 运行脚本）：
 * 1. 种子缓存推送到 app 内部 filesDir/seed-cache（任务书 §5.4 的 adb root 兜底
 *    路径：API 34 的 FUSE 对 root 直写 /data/media/0/Android/data 的下层文件
 *    向 app 呈现不可见，故改推 /data/data/com.firesky.mobile/files/seed-cache
 *    并 chown 到 app uid）。D 选取照抄 iOS 逻辑：gfs∩ecmwf 都有、且 CAMS 在
 *    D/D-1 有真实 GRIB 的最新日期；本测试在种子目录上复用同一逻辑复核。
 * 2. APK 已含 assets/eccodes/definitions（copyEccodesDefinitions）与
 *    lib/arm64-v8a/libengine_mobile.so（cargoAndroidEngine + linkEngineJni）。
 *
 * 产物：forecast/check/prepare 结果 JSON 写到 filesDir/parity-out/，
 * 宿主经 run-as/root 取出后与 drive-desktop.mjs 输出跑 compare.mjs 帧级 parity。
 *
 * kill-restart：test1 的 workspace = filesDir（与 WorkbenchStore.engineConfig
 * 一致），任务历史落 filesDir/.firesky；test2 新建 WorkbenchStore（内部新建
 * JniTransport/EngineClient）→ connect → hello → task_history → result_restore
 * 自动恢复上次 forecast（验证 §5.3 接线）。
 */
@RunWith(AndroidJUnit4::class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class RealEngineChainTest {

    private val context: Context = InstrumentationRegistry.getInstrumentation().targetContext

    /** 内部 filesDir 下的种子缓存根（adb root 直推 + chown app uid，见类注释）。 */
    private fun seedRoot(): File = File(context.filesDir, "seed-cache")

    /** parity 输出目录（宿主经 run-as 取出）。 */
    private fun parityOut(): File = File(context.filesDir, "parity-out").apply { mkdirs() }

    /**
     * D 选取（照抄 iOS RealEngineChainTests）：gfs∩ecmwf 的 ISO 日期交集中，
     * CAMS 在 D 或 D-1 有真实 .grib/.grib2 数据的最新日期。
     */
    private fun selectTargetDate(seed: File): String {
        val iso = Regex("^\\d{4}-\\d{2}-\\d{2}$")
        val gfsDates = File(seed, "gfs").listFiles()?.map { it.name }?.toSet().orEmpty()
        val ecmwfDates = File(seed, "ecmwf").listFiles()?.map { it.name }?.toSet().orEmpty()
        val formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd").withZone(ZoneOffset.UTC)
        fun previousDay(date: String): String? = runCatching {
            LocalDate.parse(date).minusDays(1).format(formatter)
        }.getOrNull()
        fun camsHasData(date: String): Boolean {
            val dir = File(seed, "cams/$date")
            if (!dir.isDirectory) return false
            return dir.walkTopDown().any { it.isFile && it.extension in setOf("grib", "grib2") }
        }
        val candidates = gfsDates.intersect(ecmwfDates).filter { iso.matches(it) }.sortedDescending()
        return candidates.firstOrNull { camsHasData(it) || previousDay(it)?.let(::camsHasData) == true }
            ?: run {
                val camsDir = File(seed, "cams")
                val diag = buildString {
                    append("cams.isDirectory=${camsDir.isDirectory} ")
                    append("cams.list=${camsDir.list()?.joinToString()} ")
                    for (date in camsDir.list().orEmpty()) {
                        val dir = File(camsDir, date)
                        append("[$date isDir=${dir.isDirectory} list=${dir.list()?.joinToString()}] ")
                        for (cycle in dir.list().orEmpty()) {
                            val cycleDir = File(dir, cycle)
                            append("[$date/$cycle list=${cycleDir.list()?.take(3)?.joinToString()} n=${cycleDir.list()?.size}] ")
                        }
                    }
                }
                throw AssertionError("种子缓存无可用 D（gfs/ecmwf 交集且 CAMS D/D-1 有 GRIB）：$candidates；诊断：$diag")
            }
    }

    /** 与 iOS/桌面 driver 同构的杭州单点 payload。 */
    private fun payload(targetDate: String): JSONObject = JSONObject()
        .put("name", "杭州")
        .put("lat", 30.2741)
        .put("lon", 120.1551)
        .put("elevation_m", 8.0)
        .put("timezone_name", "Asia/Shanghai")
        .put("target_date", targetDate)
        .put("phenomenon", "dusk")
        .put("model_source", "both")
        .put("workers", 1)
        .put("forecast_mode", "model")
        .put("satellite_source", "off")
        .put("ecmwf_source", "ecmwf")

    private fun dump(name: String, value: JSONObject) {
        File(parityOut(), name).writeText(value.toString(2) + "\n")
    }

    /**
     * 全链：hello（版本/能力）→ check → prepare → forecast
     * （allow_download:false, confirmed:true；历史周期样本另需 cycle_age_confirmed，
     * 与 iOS/桌面 driver 一致）。definitions 走 APK assets 解包（bundle 优先，
     * 不依赖宿主 env 注入——解码成功即注入生效证据）。
     */
    @Test
    fun test1PreseededCacheFullChain() {
        val seed = seedRoot()
        assertTrue("种子缓存缺失：先 adb push .cache/{gfs,ecmwf,cams} 到 ${seed.absolutePath}", seed.isDirectory)
        val targetDate = selectTargetDate(seed)
        dump("android-target-date.json", JSONObject().put("target_date", targetDate))

        // definitions 断言 1/2：APK assets 已打包完整树。
        val assetEntries = context.assets.list("eccodes/definitions").orEmpty()
        assertTrue("APK 未打包 assets/eccodes/definitions", assetEntries.isNotEmpty())

        // workspace = filesDir：与 WorkbenchStore.engineConfig 一致，任务历史
        // 落 filesDir/.firesky，供 test2 kill-restart 恢复。
        val workspace = context.filesDir
        val transport = JniTransport(context)
        val client = EngineClient(transport)
        client.start(
            JSONObject()
                .put("workspace_root", workspace.absolutePath)
                .put("cache_root", seed.absolutePath)
                .put("definitions_dir", File(workspace, "eccodes/definitions").absolutePath),
        )
        try {
            // definitions 断言 2/2：assets 已解包到 filesDir（fsm_start 注入该路径）。
            val unpacked = File(workspace, "eccodes/definitions")
            assertTrue("definitions 未解包到 filesDir", unpacked.isDirectory)
            assertTrue("definitions 解包不完整", File(unpacked, "grib2").isDirectory)

            runBlocking {
                val hello = withTimeout(60_000) { client.call("hello") }
                assertEquals("真实静态引擎握手 protocol == 1", 1, hello.optInt("protocol"))
                val capabilities = hello.optJSONArray("capabilities")
                assertNotNull("hello 应声明能力清单", capabilities)
                val caps = (0 until capabilities!!.length()).map { capabilities.optString(it) }
                assertTrue("引擎缺少 task_history/result_restore 能力", caps.containsAll(listOf("task_history", "result_restore")))

                val check = withTimeout(300_000) {
                    client.call("check", JSONObject().put("kind", "point").put("payload", payload(targetDate)))
                }
                dump("android-check.json", check)
                val session = check.optString("session")
                assertTrue("check 未返回会话令牌：$check", session.isNotEmpty())
                val checkReport = check.optJSONObject("report")
                assertTrue(
                    "预置缓存 $targetDate 未就绪：${checkReport?.optJSONArray("datasets")}",
                    checkReport?.optBoolean("ready") == true,
                )

                val prepared = withTimeout(600_000) {
                    client.call("prepare", JSONObject().put("session", session)) { event ->
                        println("[chain] prepare progress: $event")
                    }
                }
                dump("android-prepare.json", prepared)
                assertEquals("prepare 后数据应就绪", true, prepared.optJSONObject("report")?.optBoolean("ready"))

                val forecast = withTimeout(1_200_000) {
                    client.call(
                        "forecast",
                        JSONObject()
                            .put("session", session)
                            .put("allow_download", false)
                            .put("confirmed", true)
                            .put("cycle_age_confirmed", true),
                    ) { event -> println("[chain] forecast progress: $event") }
                }
                dump("android-forecast.json", forecast)
                val models = forecast.optJSONObject("model_results")
                assertNotNull("forecast 应含 model_results", models)
                assertNotNull("forecast 应含 GFS 模型结果", models!!.optJSONObject("gfs"))
                assertNotNull("forecast 应含 EC 模型结果", models.optJSONObject("ec"))
                val errors = forecast.optJSONObject("model_errors")
                assertTrue("model_errors 应为空：$errors", errors == null || errors.length() == 0)
            }
        } finally {
            client.stop()
        }
    }

    /**
     * kill-restart restore（§5.3）：同 workspace 新建 WorkbenchStore（内部新建
     * JniTransport/EngineClient）→ connect 自动 task_history → result_restore，
     * 恢复 test1 的 forecast 结果与 location/date/phenomenon 参数。
     */
    @Test
    fun test2KillRestartRestore() {
        val targetDate = JSONObject(File(parityOut(), "android-target-date.json").readText())
            .optString("target_date")
        assertTrue("test1 未产出 target_date（先跑全链）", targetDate.isNotEmpty())

        // 清 prefs，确保走默认 transportMode=ffi（而非上次运行残留）。
        context.getSharedPreferences("firesky", Context.MODE_PRIVATE).edit().clear().commit()

        val application = context.applicationContext as android.app.Application
        val store = WorkbenchStore(application)
        try {
            // connect/restore 在 Main 调度器上跑；instrumentation 线程轮询状态。
            val deadline = System.currentTimeMillis() + 180_000
            while (System.currentTimeMillis() < deadline) {
                val state = store.state.value
                if (state.result != null) break
                if (state.error.isNotEmpty() && !state.online) {
                    throw AssertionError("kill-restart 连接失败：${state.error}")
                }
                Thread.sleep(200)
            }
            val state = store.state.value
            assertTrue("kill-restart 后应自动恢复历史结果（status=${state.status} error=${state.error}）", state.result != null)
            val models = state.result!!.optJSONObject("model_results")
            assertNotNull("恢复结果应含 model_results", models)
            assertNotNull("恢复结果应含 GFS", models!!.optJSONObject("gfs"))
            assertNotNull("恢复结果应含 EC", models.optJSONObject("ec"))
            assertEquals("gfs 存在时默认选中 gfs", "gfs", state.selectedModel)
            assertEquals("恢复观测点", "杭州", state.location.name)
            assertEquals("恢复目标日期", targetDate, state.targetDate.toString())
            assertEquals("恢复天象", "dusk", state.phenomenon)
            assertEquals("恢复状态文案", "已恢复历史结果", state.status)
            dump("android-restored.json", state.result!!)

            // mock 通道仍可切换：setTransportMode("mock") 停真引擎并连 mock。
            store.setTransportMode("mock")
            val mockDeadline = System.currentTimeMillis() + 30_000
            while (System.currentTimeMillis() < mockDeadline && !store.state.value.online) {
                Thread.sleep(100)
            }
            assertTrue("切回 mock 后应在线", store.state.value.online)
            assertEquals("mock", store.state.value.transportMode)
        } finally {
            // 收尾：切到 mock（setTransportMode 先 engine.stop 回收真引擎线程；
            // mock worker 为 daemon 线程，进程退出即回收）。
            store.setTransportMode("mock")
        }
    }

    /** mock 通道独立回归：MockTransport + EngineClient 全链不依赖 .so。 */
    @Test
    fun test3MockChannelChain() {
        assertEquals("默认传输应为 ffi（MN2）", "ffi", WorkbenchState().transportMode)
        val client = EngineClient(MockTransport())
        client.start(JSONObject())
        try {
            runBlocking {
                val hello = withTimeout(10_000) { client.call("hello") }
                assertEquals(1, hello.optInt("protocol"))
                assertEquals("firesky-mock", hello.optString("engine"))
                val check = withTimeout(10_000) {
                    client.call(
                        "check",
                        JSONObject().put("kind", "point").put("payload", payload(LocalDate.now().toString())),
                    )
                }
                val session = check.optString("session")
                assertTrue(session.isNotEmpty())
                withTimeout(30_000) { client.call("prepare", JSONObject().put("session", session)) }
                val forecast = withTimeout(30_000) {
                    client.call("forecast", JSONObject().put("session", session).put("confirmed", true))
                }
                assertNotNull(forecast.optJSONObject("model_results")?.optJSONObject("gfs"))
                assertNotNull(forecast.optJSONObject("model_results")?.optJSONObject("ec"))
            }
        } finally {
            client.stop()
        }
    }
}