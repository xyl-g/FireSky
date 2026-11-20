//! Manual desktop data deletion (phase C of the 2026-11-20 redesign).
//!
//! Deletion is scope-based: the UI references cycle keys exactly as the
//! inventory displays them and never passes disk paths. Preview freezes the
//! candidate set (path + size + mtime identity hash); execute re-derives the
//! set and refuses to run when the identity no longer matches, so a changed
//! cache can never be deleted against a stale confirmation and the delete
//! scope can never silently grow. Files with live download locks
//! (flock-verified, shared with foreground compute / downloads / the sync
//! helper) are skipped instead of deleted.
use crate::{cache_retention, desktop_inventory};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Component, Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    time::UNIX_EPOCH,
};

const MAX_CANDIDATES: usize = 1_000_000;

#[derive(Clone, Debug)]
struct Scope {
    source: String,
    /// Inventory cycle key; `None` selects the whole source.
    cycle: Option<String>,
}

fn scope(params: &Value) -> Result<Scope, String> {
    let scope = params
        .get("scope")
        .ok_or("data delete requires a scope object")?;
    let source = scope["source"]
        .as_str()
        .filter(|v| matches!(*v, "gfs" | "ecmwf" | "cams"))
        .ok_or("scope source must be gfs, ecmwf or cams")?;
    let cycle = match scope.get("cycle") {
        None | Some(Value::Null) => None,
        Some(value) => {
            let raw = value.as_str().filter(|v| !v.is_empty() && v.len() <= 64);
            if raw.is_none() {
                return Err("scope cycle must be the inventory cycle key".into());
            }
            Some(raw.unwrap().to_owned())
        }
    };
    Ok(Scope {
        source: source.into(),
        cycle,
    })
}

fn root_for(workspace: &Path, source: &str) -> Result<PathBuf, String> {
    let (key, default) = match source {
        "gfs" => ("FIRESKY_GFS_CACHE_DIR", ".cache/gfs"),
        "ecmwf" => ("FIRESKY_ECMWF_CACHE_DIR", ".cache/ecmwf"),
        "cams" => ("FIRESKY_CAMS_CACHE_DIR", ".cache/cams"),
        _ => return Err("unknown data source".into()),
    };
    let value = std::env::var_os(key)
        .map(PathBuf::from)
        .unwrap_or_else(|| default.into());
    Ok(if value.is_absolute() {
        value
    } else {
        workspace.join(value)
    })
}

fn check(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("data delete cancelled".into())
    } else {
        Ok(())
    }
}

/// Relative path inside the cache root; also the identity basis for object IDs.
fn relative_path(root: &Path, path: &Path) -> Result<String, String> {
    let relative = path
        .strip_prefix(root)
        .map_err(|_| "candidate escaped the cache root".to_string())?;
    for component in relative.components() {
        if matches!(component, Component::ParentDir | Component::CurDir) {
            return Err("candidate path is not normalized".into());
        }
    }
    relative
        .to_str()
        .map(str::to_owned)
        .ok_or("candidate path is not UTF-8".into())
}

fn object_id(relative: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(relative.as_bytes());
    let digest = hasher.finalize();
    digest.iter().map(|b| format!("{b:02x}")).collect::<String>()
}

struct Candidate {
    path: PathBuf,
    relative: String,
    bytes: u64,
    mtime_ns: Option<u128>,
    sidecars: Vec<(String, PathBuf, u64, Option<u128>)>,
}

/// Derive the candidate set from the engine's own inventory scan: GRIB files
/// grouped by cycle key, each with its index/metadata sidecars. Lock and
/// `.part` files are protection markers and are never candidates.
fn candidates(
    workspace: &Path,
    scope: &Scope,
    cancel: &AtomicBool,
) -> Result<Vec<Candidate>, String> {
    let root = root_for(workspace, &scope.source)?;
    if !root.is_dir() {
        return Ok(Vec::new());
    }
    let groups = desktop_inventory::cycle_files(&scope.source, &root, cancel)?;
    let gribs: Vec<PathBuf> = match &scope.cycle {
        None => groups.into_values().flatten().collect(),
        Some(key) => {
            let paths = groups
                .get(key)
                .ok_or("requested cycle is not present in the inventory for this source")?;
            paths.clone()
        }
    };
    let all = cache_retention::walk(&root)?;
    let mut out = Vec::new();
    for grib in gribs {
        check(cancel)?;
        let Ok(metadata) = fs::symlink_metadata(&grib) else {
            continue;
        };
        if !metadata.is_file() {
            continue;
        }
        let rel = relative_path(&root, &grib)?;
        let mut sidecars = Vec::new();
        for path in cache_retention::related_files(&grib, &scope.source, &all) {
            if path == grib {
                continue;
            }
            if !matches!(
                path.extension().and_then(|v| v.to_str()),
                Some("json" | "idx")
            ) {
                continue;
            }
            let Ok(sidecar) = fs::symlink_metadata(&path) else {
                continue;
            };
            if !sidecar.is_file() {
                continue;
            }
            sidecars.push((
                relative_path(&root, &path)?,
                path,
                sidecar.len(),
                sidecar
                    .modified()
                    .ok()
                    .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                    .map(|v| v.as_nanos()),
            ));
        }
        out.push(Candidate {
            path: grib,
            relative: rel,
            bytes: metadata.len(),
            mtime_ns: metadata
                .modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|v| v.as_nanos()),
            sidecars,
        });
        if out.len() > MAX_CANDIDATES {
            return Err("delete candidate set exceeds one million files".into());
        }
    }
    out.sort_by(|a, b| a.relative.cmp(&b.relative));
    Ok(out)
}

/// Identity of the frozen candidate set: every file's relative path, size and
/// mtime. Execute recomputes it and rejects a changed set.
fn identity(scope: &Scope, candidates: &[Candidate]) -> String {
    let mut hasher = Sha256::new();
    for candidate in candidates {
        hasher.update(candidate.relative.as_bytes());
        hasher.update(format!(":{}:{}\n", candidate.bytes, candidate.mtime_ns.unwrap_or(0)).as_bytes());
        for (relative, _, bytes, mtime) in &candidate.sidecars {
            hasher.update(relative.as_bytes());
            hasher.update(format!(":{}:{}\n", bytes, mtime.unwrap_or(0)).as_bytes());
        }
    }
    hasher.update(scope.source.as_bytes());
    hasher.update(scope.cycle.as_deref().unwrap_or("").as_bytes());
    let digest = hasher.finalize();
    digest
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>()
        [..16]
        .to_owned()
}

fn protected(candidates: &[Candidate]) -> Vec<Value> {
    let mut rows = Vec::new();
    for candidate in candidates {
        if cache_retention::download_lock_held(&candidate.path) {
            rows.push(json!({
                "path": candidate.path.display().to_string(),
                "reason": "download lock held by an active task",
            }));
        }
    }
    rows
}

pub fn preview(workspace: &Path, params: &Value, cancel: &AtomicBool) -> Result<Value, String> {
    let scope = scope(params)?;
    let candidates = candidates(workspace, &scope, cancel)?;
    if candidates.is_empty() {
        return Err("nothing to delete for this scope".into());
    }
    let preview_id = identity(&scope, &candidates);
    let mut objects = Vec::new();
    let mut total_bytes = 0u64;
    for candidate in &candidates {
        objects.push(json!({
            "id": object_id(&candidate.relative),
            "kind": "data",
            "path": candidate.path.display().to_string(),
            "bytes": candidate.bytes,
        }));
        total_bytes += candidate.bytes;
        for (relative, path, bytes, _) in &candidate.sidecars {
            objects.push(json!({
                "id": object_id(relative),
                "kind": "sidecar",
                "path": path.display().to_string(),
                "bytes": bytes,
            }));
            total_bytes += bytes;
        }
    }
    let unindexed = scope
        .cycle
        .as_ref()
        .is_some_and(|key| key.is_empty());
    Ok(json!({
        "contract": "rust-data-delete-preview-v1",
        "preview_id": preview_id,
        "scope": {"source": scope.source, "cycle": scope.cycle},
        "data_file_count": candidates.len(),
        "file_count": objects.len(),
        "total_bytes": total_bytes,
        "total_mb": total_bytes as f64 / 1_048_576.0,
        "protected": protected(&candidates),
        "unindexed_included": unindexed,
        "objects": objects,
    }))
}

pub fn execute(workspace: &Path, params: &Value, cancel: &AtomicBool) -> Result<Value, String> {
    let scope = scope(params)?;
    let preview_id = params["preview_id"]
        .as_str()
        .filter(|v| !v.is_empty())
        .ok_or("data delete execute requires preview_id")?;
    if params["confirm"] != json!(true) {
        return Err("data delete execute requires confirm=true".into());
    }
    let candidates = candidates(workspace, &scope, cancel)?;
    if candidates.is_empty() {
        return Err("nothing to delete for this scope".into());
    }
    if identity(&scope, &candidates) != preview_id {
        return Err("data delete preview is stale; request a new preview".into());
    }
    let mut deleted = 0usize;
    let mut skipped = Vec::new();
    let mut failed = Vec::new();
    let mut freed_bytes = 0u64;
    for candidate in candidates {
        check(cancel)?;
        if cache_retention::download_lock_held(&candidate.path) {
            skipped.push(json!({
                "path": candidate.path.display().to_string(),
                "reason": "download lock held by an active task",
            }));
            continue;
        }
        let bytes = fs::symlink_metadata(&candidate.path).map(|meta| meta.len()).unwrap_or(0);
        match fs::remove_file(&candidate.path) {
            Ok(()) => {
                deleted += 1;
                freed_bytes += bytes;
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                failed.push(format!("{}: {error}", candidate.path.display()));
                continue;
            }
        }
        for (_, path, _, _) in &candidate.sidecars {
            check(cancel)?;
            let bytes = fs::symlink_metadata(path).map(|meta| meta.len()).unwrap_or(0);
            match fs::remove_file(path) {
                Ok(()) => {
                    freed_bytes += bytes;
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => failed.push(format!("{}: {error}", path.display())),
            }
        }
    }
    Ok(json!({
        "contract": "rust-data-delete-execute-v1",
        "scope": {"source": scope.source, "cycle": scope.cycle},
        "preview_id": preview_id,
        "deleted": deleted,
        "skipped": skipped,
        "failed": failed,
        "freed_bytes": freed_bytes,
        "freed_mb": freed_bytes as f64 / 1_048_576.0,
        "reclaimed_disk_mb": freed_bytes as f64 / 1_048_576.0,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn gfs_cycle(root: &Path, cycle: &str, name: &str) -> PathBuf {
        fs::create_dir_all(root).unwrap();
        let file = root.join(format!("{name}.grib2"));
        fs::write(&file, b"data-bytes").unwrap();
        fs::write(
            file.with_extension("grib2.json"),
            json!({"cycle": cycle, "forecast_hour": 3}).to_string(),
        )
        .unwrap();
        file
    }

    fn workspace_with_gfs() -> (tempfile::TempDir, PathBuf) {
        let workspace = tempfile::tempdir().unwrap();
        let gfs = workspace.path().join(".cache/gfs");
        (workspace, gfs)
    }

    #[test]
    fn preview_and_execute_delete_cycle_group_with_sidecars() {
        let (workspace, gfs) = workspace_with_gfs();
        gfs_cycle(&gfs, "2026-11-11T00:00:00Z", "new");
        gfs_cycle(&gfs, "2026-11-10T00:00:00Z", "old");
        let scope = json!({"source": "gfs", "cycle": "2026-11-11T00:00:00Z"});
        let preview = preview(
            workspace.path(),
            &json!({"scope": scope}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(preview["contract"], "rust-data-delete-preview-v1");
        // One GRIB + one sidecar; the other cycle is untouched.
        assert_eq!(preview["data_file_count"], 1);
        assert_eq!(preview["file_count"], 2);
        assert!(preview["total_bytes"].as_u64().unwrap() > 0);
        let result = execute(
            workspace.path(),
            &json!({"scope": scope, "preview_id": preview["preview_id"], "confirm": true}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(result["deleted"], 1);
        assert_eq!(result["skipped"].as_array().unwrap().len(), 0);
        assert_eq!(result["failed"].as_array().unwrap().len(), 0);
        assert!(!gfs.join("new.grib2").exists());
        assert!(!gfs.join("new.grib2.json").exists());
        assert!(gfs.join("old.grib2").exists());
        assert!(gfs.join("old.grib2.json").exists());
    }

    #[test]
    fn whole_source_scope_covers_unindexed_and_all_cycles() {
        let (workspace, gfs) = workspace_with_gfs();
        gfs_cycle(&gfs, "2026-11-11T00:00:00Z", "new");
        fs::write(gfs.join("unindexed.grib2"), b"x").unwrap();
        let preview = preview(
            workspace.path(),
            &json!({"scope": {"source": "gfs"}}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(preview["data_file_count"], 2);
        let result = execute(
            workspace.path(),
            &json!({"scope": {"source": "gfs"}, "preview_id": preview["preview_id"], "confirm": true}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(result["deleted"], 2);
        let left: Vec<_> = fs::read_dir(&gfs).unwrap().collect();
        assert!(left.is_empty());
    }

    #[test]
    fn execute_rejects_stale_preview_after_cache_changes() {
        let (workspace, gfs) = workspace_with_gfs();
        let file = gfs_cycle(&gfs, "2026-11-11T00:00:00Z", "new");
        let scope = json!({"source": "gfs", "cycle": "2026-11-11T00:00:00Z"});
        let preview_value = preview(
            workspace.path(),
            &json!({"scope": scope}),
            &AtomicBool::new(false),
        )
        .unwrap();
        // Bytes changed inside the scope: the frozen identity no longer holds.
        fs::write(&file, b"changed-content").unwrap();
        let error = execute(
            workspace.path(),
            &json!({"scope": scope, "preview_id": preview_value["preview_id"], "confirm": true}),
            &AtomicBool::new(false),
        )
        .unwrap_err();
        assert!(error.contains("stale"));
        assert!(file.exists());
    }

    #[test]
    fn unrelated_cycle_changes_do_not_invalidate_scope_identity() {
        let (workspace, gfs) = workspace_with_gfs();
        let file = gfs_cycle(&gfs, "2026-11-11T00:00:00Z", "new");
        let scope = json!({"source": "gfs", "cycle": "2026-11-11T00:00:00Z"});
        let preview_value = preview(
            workspace.path(),
            &json!({"scope": scope}),
            &AtomicBool::new(false),
        )
        .unwrap();
        // A new file in an unrelated cycle does not change this scope's
        // identity: the confirmed set is neither grown nor re-derived.
        gfs_cycle(&gfs, "2026-11-09T00:00:00Z", "later");
        execute(
            workspace.path(),
            &json!({"scope": scope, "preview_id": preview_value["preview_id"], "confirm": true}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert!(!file.exists());
        assert!(gfs.join("later.grib2").exists());
    }

    #[test]
    fn execute_requires_confirmation_and_known_cycle() {
        let (workspace, gfs) = workspace_with_gfs();
        gfs_cycle(&gfs, "2026-11-11T00:00:00Z", "new");
        let scope = json!({"source": "gfs", "cycle": "2026-11-11T00:00:00Z"});
        let preview_value = preview(
            workspace.path(),
            &json!({"scope": scope}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert!(execute(
            workspace.path(),
            &json!({"scope": scope, "preview_id": preview_value["preview_id"]}),
            &AtomicBool::new(false),
        )
        .unwrap_err()
        .contains("confirm"));
        assert!(preview(
            workspace.path(),
            &json!({"scope": {"source": "gfs", "cycle": "missing"}}),
            &AtomicBool::new(false),
        )
        .unwrap_err()
        .contains("not present"));
        assert!(preview(
            workspace.path(),
            &json!({"scope": {"source": "satellite"}}),
            &AtomicBool::new(false),
        )
        .unwrap_err()
        .contains("source must be"));
    }

    #[test]
    fn live_download_lock_defers_deletion_but_stale_lock_does_not() {
        let (workspace, gfs) = workspace_with_gfs();
        let file = gfs_cycle(&gfs, "2026-11-11T00:00:00Z", "new");
        // A held flock on <file>.lock must defer the delete…
        let held = crate::cache_lock::Lock::acquire(&file.with_extension("grib2.lock"), 5.0).unwrap();
        let preview_value = preview(
            workspace.path(),
            &json!({"scope": {"source": "gfs", "cycle": "2026-11-11T00:00:00Z"}}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(preview_value["protected"].as_array().unwrap().len(), 1);
        let locked = execute(
            workspace.path(),
            &json!({"scope": {"source": "gfs", "cycle": "2026-11-11T00:00:00Z"},
                    "preview_id": preview_value["preview_id"], "confirm": true}),
            &AtomicBool::new(false),
        )
        .unwrap();
        drop(held);
        assert_eq!(locked["deleted"], 0);
        assert_eq!(locked["skipped"].as_array().unwrap().len(), 1);
        assert!(file.exists());
        // …and once the flock is free a fresh preview/execute round succeeds.
        let unlocked = preview(
            workspace.path(),
            &json!({"scope": {"source": "gfs", "cycle": "2026-11-11T00:00:00Z"}}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(unlocked["protected"].as_array().unwrap().len(), 0);
        let result = execute(
            workspace.path(),
            &json!({"scope": {"source": "gfs", "cycle": "2026-11-11T00:00:00Z"},
                    "preview_id": unlocked["preview_id"], "confirm": true}),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(result["deleted"], 1);
        assert!(!file.exists());
    }

    #[test]
    fn cancel_and_vanished_files_are_not_errors() {
        let (workspace, gfs) = workspace_with_gfs();
        gfs_cycle(&gfs, "2026-11-11T00:00:00Z", "new");
        assert!(preview(
            workspace.path(),
            &json!({"scope": {"source": "gfs"}}),
            &AtomicBool::new(true),
        )
        .unwrap_err()
        .contains("cancelled"));
        // Empty cache root: preview errors with "nothing to delete".
        let (empty, _gfs) = workspace_with_gfs();
        assert!(preview(
            empty.path(),
            &json!({"scope": {"source": "cams"}}),
            &AtomicBool::new(false),
        )
        .unwrap_err()
        .contains("nothing to delete"));
    }
}
