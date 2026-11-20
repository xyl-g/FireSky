# FireSky iOS / Android 模拟器使用指南

检查日期：2026-11-20
项目：`apps/mobile-ios/`、`apps/mobile-android/`、`engine-mobile/`
结论：硬件支持双端模拟；Android 模拟器完整可用，iOS 命令行模拟和引擎测试可用。若要手动打开 iOS 模拟器窗口操作界面，还需补齐 Simulator.app。

## 1. 本机检查结果

| 项目 | 本机状态 | 结论 |
| --- | --- | --- |
| Mac | MacBook Pro，Apple M1 Max，64 GB 内存 | ARM64 iOS / Android 模拟器可运行 |
| Xcode 命令行工具 | `/Applications/Xcode.app`，Xcode 27.2 | 已安装，可构建 iOS App |
| iOS 模拟器运行时 | iOS 27.2 runtime；iPhone 18 Pro、iPhone 17 等设备 | 已安装，可由 `simctl` 启动并跑 XCTest |
| iOS Simulator 图形应用 | `Simulator.app` 未找到；`open -a Simulator` 失败 | 要手动看屏幕、点按 iOS App，需安装/修复完整 Xcode 的 Simulator 应用 |
| Xcode 命令行选择 | 当前 `xcode-select` 指向 `/Library/Developer/CommandLineTools` | 是命令路径问题；每条命令显式设置 `DEVELOPER_DIR` 即可，不必重装 Xcode |
| Android SDK | `/opt/homebrew/share/android-commandlinetools` | 已安装 SDK 34、Build Tools 34、Platform Tools 和 Android Emulator |
| Android AVD | `firesky34`，Android 14 / API 34 / arm64-v8a | 已创建，可以直接启动 |
| Android NDK | `/opt/homebrew/share/android-ndk` | 已安装，项目真实引擎交叉编译需要它 |
| Java | Homebrew JDK 17 与系统 JDK 24 并存 | FireSky Android 使用 JDK 17；不要用默认 JDK 24 跑本项目 Gradle |
| Android Studio | `/Applications` 中未发现 | 可选；命令行构建、启动 AVD 和运行测试不依赖它 |

### 需要安装什么

- **Android：不缺必须组件。** Android Studio 没安装，但不是必需项；AVD、模拟器、SDK/NDK、平台工具和 JDK 17 都已就位。
- **iOS 自动化与命令行验证：不缺运行时。** 当前已能启动 CoreSimulator、构建 App 并运行真实引擎 XCTest。
- **iOS 手动 UI 模拟：缺 Simulator.app 图形前端。** 目前 `/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app` 不存在，且 `open -a Simulator` 返回找不到应用。请从 Mac App Store 或 Apple Developer 下载并安装/修复完整 Xcode；首次打开 Xcode 后完成组件安装，再确认 Xcode 设置的 Components 中有 iOS 平台支持和 iOS Simulator runtime。已存在的 iOS 27.2 runtime 无需重复下载，除非完整 Xcode 安装后明确提示缺少兼容版本。

当前 Xcode 命令目录指向 Command Line Tools，系统默认 Java 是 24；两者都通过每条命令显式设置环境变量解决，不必更改全机设置。下文的 iOS 命令行测试可在 Simulator.app 图形前端安装前运行；安装后可用 Simulator 窗口手动走查。

## 2. iOS 模拟器

在终端设置 Xcode 路径并启动 iPhone 18 Pro 的模拟设备后端：

```bash
export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
xcrun simctl boot 5FC429FD-9199-4924-83E9-733DD4E5056B
xcrun simctl bootstatus 5FC429FD-9199-4924-83E9-733DD4E5056B -b
```

完整 Xcode 安装后，可打开图形模拟器窗口：

```bash
open -a Simulator
```

本机目前没有这个图形应用，所以以上 `simctl` 命令能启动设备和运行测试，但不会显示可手动操作的 Simulator 窗口。

如果设备已经启动，`boot` 提示设备正在运行时继续执行 `bootstatus` 即可。可用以下命令查看
设备名称和 ID：

```bash
xcrun simctl list devices available
```

构建 iOS 模拟器 App：

```bash
cd "/Users/xiaoyang/晓洋站点/firesky"
export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
xcodebuild \
  -project apps/mobile-ios/FireSkyMobile.xcodeproj \
  -scheme FireSkyMobile \
  -configuration Debug \
  -destination 'platform=iOS Simulator,id=5FC429FD-9199-4924-83E9-733DD4E5056B' \
  -derivedDataPath output/mobile-simulator-check-20261120/ios \
  build CODE_SIGNING_ALLOWED=NO
```

用模拟器安装并打开刚构建的 App：

```bash
export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
xcrun simctl install booted \
  output/mobile-simulator-check-20261120/ios/Build/Products/Debug-iphonesimulator/FireSkyMobile.app
xcrun simctl launch booted cn.firesky.mobile
```

运行 iOS 单元与真实引擎集成测试：

```bash
cd "/Users/xiaoyang/晓洋站点/firesky"
export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
xcodebuild \
  -project apps/mobile-ios/FireSkyMobile.xcodeproj \
  -scheme FireSkyMobile \
  -configuration Debug \
  -destination 'platform=iOS Simulator,id=5FC429FD-9199-4924-83E9-733DD4E5056B' \
  -derivedDataPath output/mobile-simulator-check-20261120/ios \
  -only-testing:FireSkyMobileTests \
  test CODE_SIGNING_ALLOWED=NO
```

不要在本项目构建时只指定 `-sdk iphonesimulator` 而省略具体设备 destination：该构建方式
会尝试构建 x86_64 slice，而当前项目只备有 Apple Silicon 所需的 ARM64 模拟器引擎库。
指定上面的 iPhone 设备后，iOS 模拟器构建已通过。

### iOS UI 自动化现状

通过 `simctl install` / `simctl launch` 已成功把 App 安装到模拟设备并启动，应用获得进程号并运行；当前缺图形前端，无法通过窗口手动点按。
真实引擎 XCTest 全套 18 项通过，其中包括真实引擎握手、GFS/EC 预置缓存预测链和杀进程后恢复。

当前 `FireSkyMobileUITests.testPointForecastChain` 未通过：XCTest 启动 UI 测试 App 后，在
`app.buttons["稍后"].waitForExistence` 处报告 `Application 'cn.firesky.mobile' does not have a process ID`。
同一模拟设备上的直接启动成功，因此 App 本身可以运行。XCTest 未取得进程号的原因尚未确认；缺少 Simulator.app 可能相关，补齐后应重跑 UI 自动化，再判断是否还需排查测试启动/附加链路。

## 3. Android 模拟器

先在终端设置 SDK、NDK 和项目要求的 JDK 17：

```bash
export ANDROID_HOME="/opt/homebrew/share/android-commandlinetools"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_NDK_ROOT="/opt/homebrew/share/android-ndk"
export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
```

在一个终端启动 Android 虚拟设备，并保持该终端打开：

```bash
emulator -avd firesky34 -no-snapshot-save
```

在另一个终端执行前面的环境变量设置，然后确认 AVD 已启动：

```bash
adb devices -l
adb shell getprop sys.boot_completed
```

启动完成时 `sys.boot_completed` 为 `1`，设备列表状态为 `device`。如果刚启动仍显示
`offline`，等待系统启动完毕后再构建、安装或测试。

构建 Android App 并运行 JVM 单测：

```bash
cd "/Users/xiaoyang/晓洋站点/firesky/apps/mobile-android"
export ANDROID_HOME="/opt/homebrew/share/android-commandlinetools"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_NDK_ROOT="/opt/homebrew/share/android-ndk"
export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
./gradlew assembleDebug testDebugUnitTest --console=plain
```

APK 输出位置：`apps/mobile-android/app/build/outputs/apk/debug/app-debug.apk`。
将 App 安装到正在运行的 AVD 并启动：

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.firesky.mobile/.MainActivity
adb shell pidof com.firesky.mobile
```

如果 `am start -W` 显示 `Status: timeout`，先查看进程号和前台 Activity；首次启动或模拟器
负载较高时，Activity 仍可能已经显示。不要因此立刻重复安装。

运行不依赖天气缓存的 Android 模拟器仪器测试：

```bash
./gradlew connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.class=com.firesky.mobile.engine.RealEngineChainTest#test3MockChannelChain \
  --console=plain
```

如果想一次运行该类中的真实引擎链和重启恢复测试，使用：

```bash
./gradlew connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.class=com.firesky.mobile.engine.RealEngineChainTest \
  --console=plain
```

该类的 `test1PreseededCacheFullChain` 和 `test2KillRestartRestore` 要求先把匹配的 GFS、EC、
CAMS GRIB 缓存放到 App 私有目录 `filesDir/seed-cache`。测试文件头部注释说明了数据选择和
推送/权限要求：
[RealEngineChainTest.kt](../apps/mobile-android/app/src/androidTest/java/com/firesky/mobile/engine/RealEngineChainTest.kt)。
没有预置数据时，先运行 `test3MockChannelChain`；不要把 mock 测试结果表述为真实天气预测验收。

## 4. 本次已验证

| 检查 | 结果 |
| --- | --- |
| iOS Simulator 构建 | 通过，iPhone 18 Pro / iOS 27.2 |
| iOS `FireSkyMobileTests` | 18 项通过；包括真实引擎预置缓存全链及重启恢复 |
| iOS UI 自动化 | 1 项失败，XCTest 未取得被测 App PID；Simulator.app 图形前端未安装，原因待补齐后复测 |
| Android `assembleDebug testDebugUnitTest` | 通过，Gradle 8.7 / JDK 17 |
| Android `testDebugUnitTest --rerun-tasks` | 本次强制执行 9 项，全部通过 |
| Android `test3MockChannelChain` | 通过，Android 14 / API 34 AVD |
| Android APK 安装 | 通过；MainActivity 已在模拟器前台启动 |
| Android SDK/模拟器加速 | AVD 的 ARM64 镜像已启动，Hypervisor.Framework 加速检查通过 |

首次 Android 构建约数分钟，之后 Gradle 会复用缓存。iOS 与 Android 模拟器都使用 ARM64，
足以验证界面、状态流转、协议和预置数据上的真实引擎行为；Wi-Fi/蜂窝切换、设备传感器、
耗电、锁屏后台行为、签名分发及真实设备兼容性仍需实体手机验收。

## 5. 停止与常见问题

- iOS Simulator：安装完整 Xcode 后在模拟器菜单选择 Quit，或执行 `xcrun simctl shutdown booted` 关闭设备后端。
- Android：在运行 `emulator -avd firesky34 ...` 的终端按 `Ctrl-C`；只关闭 AVD 窗口也可以。
- `xcodebuild` 找不到 `simctl`：在同一终端导出 `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`。
- `open -a Simulator` 找不到应用：安装/修复完整 Xcode，并在 Xcode > Settings > Components 检查平台与模拟器组件；仅有 `xcodebuild` 和 runtime 不能提供手动点按的 Simulator 窗口。
- Gradle 报 Android Gradle Plugin 需要 Java 17：确认 `JAVA_HOME` 指向上文 Homebrew JDK 17，并检查 `java -version`。
- `adb` 或 `emulator` 找不到：确认 `PATH` 包含 SDK 的 `platform-tools` 和 `emulator` 子目录。
- Gradle 提示 SDK XML 新旧版本不一致：本机已有 SDK 34 且本次构建成功；该提示来自 SDK 工具版本差异，不是缺少模拟器镜像。
- Android 真实引擎预测测试失败并提示 `seed-cache` 不存在：按上述说明准备真实缓存；mock 仪器测试不需要它。

本指南对应当前机器的绝对路径。换电脑后先重新检查 Xcode、模拟器 runtime、Android SDK/NDK、
AVD 和 Java 版本，再更新路径；不要把本机绝对路径提交为跨机器通用配置。
