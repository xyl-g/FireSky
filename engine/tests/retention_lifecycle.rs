//! R09 system-level acceptance for the snapshot/report/map retention lifecycle.
//!
//! These drive the public boundary (`result_snapshot::{publish, read}` and
//! `cache_retention::apply`) under concurrency and adversarial conditions:
//! parallel restore-vs-cleanup, concurrent cleanup, symlink safety, many-file
//! disk reclamation and repeated cleanup. They use synthetic snapshots, not real
//! meteorological results, and run in-process (the cross-process crash path is
//! covered separately in `result_snapshot`).
use firesky_engine::{cache_retention, result_snapshot};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};

const EXPIRED: &str = "2030-01-01T00:00:00Z";

fn snapshot_root(workspace: &Path) -> PathBuf {
    workspace.join(".firesky/state/tasks/snapshots")
}

fn publish(workspace: &Path, result: &Value) -> Value {
    result_snapshot::publish(&snapshot_root(workspace), result).unwrap()
}

fn expire(workspace: &Path) -> Value {
    cache_retention::apply(
        workspace,
        &json!({"retention":{"report_days":1,"map_days":1,"now_utc":EXPIRED}}),
    )
    .unwrap()
}

#[test]
fn parallel_restore_never_returns_corrupt_data_while_cleanup_deletes() {
    let workspace = tempfile::tempdir().unwrap();
    let result = json!({"model_results":{"gfs":{"score":0.8}},"model_errors":{}});
    let identity = publish(workspace.path(), &result);
    let root = snapshot_root(workspace.path());
    let stop = Arc::new(AtomicBool::new(false));
    let ok = Arc::new(AtomicUsize::new(0));
    let mut handles = Vec::new();
    for _ in 0..4 {
        let (root, identity, result) = (root.clone(), identity.clone(), result.clone());
        let (stop, ok) = (stop.clone(), ok.clone());
        handles.push(std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                match result_snapshot::read(&root, &identity) {
                    Ok(value) => {
                        assert_eq!(value, result, "restore returned altered data");
                        ok.fetch_add(1, Ordering::Relaxed);
                    }
                    Err(error) => {
                        assert!(
                            !error.contains("mismatch") && !error.contains("invalid result snapshot"),
                            "cleanup race produced a spurious integrity failure: {error}"
                        );
                    }
                }
            }
        }));
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut deleted = false;
    while Instant::now() < deadline {
        expire(workspace.path());
        if !root.join(format!("{}.json", identity["sha256"].as_str().unwrap())).exists() {
            deleted = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    std::thread::sleep(Duration::from_millis(20));
    stop.store(true, Ordering::Relaxed);
    for handle in handles {
        handle.join().unwrap();
    }
    assert!(deleted, "cleanup never removed the expired snapshot");
    assert!(ok.load(Ordering::Relaxed) > 0, "no concurrent restore succeeded");
}

#[test]
fn concurrent_cleanup_removes_each_snapshot_without_error() {
    let workspace = tempfile::tempdir().unwrap();
    let root = snapshot_root(workspace.path());
    let mut paths = Vec::new();
    for index in 0..40 {
        let identity = publish(workspace.path(), &json!({"index":index}));
        paths.push(root.join(format!("{}.json", identity["sha256"].as_str().unwrap())));
    }
    let mut handles = Vec::new();
    for _ in 0..3 {
        let path = workspace.path().to_owned();
        handles.push(std::thread::spawn(move || {
            for _ in 0..10 {
                expire(&path);
            }
        }));
    }
    for handle in handles {
        handle.join().unwrap();
    }
    for path in &paths {
        assert!(!path.exists(), "snapshot survived concurrent cleanup");
    }
}

#[cfg(unix)]
#[test]
fn cleanup_never_follows_a_symlinked_snapshot_to_delete_outside_files() {
    use std::os::unix::fs::symlink;
    let workspace = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    let result = json!({"a":1});
    let identity = publish(workspace.path(), &result);
    let sha = identity["sha256"].as_str().unwrap();
    let root = snapshot_root(workspace.path());
    let link = root.join(format!("{sha}.json"));
    let target = outside.path().join("target.json");
    fs::write(&target, serde_json::to_vec(&result).unwrap()).unwrap();
    fs::remove_file(&link).unwrap();
    symlink(&target, &link).unwrap();
    expire(workspace.path());
    assert!(target.exists(), "cleanup deleted the symlink target");
    assert!(fs::symlink_metadata(&link).unwrap().file_type().is_symlink());
    assert!(result_snapshot::read(&root, &identity).is_err());
}

#[test]
fn expiring_many_snapshots_reclaims_disk_and_repeated_cleanup_is_idempotent() {
    let workspace = tempfile::tempdir().unwrap();
    let root = snapshot_root(workspace.path());
    for index in 0..150 {
        publish(workspace.path(), &json!({"index":index,"payload":"x".repeat(64)}));
    }
    let first = expire(workspace.path());
    assert_eq!(first["removed_count"], 150);
    assert!(first["freed_bytes"].as_u64().unwrap() > 0);
    let remaining = fs::read_dir(&root)
        .unwrap()
        .filter_map(Result::ok)
        .filter(|entry| {
            entry.path().extension().and_then(|v| v.to_str()) == Some("json")
        })
        .count();
    assert_eq!(remaining, 0);
    let second = expire(workspace.path());
    assert_eq!(second["removed_count"], 0);
}