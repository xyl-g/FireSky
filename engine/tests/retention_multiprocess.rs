//! R09 multi-process and disk-full acceptance for the snapshot retention lifecycle.
//!
//! Complements the in-process `retention_lifecycle` suite: real OS processes
//! restore, publish and clean the same workspace concurrently, a crashed
//! cross-process reader's lease is reclaimed under concurrent cleaners, and a
//! genuinely full filesystem (small mounted HFS+ volume, macOS only, run with
//! `--ignored`) must not corrupt snapshots or block restores. All results are
//! synthetic, not real meteorological data.
use firesky_engine::{cache_retention, result_snapshot};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};

const ROLE_ENV: &str = "FIRESKY_RETENTION_MP_ROLE";
const WORKSPACE_ENV: &str = "FIRESKY_RETENTION_MP_WORKSPACE";
const EXPIRED: &str = "2030-01-01T00:00:00Z";

fn snapshot_root(workspace: &Path) -> PathBuf {
    workspace.join(".firesky/state/tasks/snapshots")
}

fn expire(workspace: &Path) -> Value {
    cache_retention::apply(
        workspace,
        &json!({"retention":{"report_days":1,"map_days":1,"now_utc":EXPIRED}}),
    )
    .unwrap()
}

struct Guard(Child);
impl Drop for Guard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn spawn_role(test: &str, role: &str, workspace: &Path) -> Guard {
    Guard(
        Command::new(std::env::current_exe().unwrap())
            .args(["--exact", test, "--nocapture"])
            .env(ROLE_ENV, role)
            .env(WORKSPACE_ENV, workspace)
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .spawn()
            .unwrap(),
    )
}

fn wait_ready(workspace: &Path, child: &mut Child) {
    let deadline = Instant::now() + Duration::from_secs(15);
    while !workspace.join("ready").is_file() {
        assert!(child.try_wait().unwrap().is_none(), "child exited early");
        assert!(Instant::now() < deadline, "child did not become ready");
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn stop_children(workspace: &Path, children: &mut [Guard]) {
    fs::write(workspace.join("stop"), b"1").unwrap();
    let deadline = Instant::now() + Duration::from_secs(15);
    for child in children.iter_mut() {
        loop {
            match child.0.try_wait().unwrap() {
                Some(status) => {
                    assert!(status.success(), "child role failed: {status}");
                    break;
                }
                None => {
                    assert!(Instant::now() < deadline, "child did not stop in time");
                    std::thread::sleep(Duration::from_millis(20));
                }
            }
        }
    }
}

fn read_stats(workspace: &Path, role: &str) -> Value {
    let path = workspace.join(format!("{role}-stats.json"));
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

/// Any restore failure must be a fail-closed availability/lease outcome, never
/// a spurious integrity error caused by concurrent publish/cleanup.
fn assert_clean_failure(error: &str) {
    assert!(
        !error.contains("mismatch") && !error.contains("invalid result snapshot"),
        "concurrent processes produced a spurious integrity failure: {error}"
    );
}

fn child_workspace() -> PathBuf {
    PathBuf::from(std::env::var_os(WORKSPACE_ENV).unwrap())
}

fn child_finished(workspace: &Path) -> bool {
    workspace.join("stop").is_file()
}

fn signal_ready(workspace: &Path, role: &str) {
    let temporary = workspace.join(format!("{role}-ready.tmp"));
    fs::write(&temporary, b"1").unwrap();
    fs::rename(&temporary, workspace.join(format!("{role}-ready"))).unwrap();
}

/// Cleaners must not start until every reader has restored at least once:
/// otherwise a fast first cleanup pass can delete all seed snapshots before
/// any reader runs, making the liveness counters timing-dependent.
fn wait_readers_ready(workspace: &Path) {
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        if ["reader-0", "reader-1", "reader-2"]
            .iter()
            .all(|role| workspace.join(format!("{role}-ready")).is_file())
        {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "readers never completed a first restore"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn write_stats(workspace: &Path, role: &str, stats: Value) {
    fs::write(
        workspace.join(format!("{role}-stats.json")),
        stats.to_string(),
    )
    .unwrap();
}

#[test]
fn multiprocess_readers_publishers_and_cleaners_share_one_workspace() {
    const TEST: &str = "multiprocess_readers_publishers_and_cleaners_share_one_workspace";
    if let Some(role) = std::env::var_os(ROLE_ENV) {
        let role = role.to_str().unwrap().to_owned();
        let workspace = child_workspace();
        let root = snapshot_root(&workspace);
        match role.split('-').next().unwrap() {
            "reader" => {
                let identities: Vec<Value> =
                    serde_json::from_slice(&fs::read(workspace.join("identities.json")).unwrap())
                        .unwrap();
                let expected: Vec<Value> =
                    serde_json::from_slice(&fs::read(workspace.join("expected.json")).unwrap())
                        .unwrap();
                let (mut ok, mut gone) = (0u64, 0u64);
                while !child_finished(&workspace) {
                    for (identity, result) in identities.iter().zip(&expected) {
                        match result_snapshot::read(&root, identity) {
                            Ok(value) => {
                                assert_eq!(&value, result, "restore returned altered data");
                                ok += 1;
                                if ok == 1 {
                                    signal_ready(&workspace, &role);
                                }
                            }
                            Err(error) => {
                                assert_clean_failure(&error);
                                gone += 1;
                            }
                        }
                    }
                }
                write_stats(&workspace, &role, json!({"ok":ok,"gone":gone}));
            }
            "publisher" => {
                let mut published = 0u64;
                let mut index = 0u64;
                while !child_finished(&workspace) {
                    let result = json!({"kind":"fresh","index":index,"payload":"x".repeat(256)});
                    if let Err(error) = result_snapshot::publish(&root, &result) {
                        let listing: Vec<String> = fs::read_dir(&root)
                            .map(|entries| {
                                entries
                                    .filter_map(Result::ok)
                                    .map(|entry| entry.file_name().to_string_lossy().into_owned())
                                    .collect()
                            })
                            .unwrap_or_default();
                        panic!("publish failed: {error}; dir listing: {listing:?}");
                    }
                    published += 1;
                    index += 1;
                }
                write_stats(&workspace, &role, json!({"published":published}));
            }
            "cleaner" => {
                wait_readers_ready(&workspace);
                let mut passes = 0u64;
                while !child_finished(&workspace) {
                    expire(&workspace);
                    passes += 1;
                }
                write_stats(&workspace, &role, json!({"passes":passes}));
            }
            other => panic!("unknown child role: {other}"),
        }
        return;
    }

    let workspace = tempfile::tempdir().unwrap();
    let root = snapshot_root(workspace.path());
    let mut identities = Vec::new();
    let mut expected = Vec::new();
    for index in 0..6 {
        let result = json!({"kind":"expired","index":index,"payload":"y".repeat(256)});
        identities.push(result_snapshot::publish(&root, &result).unwrap());
        expected.push(result);
    }
    fs::write(
        workspace.path().join("identities.json"),
        serde_json::to_vec(&identities).unwrap(),
    )
    .unwrap();
    fs::write(
        workspace.path().join("expected.json"),
        serde_json::to_vec(&expected).unwrap(),
    )
    .unwrap();

    let roles = [
        "reader-0",
        "reader-1",
        "reader-2",
        "publisher-0",
        "cleaner-0",
        "cleaner-1",
    ];
    let mut children: Vec<_> = roles
        .iter()
        .map(|role| spawn_role(TEST, role, workspace.path()))
        .collect();
    wait_readers_ready(workspace.path());
    std::thread::sleep(Duration::from_secs(3));
    stop_children(workspace.path(), &mut children);
    // A reader lease can defer the cleaners' final pass; with every child
    // exited, one last sweep must reclaim all expired snapshots.
    expire(workspace.path());

    let mut ok = 0u64;
    for role in ["reader-0", "reader-1", "reader-2"] {
        ok += read_stats(workspace.path(), role)["ok"].as_u64().unwrap();
    }
    assert!(ok > 0, "no concurrent cross-process restore succeeded");
    assert!(
        read_stats(workspace.path(), "publisher-0")["published"]
            .as_u64()
            .unwrap()
            > 0,
        "publisher never published"
    );
    for role in ["cleaner-0", "cleaner-1"] {
        assert!(
            read_stats(workspace.path(), role)["passes"]
                .as_u64()
                .unwrap()
                > 0,
            "cleaner never ran"
        );
    }
    let expired_left = identities
        .iter()
        .filter(|identity| {
            root.join(format!("{}.json", identity["sha256"].as_str().unwrap()))
                .exists()
        })
        .count();
    assert_eq!(
        expired_left, 0,
        "expired snapshots survived concurrent cleanup"
    );
    // Cleaners legitimately expire everything published during the churn
    // (now_utc is 2030), so a churn-time snapshot surviving the final pass is
    // timing luck, not an invariant. With every child exited, the workspace
    // must still accept a publish and restore.
    let final_result = json!({"kind":"fresh","index":"final","payload":"z".repeat(256)});
    let final_identity = result_snapshot::publish(&root, &final_result).unwrap();
    assert_eq!(
        result_snapshot::read(&root, &final_identity).unwrap(),
        final_result,
        "workspace unusable after concurrent churn"
    );
    let mut fresh = 0u64;
    for entry in fs::read_dir(&root).unwrap().filter_map(Result::ok) {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        assert!(
            !name.contains(".tmp"),
            "in-flight publish leftover survived clean shutdown: {name}"
        );
        if let Some(sha) = name.strip_suffix(".json") {
            let identity = json!({
                "contract": result_snapshot::CONTRACT,
                "sha256": sha,
                "bytes": entry.metadata().unwrap().len(),
            });
            let value = result_snapshot::read(&root, &identity).unwrap();
            assert_eq!(value["kind"], "fresh", "surviving snapshot is not intact");
            fresh += 1;
        }
    }
    assert!(fresh > 0, "final snapshot missing after churn");
}

#[test]
fn crashed_cross_process_reader_lease_is_reclaimed_under_concurrent_cleaners() {
    const TEST: &str = "crashed_cross_process_reader_lease_is_reclaimed_under_concurrent_cleaners";
    if std::env::var_os(ROLE_ENV).is_some() {
        let workspace = child_workspace();
        let root = snapshot_root(&workspace);
        let identity = result_snapshot::publish(&root, &json!({"a":1})).unwrap();
        let sha = identity["sha256"].as_str().unwrap().to_owned();
        let _lease = result_snapshot::shared_lease(&root, &sha).unwrap();
        // Atomic ready signal: the parent must never observe a partial sha.
        fs::write(workspace.join("ready.tmp"), &sha).unwrap();
        fs::rename(workspace.join("ready.tmp"), workspace.join("ready")).unwrap();
        loop {
            std::thread::sleep(Duration::from_secs(1));
        }
    }

    let workspace = tempfile::tempdir().unwrap();
    let mut child = spawn_role(TEST, "lease-holder", workspace.path());
    wait_ready(workspace.path(), &mut child.0);
    let sha = fs::read_to_string(workspace.path().join("ready")).unwrap();
    let root = snapshot_root(workspace.path());
    let snapshot = root.join(format!("{sha}.json"));

    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let mut cleaners = Vec::new();
    for _ in 0..3 {
        let path = workspace.path().to_owned();
        let stop = stop.clone();
        cleaners.push(std::thread::spawn(move || {
            while !stop.load(std::sync::atomic::Ordering::Relaxed) {
                expire(&path);
                std::thread::sleep(Duration::from_millis(5));
            }
        }));
    }
    std::thread::sleep(Duration::from_millis(500));
    assert!(
        snapshot.exists(),
        "cleanup deleted a snapshot while a live reader held its lease"
    );
    child.0.kill().unwrap();
    assert!(!child.0.wait().unwrap().success());
    let deadline = Instant::now() + Duration::from_secs(10);
    while snapshot.exists() {
        assert!(
            Instant::now() < deadline,
            "crashed reader lease was never reclaimed"
        );
        expire(workspace.path());
        std::thread::sleep(Duration::from_millis(10));
    }
    stop.store(true, std::sync::atomic::Ordering::Relaxed);
    for cleaner in cleaners {
        cleaner.join().unwrap();
    }
    // Lease files are never unlinked so flock inodes stay stable; the snapshot
    // itself must be gone once the crashed reader's lock was released.
    assert!(root.join(format!("{sha}.lease")).exists());
}

#[cfg(target_os = "macos")]
struct Volume(PathBuf);
#[cfg(target_os = "macos")]
impl Drop for Volume {
    fn drop(&mut self) {
        let _ = Command::new("hdiutil")
            .args(["detach", "-quiet"])
            .arg(&self.0)
            .status();
    }
}

/// Real ENOSPC pressure on a tiny mounted volume: publish must fail closed
/// without leaving a partial snapshot, existing snapshots must still restore,
/// and cleanup must free space so publishing can resume. Requires `hdiutil`;
/// run explicitly with `cargo test --test retention_multiprocess -- --ignored`.
#[cfg(target_os = "macos")]
#[test]
#[ignore = "mounts a small HFS+ volume via hdiutil; run explicitly with --ignored"]
fn publish_fails_closed_on_a_full_disk_and_restore_still_works() {
    let image_dir = tempfile::tempdir().unwrap();
    let image = image_dir.path().join("full.dmg");
    let volume_name = format!("FIRESKY-FULL-{}", std::process::id());
    let created = Command::new("hdiutil")
        .args(["create", "-size", "2m", "-fs", "HFS+", "-volname"])
        .arg(&volume_name)
        .arg(&image)
        .output()
        .unwrap();
    assert!(
        created.status.success(),
        "hdiutil create failed: {}",
        String::from_utf8_lossy(&created.stderr)
    );
    let attached = Command::new("hdiutil")
        .args(["attach", "-nobrowse"])
        .arg(&image)
        .output()
        .unwrap();
    assert!(
        attached.status.success(),
        "hdiutil attach failed: {}",
        String::from_utf8_lossy(&attached.stderr)
    );
    let mount = PathBuf::from(format!("/Volumes/{volume_name}"));
    let _volume = Volume(mount.clone());
    assert!(
        mount.is_dir(),
        "volume did not mount at {}",
        mount.display()
    );

    let workspace = mount.join("workspace");
    let root = snapshot_root(&workspace);
    let result = json!({"model_results":{"gfs":{"score":0.8}},"model_errors":{}});
    let identity = result_snapshot::publish(&root, &result).unwrap();

    // Fill the volume until the filesystem reports ENOSPC. After the first
    // failed large write, squeeze out the remaining slack byte by byte so even
    // a tiny snapshot cannot be published. The ballast lives in the map cache
    // so a short map window can reclaim it while snapshots stay retained.
    let map_root = workspace.join(".cache/map-frames");
    fs::create_dir_all(&map_root).unwrap();
    let ballast = map_root.join("ballast.bin");
    let mut file = fs::File::create(&ballast).unwrap();
    let mut chunk_size = 64 * 1024usize;
    let mut full = false;
    for _ in 0..200_000 {
        let chunk = vec![0u8; chunk_size];
        if let Err(error) = std::io::Write::write_all(&mut file, &chunk) {
            assert_eq!(
                error.raw_os_error(),
                Some(libc::ENOSPC),
                "unexpected fill error: {error}"
            );
            if chunk_size == 1 {
                full = true;
                break;
            }
            chunk_size = 1;
        }
    }
    drop(file);
    assert!(full, "2 MiB volume never filled up");

    // Publish fails closed; no partial snapshot or temp file is left behind.
    let error = result_snapshot::publish(&root, &json!({"kind":"too-big-for-full-disk"}))
        .expect_err("publish must fail on a full disk");
    assert!(
        error.to_lowercase().contains("space") || error.contains("28"),
        "expected ENOSPC, got: {error}"
    );
    let sha = identity["sha256"].as_str().unwrap();
    let mut entries: Vec<String> = fs::read_dir(&root)
        .unwrap()
        .filter_map(Result::ok)
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    entries.sort();
    let mut expected = vec![format!("{sha}.json"), format!("{sha}.lease")];
    expected.sort();
    assert_eq!(entries, expected, "full-disk publish leaked files");

    // Restore of an existing snapshot needs no new disk space and still works.
    assert_eq!(result_snapshot::read(&root, &identity).unwrap(), result);

    // Retention frees the ballast via the short map window; snapshots stay
    // retained and publishing resumes afterwards.
    let freed = cache_retention::apply(
        &workspace,
        &json!({"retention":{"report_days":3650,"map_days":1,"now_utc":EXPIRED}}),
    )
    .unwrap();
    assert!(freed["freed_bytes"].as_u64().unwrap() > 0);
    assert!(!ballast.exists());
    let again = json!({"kind":"after-cleanup"});
    let second = result_snapshot::publish(&root, &again).unwrap();
    assert_eq!(result_snapshot::read(&root, &second).unwrap(), again);
    assert_eq!(result_snapshot::read(&root, &identity).unwrap(), result);
}
