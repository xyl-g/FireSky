//! Local ADS credentials. Never return or include tokens in error messages.
use serde_json::{json, Value};
use std::{collections::BTreeMap, fs, io::Write, path::PathBuf};

pub const ADS_URL: &str = "https://ads.atmosphere.copernicus.eu/api";
const MAX_BYTES: u64 = 64 * 1024;

fn path() -> Result<PathBuf, String> {
    std::env::var_os("CDSAPI_RC")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME")
                .or_else(|| std::env::var_os("USERPROFILE"))
                .map(|home| PathBuf::from(home).join(".cdsapirc"))
        })
        .ok_or_else(|| "cannot locate ADS credentials file".into())
}

fn read(path: &std::path::Path) -> Result<BTreeMap<String, String>, String> {
    if !path.exists() {
        return Ok(BTreeMap::new());
    }
    let metadata = fs::metadata(path).map_err(|_| "cannot read ADS configuration")?;
    if !metadata.is_file() || metadata.len() > MAX_BYTES {
        return Err("invalid ADS configuration file".into());
    }
    let text = fs::read_to_string(path).map_err(|_| "cannot read ADS configuration")?;
    let mut config = BTreeMap::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') || trimmed.starts_with(';') {
            continue;
        }
        if line.starts_with(char::is_whitespace) {
            return Err("multiline ADS configuration is not allowed".into());
        }
        let (name, value) = trimmed.split_once(':').ok_or("invalid ADS configuration")?;
        if name.is_empty()
            || config
                .insert(name.to_owned(), value.trim().to_owned())
                .is_some()
        {
            return Err("invalid ADS configuration".into());
        }
    }
    Ok(config)
}

fn valid_key(key: &str) -> bool {
    !key.is_empty()
        && key.len() <= 16384
        && key
            .bytes()
            .all(|v| v.is_ascii_alphanumeric() || b"._:+/=-".contains(&v))
}

pub fn status() -> Value {
    let env_key = std::env::var("CDSAPI_KEY").ok();
    let env_url = std::env::var("CDSAPI_URL").ok();
    let path = path();
    let can_save = env_key.is_none()
        && env_url
            .as_deref()
            .is_none_or(|url| url.trim_end_matches('/') == ADS_URL);
    let mut result = json!({
        "configured":false, "state":"missing",
        "path":path.as_ref().ok().map(|p|p.to_string_lossy().into_owned()).unwrap_or_default(),
        "environment_key":env_key.is_some(), "can_save":can_save
    });
    let config = if env_key.is_none() || env_url.is_none() {
        match path.and_then(|p| read(&p)) {
            Ok(config) => config,
            Err(_) => {
                result["state"] = json!("unreadable");
                return result;
            }
        }
    } else {
        BTreeMap::new()
    };
    let key = env_key.as_ref().or_else(|| config.get("key"));
    if !key.is_some_and(|key| valid_key(key)) {
        return result;
    }
    let url = env_url.as_ref().or_else(|| config.get("url"));
    result["state"] = if !url.is_some_and(|url| url.trim_end_matches('/') == ADS_URL) {
        json!("wrong_endpoint")
    } else if config.get("verify").is_some_and(|v| v != "1") {
        json!("tls_disabled")
    } else {
        result["configured"] = json!(true);
        json!("configured")
    };
    result
}

pub(crate) fn key() -> Result<String, String> {
    if status()["configured"] != true {
        return Err("CAMS credentials are not configured".into());
    }
    std::env::var("CDSAPI_KEY")
        .ok()
        .or_else(|| read(&path().ok()?).ok()?.remove("key"))
        .ok_or_else(|| "CAMS credentials are not configured".into())
}

pub fn save(params: Value) -> Result<Value, String> {
    let key = params["key"].as_str().unwrap_or_default().trim();
    if !valid_key(key) {
        return Err("请输入有效的 ADS Personal Access Token，不要粘贴整段配置。".into());
    }
    if status()["can_save"] != true {
        return Err(
            "环境变量正在覆盖 ADS 配置，请先修改 CDSAPI_KEY / CDSAPI_URL，再重新启动。".into(),
        );
    }
    let operation = || -> Result<(), String> {
        let path = path()?;
        if fs::symlink_metadata(&path).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err("symlink ADS configuration is not writable".into());
        }
        let mut config = read(&path)?;
        config.insert("url".into(), ADS_URL.into());
        config.insert("key".into(), key.into());
        config.insert("verify".into(), "1".into());
        let content = config
            .into_iter()
            .map(|(name, value)| format!("{name}: {value}\n"))
            .collect::<String>();
        if content.len() as u64 > MAX_BYTES {
            return Err("ADS configuration exceeds limit".into());
        }
        let parent = path.parent().ok_or("invalid ADS configuration path")?;
        let mut temporary = tempfile::Builder::new()
            .prefix(".firesky-ads-")
            .tempfile_in(parent)
            .map_err(|_| "cannot create temporary ADS configuration")?;
        temporary
            .write_all(content.as_bytes())
            .and_then(|_| temporary.as_file().sync_all())
            .map_err(|_| "cannot write ADS configuration")?;
        temporary
            .persist(&path)
            .map_err(|_| "cannot replace ADS configuration")?;
        Ok(())
    };
    operation()
        .map_err(|_| "无法安全保存 CAMS 配置，请检查配置文件格式、位置和写入权限。".to_string())?;
    Ok(status())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_without_accepting_configuration_as_key() {
        assert!(valid_key("token-._:+/=="));
        assert!(!valid_key("key: token"));
        assert!(!valid_key("secret\ninjected"));
        assert!(!valid_key(""));
    }

    #[test]
    fn flat_file_parser_rejects_ambiguous_fields() {
        let mut file = tempfile::NamedTempFile::new().unwrap();
        writeln!(file, "url: {ADS_URL}\nkey: test\nverify: 1").unwrap();
        assert_eq!(read(file.path()).unwrap()["key"], "test");
        writeln!(file, "key: duplicate").unwrap();
        assert!(read(file.path()).is_err());
    }
}
