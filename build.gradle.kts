// 仓库根 Gradle 门禁入口——插件版本与 apps/mobile-android/build.gradle.kts 保持一致。
// 仅承载 :app（projectDir = apps/mobile-android/app）的插件解析；无其他项目。
plugins {
    id("com.android.application") version "8.4.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.20" apply false
}
