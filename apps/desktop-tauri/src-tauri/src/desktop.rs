use crate::sidecar::{EngineHandle, RpcError};
use serde::Deserialize;
use serde_json::json;
use std::{
    ffi::OsString,
    path::{Component, Path, PathBuf},
    process::Command,
    sync::Arc,
};
use tauri::{
    menu::{AboutMetadata, Menu, MenuBuilder, MenuItem, SubmenuBuilder},
    AppHandle, Emitter,
};

const MAX_REPORT_BYTES: u64 = 16 * 1024 * 1024;

pub fn emit_action(app: &AppHandle, action: &str) {
    let _ = app.emit("desktop-action", json!({ "action": action }));
}

pub fn menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let action = |id: &str, label: &str, accelerator: &str| {
        MenuItem::with_id(app, id, label, true, Some(accelerator))
    };
    let settings = action("settings", "设置…", "CmdOrCtrl+,")?;
    let quit = action("quit", "退出 FireSky", "CmdOrCtrl+Q")?;
    let app_menu = SubmenuBuilder::new(app, "FireSky")
        .about_with_text(
            "关于 FireSky",
            Some(AboutMetadata {
                name: Some("FireSky".into()),
                version: Some(app.package_info().version.to_string()),
                ..Default::default()
            }),
        )
        .separator()
        .item(&settings);
    #[cfg(target_os = "macos")]
    let app_menu = app_menu
        .separator()
        .services()
        .separator()
        .hide_with_text("隐藏 FireSky")
        .hide_others_with_text("隐藏其他")
        .show_all_with_text("全部显示");
    let app_menu = app_menu.separator().item(&quit).build()?;
    let edit = SubmenuBuilder::new(app, "编辑")
        .undo_with_text("撤销")
        .redo_with_text("重做")
        .separator()
        .cut_with_text("剪切")
        .copy_with_text("拷贝")
        .paste_with_text("粘贴")
        .select_all_with_text("全选")
        .build()?;
    let view = SubmenuBuilder::new(app, "视图")
        .item(&action("point", "单点预测", "CmdOrCtrl+1")?)
        .item(&action("regional", "区域预测", "CmdOrCtrl+2")?)
        .item(&action("nowcast", "实况观测", "CmdOrCtrl+3")?)
        .item(&action("data", "数据与下载", "CmdOrCtrl+4")?)
        .separator()
        .item(&action("refresh", "刷新", "F5")?)
        .item(&action("search", "搜索地点", "CmdOrCtrl+K")?)
        .build()?;
    let task = SubmenuBuilder::new(app, "任务")
        .item(&action("cancel", "取消当前任务", "Escape")?)
        .build()?;
    let window = SubmenuBuilder::new(app, "窗口")
        .minimize_with_text("最小化")
        .maximize_with_text("缩放")
        .separator()
        .close_window_with_text("关闭窗口")
        .build()?;
    MenuBuilder::new(app)
        .items(&[&app_menu, &edit, &view, &task, &window])
        .build()
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CacheSource {
    Gfs,
    Ecmwf,
    Cams,
    Satellite,
    Map,
    Point,
    Regional,
}

fn configured_path(
    workspace: &Path,
    key: &str,
    fallback: &str,
    environment: impl Fn(&str) -> Option<OsString>,
) -> PathBuf {
    let path = environment(key)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(fallback));
    if path.is_absolute() {
        path
    } else {
        workspace.join(path)
    }
}

fn cache_path(
    workspace: &Path,
    source: CacheSource,
    environment: impl Fn(&str) -> Option<OsString>,
) -> PathBuf {
    let (key, fallback) = match source {
        CacheSource::Gfs => ("FIRESKY_GFS_CACHE_DIR", ".cache/gfs"),
        CacheSource::Ecmwf => ("FIRESKY_ECMWF_CACHE_DIR", ".cache/ecmwf"),
        CacheSource::Cams => ("FIRESKY_CAMS_CACHE_DIR", ".cache/cams"),
        CacheSource::Satellite => {
            let key = if environment("FIRESKY_HSD_CACHE_DIR").is_some() {
                "FIRESKY_HSD_CACHE_DIR"
            } else {
                "FIRESKY_SATELLITE_CACHE_DIR"
            };
            (key, ".cache/himawari-hsd")
        }
        CacheSource::Map => ("FIRESKY_MAP_CACHE_DIR", ".cache/map-frames"),
        CacheSource::Point => ("FIRESKY_POINT_CACHE_DIR", ".cache/point-forecasts"),
        CacheSource::Regional => ("FIRESKY_REGIONAL_CACHE_DIR", ".cache/regional-forecasts"),
    };
    configured_path(workspace, key, fallback, environment)
}

fn report_root(workspace: &Path) -> PathBuf {
    configured_path(workspace, "FIRESKY_REPORT_DIR", "reports/web", |key| {
        std::env::var_os(key)
    })
    .join("native-html")
}

fn validate_report(root: &Path, requested: &Path) -> Result<PathBuf, String> {
    if !requested.is_absolute()
        || requested
            .components()
            .any(|part| matches!(part, Component::ParentDir))
        || requested.parent() != Some(root)
    {
        return Err("只能打开当前引擎生成的本地报告。".into());
    }
    let name = requested
        .file_name()
        .and_then(|name| name.to_str())
        .and_then(|name| name.strip_suffix(".html"))
        .ok_or("报告文件名无效。")?;
    if name.len() != 64
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("报告文件名无效。".into());
    }
    let directory = std::fs::symlink_metadata(root).map_err(|_| "报告目录不存在。")?;
    if !directory.is_dir() || directory.file_type().is_symlink() {
        return Err("报告目录无效。".into());
    }
    let metadata = std::fs::symlink_metadata(requested).map_err(|_| "报告文件不存在。")?;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > MAX_REPORT_BYTES
    {
        return Err("报告文件无效。".into());
    }
    let canonical_root = root.canonicalize().map_err(|_| "无法读取报告目录。")?;
    let canonical = requested.canonicalize().map_err(|_| "无法读取报告文件。")?;
    if canonical.parent() != Some(canonical_root.as_path()) {
        return Err("报告文件超出当前引擎目录。".into());
    }
    Ok(canonical)
}

fn validate_cache(path: &Path) -> Result<PathBuf, String> {
    let path = path.canonicalize().map_err(|_| "缓存目录尚不存在。")?;
    if !path.is_dir() {
        return Err("缓存路径不是目录。".into());
    }
    Ok(path)
}

fn workspace(engine: &EngineHandle) -> Result<PathBuf, RpcError> {
    engine
        .status()
        .workspace
        .map(PathBuf::from)
        .ok_or_else(|| RpcError::new("engine_unavailable", "引擎数据目录不可用，请先连接引擎。"))
}

fn open_path(path: &Path) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let mut command = Command::new("/usr/bin/open");
    #[cfg(target_os = "windows")]
    let mut command = Command::new(
        std::env::var_os("SystemRoot")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(r"C:\Windows"))
            .join("explorer.exe"),
    );
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    return Err("当前平台不支持打开本地文件。".into());
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    {
        // The validated absolute path is one argument, never shell text or a URL.
        let mut child = command
            .arg(path)
            .spawn()
            .map_err(|_| "无法调用系统打开本地文件。".to_string())?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        Ok(())
    }
}

#[tauri::command]
pub async fn desktop_open_report(
    state: tauri::State<'_, Arc<EngineHandle>>,
    path: String,
) -> Result<(), RpcError> {
    let root = report_root(&workspace(&state)?);
    tauri::async_runtime::spawn_blocking(move || {
        validate_report(&root, Path::new(&path)).and_then(|path| open_path(&path))
    })
    .await
    .map_err(|_| RpcError::new("operation_failed", "打开报告任务异常退出。"))?
    .map_err(|message| RpcError::new("operation_failed", message))
}

#[tauri::command]
pub async fn desktop_open_cache(
    state: tauri::State<'_, Arc<EngineHandle>>,
    source: CacheSource,
) -> Result<(), RpcError> {
    let path = cache_path(&workspace(&state)?, source, |key| std::env::var_os(key));
    tauri::async_runtime::spawn_blocking(move || {
        validate_cache(&path).and_then(|path| open_path(&path))
    })
    .await
    .map_err(|_| RpcError::new("operation_failed", "打开缓存任务异常退出。"))?
    .map_err(|message| RpcError::new("operation_failed", message))
}

#[tauri::command]
pub fn desktop_quit(app: AppHandle) {
    app.exit(0);
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Scratch(PathBuf);
    impl Scratch {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!("firesky-paths-{}", uuid::Uuid::new_v4()));
            std::fs::create_dir(&path).unwrap();
            Self(path.canonicalize().unwrap())
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    fn report(scratch: &Scratch) -> (PathBuf, PathBuf) {
        let root = scratch.0.join("native-html");
        std::fs::create_dir(&root).unwrap();
        let path = root.join(format!("{}.html", "a".repeat(64)));
        std::fs::write(&path, b"<html>fixture</html>").unwrap();
        (root, path)
    }

    #[test]
    fn permits_only_existing_generated_report_inside_root() {
        let scratch = Scratch::new();
        let (root, path) = report(&scratch);
        assert_eq!(validate_report(&root, &path).unwrap(), path);
        assert!(validate_report(&root, Path::new("https://example.com/report.html")).is_err());
        assert!(validate_report(&root, Path::new("file:///tmp/report.html")).is_err());
        assert!(validate_report(&root, Path::new("relative.html")).is_err());
        assert!(validate_report(&root, &root.join("index.html")).is_err());
        assert!(validate_report(&root, &root.join(format!("{}.html", "b".repeat(64)))).is_err());
        assert!(validate_report(&root, &scratch.0.join(path.file_name().unwrap())).is_err());
        let traversal = root
            .join("..")
            .join("native-html")
            .join(path.file_name().unwrap());
        assert!(validate_report(&root, &traversal).is_err());
    }

    #[test]
    fn refuses_report_directories_and_oversized_files() {
        let scratch = Scratch::new();
        let (root, path) = report(&scratch);
        std::fs::remove_file(&path).unwrap();
        std::fs::create_dir(&path).unwrap();
        assert!(validate_report(&root, &path).is_err());
        std::fs::remove_dir(&path).unwrap();
        let file = std::fs::File::create(&path).unwrap();
        file.set_len(MAX_REPORT_BYTES + 1).unwrap();
        assert!(validate_report(&root, &path).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn refuses_report_symlink_files_and_roots() {
        let scratch = Scratch::new();
        let (root, path) = report(&scratch);
        let external = scratch.0.join("external.html");
        std::fs::rename(&path, &external).unwrap();
        std::os::unix::fs::symlink(&external, &path).unwrap();
        assert!(validate_report(&root, &path).is_err());
        std::fs::remove_file(&path).unwrap();
        std::fs::remove_dir(&root).unwrap();
        let external_root = scratch.0.join("external-root");
        std::fs::create_dir(&external_root).unwrap();
        std::os::unix::fs::symlink(&external_root, &root).unwrap();
        assert!(validate_report(&root, &path).is_err());
    }

    #[test]
    fn cache_sources_match_runtime_environment_and_default_paths() {
        let scratch = Scratch::new();
        let unset = |_: &str| None;
        assert_eq!(
            cache_path(&scratch.0, CacheSource::Gfs, unset),
            scratch.0.join(".cache/gfs")
        );
        assert_eq!(
            cache_path(&scratch.0, CacheSource::Satellite, unset),
            scratch.0.join(".cache/himawari-hsd")
        );
        let override_path = scratch.0.join("external-cache");
        let environment = |key: &str| {
            (key == "FIRESKY_ECMWF_CACHE_DIR").then(|| override_path.clone().into_os_string())
        };
        assert_eq!(
            cache_path(&scratch.0, CacheSource::Ecmwf, environment),
            override_path
        );
        let environment = |key: &str| {
            (key == "FIRESKY_HSD_CACHE_DIR").then(|| OsString::from("relative-satellite"))
        };
        assert_eq!(
            cache_path(&scratch.0, CacheSource::Satellite, environment),
            scratch.0.join("relative-satellite")
        );
        assert!(validate_cache(&scratch.0.join("not-created")).is_err());
        assert_eq!(validate_cache(&scratch.0).unwrap(), scratch.0);
        assert!(serde_json::from_str::<CacheSource>(r#""../private""#).is_err());
    }
}
