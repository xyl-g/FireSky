// 仓库根 Gradle 门禁入口（2026-11-12）：仓库根没有 Android 构建子树之外的项目，
// 此处把 :app 指向 apps/mobile-android/app，使仓库根目录的 gradlew 调用
// （门禁脚本以 worktree 根为 cwd 运行 wrapper）能定位到 Android 构建。
// 插件版本必须与 apps/mobile-android/build.gradle.kts 保持一致；正式构建入口仍在
// apps/mobile-android（含独立 settings.gradle.kts，cwd 在其内时优先生效）。
pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "firesky"
include(":app")
project(":app").projectDir = file("apps/mobile-android/app")
