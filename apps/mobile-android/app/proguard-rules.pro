# FireSky Android 混淆规则（release 未启用 minify，规则为后续收紧时预留）
# engine-mobile JNI 桥接（MN2 接入后）：保留 JniTransport 的 native 入口
-keepclasseswithmembernames class com.firesky.mobile.engine.JniTransport {
    native <methods>;
}
