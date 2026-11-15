//! Immutable content-addressed result snapshots for long-term history restore.
//!
//! Unlike the short-lived runtime cache, a snapshot is never overwritten by a
//! refresh of the same request and is not revalidated against raw meteorological
//! inputs, the current engine source id or the active model cycle. Identity is
//! the SHA-256 of the exact JSON bytes, so a receipt can restore the precise
//! result it published even after the original inputs have been evicted.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

pub const CONTRACT: &str = "firesky-result-snapshot-v1";
const MAX_SNAPSHOT: u64 = 512 * 1024 * 1024;
const LEASE_TIMEOUT: Duration = Duration::from_secs(5);

fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|value| format!("{value:02x}"))
        .collect()
}

fn identifier(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn directory(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(|error| error.to_string())?;
    let metadata = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err("result snapshot directory must not be a symlink".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn lease_path(dir: &Path, sha: &str) -> std::path::PathBuf {
    dir.join(format!("{sha}.lease"))
}

fn open_lease(dir: &Path, sha: &str) -> Result<File, String> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options
        .open(lease_path(dir, sha))
        .map_err(|error| error.to_string())?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("result snapshot lease must be a regular file".into());
    }
    Ok(file)
}

/// Shared read lease held for the duration of a restore. The OS releases the
/// flock if the reading process crashes, so cleanup can reclaim it later.
pub fn shared_lease(dir: &Path, sha: &str) -> Result<File, String> {
    let file = open_lease(dir, sha)?;
    let deadline = Instant::now() + LEASE_TIMEOUT;
    loop {
        match fs2::FileExt::try_lock_shared(&file) {
            Ok(()) => return Ok(file),
            Err(error) if error.raw_os_error() == fs2::lock_contended_error().raw_os_error() => {
                if Instant::now() >= deadline {
                    return Err("result snapshot read lease timed out".into());
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Err(error.to_string()),
        }
    }
}

/// Exclusive cleanup lease. Returns `None` when a live reader holds the snapshot
/// (defer deletion); the caller keeps the returned file until the snapshot is
/// removed. A crashed cleaner's lock is released by the OS automatically.
pub fn exclusive_lease(dir: &Path, sha: &str) -> Result<Option<File>, String> {
    let file = open_lease(dir, sha)?;
    match fs2::FileExt::try_lock_exclusive(&file) {
        Ok(()) => Ok(Some(file)),
        Err(error) if error.raw_os_error() == fs2::lock_contended_error().raw_os_error() => {
            Ok(None)
        }
        Err(error) => Err(error.to_string()),
    }
}

/// Publish `result` as an immutable snapshot and return its identity. The caller
/// stores only this identity in the durable receipt, never the result itself.
pub fn publish(dir: &Path, result: &Value) -> Result<Value, String> {
    if !result.is_object() {
        return Err("result snapshot must be a JSON object".into());
    }
    let bytes = serde_json::to_vec(result).map_err(|error| error.to_string())?;
    if bytes.len() as u64 > MAX_SNAPSHOT {
        return Err("result snapshot exceeds size limit".into());
    }
    let sha = digest(&bytes);
    directory(dir)?;
    let path = dir.join(format!("{sha}.json"));
    // Hold the shared read lease for the whole publish and name the temp file
    // after the sha: cleanup defers `<sha>.tmp*` while the lease is held, so a
    // concurrent retention pass cannot delete the in-flight temp file.
    let _lease = shared_lease(dir, &sha)?;
    let mut temporary = tempfile::Builder::new()
        .prefix(&format!("{sha}.tmp"))
        .tempfile_in(dir)
        .map_err(|error| error.to_string())?;
    temporary
        .write_all(&bytes)
        .map_err(|error| error.to_string())?;
    temporary
        .as_file()
        .sync_all()
        .map_err(|error| error.to_string())?;
    temporary
        .persist(&path)
        .map_err(|error| error.to_string())?;
    Ok(json!({"contract":CONTRACT,"sha256":sha,"bytes":bytes.len()}))
}

/// Read and verify a snapshot by identity. Fails closed on a missing, replaced,
/// symlinked, oversized or content-mismatched file.
pub fn read(dir: &Path, identity: &Value) -> Result<Value, String> {
    if identity["contract"] != CONTRACT {
        return Err("invalid result snapshot contract".into());
    }
    let sha = identity["sha256"]
        .as_str()
        .filter(|value| identifier(value))
        .ok_or("invalid result snapshot sha256")?;
    let expected_bytes = identity["bytes"]
        .as_u64()
        .filter(|bytes| (1..=MAX_SNAPSHOT).contains(bytes))
        .ok_or("invalid result snapshot size")?;
    let path = dir.join(format!("{sha}.json"));
    let _lease = shared_lease(dir, sha)?;
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Err("result snapshot is no longer available (expired or removed)".into());
        }
        Err(error) => return Err(error.to_string()),
    };
    if !metadata.file_type().is_file() || metadata.len() > MAX_SNAPSHOT {
        return Err("invalid result snapshot file".into());
    }
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options.open(&path).map_err(|error| error.to_string())?;
    let metadata = file.metadata().map_err(|error| error.to_string())?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_SNAPSHOT {
        return Err("invalid result snapshot file".into());
    }
    let mut bytes = Vec::new();
    file.take(MAX_SNAPSHOT + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() as u64 != expected_bytes || digest(&bytes) != sha {
        return Err("result snapshot content identity mismatch".into());
    }
    let value: Value = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
    if !value.is_object() {
        return Err("result snapshot must be a JSON object".into());
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_preserves_exact_result_and_identity() {
        let dir = tempfile::tempdir().unwrap();
        let result = json!({"model_results":{"gfs":{"score":0.8}},"model_errors":{}});
        let identity = publish(dir.path(), &result).unwrap();
        assert_eq!(identity["contract"], CONTRACT);
        assert_eq!(
            identity["bytes"],
            serde_json::to_vec(&result).unwrap().len()
        );
        assert_eq!(read(dir.path(), &identity).unwrap(), result);
    }

    #[test]
    fn republish_is_idempotent_and_does_not_change_identity() {
        let dir = tempfile::tempdir().unwrap();
        let result = json!({"a":1});
        let first = publish(dir.path(), &result).unwrap();
        let second = publish(dir.path(), &result).unwrap();
        assert_eq!(first, second);
        assert_eq!(read(dir.path(), &first).unwrap(), result);
    }

    #[test]
    fn rejects_corruption_replacement_and_wrong_size() {
        let dir = tempfile::tempdir().unwrap();
        let result = json!({"a":1});
        let identity = publish(dir.path(), &result).unwrap();
        let path = dir
            .path()
            .join(format!("{}.json", identity["sha256"].as_str().unwrap()));
        fs::write(&path, b"tampered").unwrap();
        assert!(read(dir.path(), &identity).is_err());
        let mut wrong_size = identity.clone();
        wrong_size["bytes"] = json!(999);
        assert!(read(dir.path(), &wrong_size).is_err());
    }

    #[test]
    fn rejects_invalid_identity_and_traversal() {
        let dir = tempfile::tempdir().unwrap();
        assert!(read(
            dir.path(),
            &json!({"contract":CONTRACT,"sha256":"../escape","bytes":1})
        )
        .is_err());
        assert!(read(
            dir.path(),
            &json!({"contract":"other","sha256":"a".repeat(64),"bytes":1})
        )
        .is_err());
        assert!(read(
            dir.path(),
            &json!({"contract":CONTRACT,"sha256":"a".repeat(64),"bytes":0})
        )
        .is_err());
        assert!(read(
            dir.path(),
            &json!({"contract":CONTRACT,"sha256":"A".repeat(64),"bytes":1})
        )
        .is_err());
    }

    #[test]
    fn publish_rejects_non_object_and_oversized_results() {
        let dir = tempfile::tempdir().unwrap();
        assert!(publish(dir.path(), &json!([1, 2, 3])).is_err());
        assert!(publish(dir.path(), &json!("text")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn refuses_symlinked_snapshot_file_and_directory() {
        use std::os::unix::fs::symlink;
        let dir = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let result = json!({"a":1});
        let identity = publish(dir.path(), &result).unwrap();
        let sha = identity["sha256"].as_str().unwrap();
        let path = dir.path().join(format!("{sha}.json"));
        fs::remove_file(&path).unwrap();
        let target = outside.path().join("target.json");
        fs::write(&target, serde_json::to_vec(&result).unwrap()).unwrap();
        symlink(&target, &path).unwrap();
        assert!(read(dir.path(), &identity).is_err());

        let linked_dir = tempfile::tempdir().unwrap();
        symlink(outside.path(), linked_dir.path().join("snapshots")).unwrap();
        assert!(publish(&linked_dir.path().join("snapshots"), &result).is_err());
    }

    #[test]
    fn shared_read_lease_defers_exclusive_cleanup_until_released() {
        let dir = tempfile::tempdir().unwrap();
        let identity = publish(dir.path(), &json!({"a":1})).unwrap();
        let sha = identity["sha256"].as_str().unwrap();
        let reader = shared_lease(dir.path(), sha).unwrap();
        assert!(exclusive_lease(dir.path(), sha).unwrap().is_none());
        drop(reader);
        let cleaner = exclusive_lease(dir.path(), sha).unwrap();
        assert!(cleaner.is_some());
        assert!(exclusive_lease(dir.path(), sha).unwrap().is_none());
    }

    #[test]
    fn read_reports_expired_snapshot_as_unavailable() {
        let dir = tempfile::tempdir().unwrap();
        let identity = publish(dir.path(), &json!({"a":1})).unwrap();
        let sha = identity["sha256"].as_str().unwrap();
        fs::remove_file(dir.path().join(format!("{sha}.json"))).unwrap();
        let error = read(dir.path(), &identity).unwrap_err();
        assert!(error.contains("no longer available"));
    }

    #[test]
    fn crashed_reader_lease_is_reclaimed_by_cleanup() {
        const CHILD_ENV: &str = "FIRESKY_SNAPSHOT_LEASE_TEST_CHILD";
        if let Some(dir) = std::env::var_os(CHILD_ENV) {
            let dir = std::path::PathBuf::from(dir);
            let identity = publish(&dir, &json!({"a":1})).unwrap();
            let sha = identity["sha256"].as_str().unwrap().to_owned();
            let _lease = shared_lease(&dir, &sha).unwrap();
            // Atomic ready signal: the parent must never observe a partially
            // written sha.
            fs::write(dir.join("ready.tmp"), &sha).unwrap();
            fs::rename(dir.join("ready.tmp"), dir.join("ready")).unwrap();
            loop {
                std::thread::sleep(Duration::from_secs(1));
            }
        }
        struct Child(std::process::Child);
        impl Drop for Child {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }
        let dir = tempfile::tempdir().unwrap();
        let mut child = Child(
            std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "result_snapshot::tests::crashed_reader_lease_is_reclaimed_by_cleanup",
                    "--nocapture",
                ])
                .env(CHILD_ENV, dir.path())
                .stdout(std::process::Stdio::null())
                .spawn()
                .unwrap(),
        );
        let deadline = Instant::now() + Duration::from_secs(10);
        while !dir.path().join("ready").is_file() {
            assert!(child.0.try_wait().unwrap().is_none(), "child exited early");
            assert!(Instant::now() < deadline, "child did not become ready");
            std::thread::sleep(Duration::from_millis(10));
        }
        let sha = fs::read_to_string(dir.path().join("ready")).unwrap();
        assert!(exclusive_lease(dir.path(), &sha).unwrap().is_none());
        child.0.kill().unwrap();
        assert!(!child.0.wait().unwrap().success());
        assert!(exclusive_lease(dir.path(), &sha).unwrap().is_some());
    }
}
