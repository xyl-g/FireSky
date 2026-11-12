// FireSky Tauri 2 壳（M0/M1 骨架，docs/TAURI_DESKTOP_PLAN.md）。
// 职责：窗口、invoke 命令 → sidecar 帧桥、退出清理。UI 全部在 Svelte 前端。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod desktop;
mod sidecar;

use sidecar::EngineHandle;
use std::sync::Arc;
use tauri::{Manager, RunEvent};

/// 转发协议请求：method + params（对象）→ result 帧载荷 / error 帧（三码）。
/// 前端封装见 src/lib/ipc.ts（rpcRequest）。
#[tauri::command]
async fn rpc_request(
    state: tauri::State<'_, Arc<EngineHandle>>,
    method: String,
    params: serde_json::Value,
) -> Result<serde_json::Value, sidecar::RpcError> {
    state.request(method, params).await
}

/// 协议级取消帧（协作式，desktop_rpc.py:486-492）：返回 {requested}。
/// 前端封装见 src/lib/ipc.ts（rpcCancel）。
#[tauri::command]
async fn rpc_cancel(
    state: tauri::State<'_, Arc<EngineHandle>>,
    request_id: String,
) -> Result<serde_json::Value, sidecar::RpcError> {
    state.cancel(request_id).await
}

/// 壳侧 sidecar 进程状态（非协议帧）：running/workspace/engineBinary/spawnError。
#[tauri::command]
fn rpc_status(state: tauri::State<'_, Arc<EngineHandle>>) -> sidecar::EngineStatus {
    state.status()
}

/// Acknowledges registration and a flushed frame before cancellation can be sent.
#[tauri::command]
async fn rpc_start(
    state: tauri::State<'_, Arc<EngineHandle>>,
    request_id: String,
    method: String,
    params: serde_json::Value,
) -> Result<sidecar::RequestTicket, sidecar::RpcError> {
    let engine = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || engine.start(request_id, method, params))
        .await
        .map_err(|_| sidecar::RpcError::new("operation_failed", "IPC 注册线程异常退出。"))?
}

#[tauri::command]
async fn rpc_wait(
    state: tauri::State<'_, Arc<EngineHandle>>,
    request_id: String,
    generation: u64,
) -> Result<serde_json::Value, sidecar::RpcError> {
    state.wait(request_id, generation).await
}

#[tauri::command]
async fn rpc_restart(
    state: tauri::State<'_, Arc<EngineHandle>>,
) -> Result<sidecar::EngineStatus, sidecar::RpcError> {
    let engine = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || engine.restart())
        .await
        .map_err(|_| sidecar::RpcError::new("operation_failed", "IPC 重启线程异常退出。"))?
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .menu(desktop::menu)
        .on_menu_event(|app, event| {
            let action = event.id().as_ref();
            if matches!(
                action,
                "settings"
                    | "point"
                    | "regional"
                    | "nowcast"
                    | "data"
                    | "refresh"
                    | "cancel"
                    | "search"
                    | "quit"
            ) {
                desktop::emit_action(app, action);
            }
        })
        .setup(|app| {
            // 会话令牌属于长命 Rust desktop 进程，不能按请求拉起。
            // 启动失败不阻断窗口：侧栏徽标经 rpc_status 显示原因。
            let handle = app.handle().clone();
            let engine = Arc::new(EngineHandle::new(handle));
            if let Err(message) = engine.spawn() {
                eprintln!("[firesky] sidecar 启动失败：{message}");
            }
            app.manage(engine);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            rpc_request,
            rpc_start,
            rpc_wait,
            rpc_cancel,
            rpc_status,
            rpc_restart,
            desktop::desktop_open_report,
            desktop::desktop_open_cache,
            desktop::desktop_quit
        ])
        .build(tauri::generate_context!())
        .expect("FireSky Tauri 壳初始化失败")
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested {
                code: None, api, ..
            } => {
                // A destroyed last window cannot receive the confirmation event.
                if app_handle.get_webview_window("main").is_some() {
                    api.prevent_exit();
                    desktop::emit_action(app_handle, "quit");
                }
            }
            RunEvent::Exit => {
                app_handle
                    .state::<Arc<EngineHandle>>()
                    .shutdown(sidecar::SHUTDOWN_GRACE);
            }
            _ => {}
        });
}
