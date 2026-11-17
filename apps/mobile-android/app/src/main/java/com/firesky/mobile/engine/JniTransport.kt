package com.firesky.mobile.engine

import android.content.Context
import android.content.res.AssetManager
import org.json.JSONObject
import java.io.File

/**
 * JniTransport：engine-mobile C 固定契约（仓库根 `engine-mobile/include/fsmobile.h`，
 * 2026-11-12 裁定版）的 Android FFI 桥——单实例进程内 JSON-lines 服务，不透传句柄。
 *
 * 契约要点（与 fsmobile.h 逐条对应；帧协议与桌面 stdio 服务逐帧一致）：
 * - `fsm_start(workspace_root, definitions_path, cache_root, callback, userdata)` → 0/-1；
 *   单实例，重复启动失败（先 fsm_stop 回收，幂等）。definitions_path 非空时由引擎侧
 *   **先注入单数 ECCODES_DEFINITION_PATH 再初始化引擎**（MM0 坑 10；ffi.rs 中
 *   set_var 先于 service::start，此处只需把解压后的目录绝对路径交过去）。
 * - 回调帧：UTF-8 单行 JSON（NUL 结尾、不含换行），引擎侧分配、回调返回后失效。
 * - `fsm_send(line)`：不含换行的单行 JSON（容忍一个结尾换行），≤1 MiB。
 * - `fsm_cancel(request_id)`：0=已置位 / 1=无此进行中请求 / -1=未启动。
 * - `fsm_stop()`：幂等；不得在帧回调内调用。
 * - `fsm_last_error()`：线程局部，无错误返回 null。
 *
 * MN2 接线状态（本文件只对齐签名与调用次序，运行接线待 MN2）：
 * 1. `libengine_mobile.so` 尚未随 APK 打包（app/build.gradle.kts 未配置 jniLibs）；
 * 2. .so 目前仅导出 fsm_* C 符号，Kotlin `external fun` 需 `Java_com_firesky_mobile_…`
 *    命名的 JNI 绑定层（NDK C shim 或 Rust jni 导出）——MN2 落地；
 * 3. 库未加载时 start() 显式抛 EngineError（不静默回退），Mock 通道不受影响。
 */
class JniTransport(private val context: Context) : EngineTransport {

    @Volatile
    override var onFrame: FrameListener? = null

    @Volatile
    private var started = false

    override fun start(config: JSONObject) {
        stop()
        // definitions（MM0 坑 10/13）：APK asset 不能以目录形式直接交给引擎，先解压
        // 到 filesDir，再把绝对路径交给 fsm_start；assets 未打包时传 null（契约：
        // NULL 则不注入）。注入先于引擎初始化由引擎侧保证（ffi.rs set_var 先于线程启动）。
        val filesDir = context.filesDir
        val workspace = config.optString("workspace_root", filesDir.absolutePath)
        val cache = config.optString("cache_root", File(filesDir, "cache").absolutePath)
        val definitionsDir = config.optString(
            "definitions_dir",
            File(filesDir, "eccodes/definitions").absolutePath,
        )
        val definitionsPath = unpackDefinitions(context.assets, File(definitionsDir))

        loadLibraryOnce()
        val status = fsmStart(
            workspace,
            definitionsPath?.absolutePath,
            cache,
            { frame, _ -> onFrame?.onFrame(frame) },
            null,
        )
        if (status != 0) {
            throw EngineError("引擎启动失败：${fsmLastError() ?: "未知错误"}")
        }
        started = true
    }

    override fun send(frame: String) {
        if (!started) throw EngineError("FFI 引擎尚未接入。")
        val status = fsmSend(frame)
        if (status != 0) {
            throw EngineError("FFI 发送失败：${fsmLastError() ?: "status=$status"}")
        }
    }

    override fun cancel(requestId: String) {
        if (started) fsmCancel(requestId)
    }

    override fun stop() {
        if (!started) return
        started = false
        fsmStop()
    }

    // ---------------------------------------------------------------- JNI 入口
    // external 声明与 fsmobile.h 一一对应（固定契约，无句柄）；随 libengine_mobile.so
    // 的 Java_* JNI 绑定层提供（MN2）。库未加载时不会被调用——start 先 loadLibrary，
    // 失败即抛 EngineError。

    /** fsm_start(workspace_root, definitions_path, cache_root, callback, userdata) —— 0 成功。 */
    private external fun fsmStart(
        workspaceRoot: String,
        definitionsPath: String?,
        cacheRoot: String?,
        callback: FrameCallback,
        userdata: Any?,
    ): Int

    /** fsm_send(line) —— 0 成功。 */
    private external fun fsmSend(line: String): Int

    /** fsm_cancel(request_id) —— 0=已置位 / 1=无此请求 / -1=未启动。 */
    private external fun fsmCancel(requestId: String): Int

    /** fsm_stop() —— 幂等，重复调用返回 0。 */
    private external fun fsmStop(): Int

    /** fsm_last_error() —— 本线程最近一次失败原因，无错误返回 null。 */
    private external fun fsmLastError(): String?

    /** 帧回调（fsm_frame_cb 的 Kotlin 形）：frame 为单行 JSON（不含换行）。 */
    fun interface FrameCallback {
        fun onFrame(frame: String, userdata: Any?)
    }

    // ---------------------------------------------------------------- 辅助

    private fun loadLibraryOnce() {
        if (JniTransport.loaded) return
        synchronized(JniTransport) {
            if (JniTransport.loaded) return
            try {
                System.loadLibrary("engine_mobile")
            } catch (error: UnsatisfiedLinkError) {
                throw EngineError(
                    "FFI 引擎尚未接入：libengine_mobile.so 未随 APK 打包（MN2 jniLibs），" +
                        "请使用内置模拟引擎。（${error.message}）",
                )
            }
            JniTransport.loaded = true
        }
    }

    companion object {
        @Volatile
        private var loaded = false
    }
}

/** APK assets 内 ecCodes definitions 目录（单数 ECCODES_DEFINITION_PATH 对应的资源）。 */
private const val ASSET_DEFINITIONS = "eccodes/definitions"

/**
 * 解包 assets/eccodes/definitions/ 下的资源到 targetDir（filesDir 内），返回解压目录；
 * assets 未打包该目录时返回 null（契约：fsm_start 收到 NULL 则不注入）。
 * 已解压过则跳过（APK 资源随版本更新需清 app 数据或升 versionCode 重建目录）。
 */
fun unpackDefinitions(assets: AssetManager, targetDir: File): File? {
    val entries = runCatching { assets.list(ASSET_DEFINITIONS) }.getOrNull() ?: return null
    if (entries.isEmpty()) return null
    if (!targetDir.isDirectory || targetDir.listFiles().isNullOrEmpty()) {
        copyAssets(assets, ASSET_DEFINITIONS, targetDir)
    }
    return targetDir
}

private fun copyAssets(assets: AssetManager, from: String, to: File) {
    val entries = assets.list(from).orEmpty()
    if (entries.isEmpty()) {
        // 叶子节点（assets.list 对文件返回空数组）。
        to.parentFile?.mkdirs()
        assets.open(from).use { input ->
            to.outputStream().use { output -> input.copyTo(output) }
        }
        return
    }
    to.mkdirs()
    for (name in entries) copyAssets(assets, "$from/$name", File(to, name))
}
