//! Persisted cache-retention preferences for the native desktop/runtime path.
//!
//! The settings file contains policy only; applying the policy remains owned by
//! `cache_retention`. A missing file means retention is disabled until the user
//! explicitly chooses values in Settings.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};

const MAX_CYCLES: usize = 10_000;
const MAX_DAYS: f64 = 3_650.0;
const MAX_BYTES: u64 = 16 * 1024;
const CONTRACT: &str = "rust-retention-settings-v1";

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct RetentionSettings {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gfs_cycles: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ecmwf_cycles: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cams_cycles: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub satellite_days: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub report_days: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub map_days: Option<f64>,
}

fn path(workspace: &Path) -> PathBuf {
    let configured = std::env::var_os("FIRESKY_RETENTION_SETTINGS_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            crate::paths::resolve(workspace, "", ".firesky/settings/retention.json", "settings/retention.json")
        });
    if configured.is_absolute() {
        configured
    } else {
        workspace.join(configured)
    }
}

fn validate_count(value: Option<usize>, key: &str) -> Result<(), String> {
    if let Some(value) = value {
        if !(1..=MAX_CYCLES).contains(&value) {
            return Err(format!("{key} must be an integer in 1..{MAX_CYCLES}"));
        }
    }
    Ok(())
}

fn validate_days(value: Option<f64>, key: &str) -> Result<(), String> {
    if let Some(value) = value {
        if !value.is_finite() || !(0.0..=MAX_DAYS).contains(&value) {
            return Err(format!("{key} must be within 0..{MAX_DAYS}"));
        }
    }
    Ok(())
}

fn validate(settings: &RetentionSettings) -> Result<(), String> {
    validate_count(settings.gfs_cycles, "gfs_cycles")?;
    validate_count(settings.ecmwf_cycles, "ecmwf_cycles")?;
    validate_count(settings.cams_cycles, "cams_cycles")?;
    validate_days(settings.satellite_days, "satellite_days")?;
    validate_days(settings.report_days, "report_days")?;
    validate_days(settings.map_days, "map_days")?;
    Ok(())
}

fn regular_file(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() {
                return Err("retention settings path cannot be a symlink".into());
            }
            Ok(metadata.is_file())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.to_string()),
    }
}

fn read_settings(path: &Path) -> Result<RetentionSettings, String> {
    if !regular_file(path)? {
        return Ok(RetentionSettings::default());
    }
    let metadata = fs::metadata(path).map_err(|e| e.to_string())?;
    if metadata.len() > MAX_BYTES {
        return Err("retention settings file exceeds limit".into());
    }
    let value: Value = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
        .map_err(|_| "retention settings file is invalid".to_string())?;
    if value["contract"] != CONTRACT {
        return Err("retention settings contract is invalid".into());
    }
    let settings: RetentionSettings = serde_json::from_value(value["settings"].clone())
        .map_err(|_| "retention settings payload is invalid".to_string())?;
    validate(&settings)?;
    Ok(settings)
}

fn serialized(settings: &RetentionSettings) -> Result<Vec<u8>, String> {
    validate(settings)?;
    serde_json::to_vec_pretty(&json!({
        "contract": CONTRACT,
        "settings": settings,
    }))
    .map_err(|e| e.to_string())
}

pub fn load(workspace: &Path) -> Result<RetentionSettings, String> {
    read_settings(&path(workspace))
}

pub fn status(workspace: &Path) -> Result<Value, String> {
    let file = path(workspace);
    let settings = read_settings(&file)?;
    Ok(json!({
        "contract": CONTRACT,
        "settings": settings,
        "configured": settings != RetentionSettings::default(),
        "path": file,
        "runtime_python_used": false,
    }))
}

pub fn save(workspace: &Path, payload: Value) -> Result<Value, String> {
    let raw = payload.get("settings").cloned().unwrap_or(payload);
    let settings: RetentionSettings =
        serde_json::from_value(raw).map_err(|_| "invalid retention settings".to_string())?;
    let bytes = serialized(&settings)?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err("retention settings exceed limit".into());
    }
    let destination = path(workspace);
    if fs::symlink_metadata(&destination).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("retention settings path cannot be a symlink".into());
    }
    let parent = destination
        .parent()
        .ok_or("retention settings path has no parent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".retention-settings-")
        .tempfile_in(parent)
        .map_err(|e| e.to_string())?;
    temporary
        .write_all(&bytes)
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|e| e.to_string())?;
    temporary
        .persist(&destination)
        .map_err(|e| e.error.to_string())?;
    status(workspace)
}

/// Apply the configured policy after a successful data preparation.
///
/// A missing or empty settings file is intentionally a no-op. The returned
/// value is suitable for attaching to a preparation response when cleanup ran.
pub fn apply_configured(workspace: &Path) -> Result<Option<Value>, String> {
    let settings = load(workspace)?;
    if settings == RetentionSettings::default() {
        return Ok(None);
    }
    let policy = serde_json::to_value(&settings).map_err(|e| e.to_string())?;
    Ok(Some(crate::cache_retention::apply(
        workspace,
        &json!({"retention": policy}),
    )?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_settings_are_disabled() {
        let workspace = tempfile::tempdir().unwrap();
        let value = status(workspace.path()).unwrap();
        assert_eq!(value["configured"], false);
        // 全新工作区 → 可见布局；旧布局存在时仍解析到 .firesky（Legacy）。
        let expected = if workspace.path().join(".firesky").exists() {
            ".firesky/settings/retention.json"
        } else {
            "settings/retention.json"
        };
        assert!(path(workspace.path()).ends_with(expected));
    }

    #[test]
    fn save_round_trips_and_replaces_atomically() {
        let workspace = tempfile::tempdir().unwrap();
        let value = save(
            workspace.path(),
            json!({"settings":{"gfs_cycles":5,"ecmwf_cycles":null,"satellite_days":3.5}}),
        )
        .unwrap();
        assert_eq!(value["configured"], true);
        assert_eq!(value["settings"]["gfs_cycles"], 5);
        assert_eq!(value["settings"]["satellite_days"], 3.5);
        assert_eq!(
            load(workspace.path()).unwrap(),
            RetentionSettings {
                gfs_cycles: Some(5),
                satellite_days: Some(3.5),
                ..Default::default()
            }
        );
    }

    #[test]
    fn rejects_invalid_ranges_and_unknown_fields() {
        let workspace = tempfile::tempdir().unwrap();
        assert!(save(workspace.path(), json!({"settings":{"gfs_cycles":0}})).is_err());
        assert!(save(workspace.path(), json!({"settings":{"report_days":3651}})).is_err());
        assert!(save(workspace.path(), json!({"settings":{"unknown":1}})).is_err());
    }

    #[test]
    fn configured_policy_applies_only_when_enabled() {
        let workspace = tempfile::tempdir().unwrap();
        assert!(apply_configured(workspace.path()).unwrap().is_none());
        save(workspace.path(), json!({"settings":{"gfs_cycles":1}})).unwrap();
        let result = apply_configured(workspace.path()).unwrap().unwrap();
        assert_eq!(result["policy"]["gfs_cycles"], 1);
        assert_eq!(result["runtime_python_used"], false);
    }
}
