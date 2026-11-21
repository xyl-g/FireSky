import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// ---------------------------------------------------------------------------
// MN2 真引擎接线（任务书 T1 §5.2）：
//   cargoAndroidEngine → linkEngineJni → jniLibs/arm64-v8a/libengine_mobile.so
//   copyEccodesDefinitions → assets/eccodes/definitions（JniTransport 解包到 filesDir）
// 模块目录 apps/mobile-android/app 的 ../../../ = 仓库根；仓库根与模块内双
// settings 入口通用。生成物（jniLibs/assets）已由根 .gitignore 排除。
// ---------------------------------------------------------------------------
val repoRoot = projectDir.resolve("../../..").canonicalFile
val engineMobileDir = repoRoot.resolve("engine-mobile")
val crossLibDir = repoRoot.resolve(".cache/mm0-cross/build-android/lib")
val jniLibDir = projectDir.resolve("src/main/jniLibs/arm64-v8a")
val definitionsAssetDir = projectDir.resolve("src/main/assets/eccodes/definitions")

// NDK 路径：local.properties 的 ndk.dir（T0 已写好，双入口各一份），
// 其次 ANDROID_NDK_ROOT 环境变量，最后 Homebrew 默认 symlink。
val ndkDir: File = run {
    val props = Properties()
    val localProps = rootProject.file("local.properties")
    if (localProps.isFile) localProps.inputStream().use { props.load(it) }
    val fromProps = props.getProperty("ndk.dir")
    val fromEnv = System.getenv("ANDROID_NDK_ROOT")
    File(fromProps ?: fromEnv ?: "/opt/homebrew/share/android-ndk")
}

// brew ecCodes definitions 完整树（与桌面内容一致；131MB，裁剪是后续优化）。
val hostDefinitionsDir = File(
    System.getenv("FIRESKY_ECCODES_DEFINITIONS") ?: "/opt/homebrew/share/eccodes/definitions",
)

val cargoAndroidEngine = tasks.register<Exec>("cargoAndroidEngine") {
    group = "firesky"
    description = "交叉编译 engine-mobile 静态库（aarch64-linux-android，release）"
    workingDir = engineMobileDir
    environment("MM0_ECCODES_ANDROID_LIB", crossLibDir.absolutePath)
    // rustup 优先于 Homebrew rust（MM0 坑 11）；全局 ~/.cargo/config.toml 已钉扎 rustc。
    environment(
        "PATH",
        "${System.getProperty("user.home")}/.cargo/bin${File.pathSeparator}${System.getenv("PATH")}",
    )
    commandLine("cargo", "build", "--release", "--target", "aarch64-linux-android")
    doFirst {
        if (!crossLibDir.resolve("libeccodes.a").isFile || !crossLibDir.resolve("libaec.a").isFile) {
            throw GradleException(
                "缺少交叉编译产物 ${crossLibDir}/lib{eccodes,aec}.a——先运行 bash .cache/mm0-cross/build-all.sh",
            )
        }
    }
}

val linkEngineJni = tasks.register<Exec>("linkEngineJni") {
    group = "firesky"
    description = "把 JNI shim + engine-mobile/ecCodes/libaec 静态库链接为 libengine_mobile.so"
    dependsOn(cargoAndroidEngine)
    workingDir = repoRoot
    // NDK r30 在 Apple Silicon 上只有 darwin-x86_64 预构建目录（经 Rosetta 运行）；
    // 若未来出现 darwin-aarch64 则优先选用。
    val prebuilt = ndkDir.resolve("toolchains/llvm/prebuilt")
    val hostTag = if (prebuilt.resolve("darwin-aarch64").isDirectory) "darwin-aarch64" else "darwin-x86_64"
    val clang = prebuilt.resolve("$hostTag/bin/aarch64-linux-android26-clang")
    val staticLib = engineMobileDir.resolve("target/aarch64-linux-android/release/libengine_mobile.a")
    val output = jniLibDir.resolve("libengine_mobile.so")
    commandLine(
        clang.absolutePath, "-shared", "-O2",
        "-I", engineMobileDir.resolve("include").absolutePath,
        "-o", output.absolutePath,
        engineMobileDir.resolve("jni/fsm_jni.c").absolutePath,
        // 三个 .a 用 --start-group 包住，避免循环依赖导致的未解析符号。
        "-Wl,--start-group",
        staticLib.absolutePath,
        crossLibDir.resolve("libeccodes.a").absolutePath,
        crossLibDir.resolve("libaec.a").absolutePath,
        "-Wl,--end-group",
        "-lc++_static", "-lc++abi", "-lm", "-ldl", "-llog",
        "-Wl,-z,max-page-size=16384",
    )
    doFirst {
        jniLibDir.mkdirs()
        if (!staticLib.isFile) {
            throw GradleException("缺少 $staticLib——cargoAndroidEngine 未产出静态库")
        }
    }
}

val copyEccodesDefinitions = tasks.register<Copy>("copyEccodesDefinitions") {
    group = "firesky"
    description = "复制宿主 ecCodes definitions 完整树到 APK assets（生成物不入库）"
    from(hostDefinitionsDir)
    into(definitionsAssetDir)
    doFirst {
        if (!hostDefinitionsDir.isDirectory) {
            throw GradleException(
                "宿主缺少 ecCodes definitions（brew install eccodes 或设 FIRESKY_ECCODES_DEFINITIONS）",
            )
        }
    }
}

// release 签名：keystore 与口令在仓库外 ~/.firesky-release/（永不入库）。
// 后续更新必须使用同一 keystore，妥善备份。
val releaseKeystorePropsFile = File(System.getProperty("user.home"), ".firesky-release/keystore.properties")
val releaseKeystoreProps = Properties()
if (releaseKeystorePropsFile.isFile) {
    releaseKeystorePropsFile.inputStream().use { releaseKeystoreProps.load(it) }
}

android {
    namespace = "com.firesky.mobile"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.firesky.mobile"
        minSdk = 26
        targetSdk = 34
        versionCode = 17
        versionName = "1.9.4"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // 真引擎 .so 仅 arm64-v8a（AVD firesky34 同架构）。
        ndk {
            abiFilters += "arm64-v8a"
        }
    }

    signingConfigs {
        create("release") {
            if (releaseKeystorePropsFile.isFile) {
                storeFile = file(releaseKeystoreProps.getProperty("storeFile"))
                storePassword = releaseKeystoreProps.getProperty("storePassword")
                keyAlias = releaseKeystoreProps.getProperty("keyAlias")
                keyPassword = releaseKeystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (releaseKeystorePropsFile.isFile) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    packaging {
        jniLibs {
            // 引擎 derived cache 的 cache_identity 会对解码器模块做
            // dladdr → canonicalize → 读文件哈希（engine/src/grib.rs::loaded_module_path）。
            // AGP 默认 useLegacyPackaging=false 时 .so 直接从 base.apk 内加载，
            // dladdr 返回 "base.apk!/lib/arm64-v8a/libengine_mobile.so" 这类虚拟路径，
            // canonicalize 报 ENOENT → forecast 两模型齐失败（MN2 实测 strace 定位）。
            // 置 true 让安装时把 .so 提取为 lib/arm64 下的实体文件（与 iOS bundle 同构）。
            useLegacyPackaging = true
        }
    }
}

// 真引擎接线挂到 preBuild：assembleDebug/connectedAndroidTest 前自动
// cargo → 链接 .so → 复制 definitions assets。
// FIRESKY_SKIP_NATIVE=1（CI 单元测试）跳过该链：无 NDK/交叉库环境跑
// Kotlin 编译与 Mock 链单元测试，不产出带真引擎的 APK。
if (System.getenv("FIRESKY_SKIP_NATIVE") != "1") {
    tasks.named("preBuild") {
        dependsOn(linkEngineJni, copyEccodesDefinitions)
    }
}

tasks.matching { it.name == "assembleRelease" }.configureEach {
    doFirst {
        if (!releaseKeystorePropsFile.isFile) {
            throw GradleException(
                "缺少 ~/.firesky-release/keystore.properties——release 签名需要仓库外 keystore（keytool 生成，见发布文档；口令永不入库）",
            )
        }
    }
}

dependencies {
    // Compose BOM（2024.06.00 → compose 1.6.8 / material3 1.2.1，均要求 compileSdk 34，与本工程对齐）
    val composeBom = platform("androidx.compose:compose-bom:2024.06.00")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.compose.material:material-icons-extended")

    debugImplementation("androidx.compose.ui:ui-tooling")

    // instrumented 全链测试（RealEngineChainTest，任务书 T1 §5.2/5.4）
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test:runner:1.5.2")
    androidTestImplementation("androidx.test:rules:1.5.0")

    // JVM 单测：mock 通道回归（org.json 在 unit test 需真实实现替换 android.jar 桩）
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
}
