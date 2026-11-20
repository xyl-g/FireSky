/*
 * FireSky engine-mobile Android JNI shim（T1 / MN2）。
 *
 * libengine_mobile.a 只导出 fsmobile.h 的 fsm_* C 符号；Kotlin
 * `JniTransport.external fun` 需要 Java_com_firesky_mobile_engine_JniTransport_*
 * 命名的 JNI 绑定。本文件与静态库一起链接进 libengine_mobile.so：
 *
 *   aarch64-linux-android26-clang -shared -Iengine-mobile/include \
 *     engine-mobile/jni/fsm_jni.c libengine_mobile.a libeccodes.a libaec.a \
 *     -lc++_static -lc++abi -lm -ldl -llog
 *
 * 契约要点（include/fsmobile.h）：
 * - 单实例；Kotlin 侧保证 start 前先 stop（JniTransport.start 首行 stop()）。
 * - 帧回调可能来自引擎工作线程，同一时刻至多一个回调（引擎内部串行锁）。
 * - 回调帧为 NUL 结尾单行 UTF-8 JSON，回调返回后失效 → 必须在回调内拷贝。
 * - fsm_stop 等待线程收尾，返回后不会再有回调 → 之后才能 DeleteGlobalRef。
 *
 * UTF-8 正确性：GetStringUTFChars/NewStringUTF 使用 modified-UTF-8
 * （补充平面字符编成 6 字节代理对、U+0000 编成 0xC0 0x80），与 Rust 标准
 * UTF-8 不兼容。因此双向一律走 byte[] + StandardCharsets.UTF_8：
 * - 入参：String.getBytes(UTF_8) → 拷贝补 NUL → C 字符串；
 * - 出参：NewByteArray + SetByteArrayRegion → new String(bytes, UTF_8)。
 *
 * 线程模型：引擎工作线程未 attach 到 ART，frame_cb 内 GetEnv 失败则
 * AttachCurrentThread，回调结束 DetachCurrentThread（Rust 线程退出时若仍
 * attach 会 abort ART，必须每回调 attach/detach）。Java 异常不得穿透进
 * Rust：CallVoidMethod 后 ExceptionCheck → Describe + Clear。
 */
#include <jni.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "fsmobile.h"

/* ---------------------------------------------------------------- 全局缓存 */

static JavaVM *g_vm = NULL;

/* JNI_OnLoad 缓存的类/方法/字段（GlobalRef，进程生命周期）。 */
static jclass g_string_class = NULL;
static jmethodID g_string_get_bytes = NULL;   /* String.getBytes(Charset)[B */
static jmethodID g_string_init_bytes = NULL;  /* String.<init>([B,Charset)V */
static jobject g_utf8_charset = NULL;         /* StandardCharsets.UTF_8 */

/* fsmStart 建立的回调引用；fsmStop 先 fsm_stop() 再释放。 */
static jobject g_callback = NULL;
static jobject g_userdata = NULL;
static jmethodID g_on_frame = NULL;

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM *vm, void *reserved) {
    (void)reserved;
    g_vm = vm;
    JNIEnv *env = NULL;
    if ((*vm)->GetEnv(vm, (void **)&env, JNI_VERSION_1_6) != JNI_OK) {
        return JNI_ERR;
    }
    jclass string_local = (*env)->FindClass(env, "java/lang/String");
    if (string_local == NULL) return JNI_ERR;
    g_string_class = (*env)->NewGlobalRef(env, string_local);
    (*env)->DeleteLocalRef(env, string_local);
    g_string_get_bytes =
        (*env)->GetMethodID(env, g_string_class, "getBytes", "(Ljava/nio/charset/Charset;)[B");
    g_string_init_bytes =
        (*env)->GetMethodID(env, g_string_class, "<init>", "([BLjava/nio/charset/Charset;)V");
    if (g_string_get_bytes == NULL || g_string_init_bytes == NULL) return JNI_ERR;

    jclass charsets_local = (*env)->FindClass(env, "java/nio/charset/StandardCharsets");
    if (charsets_local == NULL) return JNI_ERR;
    jfieldID utf8_field = (*env)->GetStaticFieldID(
        env, charsets_local, "UTF_8", "Ljava/nio/charset/Charset;");
    if (utf8_field == NULL) return JNI_ERR;
    jobject utf8_local = (*env)->GetStaticObjectField(env, charsets_local, utf8_field);
    (*env)->DeleteLocalRef(env, charsets_local);
    if (utf8_local == NULL) return JNI_ERR;
    g_utf8_charset = (*env)->NewGlobalRef(env, utf8_local);
    (*env)->DeleteLocalRef(env, utf8_local);
    return JNI_VERSION_1_6;
}

/* ---------------------------------------------------------------- UTF-8 桥 */

/*
 * jstring → malloc 的 NUL 结尾标准 UTF-8 C 字符串；NULL 入参返回 NULL。
 * 调用方负责 free。OOM/异常时返回 NULL（异常保持 pending，由 JVM 抛出）。
 */
static char *jstring_to_cstr(JNIEnv *env, jstring value) {
    if (value == NULL) return NULL;
    jbyteArray bytes =
        (*env)->CallObjectMethod(env, value, g_string_get_bytes, g_utf8_charset);
    if (bytes == NULL) return NULL; /* 异常 pending */
    jsize length = (*env)->GetArrayLength(env, bytes);
    char *buffer = (char *)malloc((size_t)length + 1);
    if (buffer == NULL) {
        (*env)->DeleteLocalRef(env, bytes);
        return NULL;
    }
    (*env)->GetByteArrayRegion(env, bytes, 0, length, (jbyte *)buffer);
    buffer[length] = '\0';
    (*env)->DeleteLocalRef(env, bytes);
    return buffer;
}

/* NUL 结尾标准 UTF-8 C 字符串 → jstring；NULL 入参返回 NULL。 */
static jstring cstr_to_jstring(JNIEnv *env, const char *value) {
    if (value == NULL) return NULL;
    jsize length = (jsize)strlen(value);
    jbyteArray bytes = (*env)->NewByteArray(env, length);
    if (bytes == NULL) return NULL;
    (*env)->SetByteArrayRegion(env, bytes, 0, length, (const jbyte *)value);
    jstring result =
        (*env)->NewObject(env, g_string_class, g_string_init_bytes, bytes, g_utf8_charset);
    (*env)->DeleteLocalRef(env, bytes);
    return result;
}

/* ---------------------------------------------------------------- 帧回调 */

/*
 * 引擎工作线程 → Kotlin FrameCallback.onFrame(String, Object?)。
 * 每回调 attach/detach；异常 Describe+Clear，不得穿透进 Rust。
 */
static void frame_cb(const char *frame, void *userdata) {
    (void)userdata; /* 透传对象经 g_userdata GlobalRef 交付 */
    if (g_vm == NULL || g_callback == NULL || g_on_frame == NULL) return;
    JNIEnv *env = NULL;
    jint status = (*g_vm)->GetEnv(g_vm, (void **)&env, JNI_VERSION_1_6);
    int attached_here = 0;
    if (status == JNI_EDETACHED) {
        JavaVMAttachArgs args;
        args.version = JNI_VERSION_1_6;
        args.name = (char *)"firesky-engine-frame";
        args.group = NULL;
        if ((*g_vm)->AttachCurrentThread(g_vm, &env, &args) != JNI_OK) return;
        attached_here = 1;
    } else if (status != JNI_OK) {
        return;
    }
    jstring jframe = cstr_to_jstring(env, frame);
    if (jframe != NULL) {
        (*env)->CallVoidMethod(env, g_callback, g_on_frame, jframe, g_userdata);
        (*env)->DeleteLocalRef(env, jframe);
    }
    if ((*env)->ExceptionCheck(env)) {
        (*env)->ExceptionDescribe(env);
        (*env)->ExceptionClear(env);
    }
    if (attached_here) {
        (*g_vm)->DetachCurrentThread(g_vm);
    }
}

/* ---------------------------------------------------------------- 导出绑定 */

JNIEXPORT jint JNICALL
Java_com_firesky_mobile_engine_JniTransport_fsmStart(JNIEnv *env, jobject self,
                                                     jstring workspace, jstring definitions,
                                                     jstring cache, jobject callback,
                                                     jobject userdata) {
    (void)self;
    if (callback == NULL) return -1;
    char *workspace_c = jstring_to_cstr(env, workspace);
    char *definitions_c = jstring_to_cstr(env, definitions);
    char *cache_c = jstring_to_cstr(env, cache);
    if (workspace_c == NULL) {
        free(definitions_c);
        free(cache_c);
        return -1;
    }
    jobject callback_ref = (*env)->NewGlobalRef(env, callback);
    jobject userdata_ref = userdata != NULL ? (*env)->NewGlobalRef(env, userdata) : NULL;
    jclass callback_class = (*env)->GetObjectClass(env, callback);
    jmethodID on_frame = (*env)->GetMethodID(
        env, callback_class, "onFrame", "(Ljava/lang/String;Ljava/lang/Object;)V");
    (*env)->DeleteLocalRef(env, callback_class);
    if (callback_ref == NULL || on_frame == NULL ||
        (userdata != NULL && userdata_ref == NULL)) {
        if (callback_ref != NULL) (*env)->DeleteGlobalRef(env, callback_ref);
        if (userdata_ref != NULL) (*env)->DeleteGlobalRef(env, userdata_ref);
        free(workspace_c);
        free(definitions_c);
        free(cache_c);
        return -1;
    }
    g_callback = callback_ref;
    g_userdata = userdata_ref;
    g_on_frame = on_frame;
    /*
     * Android app 进程没有 /tmp（TMPDIR 未设置时 std::env::temp_dir() 回退
     * "/tmp"）：引擎 forecast 的 tempfile::tempdir() 会 ENOENT。把 TMPDIR
     * 指到 <workspace>/.tmp（每次 start 幂等创建）。Rust std::env 经 getenv
     * 读取，与 C setenv 共享同一 environ，fsm_start 前注入即可。
     */
    {
        size_t workspace_len = strlen(workspace_c);
        if (workspace_len + 6 < 4096) {
            char tmp_path[4096];
            memcpy(tmp_path, workspace_c, workspace_len);
            memcpy(tmp_path + workspace_len, "/.tmp", 6); /* 含 NUL */
            mkdir(tmp_path, 0700); /* EEXIST 无害；失败则引擎侧报错 */
            setenv("TMPDIR", tmp_path, 1);
        }
    }
    int status = fsm_start(workspace_c, definitions_c, cache_c, frame_cb, NULL);
    free(workspace_c);
    free(definitions_c);
    free(cache_c);
    if (status != 0) {
        /* 启动失败：引擎不会再回调，立即回收引用。 */
        (*env)->DeleteGlobalRef(env, g_callback);
        if (g_userdata != NULL) (*env)->DeleteGlobalRef(env, g_userdata);
        g_callback = NULL;
        g_userdata = NULL;
        g_on_frame = NULL;
    }
    return status;
}

JNIEXPORT jint JNICALL
Java_com_firesky_mobile_engine_JniTransport_fsmSend(JNIEnv *env, jobject self, jstring line) {
    (void)self;
    char *line_c = jstring_to_cstr(env, line);
    if (line_c == NULL) return -1;
    int status = fsm_send(line_c);
    free(line_c);
    return status;
}

JNIEXPORT jint JNICALL
Java_com_firesky_mobile_engine_JniTransport_fsmCancel(JNIEnv *env, jobject self,
                                                      jstring request_id) {
    (void)self;
    char *request_id_c = jstring_to_cstr(env, request_id);
    if (request_id_c == NULL) return -1;
    int status = fsm_cancel(request_id_c);
    free(request_id_c);
    return status;
}

JNIEXPORT jint JNICALL
Java_com_firesky_mobile_engine_JniTransport_fsmStop(JNIEnv *env, jobject self) {
    (void)self;
    /* 先 fsm_stop：等待工作线程收尾，返回后不会再有 frame_cb。 */
    int status = fsm_stop();
    if (g_callback != NULL) {
        (*env)->DeleteGlobalRef(env, g_callback);
        g_callback = NULL;
    }
    if (g_userdata != NULL) {
        (*env)->DeleteGlobalRef(env, g_userdata);
        g_userdata = NULL;
    }
    g_on_frame = NULL;
    return status;
}

JNIEXPORT jstring JNICALL
Java_com_firesky_mobile_engine_JniTransport_fsmLastError(JNIEnv *env, jobject self) {
    (void)self;
    return cstr_to_jstring(env, fsm_last_error());
}