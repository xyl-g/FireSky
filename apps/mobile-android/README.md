# FireSky Android（apps/mobile-android）

Android 原生骨架（蓝图 `docs/MOBILE_NATIVE_PLAN.md`，MN2 parity 目标）。单模块 Gradle 工程：
Kotlin 2.x + Jetpack Compose（BOM 2024.06.00），minSdk 26 / compileSdk 34 / targetSdk 34。

## 结构（镜像 iOS 单点工作台）

| iOS 源（apps/macos/Sources/FireSkyNative） | Android 对应 | 说明 |
|---|---|---|
| EngineClient.swift | `app/src/main/java/com/firesky/mobile/engine/EngineClient.kt` | JSON-lines 协议客户端同构：帧/握手/进度三链/取消语义照抄，仅换传输层 |
| （传输层：Process stdin/stdout） | `engine/EngineTransport.kt` + `engine/MockTransport.kt` | §1.1 五原语抽象；Mock 在进程内模拟 desktop 服务，编译即可全链路运行 |
| （MN2 UniFFI/C ABI） | `engine/JniTransport.kt` + 仓库根 `engine-mobile/include/fsmobile.h` | FFI 预留：external 声明按 C 契约固定，`libengine_mobile.so` 产出前显式失败不静默回退 |
| Store.swift（point 作用域） | `store/WorkbenchStore.kt` | ViewModel + StateFlow；connect/search/metadata→check→publications→prepare→confirm→result、取消、CAMS 设置 |
| Workbench.swift（WorkLocationView/WorkParametersView/Run/Progress） | `ui/WorkbenchScreen.kt` | 参数表单（搜索/收藏/坐标/日期/现象）+ 运行按钮 + 进度条 + 准备面板（PreflightView 同构） |
| Workbench.swift（WorkHeroView/WorkResultsView） | `ui/ResultCards.kt` | GFS/EC hero 卡、指标卡三组、霞光质量曲线（Canvas）、逐时台账、诊断 |
| DialogViews.swift（SettingsView） | `ui/SettingsScreen.kt` | 外观 / EC 下载源 / CAMS Key / 本地引擎（传输通道选择） |
| Workbench.swift 色板 | `ui/Theme.kt` + `ui/Fmt.kt` | warm/teal 双色深浅主题；结果格式化（metricValue/timeLabel/windowLabel 同构） |

**v1 范围**（蓝图 §3）：仅单点预测；区域预测与实况已排除。地图选点延后（openQuestions D1）；
系统定位延后（MN3，`locate()` 显式提示）。

## 构建

```bash
# JDK 17（AGP 8.4 要求）+ Android SDK（platforms;android-34, build-tools;34.0.0）
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
./gradlew assembleDebug          # 产物 build/app/outputs/apk/debug/app-debug.apk
```

`local.properties`（不提交）里写 `sdk.dir=<Android SDK 根>`；或用 `ANDROID_HOME`。

**仓库根门禁入口**：Gradle 从 cwd 向上定位构建，仓库根另有一份薄入口
（`/settings.gradle.kts` + `/build.gradle.kts` + `/gradlew` + `/gradle.properties` + `/local.properties`），
把 `:app` 指向 `apps/mobile-android/app`，使 `./gradlew assembleDebug` 或
`apps/mobile-android/gradlew assembleDebug`（cwd = worktree 根）都能工作。
根入口的插件版本与 `apps/mobile-android/build.gradle.kts` 必须保持同步；
cwd 在 apps/mobile-android 内时，其独立 settings 优先生效，行为不变。

## 传输通道

- **mock**（默认）：`MockTransport` 按桌面帧契约模拟 hello→check→publications→prepare→forecast
  （含 progress 三链与 cancel）；GFS/EC 预置就绪、CAMS 缺失以演示「下载缺失数据」链路。
- **ffi**（预留）：`JniTransport`，native 入口按 `engine-mobile/include/fsmobile.h` 固定；
  engine-mobile crate（MN2）产出 `libengine_mobile.so` 后启用加载并从设置页切换。

## 协议快照

- 请求：`{"id":<uuid>,"method":<str>,"params":{…}}\n`，1 MiB 行上限（对齐 engine/src/rpc.rs）。
- 响应：`{"id","type":"progress","event":{percent,source,message}}` /
  `{"id","type":"error","error":{code,message}}` / `{"id","type":"result","result":{…}}`。
- 握手：`hello` → `protocol == 1`；取消：独立 id 的 `method:"cancel", params:{request_id}`。

## 状态备忘（2026-11-12，MN2 骨架）

- `assembleDebug` 为本骨架的编译门禁；模拟器/真机冒烟属 MN2 验收，未在本骨架执行。
- `engine-mobile/include/fsmobile.h` 在仓库快照中不存在，由本任务按蓝图 §1.1 五原语起草，
  MN1/MN2 实施 engine-mobile crate 时以 crate 作者确认后的版本为准（D2：UniFFI 为主，此为备选 ABI 面）。
