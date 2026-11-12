fn main() {
    // tauri-build：校验 tauri.conf.json（schema）、capabilities、
    // frontendDist（../dist，占位 index.html 保证首检通过），并注入平台资源。
    tauri_build::build()
}
