import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
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
        versionCode = 25
        versionName = "1.10.1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
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
    androidTestImplementation(composeBom)

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
    debugImplementation("androidx.compose.ui:ui-test-manifest")

    // instrumented UI 测试（Compose UI Test）
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test:runner:1.5.2")
    androidTestImplementation("androidx.test:rules:1.5.0")

    // JVM 单测：mock 与远程 HTTP/SSE 通道回归（org.json 在 unit test 需真实实现替换 android.jar 桩）
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
}
