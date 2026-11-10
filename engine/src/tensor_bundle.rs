//! Bounded, manifest-committed transport, separate from forecast cache formats.

use npyz::WriterBuilder;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::thread::{self, JoinHandle};
use std::time::Instant;

pub const MAX_BUNDLE_BYTES: u64 = 1024 * 1024 * 1024;
pub const MAX_MANIFEST_BYTES: usize = 256 * 1024;
const MAX_ARRAYS: usize = 128;
const MAX_PENDING_SYNCS: usize = 2;
const WRITE_BUFFER_BYTES: usize = 64 * 1024;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceIdentity {
    pub bytes: u64,
    pub sha256: String,
}

pub fn source_identity(path: &Path) -> Result<SourceIdentity, String> {
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    file_identity(&mut file)
}

fn file_identity(file: &mut File) -> Result<SourceIdentity, String> {
    file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file() {
        return Err("tensor source must be a regular file".into());
    }
    let mut hash = Sha256::new();
    let mut bytes = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        bytes += count as u64;
        hash.update(&buffer[..count]);
    }
    if metadata.len() != bytes || file.metadata().map_err(|e| e.to_string())?.len() != bytes {
        return Err("tensor source changed while hashing".into());
    }
    Ok(SourceIdentity {
        bytes,
        sha256: hash
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect(),
    })
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ArrayEntry {
    file: String,
    dtype: String,
    shape: Vec<u64>,
    bytes: u64,
    sha256: String,
}

fn unique_arrays<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<BTreeMap<String, ArrayEntry>, D::Error> {
    struct Unique;
    impl<'de> serde::de::Visitor<'de> for Unique {
        type Value = BTreeMap<String, ArrayEntry>;
        fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            f.write_str("unique array descriptors")
        }
        fn visit_map<M: serde::de::MapAccess<'de>>(
            self,
            mut input: M,
        ) -> Result<Self::Value, M::Error> {
            let mut arrays = BTreeMap::new();
            while let Some((name, entry)) = input.next_entry::<String, ArrayEntry>()? {
                if arrays.insert(name, entry).is_some() {
                    return Err(serde::de::Error::custom("duplicate tensor array key"));
                }
            }
            Ok(arrays)
        }
    }
    deserializer.deserialize_map(Unique)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReadManifest<M = Value> {
    schema: String,
    producer: String,
    kind: String,
    source: SourceIdentity,
    metadata: M,
    #[serde(deserialize_with = "unique_arrays")]
    arrays: BTreeMap<String, ArrayEntry>,
}

fn regular_file(path: &Path) -> Result<File, String> {
    if !fs::symlink_metadata(path)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_file()
    {
        return Err("tensor input must be a regular file, not a symlink".into());
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path).map_err(|e| e.to_string())?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("tensor input changed file type".into());
    }
    Ok(file)
}

pub(crate) fn regular_identity(path: &Path) -> Result<SourceIdentity, String> {
    file_identity(&mut regular_file(path)?)
}

pub(crate) struct VerifiedFiles {
    pub manifest: SourceIdentity,
    pub source: SourceIdentity,
    pub metadata: Value,
    pub files: BTreeMap<String, SourceIdentity>,
    pub shapes: BTreeMap<String, Vec<u64>>,
}

/// Verify complete payloads and NPY headers without materializing the tensors.
pub(crate) fn verify_files(path: &Path, kind: &str) -> Result<VerifiedFiles, String> {
    let root = path.parent().ok_or("bundle manifest has no parent")?;
    if path.file_name().and_then(|v| v.to_str()) != Some("manifest.json")
        || !fs::symlink_metadata(root)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_dir()
    {
        return Err("invalid snapshot bundle location".into());
    }
    let (manifest, identity): (ReadManifest, _) =
        crate::request_file::read(path, MAX_MANIFEST_BYTES as u64)?;
    if manifest.schema != "firesky.tensor-bundle.v1"
        || manifest.producer != concat!("firesky-engine/", env!("CARGO_PKG_VERSION"))
        || manifest.kind != kind
        || !manifest.metadata.is_object()
        || !valid_sha(&manifest.source.sha256)
        || manifest.arrays.is_empty()
        || manifest.arrays.len() > MAX_ARRAYS
    {
        return Err("invalid snapshot manifest contract".into());
    }
    let mut files = BTreeMap::from([("manifest.json".into(), identity.clone())]);
    let mut shapes = BTreeMap::new();
    let mut total = identity.bytes;
    for (name, entry) in manifest.arrays {
        let count = entry
            .shape
            .iter()
            .try_fold(1u64, |n, &d| if d == 0 { None } else { n.checked_mul(d) })
            .ok_or("invalid snapshot dimensions")?;
        let size = match entry.dtype.as_str() {
            "<f4" => 4,
            "<f8" => 8,
            _ => return Err("invalid snapshot dtype".into()),
        };
        if name.is_empty()
            || name.len() > 64
            || !name
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
            || entry.file != format!("{name}.npy")
            || entry.shape.is_empty()
            || entry.shape.len() > 4
            || count.checked_mul(size).is_none_or(|v| v > MAX_BUNDLE_BYTES)
            || entry.bytes > MAX_BUNDLE_BYTES
            || !valid_sha(&entry.sha256)
        {
            return Err("invalid snapshot array descriptor".into());
        }
        total = total
            .checked_add(entry.bytes)
            .ok_or("snapshot byte count overflow")?;
        if total > MAX_BUNDLE_BYTES {
            return Err("snapshot bundle exceeds bound".into());
        }
        let (_, actual) = open_float_array(root, &entry)?;
        files.insert(entry.file, actual);
        shapes.insert(name, entry.shape);
    }
    if regular_identity(path)? != identity {
        return Err("snapshot manifest changed".into());
    }
    Ok(VerifiedFiles {
        manifest: identity,
        source: manifest.source,
        metadata: manifest.metadata,
        files,
        shapes,
    })
}

fn valid_sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn open_float_array(root: &Path, entry: &ArrayEntry) -> Result<(File, SourceIdentity), String> {
    let mut file = regular_file(&root.join(&entry.file))?;
    if file.metadata().map_err(|e| e.to_string())?.len() != entry.bytes {
        return Err("tensor array size mismatch".into());
    }
    let identity = file_identity(&mut file)?;
    if identity.bytes != entry.bytes || identity.sha256 != entry.sha256 {
        return Err("tensor array checksum or size mismatch".into());
    }
    // Bound the NPY header before handing it to the standard parser.
    file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let mut magic = [0; 8];
    file.read_exact(&mut magic).map_err(|e| e.to_string())?;
    if &magic[..6] != b"\x93NUMPY" {
        return Err("invalid NPY magic".into());
    }
    let (prefix_len, header_len) = match &magic[6..] {
        [1, 0] => {
            let mut length = [0; 2];
            file.read_exact(&mut length).map_err(|e| e.to_string())?;
            (10, u16::from_le_bytes(length) as u64)
        }
        [2, 0] => {
            let mut length = [0; 4];
            file.read_exact(&mut length).map_err(|e| e.to_string())?;
            (12, u32::from_le_bytes(length) as u64)
        }
        _ => return Err("unsupported NPY version".into()),
    };
    let header_end = prefix_len + header_len;
    let item_size = if entry.dtype == "<f4" { 4 } else { 8 };
    let count = entry.shape.iter().product::<u64>();
    if header_len > 4096 || header_end + count * item_size != entry.bytes {
        return Err("NPY header or payload extent exceeds bounds".into());
    }
    file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let array = npyz::NpyFile::new(BufReader::with_capacity(64 * 1024, &mut file))
        .map_err(|e| e.to_string())?;
    if array.shape() != entry.shape
        || array.order() != npyz::Order::C
        || array.dtype().descr() != format!("'{}'", entry.dtype)
    {
        return Err("tensor NPY header disagrees with manifest".into());
    }
    drop(array);
    file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    Ok((file, identity))
}

fn read_float_array(root: &Path, entry: &ArrayEntry) -> Result<Vec<f64>, String> {
    let (mut file, identity) = open_float_array(root, entry)?;
    let array = npyz::NpyFile::new(BufReader::with_capacity(64 * 1024, &mut file))
        .map_err(|e| e.to_string())?;
    let values = if entry.dtype == "<f4" {
        array
            .into_vec::<f32>()
            .map_err(|e| e.to_string())?
            .into_iter()
            .map(f64::from)
            .collect()
    } else {
        array.into_vec::<f64>().map_err(|e| e.to_string())?
    };
    if file_identity(&mut file)? != identity {
        return Err("tensor array changed while reading".into());
    }
    Ok(values)
}

/// Strict float32 cache reader. Rows are streamed into their final allocation,
/// avoiding a second full-size flat pressure-profile buffer.
pub(crate) struct F32Bundle<M> {
    path: PathBuf,
    identity: SourceIdentity,
    manifest: ReadManifest<M>,
}
impl<M: serde::de::DeserializeOwned> F32Bundle<M> {
    pub(crate) fn open(path: &Path, kind: &str, source: &SourceIdentity) -> Result<Self, String> {
        let mut file = regular_file(path)?;
        if file.metadata().map_err(|e| e.to_string())?.len() > MAX_MANIFEST_BYTES as u64 {
            return Err("cache manifest exceeds bound".into());
        }
        let identity = file_identity(&mut file)?;
        file.rewind().map_err(|e| e.to_string())?;
        let mut bytes = Vec::new();
        (&mut file)
            .take(MAX_MANIFEST_BYTES as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|e| e.to_string())?;
        if bytes.len() > MAX_MANIFEST_BYTES {
            return Err("cache manifest exceeds bound".into());
        }
        let manifest: ReadManifest<M> =
            serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        if manifest.schema != "firesky.tensor-bundle.v1"
            || manifest.producer != concat!("firesky-engine/", env!("CARGO_PKG_VERSION"))
            || manifest.kind != kind
            || &manifest.source != source
            || manifest.arrays.is_empty()
            || manifest.arrays.len() > MAX_ARRAYS
        {
            return Err("cache manifest contract mismatch".into());
        }
        let mut total = identity.bytes;
        for (name, entry) in &manifest.arrays {
            let count = entry
                .shape
                .iter()
                .try_fold(1u64, |n, &d| if d == 0 { None } else { n.checked_mul(d) })
                .ok_or("invalid cache array dimensions")?;
            if name.is_empty()
                || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
                || entry.file != format!("{name}.npy")
                || entry.dtype != "<f4"
                || entry.shape.is_empty()
                || entry.shape.len() > 4
                || !valid_sha(&entry.sha256)
                || count.checked_mul(4).is_none_or(|v| v > MAX_BUNDLE_BYTES)
                || entry.bytes > MAX_BUNDLE_BYTES
            {
                return Err("invalid float32 cache descriptor".into());
            }
            total = total
                .checked_add(entry.bytes)
                .ok_or("cache size overflow")?;
        }
        if total > MAX_BUNDLE_BYTES || file_identity(&mut file)? != identity {
            return Err("cache manifest changed or exceeds size bound".into());
        }
        Ok(Self {
            path: path.to_owned(),
            identity,
            manifest,
        })
    }
    pub(crate) fn metadata(&self) -> &M {
        &self.manifest.metadata
    }
    pub(crate) fn names(&self) -> impl Iterator<Item = &str> {
        self.manifest.arrays.keys().map(String::as_str)
    }
    pub(crate) fn rows<const W: usize>(
        &self,
        name: &str,
        shape: &[u64],
    ) -> Result<Vec<[f32; W]>, String> {
        let entry = self
            .manifest
            .arrays
            .get(name)
            .ok_or("missing cache array")?;
        if entry.shape != shape || W == 0 {
            return Err("cache array shape mismatch".into());
        }
        let count = entry.shape.iter().product::<u64>() as usize;
        if !count.is_multiple_of(W) {
            return Err("cache row width mismatch".into());
        }
        let (mut file, identity) = open_float_array(self.path.parent().unwrap(), entry)?;
        let array = npyz::NpyFile::new(BufReader::new(&mut file)).map_err(|e| e.to_string())?;
        let mut values = array.data::<f32>().map_err(|e| e.to_string())?;
        let mut rows = Vec::with_capacity(count / W);
        for _ in 0..count / W {
            let mut row = [0.0; W];
            for value in &mut row {
                *value = values
                    .next()
                    .ok_or("short cache array")?
                    .map_err(|e| e.to_string())?;
            }
            rows.push(row);
        }
        if values.next().is_some() {
            return Err("long cache array".into());
        }
        drop(values);
        if file_identity(&mut file)? != identity {
            return Err("cache array changed during read".into());
        }
        Ok(rows)
    }
    pub(crate) fn revalidate(&self) -> Result<(), String> {
        if file_identity(&mut regular_file(&self.path)?)? != self.identity {
            return Err("cache manifest changed during read".into());
        }
        for entry in self.manifest.arrays.values() {
            let identity = file_identity(&mut regular_file(
                &self.path.parent().unwrap().join(&entry.file),
            )?)?;
            if identity.bytes != entry.bytes || identity.sha256 != entry.sha256 {
                return Err("cache array changed during snapshot".into());
            }
        }
        Ok(())
    }
}

/// A complete independently committed pressure-profile frame. Loading stays
/// float32; callers bound the number of simultaneously resident frames.
pub(crate) struct ProfileTensor {
    path: PathBuf,
    identity: SourceIdentity,
    entry: ArrayEntry,
    metadata: Value,
}

impl ProfileTensor {
    pub(crate) fn open(
        path: &Path,
        identity: SourceIdentity,
        cells: usize,
        levels: usize,
    ) -> Result<Self, String> {
        if cells == 0 || cells > 500_000 || levels == 0 || levels > 256 {
            return Err("invalid profile frame shape".into());
        }
        let root = path.parent().ok_or("profile manifest has no parent")?;
        if path.file_name().and_then(|v| v.to_str()) != Some("manifest.json")
            || !fs::symlink_metadata(root)
                .map_err(|e| e.to_string())?
                .file_type()
                .is_dir()
            || identity.bytes > MAX_MANIFEST_BYTES as u64
        {
            return Err("invalid profile manifest location or bound".into());
        }
        let mut file = regular_file(path)?;
        if file.metadata().map_err(|e| e.to_string())?.len() != identity.bytes {
            return Err("profile manifest size mismatch".into());
        }
        let mut bytes = Vec::new();
        (&mut file)
            .take(MAX_MANIFEST_BYTES as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|e| e.to_string())?;
        let actual = SourceIdentity {
            bytes: bytes.len() as u64,
            sha256: Sha256::digest(&bytes)
                .iter()
                .map(|v| format!("{v:02x}"))
                .collect(),
        };
        if actual != identity {
            return Err("profile manifest checksum mismatch".into());
        }
        let mut manifest: ReadManifest =
            serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        if manifest.schema != "firesky.tensor-bundle.v1"
            || !manifest.producer.starts_with("firesky-engine/")
            || manifest.kind != "regional-profiles-v1"
            || !manifest.metadata.is_object()
            || !valid_sha(&manifest.source.sha256)
            || manifest.arrays.len() != 1
        {
            return Err("incompatible profile manifest".into());
        }
        let entry = manifest
            .arrays
            .remove("profiles")
            .ok_or("missing profiles array")?;
        if entry.file != "profiles.npy"
            || entry.dtype != "<f4"
            || entry.shape != [cells as u64, levels as u64, 24]
            || !valid_sha(&entry.sha256)
            || entry.bytes > MAX_BUNDLE_BYTES
            || entry.bytes < cells as u64 * levels as u64 * 24 * 4
        {
            return Err("invalid profile tensor descriptor".into());
        }
        Ok(Self {
            path: path.to_owned(),
            identity,
            entry,
            metadata: manifest.metadata,
        })
    }

    pub(crate) fn validate_axis(
        &self,
        index: usize,
        hour: f64,
        levels: &[f32],
    ) -> Result<(), String> {
        if self.metadata["frame_index"].as_u64() != Some(index as u64)
            || self.metadata["hour"].as_f64() != Some(hour)
            || self.metadata["profile_fields"].as_u64() != Some(24)
            || self.metadata["pressure_levels"]
                .as_array()
                .is_none_or(|axis| {
                    axis.len() != levels.len()
                        || axis.iter().zip(levels).any(|(a, &b)| {
                            a.as_f64().filter(|v| v.is_finite()).map(|v| v as f32) != Some(b)
                        })
                })
        {
            return Err("profile frame does not match its declared time/pressure axes".into());
        }
        Ok(())
    }

    pub(crate) fn load(&self) -> Result<Vec<f32>, String> {
        let (mut file, identity) = open_float_array(self.path.parent().unwrap(), &self.entry)?;
        let values = npyz::NpyFile::new(BufReader::with_capacity(64 * 1024, &mut file))
            .map_err(|e| e.to_string())?
            .into_vec::<f32>()
            .map_err(|e| e.to_string())?;
        if file_identity(&mut file)? != identity {
            return Err("profile tensor changed while reading".into());
        }
        self.revalidate_manifest()?;
        Ok(values)
    }

    /// Verify the entire file, but allocate/decode only sorted requested columns.
    pub(crate) fn load_cells(&self, cells: &[usize]) -> Result<Vec<f32>, String> {
        let total_cells = self.entry.shape[0] as usize;
        let fields = self.entry.shape[1] as usize * 24;
        if cells.is_empty()
            || cells.len() > total_cells
            || cells.iter().any(|&c| c >= total_cells)
            || cells.windows(2).any(|w| w[0] >= w[1])
        {
            return Err(
                "sparse profile cells must be nonempty, bounded and strictly increasing".into(),
            );
        }
        let (mut file, identity) = open_float_array(self.path.parent().unwrap(), &self.entry)?;
        let mut values = Vec::with_capacity(cells.len() * fields);
        {
            let mut reader = npyz::NpyFile::new(BufReader::with_capacity(64 * 1024, &mut file))
                .map_err(|e| e.to_string())?
                .data::<f32>()
                .map_err(|e| e.to_string())?;
            for &cell in cells {
                reader
                    .seek_to((cell * fields) as u64)
                    .map_err(|e| e.to_string())?;
                for _ in 0..fields {
                    values.push(
                        reader
                            .next()
                            .ok_or("truncated profile column")?
                            .map_err(|e| e.to_string())?,
                    );
                }
            }
        }
        if file_identity(&mut file)? != identity {
            return Err("profile tensor changed while reading selected columns".into());
        }
        self.revalidate_manifest()?;
        Ok(values)
    }

    fn revalidate_manifest(&self) -> Result<(), String> {
        if file_identity(&mut regular_file(&self.path)?)? != self.identity {
            return Err("profile manifest changed".into());
        }
        Ok(())
    }

    pub(crate) fn revalidate(&self) -> Result<(), String> {
        self.revalidate_manifest()?;
        let actual = file_identity(&mut regular_file(
            &self.path.parent().unwrap().join(&self.entry.file),
        )?)?;
        if actual.bytes != self.entry.bytes || actual.sha256 != self.entry.sha256 {
            return Err("profile tensor changed".into());
        }
        Ok(())
    }
}

pub struct CoordinateGrid {
    pub latitudes: Vec<f64>,
    pub longitudes: Vec<f64>,
    pub source_manifest: SourceIdentity,
    pub source: SourceIdentity,
    path: PathBuf,
    entries: BTreeMap<String, ArrayEntry>,
}

impl CoordinateGrid {
    /// Validate and read only coordinate arrays; model payloads remain unloaded.
    pub fn read(path: &Path, max_cells: usize) -> Result<Self, String> {
        let root = path.parent().ok_or("tensor manifest has no parent")?;
        if path.file_name().and_then(|v| v.to_str()) != Some("manifest.json")
            || !fs::symlink_metadata(root)
                .map_err(|e| e.to_string())?
                .file_type()
                .is_dir()
        {
            return Err("invalid tensor manifest location".into());
        }
        let mut file = regular_file(path)?;
        if file.metadata().map_err(|e| e.to_string())?.len() > MAX_MANIFEST_BYTES as u64 {
            return Err("tensor manifest exceeds size limit".into());
        }
        let mut bytes = Vec::new();
        (&mut file)
            .take(MAX_MANIFEST_BYTES as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|e| e.to_string())?;
        if bytes.len() > MAX_MANIFEST_BYTES {
            return Err("tensor manifest exceeds size limit".into());
        }
        let manifest: ReadManifest = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        if manifest.schema != "firesky.tensor-bundle.v1"
            || !manifest.producer.starts_with("firesky-engine/")
            || manifest.kind.is_empty()
            || !manifest.metadata.is_object()
            || !valid_sha(&manifest.source.sha256)
            || manifest.arrays.is_empty()
            || manifest.arrays.len() > MAX_ARRAYS
        {
            return Err("incompatible tensor manifest".into());
        }
        let mut total = 0_u64;
        for (name, entry) in &manifest.arrays {
            let count = entry
                .shape
                .iter()
                .try_fold(1_u64, |a, b| a.checked_mul(*b))
                .ok_or("tensor shape overflow")?;
            let item_size = match entry.dtype.as_str() {
                "<f4" => 4,
                "<f8" => 8,
                _ => return Err("invalid tensor dtype".into()),
            };
            if name.is_empty()
                || name.len() > 64
                || !name
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
                || entry.file != format!("{name}.npy")
                || !valid_sha(&entry.sha256)
                || entry.shape.is_empty()
                || entry.shape.len() > 3
                || entry.shape.iter().any(|&n| n == 0 || n > 5_000_000)
                || count.checked_mul(item_size).is_none_or(|n| n > entry.bytes)
                || entry.bytes == 0
                || entry.bytes > MAX_BUNDLE_BYTES
            {
                return Err("invalid tensor array descriptor".into());
            }
            total = total
                .checked_add(entry.bytes)
                .ok_or("tensor size overflow")?;
            if total > MAX_BUNDLE_BYTES {
                return Err("tensor bundle exceeds size limit".into());
            }
        }
        let lat = manifest
            .arrays
            .get("latitudes")
            .ok_or("missing target latitudes")?;
        let lon = manifest
            .arrays
            .get("longitudes")
            .ok_or("missing target longitudes")?;
        if lat.shape.len() != 1 || lat.shape[0] > max_cells as u64 || lon.shape != lat.shape {
            return Err("invalid target coordinate shape or cell limit".into());
        }
        let latitudes = read_float_array(root, lat)?;
        let longitudes = read_float_array(root, lon)?;
        if latitudes.iter().chain(&longitudes).any(|v| !v.is_finite()) {
            return Err("target coordinates must be finite".into());
        }
        let source_manifest = SourceIdentity {
            bytes: bytes.len() as u64,
            sha256: Sha256::digest(&bytes)
                .iter()
                .map(|v| format!("{v:02x}"))
                .collect(),
        };
        let result = Self {
            latitudes,
            longitudes,
            source_manifest,
            source: manifest.source,
            path: path.to_path_buf(),
            entries: manifest.arrays,
        };
        result.revalidate()?;
        Ok(result)
    }

    pub fn revalidate(&self) -> Result<(), String> {
        if file_identity(&mut regular_file(&self.path)?)? != self.source_manifest {
            return Err("target manifest changed".into());
        }
        for name in ["latitudes", "longitudes"] {
            let entry = &self.entries[name];
            let identity = file_identity(&mut regular_file(
                &self.path.parent().unwrap().join(&entry.file),
            )?)?;
            if identity.bytes != entry.bytes || identity.sha256 != entry.sha256 {
                return Err("target coordinates changed".into());
            }
        }
        Ok(())
    }
}

#[derive(Serialize)]
struct Manifest {
    schema: &'static str,
    producer: &'static str,
    kind: String,
    source: SourceIdentity,
    metadata: Value,
    arrays: BTreeMap<String, ArrayEntry>,
}

pub struct BundleWriter {
    root: PathBuf,
    created: Vec<PathBuf>,
    total_bytes: u64,
    manifest: Manifest,
    committed: bool,
    timings: WriteTimings,
    pending_syncs: VecDeque<JoinHandle<Result<f64, String>>>,
    sync_error: Option<String>,
}

#[derive(Clone, Default, Serialize)]
pub struct WriteTimings {
    pub serialization_ms: f64,
    pub flush_sync_ms: f64,
    pub array_hash_ms: f64,
    /// Sum of worker wall times, not an additive caller critical-path phase.
    pub sync_worker_ms: f64,
    pub source_manifest_ms: f64,
}

impl BundleWriter {
    pub fn metadata_mut(&mut self) -> &mut Value {
        &mut self.manifest.metadata
    }

    /// Companions share the bundle byte budget and owned-file rollback.
    pub(crate) fn json_artifact(
        &mut self,
        name: &str,
        value: &impl Serialize,
    ) -> Result<SourceIdentity, String> {
        self.artifact(name, "json", |writer| {
            serde_json::to_writer(writer, value).map_err(|e| e.to_string())
        })
    }

    pub(crate) fn html_artifact(
        &mut self,
        name: &str,
        value: &str,
    ) -> Result<SourceIdentity, String> {
        self.artifact(name, "html", |writer| {
            writer
                .write_all(value.as_bytes())
                .map_err(|e| e.to_string())
        })
    }

    fn artifact(
        &mut self,
        name: &str,
        extension: &str,
        serialize: impl FnOnce(&mut LimitedWriter<BufWriter<File>>) -> Result<(), String>,
    ) -> Result<SourceIdentity, String> {
        if name.is_empty()
            || name.len() > 64
            || name == "manifest"
            || !name.bytes().all(|b| b.is_ascii_lowercase() || b == b'_')
        {
            return Err("invalid artifact name".into());
        }
        let path = self.root.join(format!("{name}.{extension}"));
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| e.to_string())?;
        self.created.push(path.clone());
        let mut writer = LimitedWriter {
            inner: BufWriter::with_capacity(WRITE_BUFFER_BYTES, file),
            remaining: MAX_BUNDLE_BYTES.saturating_sub(self.total_bytes),
        };
        let result = (|| {
            serialize(&mut writer)?;
            writer.flush().map_err(|e| e.to_string())?;
            writer
                .inner
                .into_inner()
                .map_err(|e| e.to_string())?
                .sync_all()
                .map_err(|e| e.to_string())?;
            let identity = source_identity(&path)?;
            self.total_bytes += identity.bytes;
            Ok(identity)
        })();
        // A caught serialization error must not permit publishing a partial file.
        if let Err(error) = &result {
            self.sync_error
                .get_or_insert_with(|| format!("{extension} artifact failed: {error}"));
        }
        result
    }
    /// The destination must not exist, including an empty directory or symlink.
    pub fn create(
        root: &Path,
        kind: &str,
        source: SourceIdentity,
        metadata: Value,
    ) -> Result<Self, String> {
        if !root.is_absolute() {
            return Err("tensor output_dir must be absolute".into());
        }
        fs::create_dir(root).map_err(|e| format!("cannot create tensor output directory: {e}"))?;
        Ok(Self {
            root: root.to_path_buf(),
            created: Vec::new(),
            total_bytes: 0,
            manifest: Manifest {
                schema: "firesky.tensor-bundle.v1",
                producer: concat!("firesky-engine/", env!("CARGO_PKG_VERSION")),
                kind: kind.into(),
                source,
                metadata,
                arrays: BTreeMap::new(),
            },
            committed: false,
            timings: WriteTimings::default(),
            pending_syncs: VecDeque::new(),
            sync_error: None,
        })
    }

    fn join_next_sync(&mut self) -> Result<(), String> {
        let Some(job) = self.pending_syncs.pop_front() else {
            return Ok(());
        };
        let start = Instant::now();
        let result = job
            .join()
            .map_err(|_| "tensor sync worker panicked".to_string());
        self.timings.flush_sync_ms += start.elapsed().as_secs_f64() * 1000.0;
        match result.and_then(|v| v) {
            Ok(ms) => {
                self.timings.sync_worker_ms += ms;
                Ok(())
            }
            Err(error) => {
                self.sync_error.get_or_insert_with(|| error.clone());
                Err(error)
            }
        }
    }

    fn finish_syncs(&mut self) -> Result<(), String> {
        // Join every owned worker even when one fails, before any cleanup.
        while !self.pending_syncs.is_empty() {
            let _ = self.join_next_sync();
        }
        self.sync_error.clone().map_or(Ok(()), Err)
    }

    pub fn f32(
        &mut self,
        name: &str,
        shape: &[u64],
        values: impl IntoIterator<Item = f32>,
    ) -> Result<(), String> {
        self.array(name, shape, values, "<f4")
    }

    pub fn f64(
        &mut self,
        name: &str,
        shape: &[u64],
        values: impl IntoIterator<Item = f64>,
    ) -> Result<(), String> {
        self.array(name, shape, values, "<f8")
    }

    fn array<T: npyz::AutoSerialize>(
        &mut self,
        name: &str,
        shape: &[u64],
        values: impl IntoIterator<Item = T>,
        dtype: &str,
    ) -> Result<(), String> {
        let start = Instant::now();
        if name.is_empty()
            || name.len() > 64
            || !name
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
            || self.manifest.arrays.contains_key(name)
            || self.manifest.arrays.len() >= MAX_ARRAYS
        {
            return Err("invalid or duplicate tensor array name".into());
        }
        let count = shape
            .iter()
            .try_fold(1_u64, |count, &size| count.checked_mul(size))
            .ok_or("tensor shape overflow")?;
        let payload_bytes = count
            .checked_mul(std::mem::size_of::<T>() as u64)
            .ok_or("tensor byte size overflow")?;
        if shape.is_empty()
            || shape.len() > 3
            || shape.iter().any(|&size| size == 0 || size > 5_000_000)
            || payload_bytes > MAX_BUNDLE_BYTES
            || self
                .total_bytes
                .saturating_add(payload_bytes)
                .saturating_add(4096)
                > MAX_BUNDLE_BYTES
        {
            return Err("tensor bundle size or shape limit exceeded".into());
        }
        // npyz's primitive float default is little-endian on supported targets.
        if cfg!(target_endian = "big") {
            return Err("tensor bundle requires a little-endian target".into());
        }
        let filename = format!("{name}.npy");
        let path = self.root.join(&filename);
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| e.to_string())?;
        self.created.push(path.clone());
        let mut file = BufWriter::with_capacity(WRITE_BUFFER_BYTES, file);
        {
            let mut writer = npyz::WriteOptions::new()
                .default_dtype()
                .shape(shape)
                .writer(&mut file)
                .begin_nd()
                .map_err(|e| e.to_string())?;
            // Bound even an incorrectly implemented iterator before it reaches disk.
            let mut written = 0;
            for value in values {
                if written >= count {
                    return Err("tensor iterator exceeds its shape".into());
                }
                writer.push(&value).map_err(|e| e.to_string())?;
                written += 1;
            }
            if written != count {
                return Err("tensor iterator does not fill its shape".into());
            }
            writer.finish().map_err(|e| e.to_string())?;
        }
        self.timings.serialization_ms += start.elapsed().as_secs_f64() * 1000.0;
        let start = Instant::now();
        file.flush().map_err(|e| e.to_string())?;
        let file = file.into_inner().map_err(|e| e.to_string())?;
        self.timings.flush_sync_ms += start.elapsed().as_secs_f64() * 1000.0;
        if self.pending_syncs.len() >= MAX_PENDING_SYNCS {
            self.join_next_sync()?;
        }
        // Hash reads see the fully flushed data while sync completes in a
        // bounded worker. Publication still waits for every successful sync.
        let job = thread::Builder::new()
            .name("tensor-sync".into())
            .spawn(move || {
                let start = Instant::now();
                file.sync_all().map_err(|e| e.to_string())?;
                Ok(start.elapsed().as_secs_f64() * 1000.0)
            })
            .map_err(|e| {
                let message = e.to_string();
                self.sync_error.get_or_insert_with(|| message.clone());
                message
            })?;
        self.pending_syncs.push_back(job);
        let start = Instant::now();
        let identity = source_identity(&path)?;
        self.timings.array_hash_ms += start.elapsed().as_secs_f64() * 1000.0;
        self.total_bytes = self.total_bytes.saturating_add(identity.bytes);
        if self.total_bytes > MAX_BUNDLE_BYTES {
            return Err("tensor bundle size limit exceeded".into());
        }
        self.manifest.arrays.insert(
            name.into(),
            ArrayEntry {
                file: filename,
                dtype: dtype.into(),
                shape: shape.to_vec(),
                bytes: identity.bytes,
                sha256: identity.sha256,
            },
        );
        Ok(())
    }

    pub fn commit(self, source_path: &Path) -> Result<PathBuf, String> {
        self.commit_profiled(source_path).map(|(path, _)| path)
    }

    pub fn commit_profiled(
        mut self,
        source_path: &Path,
    ) -> Result<(PathBuf, WriteTimings), String> {
        self.finish_syncs()?;
        let start = Instant::now();
        if source_identity(source_path)? != self.manifest.source {
            return Err("tensor source changed during decoding".into());
        }
        let bytes = serde_json::to_vec(&self.manifest).map_err(|e| e.to_string())?;
        if self.manifest.arrays.is_empty() || bytes.len() > MAX_MANIFEST_BYTES {
            return Err("invalid tensor manifest size".into());
        }
        // Readers accept only a complete manifest. No existing destination is replaced.
        let pending = self.root.join("manifest.pending");
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&pending)
            .map_err(|e| e.to_string())?;
        self.created.push(pending.clone());
        file.write_all(&bytes).map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        drop(file);
        let path = self.root.join("manifest.json");
        fs::rename(pending, &path).map_err(|e| e.to_string())?;
        self.timings.source_manifest_ms = start.elapsed().as_secs_f64() * 1000.0;
        self.committed = true;
        Ok((path, std::mem::take(&mut self.timings)))
    }
}

struct LimitedWriter<W> {
    inner: W,
    remaining: u64,
}
impl<W: Write> Write for LimitedWriter<W> {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() as u64 > self.remaining {
            return Err(std::io::Error::other(
                "JSON artifact exceeds bundle byte budget",
            ));
        }
        let count = self.inner.write(bytes)?;
        self.remaining -= count as u64;
        Ok(count)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.inner.flush()
    }
}

impl Drop for BundleWriter {
    fn drop(&mut self) {
        let _ = self.finish_syncs();
        if !self.committed {
            for file in &self.created {
                let _ = fs::remove_file(file);
            }
            // Never recursively remove an unexpected file placed by another actor.
            let _ = fs::remove_dir(&self.root);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);

    fn directory() -> PathBuf {
        std::env::temp_dir().join(format!(
            "firesky-bundle-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ))
    }

    #[test]
    fn writes_npy_with_nonfinite_values_and_manifest() {
        let root = directory();
        let source = root.with_extension("source");
        fs::write(&source, b"input").unwrap();
        let mut writer = BundleWriter::create(
            &root,
            "test",
            source_identity(&source).unwrap(),
            serde_json::json!({}),
        )
        .unwrap();
        writer
            .f32("values", &[2, 2], [1.0, f32::NAN, f32::INFINITY, -0.0])
            .unwrap();
        assert!(!root.join("manifest.json").exists());
        writer.commit(&source).unwrap();
        let bytes = fs::read(root.join("values.npy")).unwrap();
        let array = npyz::NpyFile::new(&bytes[..]).unwrap();
        assert_eq!(array.shape(), &[2, 2]);
        let values = array.into_vec::<f32>().unwrap();
        assert!(values[1].is_nan());
        assert_eq!(values[2], f32::INFINITY);
        assert_eq!(values[3].to_bits(), (-0.0_f32).to_bits());
        fs::remove_dir_all(root).unwrap();
        fs::remove_file(source).unwrap();
    }

    #[test]
    fn profile_frame_is_float32_bounded_and_revalidated() {
        let root = directory();
        let source = root.with_extension("source");
        fs::write(&source, b"model").unwrap();
        let mut writer = BundleWriter::create(
            &root,
            "regional-profiles-v1",
            source_identity(&source).unwrap(),
            serde_json::json!({
                "frame_index":0,"hour":24,"pressure_levels":[249.9_f32],"profile_fields":24,
            }),
        )
        .unwrap();
        let values = (0..48).map(|i| if i == 7 { f32::NAN } else { i as f32 });
        writer.f32("profiles", &[2, 1, 24], values).unwrap();
        let path = writer.commit(&source).unwrap();
        let identity = source_identity(&path).unwrap();
        let frame = ProfileTensor::open(&path, identity.clone(), 2, 1).unwrap();
        frame.validate_axis(0, 24.0, &[249.9]).unwrap();
        assert!(frame.validate_axis(1, 24.0, &[249.9]).is_err());
        assert!(frame.validate_axis(0, 23.0, &[249.9]).is_err());
        assert!(frame.validate_axis(0, 24.0, &[250.0]).is_err());
        let values = frame.load().unwrap();
        assert_eq!(values.len(), 48);
        assert!(values[7].is_nan());
        assert_eq!(values[47], 47.0);
        let sparse = frame.load_cells(&[0, 1]).unwrap();
        assert!(values
            .iter()
            .zip(sparse)
            .all(|(a, b)| a.to_bits() == b.to_bits()));
        assert_eq!(frame.load_cells(&[1]).unwrap(), values[24..]);
        for cells in [&[][..], &[2], &[1, 0], &[0, 0]] {
            assert!(frame.load_cells(cells).is_err());
        }
        assert!(ProfileTensor::open(&path, identity.clone(), 3, 1).is_err());
        assert!(ProfileTensor::open(&path, identity.clone(), 2, 257).is_err());
        let mut wrong = identity;
        wrong.sha256.replace_range(..1, "z");
        assert!(ProfileTensor::open(&path, wrong, 2, 1).is_err());
        let payload = root.join("profiles.npy");
        let mut bytes = fs::read(&payload).unwrap();
        let end = bytes.len() - 1;
        bytes[end] ^= 1;
        fs::write(&payload, &bytes).unwrap();
        assert!(frame.revalidate().is_err());
        assert!(frame.load().is_err());
        // Corruption outside the requested column is still rejected.
        assert!(frame.load_cells(&[0]).is_err());
        fs::remove_dir_all(root).unwrap();
        fs::remove_file(source).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn profile_reader_rejects_symlinks_and_rehashed_bad_headers() {
        let root = directory();
        let source = root.with_extension("source");
        fs::write(&source, b"model").unwrap();
        let mut writer = BundleWriter::create(
            &root,
            "regional-profiles-v1",
            source_identity(&source).unwrap(),
            serde_json::json!({}),
        )
        .unwrap();
        writer.f32("profiles", &[1, 1, 24], [0.0; 24]).unwrap();
        let path = writer.commit(&source).unwrap();
        let frame = ProfileTensor::open(&path, source_identity(&path).unwrap(), 1, 1).unwrap();
        let payload = root.join("profiles.npy");
        let bytes = fs::read(&payload).unwrap();
        fs::remove_file(&payload).unwrap();
        std::os::unix::fs::symlink(&source, &payload).unwrap();
        assert!(frame.load().is_err());
        fs::remove_file(&payload).unwrap();
        let mut corrupt = bytes;
        corrupt[8..10].copy_from_slice(&u16::MAX.to_le_bytes());
        fs::write(&payload, corrupt).unwrap();
        let mut manifest: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        manifest["arrays"]["profiles"]["sha256"] =
            serde_json::json!(source_identity(&payload).unwrap().sha256);
        fs::write(&path, serde_json::to_vec(&manifest).unwrap()).unwrap();
        let frame = ProfileTensor::open(&path, source_identity(&path).unwrap(), 1, 1).unwrap();
        assert!(frame.load().unwrap_err().contains("header"));
        fs::remove_dir_all(root).unwrap();
        fs::remove_file(source).unwrap();
    }

    #[test]
    fn failed_shape_or_changed_source_does_not_publish() {
        for change_source in [false, true] {
            let root = directory();
            let source = root.with_extension("source");
            fs::write(&source, b"before").unwrap();
            let mut writer = BundleWriter::create(
                &root,
                "test",
                source_identity(&source).unwrap(),
                serde_json::json!({}),
            )
            .unwrap();
            if change_source {
                writer.f32("x", &[1], [0.0]).unwrap();
                fs::write(&source, b"after!").unwrap();
                assert!(writer.commit(&source).is_err());
            } else {
                assert!(writer.f32("x", &[1], [0.0, 1.0]).is_err());
                drop(writer);
            }
            assert!(!root.exists());
            fs::remove_file(source).unwrap();
        }
    }

    #[test]
    fn f64_coordinates_are_exact_and_revalidation_detects_changes() {
        let root = directory();
        let source = root.with_extension("source");
        fs::write(&source, b"model").unwrap();
        let mut writer = BundleWriter::create(
            &root,
            "coordinates",
            source_identity(&source).unwrap(),
            serde_json::json!({}),
        )
        .unwrap();
        writer
            .f64("latitudes", &[2], [1.00000000001, -0.0])
            .unwrap();
        writer.f32("longitudes", &[2], [101.0, 102.0]).unwrap();
        let path = writer.commit(&source).unwrap();
        let grid = CoordinateGrid::read(&path, 2).unwrap();
        assert_eq!(grid.latitudes[0].to_bits(), 1.00000000001f64.to_bits());
        assert_eq!(grid.latitudes[1].to_bits(), (-0.0f64).to_bits());
        assert_eq!(grid.longitudes, [101.0, 102.0]);
        assert!(CoordinateGrid::read(&path, 1).is_err());
        fs::write(root.join("longitudes.npy"), b"changed").unwrap();
        assert!(grid.revalidate().is_err());
        assert!(CoordinateGrid::read(&path, 2).is_err());
        fs::remove_dir_all(root).unwrap();
        fs::remove_file(source).unwrap();
    }

    #[test]
    fn failed_sync_never_publishes_and_joins_all_workers_before_cleanup() {
        use std::sync::{atomic::AtomicBool, Arc};
        for failure in 0..3 {
            let root = directory();
            let source = root.with_extension("source");
            fs::write(&source, b"input").unwrap();
            let mut writer = BundleWriter::create(
                &root,
                "test",
                source_identity(&source).unwrap(),
                serde_json::json!({}),
            )
            .unwrap();
            writer.f64("values", &[1], [1.0]).unwrap();
            if failure != 0 {
                writer.pending_syncs.push_back(thread::spawn(move || {
                    assert_ne!(failure, 2, "injected sync worker panic");
                    Err("injected fsync failure".into())
                }));
                assert!(writer.join_next_sync().is_ok());
                assert!(writer.join_next_sync().is_err());
                // An observed error must poison publication even after its
                // worker has already been joined by an earlier operation.
                assert!(writer.pending_syncs.is_empty());
            }
            let finished = Arc::new(AtomicBool::new(false));
            let done = finished.clone();
            let array_path = root.join("values.npy");
            writer.pending_syncs.push_back(thread::spawn(move || {
                thread::sleep(std::time::Duration::from_millis(10));
                assert!(array_path.is_file(), "cleanup raced a live worker");
                done.store(true, Ordering::SeqCst);
                Ok(0.0)
            }));
            if failure != 0 {
                assert!(writer.commit(&source).is_err());
            } else {
                drop(writer);
            }
            assert!(finished.load(Ordering::SeqCst));
            assert!(!root.exists());
            fs::remove_file(source).unwrap();
        }
    }

    #[test]
    fn bounds_pending_sync_workers_across_many_arrays() {
        let root = directory();
        let source = root.with_extension("source");
        fs::write(&source, b"input").unwrap();
        let mut writer = BundleWriter::create(
            &root,
            "test",
            source_identity(&source).unwrap(),
            serde_json::json!({}),
        )
        .unwrap();
        for index in 0..8 {
            writer
                .f32(&format!("array{index}"), &[2], [0.0, 1.0])
                .unwrap();
            assert!(writer.pending_syncs.len() <= MAX_PENDING_SYNCS);
            assert!(!root.join("manifest.json").exists());
        }
        writer.commit(&source).unwrap();
        assert!(root.join("manifest.json").is_file());
        fs::remove_dir_all(root).unwrap();
        fs::remove_file(source).unwrap();
    }

    #[test]
    fn refuses_existing_destination_and_unbounded_shape() {
        let root = directory();
        fs::create_dir(&root).unwrap();
        let identity = SourceIdentity {
            bytes: 0,
            sha256: String::new(),
        };
        assert!(BundleWriter::create(&root, "test", identity.clone(), Value::Null).is_err());
        fs::remove_dir(&root).unwrap();
        let mut writer = BundleWriter::create(&root, "test", identity, Value::Null).unwrap();
        assert!(writer.f32("x", &[u64::MAX, 2], []).is_err());
        assert!(writer.f32("../x", &[1], [0.0]).is_err());
        drop(writer);
        assert!(!root.exists());
    }

    #[test]
    fn json_companion_is_hashed_budgeted_and_published_with_parent() {
        let root = directory();
        let source = root.with_extension("source");
        fs::write(&source, b"input").unwrap();
        let mut writer = BundleWriter::create(
            &root,
            "frames",
            source_identity(&source).unwrap(),
            Value::Null,
        )
        .unwrap();
        writer.f32("x", &[1], [1.0]).unwrap();
        let before = writer.total_bytes;
        let value = serde_json::json!({"results":[{"name":"observer"}]});
        let identity = writer.json_artifact("forecast", &value).unwrap();
        assert_eq!(writer.total_bytes, before + identity.bytes);
        assert_eq!(
            source_identity(&root.join("forecast.json")).unwrap().sha256,
            identity.sha256
        );
        writer
            .metadata_mut()
            .clone_from(&serde_json::json!({"forecast":identity}));
        assert!(!root.join("manifest.json").exists());
        writer.commit(&source).unwrap();
        assert_eq!(
            serde_json::from_slice::<Value>(&fs::read(root.join("forecast.json")).unwrap())
                .unwrap(),
            value
        );
        fs::remove_dir_all(root).unwrap();
        fs::remove_file(source).unwrap();
    }

    #[test]
    fn oversized_json_poisons_commit_and_joins_workers_before_rollback() {
        use std::sync::{atomic::AtomicBool, Arc};
        let root = directory();
        let source = root.with_extension("source");
        fs::write(&source, b"input").unwrap();
        let mut writer = BundleWriter::create(
            &root,
            "frames",
            source_identity(&source).unwrap(),
            Value::Null,
        )
        .unwrap();
        writer.f32("x", &[1], [1.0]).unwrap();
        writer.total_bytes = MAX_BUNDLE_BYTES - 5;
        assert!(writer
            .json_artifact("forecast", &serde_json::json!({"too":"large"}))
            .is_err());
        assert!(fs::metadata(root.join("forecast.json")).unwrap().len() <= 5);
        let finished = Arc::new(AtomicBool::new(false));
        let done = finished.clone();
        let payload = root.join("x.npy");
        writer.pending_syncs.push_back(thread::spawn(move || {
            thread::sleep(std::time::Duration::from_millis(10));
            assert!(payload.is_file());
            done.store(true, Ordering::SeqCst);
            Ok(0.0)
        }));
        assert!(writer.commit(&source).is_err());
        assert!(finished.load(Ordering::SeqCst));
        assert!(!root.exists());
        fs::remove_file(source).unwrap();
    }

    #[test]
    fn json_names_and_cleanup_preserve_unowned_files() {
        let root = directory();
        let mut writer = BundleWriter::create(
            &root,
            "frames",
            SourceIdentity {
                bytes: 0,
                sha256: String::new(),
            },
            Value::Null,
        )
        .unwrap();
        for name in ["", "../outside", "manifest", "Mixed", "with0"] {
            assert!(writer.json_artifact(name, &Value::Null).is_err());
        }
        fs::write(root.join("forecast.json"), b"unowned").unwrap();
        assert!(writer.json_artifact("forecast", &Value::Null).is_err());
        writer.json_artifact("owned", &Value::Null).unwrap();
        drop(writer);
        assert_eq!(fs::read(root.join("forecast.json")).unwrap(), b"unowned");
        assert!(!root.join("owned.json").exists());
        assert!(!root.join("manifest.json").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn html_is_hashed_and_rollback_preserves_unowned_report() {
        let root = directory();
        let mut writer = BundleWriter::create(
            &root,
            "export",
            SourceIdentity {
                bytes: 0,
                sha256: String::new(),
            },
            Value::Null,
        )
        .unwrap();
        let html = "<!doctype html><p>report &amp; result</p>";
        let identity = writer.html_artifact("owned", html).unwrap();
        assert_eq!(identity, source_identity(&root.join("owned.html")).unwrap());
        assert_eq!(writer.total_bytes, html.len() as u64);
        assert_eq!(fs::read_to_string(root.join("owned.html")).unwrap(), html);
        fs::write(root.join("report.html"), b"user-owned").unwrap();
        assert!(writer.html_artifact("report", html).is_err());
        drop(writer);
        assert!(!root.join("owned.html").exists());
        assert!(!root.join("manifest.json").exists());
        assert_eq!(fs::read(root.join("report.html")).unwrap(), b"user-owned");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn partial_html_poisoned_commit_removes_owned_files() {
        let root = directory();
        let source = root.with_extension("source");
        fs::write(&source, b"input").unwrap();
        let mut writer = BundleWriter::create(
            &root,
            "export",
            source_identity(&source).unwrap(),
            Value::Null,
        )
        .unwrap();
        writer.f32("x", &[1], [1.0]).unwrap();
        writer.total_bytes = MAX_BUNDLE_BYTES - 5;
        assert!(writer.html_artifact("report", "<!doctype html>").is_err());
        assert!(fs::metadata(root.join("report.html")).unwrap().len() <= 5);
        assert!(writer.commit(&source).is_err());
        assert!(!root.exists());
        fs::remove_file(source).unwrap();
    }
}
