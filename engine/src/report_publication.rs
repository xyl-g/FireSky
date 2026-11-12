//! Content-addressed HTML reports, independent of the legacy file registry.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::Path,
};

const MAX_HTML: u64 = 16 * 1024 * 1024;
fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|value| format!("{value:02x}"))
        .collect()
}

pub fn publish(directory: &Path, report: &mut Value) -> Result<(), String> {
    let now: chrono::DateTime<chrono::Utc> = std::time::SystemTime::now().into();
    let html = crate::html_report::render(report, &crate::time_contract::iso(now))?;
    if html.len() as u64 > MAX_HTML {
        return Err("native HTML report exceeds size limit".into());
    }
    fs::create_dir_all(directory.join("native-html")).map_err(|e| e.to_string())?;
    let id = format!("{}.html", digest(html.as_bytes()));
    let path = directory.join("native-html").join(&id);
    let mut temporary =
        tempfile::NamedTempFile::new_in(path.parent().unwrap()).map_err(|e| e.to_string())?;
    temporary
        .write_all(html.as_bytes())
        .map_err(|e| e.to_string())?;
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    temporary.persist(&path).map_err(|e| e.to_string())?;
    report["html_report_path"] = json!(path);
    report["html_report_url"] = json!(format!("/api/v1/firesky/reports/{id}"));
    Ok(())
}

pub fn read(directory: &Path, id: &str) -> Result<String, String> {
    let hash = id.strip_suffix(".html").ok_or("invalid HTML report id")?;
    if hash.len() != 64
        || !hash
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
    {
        return Err("invalid HTML report id".into());
    }
    let path = directory.join("native-html").join(id);
    let metadata = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_HTML {
        return Err("invalid native HTML report file".into());
    }
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options.open(&path).map_err(|e| e.to_string())?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_HTML {
        return Err("invalid native HTML report file".into());
    }
    let mut bytes = Vec::new();
    file.take(MAX_HTML + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_HTML || digest(&bytes) != hash {
        return Err("native HTML report content identity mismatch".into());
    }
    String::from_utf8(bytes).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_traversal_and_modified_reports() {
        let directory = tempfile::tempdir().unwrap();
        assert!(read(directory.path(), "../index.html").is_err());
        let bytes = b"<html>report</html>";
        let id = format!("{}.html", digest(bytes));
        fs::create_dir(directory.path().join("native-html")).unwrap();
        let path = directory.path().join("native-html").join(&id);
        fs::write(&path, bytes).unwrap();
        assert_eq!(read(directory.path(), &id).unwrap(), "<html>report</html>");
        fs::write(path, b"changed").unwrap();
        assert!(read(directory.path(), &id).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn refuses_symbolic_links() {
        let directory = tempfile::tempdir().unwrap();
        let bytes = b"<html>report</html>";
        let id = format!("{}.html", digest(bytes));
        fs::create_dir(directory.path().join("native-html")).unwrap();
        let source = directory.path().join("source.html");
        fs::write(&source, bytes).unwrap();
        std::os::unix::fs::symlink(source, directory.path().join("native-html").join(&id)).unwrap();
        assert!(read(directory.path(), &id).is_err());
    }
}
