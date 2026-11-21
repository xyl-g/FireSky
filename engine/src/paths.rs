//! Data layout resolution and migration (phase B of the 2026-11-20 redesign).
//!
//! Two generations share one resolver:
//! - `Visible` (v1.7+): user-facing category directories at the workspace
//!   root (`gfs/`, `ecmwf/`, `cams/`, `srtm/`, `himawari-hsd/`, `locations.json`,
//!   `profiles/`, `map-frames/`, `point-forecasts/`, `regional-forecasts/`,
//!   `reports/`, `settings/`, `state/`), marked by `data-layout-v1.json`.
//! - `Legacy`: the pre-1.7 hidden layout (`.cache/…`, `.firesky/…`,
//!   `reports/web`), kept for read/write compatibility until the user migrates.
//!
//! Resolution order: an explicit `FIRESKY_*` environment variable always wins
//! (developer/operator choice — never migrated), then the detected generation
//! default. Mobile hosts pin their paths through those variables, so they are
//! unaffected by this module.
//!
//! Migration moves entries with same-volume renames (atomic per entry) and
//! rolls every completed move back if any later entry conflicts, so a failed
//! migration leaves the legacy layout fully intact.
use serde_json::{json, Value};
use std::{
    env, fs,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

pub const MARKER_FILE: &str = "data-layout-v1.json";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Generation {
    Visible,
    Legacy,
}

/// Detect the workspace generation: an explicit marker wins, then the
/// presence of legacy roots, then a brand-new workspace defaults to Visible.
pub fn generation(workspace: &Path) -> Generation {
    if workspace.join(MARKER_FILE).is_file() {
        return Generation::Visible;
    }
    if workspace.join(".cache").exists() || workspace.join(".firesky").exists() {
        return Generation::Legacy;
    }
    Generation::Visible
}

fn write_marker(workspace: &Path) -> Result<(), String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|v| v.as_secs())
        .unwrap_or(0);
    let marker = json!({
        "contract": "firesky-data-layout-v1",
        "visible": true,
        "migrated_at_unix": now,
    });
    let path = workspace.join(MARKER_FILE);
    if path.exists() {
        return Ok(());
    }
    serde_json::to_writer(fs::File::create(&path).map_err(|e| e.to_string())?, &marker)
        .map_err(|e| e.to_string())
}

/// Resolve one path: explicit environment variable first, then the
/// generation-specific default (relative paths join the workspace).
pub fn resolve(workspace: &Path, env_key: &str, legacy: &str, visible: &str) -> PathBuf {
    if let Some(value) = env::var_os(env_key) {
        let value = PathBuf::from(value);
        return if value.is_absolute() {
            value
        } else {
            workspace.join(value)
        };
    }
    let default = match generation(workspace) {
        Generation::Visible => visible,
        Generation::Legacy => legacy,
    };
    workspace.join(default)
}

// ---------------------------------------------------------------- 迁移

struct Move {
    /// 迁移来源（相对 workspace）。
    from: &'static str,
    /// 迁移目标（相对 workspace）；目标的父目录会按需创建。
    to: &'static str,
}

const MIGRATION_MOVES: &[Move] = &[
    Move { from: ".cache/gfs", to: "gfs" },
    Move { from: ".cache/ecmwf", to: "ecmwf" },
    Move { from: ".cache/cams", to: "cams" },
    Move { from: ".cache/himawari-hsd", to: "himawari-hsd" },
    Move { from: ".cache/srtm", to: "srtm" },
    Move { from: ".cache/locations.json", to: "locations.json" },
    Move { from: ".cache/profiles", to: "profiles" },
    Move { from: ".cache/map-frames", to: "map-frames" },
    Move { from: ".cache/point-forecasts", to: "point-forecasts" },
    Move { from: ".cache/regional-forecasts", to: "regional-forecasts" },
    // 报告：reports/web → reports（web 目录语义不变，内容上移一层）。
    Move { from: "reports/web", to: "reports" },
    Move { from: ".firesky/state/tasks", to: "state/tasks" },
    Move { from: ".firesky/state/data-sync.json", to: "state/data-sync.json" },
    Move { from: ".firesky/settings", to: "settings" },
];

/// 目录树大小（迁移预览用；跳过符号链接）。
fn tree_bytes(path: &Path) -> u64 {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(_) => return 0,
    };
    if metadata.file_type().is_symlink() {
        return 0;
    }
    if metadata.is_file() {
        return metadata.len();
    }
    if !metadata.is_dir() {
        return 0;
    }
    let mut bytes = 0u64;
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.flatten() {
            bytes = bytes.saturating_add(tree_bytes(&entry.path()));
        }
    }
    bytes
}

/// 目录的直接子项（不含符号链接解析），供父目录壳特例判断。
fn dir_contents(path: &Path) -> Vec<PathBuf> {
    fs::read_dir(path)
        .map(|entries| entries.flatten().map(|entry| entry.path()).collect())
        .unwrap_or_default()
}

fn count_entries(path: &Path) -> usize {
    if !path.is_dir() {
        return 0;
    }
    let mut count = 0usize;
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.flatten() {
            count += 1;
            if entry.file_type().map(|v| v.is_dir()).unwrap_or(false) {
                count += count_entries(&entry.path());
            }
        }
    }
    count
}

/// 迁移状态预览：布局代际 + 每个映射项的来源/目标/是否存在/规模。
pub fn status(workspace: &Path) -> Value {
    let generation = generation(workspace);
    let items: Vec<Value> = MIGRATION_MOVES
        .iter()
        .map(|item| {
            let from = workspace.join(item.from);
            let exists = from.exists();
            json!({
                "from": item.from,
                "to": item.to,
                "exists": exists,
                "file_count": if exists { count_entries(&from) } else { 0 },
                "bytes": if exists { tree_bytes(&from) } else { 0 },
            })
        })
        .collect();
    let overrides: Vec<&str> = [
        "FIRESKY_GFS_CACHE_DIR",
        "FIRESKY_ECMWF_CACHE_DIR",
        "FIRESKY_CAMS_CACHE_DIR",
        "FIRESKY_HSD_CACHE_DIR",
        "FIRESKY_SRTM_CACHE_DIR",
        "FIRESKY_LOCATIONS_PATH",
        "FIRESKY_REPORT_DIR",
        "FIRESKY_MAP_CACHE_DIR",
        "FIRESKY_POINT_CACHE_DIR",
        "FIRESKY_REGIONAL_CACHE_DIR",
        "FIRESKY_TASK_HISTORY_DIR",
        "FIRESKY_PROFILES_DIR",
        "FIRESKY_SETTINGS_DIR",
        "FIRESKY_STATE_DIR",
    ]
    .iter()
    .filter(|key| env::var_os(key).is_some())
    .copied()
    .collect();
    json!({
        "contract": "firesky-data-layout-v1",
        "generation": match generation {
            Generation::Visible => "visible",
            Generation::Legacy => "legacy",
        },
        "marker": workspace.join(MARKER_FILE).display().to_string(),
        "env_overrides": overrides,
        "items": items,
    })
}

/// 执行迁移：同卷 rename 逐项移动，任何冲突立即整单回滚（已完成的移动
/// 按逆序移回），失败时旧布局保持完整可用。迁移成功写入可见布局标记；
/// `.cache`/`.firesky` 残壳不删除（确认后的清理交给用户，方案 8.2-7）。
pub fn migrate(workspace: &Path) -> Result<Value, String> {
    if generation(workspace) == Generation::Visible {
        return Ok(json!({
            "contract": "firesky-data-layout-migrate-v1",
            "status": "already-visible",
            "migrated": [], "skipped": [], "failed": [],
        }));
    }
    let mut completed: Vec<(&Move, PathBuf)> = Vec::new();
    let mut migrated = Vec::new();
    let mut skipped = Vec::new();
    let mut failure: Option<String> = None;
    for item in MIGRATION_MOVES {
        let from = workspace.join(item.from);
        let to = workspace.join(item.to);
        if !from.exists() {
            skipped.push(json!({"from": item.from, "to": item.to, "reason": "source missing"}));
            continue;
        }
        // 特例：reports/web → reports（父目录壳）。reports/ 若仅含 web/（历史
        // 布局的必然形态），三步上移：web→临时、删壳、临时→reports。
        if item.to == "reports"
            && from.parent() == Some(&to)
            && to.is_dir()
            && dir_contents(&to).len() == 1
            && dir_contents(&to)[0] == from
        {
            let staged = to.parent().unwrap().join("reports-migrating-tmp");
            let mut step = || -> Result<(), String> {
                fs::rename(&from, &staged).map_err(|e| e.to_string())?;
                fs::remove_dir_all(&to).map_err(|e| e.to_string())?;
                fs::rename(&staged, &to).map_err(|e| e.to_string())
            };
            match step() {
                Ok(()) => {
                    completed.push((item, to.clone()));
                    migrated.push(json!({
                        "from": item.from, "to": item.to,
                        "bytes": tree_bytes(&to),
                    }));
                    continue;
                }
                Err(error) => {
                    failure = Some(format!("{}: {error}", item.from));
                    break;
                }
            }
        }
        if to.exists() {
            // 目标已存在：非空视为冲突（不静默合并）；空目录先移除再收。
            let empty = to.is_dir()
                && fs::read_dir(&to)
                    .map(|mut d| d.next().is_none())
                    .unwrap_or(false);
            if !empty {
                failure = Some(format!("{} already exists", item.to));
                break;
            }
            if let Err(error) = fs::remove_dir(&to) {
                failure = Some(format!("{}: {error}", item.to));
                break;
            }
        }
        if let Some(parent) = to.parent() {
            if let Err(error) = fs::create_dir_all(parent) {
                failure = Some(format!("{}: {error}", parent.display()));
                break;
            }
        }
        match fs::rename(&from, &to) {
            Ok(()) => {
                completed.push((item, to.clone()));
                migrated.push(json!({
                    "from": item.from, "to": item.to,
                    "bytes": tree_bytes(&to),
                }));
            }
            Err(error) => {
                failure = Some(format!("{}: {error}", item.from));
                break;
            }
        }
    }
    if let Some(error) = failure {
        // 逆序回滚：迁移中途失败的每一项移回原位，旧布局保持完整。
        for (item, to) in completed.iter().rev() {
            let from = workspace.join(item.from);
            if let Some(parent) = from.parent() {
                let _ = fs::create_dir_all(parent);
            }
            if let Err(rollback) = fs::rename(to, &from) {
                return Err(format!(
                    "migration failed ({error}) AND rollback failed ({rollback}); \
                     inspect the workspace manually"
                ));
            }
        }
        return Err(format!("migration rolled back: {error}"));
    }
    write_marker(workspace)?;
    Ok(json!({
        "contract": "firesky-data-layout-migrate-v1",
        "status": "migrated",
        "marker": workspace.join(MARKER_FILE).display().to_string(),
        "migrated": migrated,
        "skipped": skipped,
        "failed": [],
        "residual_note": ".cache/.firesky 残壳保留在原处，可自行确认后删除",
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn remove_env(key: &str) {
        // 测试进程内不遗留跨用例的覆盖变量。
        unsafe { env::remove_var(key) };
    }

    #[test]
    fn generation_detects_marker_legacy_and_fresh() {
        let dir = tempfile::tempdir().unwrap();
        // 全新工作区 → Visible（新装直接用可见布局）。
        assert_eq!(generation(dir.path()), Generation::Visible);
        // 旧布局存在 → Legacy。
        fs::create_dir_all(dir.path().join(".cache/gfs")).unwrap();
        assert_eq!(generation(dir.path()), Generation::Legacy);
        // 标记写入后 → Visible（残壳不再影响判定）。
        write_marker(dir.path()).unwrap();
        assert_eq!(generation(dir.path()), Generation::Visible);
        assert!(dir.path().join(".cache/gfs").exists());
    }

    #[test]
    fn resolve_prefers_explicit_env_then_generation() {
        let dir = tempfile::tempdir().unwrap();
        remove_env("FIRESKY_TEST_PATH");
        // 全新工作区 → Visible 默认。
        assert_eq!(
            resolve(dir.path(), "FIRESKY_TEST_PATH", ".cache/gfs", "gfs"),
            dir.path().join("gfs")
        );
        // 显式 env（绝对路径）永远优先。
        env::set_var("FIRESKY_TEST_PATH", "/tmp/override");
        assert_eq!(
            resolve(dir.path(), "FIRESKY_TEST_PATH", ".cache/gfs", "gfs"),
            PathBuf::from("/tmp/override")
        );
        // 相对 env 相对 workspace 展开。
        env::set_var("FIRESKY_TEST_PATH", "custom");
        assert_eq!(
            resolve(dir.path(), "FIRESKY_TEST_PATH", ".cache/gfs", "gfs"),
            dir.path().join("custom")
        );
        remove_env("FIRESKY_TEST_PATH");
        // Legacy 工作区 → 旧默认。
        fs::create_dir_all(dir.path().join(".cache")).unwrap();
        assert_eq!(
            resolve(dir.path(), "FIRESKY_TEST_PATH", ".cache/gfs", "gfs"),
            dir.path().join(".cache/gfs")
        );
    }

    #[test]
    fn migrate_moves_entries_and_writes_marker() {
        let dir = tempfile::tempdir().unwrap();
        let workspace = dir.path();
        fs::create_dir_all(workspace.join(".cache/gfs")).unwrap();
        fs::write(workspace.join(".cache/gfs/2026111100.grib2"), b"data").unwrap();
        fs::write(workspace.join(".cache/locations.json"), b"{}").unwrap();
        fs::create_dir_all(workspace.join("reports/web")).unwrap();
        fs::write(workspace.join("reports/web/result.json"), b"{}").unwrap();
        fs::create_dir_all(workspace.join(".firesky/state/tasks")).unwrap();
        fs::write(workspace.join(".firesky/state/data-sync.json"), b"{}").unwrap();
        let result = migrate(workspace).unwrap();
        assert_eq!(result["status"], "migrated");
        assert!(workspace.join("gfs/2026111100.grib2").is_file());
        assert!(workspace.join("locations.json").is_file());
        assert!(workspace.join("reports/result.json").is_file());
        assert!(workspace.join("state/tasks").is_dir());
        assert!(workspace.join("state/data-sync.json").is_file());
        assert!(workspace.join(MARKER_FILE).is_file());
        // 残壳保留（确认后的清理交给用户）。
        assert!(workspace.join(".cache").is_dir());
        assert!(workspace.join(".firesky").is_dir());
        // 迁移后再次 migrate → already-visible。
        assert_eq!(migrate(workspace).unwrap()["status"], "already-visible");
    }

    #[test]
    fn migrate_rolls_back_on_conflict_and_keeps_legacy_usable() {
        let dir = tempfile::tempdir().unwrap();
        let workspace = dir.path();
        fs::create_dir_all(workspace.join(".cache/gfs")).unwrap();
        fs::write(workspace.join(".cache/gfs/a.grib2"), b"a").unwrap();
        fs::create_dir_all(workspace.join(".cache/srtm")).unwrap();
        fs::write(workspace.join(".cache/srtm/N30E120.hgt"), b"s").unwrap();
        // 冲突目标：非空 srtm/ 会让第二项迁移失败。
        fs::create_dir_all(workspace.join("srtm")).unwrap();
        fs::write(workspace.join("srtm/existing.hgt"), b"x").unwrap();
        let error = migrate(workspace).unwrap_err();
        assert!(error.contains("rolled back"), "{error}");
        // 回滚后旧布局完整可用（gfs 已移回），且没有写入标记。
        assert!(workspace.join(".cache/gfs/a.grib2").is_file());
        assert!(workspace.join(".cache/srtm/N30E120.hgt").is_file());
        assert!(!workspace.join(MARKER_FILE).exists());
        // 清掉冲突后迁移成功。
        fs::remove_dir_all(workspace.join("srtm")).unwrap();
        let result = migrate(workspace).unwrap();
        assert_eq!(result["status"], "migrated");
        assert!(workspace.join("srtm/N30E120.hgt").is_file());
    }

    #[test]
    fn status_reports_generation_items_and_overrides() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir_all(dir.path().join(".cache/gfs")).unwrap();
        fs::write(dir.path().join(".cache/gfs/a.grib2"), b"a").unwrap();
        remove_env("FIRESKY_GFS_CACHE_DIR");
        let value = status(dir.path());
        assert_eq!(value["generation"], "legacy");
        let items = value["items"].as_array().unwrap();
        let gfs = items
            .iter()
            .find(|item| item["from"] == ".cache/gfs")
            .unwrap();
        assert_eq!(gfs["exists"], true);
        assert_eq!(gfs["file_count"], 1);
        assert_eq!(gfs["bytes"], 1);
        // env 覆盖出现在状态里（迁移拒绝被 env 锁定的路径）。
        env::set_var("FIRESKY_GFS_CACHE_DIR", "/tmp/locked");
        let value = status(dir.path());
        assert!(value["env_overrides"]
            .as_array()
            .unwrap()
            .iter()
            .any(|v| v == "FIRESKY_GFS_CACHE_DIR"));
        remove_env("FIRESKY_GFS_CACHE_DIR");
    }
}

/// 数据管理页的类目扫描（v1.9.2，方案 §5.1 完整表格）：模型源之外的四类
/// 数据（卫星/地形/报告/地图帧）与两类结果缓存（单点/区域）。只读统计，
/// 删除走 desktop_delete 的类目 scope。
pub fn inventory_categories(workspace: &Path, cancel: &AtomicBool) -> Result<Value, String> {
    use std::sync::atomic::Ordering;
    fn check(cancel: &AtomicBool) -> Result<(), String> {
        if cancel.load(Ordering::Acquire) {
            Err("inventory operation cancelled".into())
        } else {
            Ok(())
        }
    }
    fn scan(path: &Path, cancel: &AtomicBool) -> Result<(usize, u64), String> {
        check(cancel)?;
        let metadata = match fs::symlink_metadata(path) {
            Ok(metadata) => metadata,
            Err(_) => return Ok((0, 0)),
        };
        if metadata.is_file() {
            return Ok((1, metadata.len()));
        }
        if !metadata.is_dir() {
            return Ok((0, 0));
        }
        let mut files = 0usize;
        let mut bytes = 0u64;
        let mut pending = vec![path.to_owned()];
        while let Some(directory) = pending.pop() {
            check(cancel)?;
            for entry in fs::read_dir(&directory).map_err(|e| e.to_string())? {
                let entry = entry.map_err(|e| e.to_string())?;
                let metadata = entry.metadata().map_err(|e| e.to_string())?;
                if metadata.is_dir() {
                    pending.push(entry.path());
                } else if metadata.is_file() {
                    files += 1;
                    bytes = bytes.saturating_add(metadata.len());
                }
            }
            if files > 1_000_000 {
                return Err("inventory category exceeds one million files".into());
            }
        }
        Ok((files, bytes))
    }
    let categories: Vec<(&str, PathBuf)> = vec![
        ("satellite", resolve(workspace, "FIRESKY_HSD_CACHE_DIR", ".cache/himawari-hsd", "himawari-hsd")),
        ("srtm", resolve(workspace, "FIRESKY_SRTM_CACHE_DIR", ".cache/srtm", "srtm")),
        ("reports", resolve(workspace, "FIRESKY_REPORT_DIR", "reports/web", "reports")),
        ("map-frames", resolve(workspace, "FIRESKY_MAP_CACHE_DIR", ".cache/map-frames", "map-frames")),
        ("point-forecasts", resolve(workspace, "FIRESKY_POINT_CACHE_DIR", ".cache/point-forecasts", "point-forecasts")),
        ("regional-forecasts", resolve(workspace, "FIRESKY_REGIONAL_CACHE_DIR", ".cache/regional-forecasts", "regional-forecasts")),
    ];
    let mut rows = Vec::new();
    for (category, path) in categories {
        let (file_count, bytes) = scan(&path, cancel)?;
        rows.push(json!({
            "category": category,
            "path": path.display().to_string(),
            "file_count": file_count,
            "bytes": bytes,
        }));
    }
    Ok(json!({"contract": "firesky-data-categories-v1", "categories": rows}))
}
