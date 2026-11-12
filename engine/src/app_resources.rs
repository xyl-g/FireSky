//! Immutable application resources shared by Tauri, Docker and the Rust HTTP
//! service. User data never lives below this directory.

use crate::tensor_bundle::{source_identity, SourceIdentity};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
};

const MANIFEST: &str = "RESOURCE-MANIFEST.json";
const MAX_MANIFEST_BYTES: u64 = 512 * 1024;
const MAX_FILES: usize = 1024;
const MAX_TOTAL_BYTES: u64 = 128 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct Paths {
    pub root: Option<PathBuf>,
    pub city_catalog: PathBuf,
    pub timezone_manifest: Option<PathBuf>,
}

fn configured_path(workspace: &Path, key: &str) -> Option<PathBuf> {
    std::env::var_os(key).map(|value| {
        let path = PathBuf::from(value);
        if path.is_absolute() {
            path
        } else {
            workspace.join(path)
        }
    })
}

pub fn strict_required() -> bool {
    matches!(
        std::env::var("FIRESKY_REQUIRE_APP_RESOURCES")
            .ok()
            .as_deref()
            .map(str::trim)
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some("1" | "true" | "yes")
    )
}

fn regular_file(path: &Path, label: &str) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| format!("{label}: {e}"))?;
    if !metadata.file_type().is_file() {
        return Err(format!("{label} must be a regular file"));
    }
    Ok(())
}

fn regular_directory(path: &Path, label: &str) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| format!("{label}: {e}"))?;
    if !metadata.file_type().is_dir() {
        return Err(format!("{label} must be a directory"));
    }
    Ok(())
}

fn safe_relative(path: &str) -> bool {
    !path.is_empty()
        && !path.starts_with('/')
        && !path.contains('\\')
        && !path.contains('\0')
        && path
            .split('/')
            .all(|part| !part.is_empty() && part != "." && part != "..")
}

fn verify_manifest(root: &Path) -> Result<(), String> {
    let path = root.join(MANIFEST);
    regular_file(&path, "application resource manifest")?;
    let metadata = fs::metadata(&path).map_err(|e| e.to_string())?;
    if metadata.len() > MAX_MANIFEST_BYTES {
        return Err("application resource manifest is too large".into());
    }
    let bytes = fs::read(&path).map_err(|e| e.to_string())?;
    let value: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    if value["schema"] != "firesky-app-resources-v1"
        || value["read_only"] != true
        || value["user_data_is_external"] != true
    {
        return Err("unsupported application resource manifest".into());
    }
    let files = value["files"]
        .as_array()
        .filter(|rows| !rows.is_empty() && rows.len() <= MAX_FILES)
        .ok_or("application resource manifest has no files")?;
    let mut seen = BTreeMap::new();
    let mut total = 0_u64;
    for row in files {
        let relative = row["path"]
            .as_str()
            .ok_or("resource file path is invalid")?;
        if !safe_relative(relative) {
            return Err("resource file path escapes the resource root".into());
        }
        let bytes = row["bytes"]
            .as_u64()
            .ok_or("resource file size is invalid")?;
        let sha256 = row["sha256"]
            .as_str()
            .ok_or("resource file checksum is invalid")?;
        if sha256.len() != 64 || !sha256.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err("resource file checksum is invalid".into());
        }
        if seen.insert(relative, ()).is_some() {
            return Err("application resource manifest contains duplicate files".into());
        }
        total = total
            .checked_add(bytes)
            .filter(|total| *total <= MAX_TOTAL_BYTES)
            .ok_or("application resources exceed the supported size")?;
        let file = root.join(relative);
        regular_file(&file, &format!("application resource {relative}"))?;
        if source_identity(&file)?
            != (SourceIdentity {
                bytes,
                sha256: sha256.into(),
            })
        {
            return Err(format!(
                "application resource checksum mismatch: {relative}"
            ));
        }
    }
    Ok(())
}

fn validate_root(root: &Path) -> Result<(), String> {
    static VERIFIED: OnceLock<Mutex<BTreeMap<PathBuf, SourceIdentity>>> = OnceLock::new();
    let manifest = root.join(MANIFEST);
    regular_file(&manifest, "application resource manifest")?;
    let identity = source_identity(&manifest)?;
    let mut cache = VERIFIED
        .get_or_init(|| Mutex::new(BTreeMap::new()))
        .lock()
        .map_err(|_| "application resource validation lock poisoned")?;
    if cache.get(root) == Some(&identity) {
        return Ok(());
    }
    verify_manifest(root)?;
    cache.insert(root.to_owned(), identity);
    Ok(())
}

pub fn resolve(workspace: &Path) -> Result<Paths, String> {
    let explicit_root = configured_path(workspace, "FIRESKY_RESOURCE_DIR");
    let root = if let Some(root) = explicit_root {
        regular_directory(&root, "FIRESKY_RESOURCE_DIR")?;
        validate_root(&root)?;
        Some(root)
    } else {
        let candidate = workspace.join("resources/app-data");
        if candidate.is_dir() {
            validate_root(&candidate)?;
            Some(candidate)
        } else if strict_required() {
            return Err(
                "FireSky application resources are required but resources/app-data is missing"
                    .into(),
            );
        } else {
            None
        }
    };

    let city_catalog = configured_path(workspace, "FIRESKY_CITY_CATALOG_PATH")
        .or_else(|| {
            root.as_ref()
                .map(|path| path.join("cities/china-city-catalog.json"))
        })
        .unwrap_or_else(|| workspace.join("data/china-city-catalog.json"));
    let timezone_manifest = configured_path(workspace, "FIRESKY_TIMEZONE_MANIFEST").or_else(|| {
        root.as_ref()
            .map(|path| path.join("timezones/manifest.json"))
    });

    if root.is_some() {
        regular_file(&city_catalog, "city catalog")?;
        let timezone = timezone_manifest
            .as_deref()
            .ok_or("application resources have no timezone manifest")?;
        regular_file(timezone, "timezone manifest")?;
    }
    Ok(Paths {
        root,
        city_catalog,
        timezone_manifest,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn temporary_workspace_can_use_explicit_legacy_paths() {
        let workspace = tempfile::tempdir().unwrap();
        let paths = resolve(workspace.path()).unwrap();
        assert!(paths.root.is_none());
        assert_eq!(
            paths.city_catalog,
            workspace.path().join("data/china-city-catalog.json")
        );
        assert!(paths.timezone_manifest.is_none());
    }
}
