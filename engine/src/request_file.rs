//! Bounded JSON request snapshots, shared by offline preparation workflows.
use crate::tensor_bundle::SourceIdentity;
use serde::de::DeserializeOwned;
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::Path;

pub fn read<T: DeserializeOwned>(path: &Path, limit: u64) -> Result<(T, SourceIdentity), String> {
    if !std::fs::symlink_metadata(path)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_file()
    {
        return Err("request must be a regular file, not a symlink".into());
    }
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path).map_err(|e| e.to_string())?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.len() > limit {
        return Err(format!(
            "request exceeds {limit} bytes or is not a regular file"
        ));
    }
    let mut bytes = Vec::new();
    file.take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > limit {
        return Err(format!("request exceeds {limit} bytes"));
    }
    let source = SourceIdentity {
        bytes: bytes.len() as u64,
        sha256: Sha256::digest(&bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
    };
    Ok((
        serde_json::from_slice(&bytes).map_err(|e| e.to_string())?,
        source,
    ))
}
