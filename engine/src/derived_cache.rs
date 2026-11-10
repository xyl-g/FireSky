//! Content-addressed, optional native model-frame cache. Never caches CAMS,
//! scores, observers, or reports; complete profile children remain in outputs.
use crate::{
    assessment::{Geometry, Model},
    cache_lock::Lock,
    grib::EcCodes,
    tensor_bundle::{source_identity, BundleWriter, F32Bundle, SourceIdentity},
};
use firesky_native::{derivation, gfs};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
    time::Instant,
};

const KIND: &str = "native-derived-frame-v1";
fn default_timeout() -> f64 {
    30.0
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Config {
    pub directory: Option<PathBuf>,
    #[serde(default = "default_timeout")]
    pub lock_timeout_seconds: f64,
}
impl Config {
    pub(crate) fn resolve(&mut self, base: &Path) -> Result<(), String> {
        if !self.lock_timeout_seconds.is_finite()
            || !(0.0..=3600.0).contains(&self.lock_timeout_seconds)
            || self
                .directory
                .as_ref()
                .is_some_and(|p| p.as_os_str().is_empty())
        {
            return Err("invalid derived cache directory or lock timeout".into());
        }
        if let Some(path) = &mut self.directory {
            if path.is_relative() {
                let base = if base.as_os_str().is_empty() {
                    Path::new(".")
                } else {
                    base
                };
                *path = base.canonicalize().map_err(|e| e.to_string())?.join(&path);
            }
        }
        Ok(())
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Metadata {
    key: String,
    cells: usize,
    pressure_levels: Vec<i32>,
    boundary_semantics: String,
    ec_proxy_coverage: Option<f64>,
    ec_native_grid_shape: Option<[usize; 2]>,
}
pub(crate) struct Data {
    pub latitudes: Vec<f32>,
    pub longitudes: Vec<f32>,
    pub surface: Vec<f32>,
    pub derived: derivation::DerivedGrid,
    pub boundary_semantics: String,
    pub ec_proxy_coverage: Option<f64>,
    pub ec_native_grid_shape: Option<[usize; 2]>,
}
impl Data {
    fn validate(&self, model: Model) -> Result<(), String> {
        let cells = self.latitudes.len();
        let levels = &self.derived.levels;
        if cells == 0
            || cells > derivation::MAX_CELLS
            || self.longitudes.len() != cells
            || self.surface.len() != cells
            || self.derived.columns.len() != cells
            || levels.is_empty()
            || levels.len() > gfs::PRESSURE_LEVELS.len()
            || levels.windows(2).any(|w| w[0] >= w[1])
            || levels.iter().any(|v| !gfs::PRESSURE_LEVELS.contains(v))
            || self.derived.profiles.len() != cells * levels.len()
            || !matches!(
                self.boundary_semantics.as_str(),
                "instantaneous" | "average-envelope-not-instantaneous" | "unknown"
            )
            || self
                .surface
                .iter()
                .any(|v| !v.is_finite() || !(-500.0..=9000.0).contains(v))
        {
            return Err("invalid derived cache frame geometry/levels/terrain".into());
        }
        match model {
            Model::Gfs
                if self.ec_proxy_coverage.is_none() && self.ec_native_grid_shape.is_none() => {}
            Model::EcmwfBasic
                if self
                    .ec_proxy_coverage
                    .is_some_and(|v| v.is_finite() && (0.0..=1.0).contains(&v))
                    && self.ec_native_grid_shape.is_some_and(|v| {
                        v.iter().all(|n| *n > 0)
                            && v[0]
                                .checked_mul(v[1])
                                .is_some_and(|n| n >= cells && n <= 5_000_000)
                    })
                    && levels.iter().all(|v| crate::ecmwf::LEVELS.contains(v)) => {}
            _ => return Err("invalid derived cache model metadata".into()),
        }
        let mut coordinates = gfs::GfsGrid {
            latitudes: self.latitudes.clone(),
            longitudes: self.longitudes.clone(),
            fields: Default::default(),
        };
        Geometry::canonicalize(&mut coordinates)?;
        if coordinates.latitudes != self.latitudes || coordinates.longitudes != self.longitudes {
            return Err("derived cache coordinates are not canonical".into());
        }
        Ok(())
    }
    fn read(root: &Path, key: &str, source: &SourceIdentity, model: Model) -> Result<Self, String> {
        let bundle = F32Bundle::<Metadata>::open(&root.join("manifest.json"), KIND, source)?;
        let meta = bundle.metadata().clone();
        if meta.key != key
            || meta.cells == 0
            || meta.cells > derivation::MAX_CELLS
            || meta.pressure_levels.is_empty()
            || meta.pressure_levels.len() > gfs::PRESSURE_LEVELS.len()
            || bundle.names().collect::<Vec<_>>()
                != ["columns", "latitudes", "longitudes", "profiles", "surface"]
        {
            return Err("invalid derived cache metadata/arrays".into());
        }
        let n = meta.cells as u64;
        let p = meta.pressure_levels.len() as u64;
        let scalar = |name| -> Result<Vec<f32>, String> {
            Ok(bundle
                .rows::<1>(name, &[n])?
                .into_iter()
                .map(|v| v[0])
                .collect())
        };
        let data = Self {
            latitudes: scalar("latitudes")?,
            longitudes: scalar("longitudes")?,
            surface: scalar("surface")?,
            derived: derivation::DerivedGrid {
                levels: meta.pressure_levels,
                columns: bundle.rows::<36>("columns", &[n, 36])?,
                profiles: bundle.rows::<24>("profiles", &[n, p, 24])?,
            },
            boundary_semantics: meta.boundary_semantics,
            ec_proxy_coverage: meta.ec_proxy_coverage,
            ec_native_grid_shape: meta.ec_native_grid_shape,
        };
        data.validate(model)?;
        bundle.revalidate()?;
        Ok(data)
    }
    fn write(
        &self,
        root: &Path,
        key: &str,
        source_path: &Path,
        source: &SourceIdentity,
    ) -> Result<(), String> {
        let mut writer = BundleWriter::create(
            root,
            KIND,
            source.clone(),
            serde_json::to_value(Metadata {
                key: key.into(),
                cells: self.latitudes.len(),
                pressure_levels: self.derived.levels.clone(),
                boundary_semantics: self.boundary_semantics.clone(),
                ec_proxy_coverage: self.ec_proxy_coverage,
                ec_native_grid_shape: self.ec_native_grid_shape,
            })
            .map_err(|e| e.to_string())?,
        )?;
        let n = self.latitudes.len() as u64;
        for (name, values) in [
            ("latitudes", &self.latitudes),
            ("longitudes", &self.longitudes),
            ("surface", &self.surface),
        ] {
            writer.f32(name, &[n], values.iter().copied())?;
        }
        writer.f32(
            "columns",
            &[n, 36],
            self.derived.columns.iter().flatten().copied(),
        )?;
        writer.f32(
            "profiles",
            &[n, self.derived.levels.len() as u64, 24],
            self.derived.profiles.iter().flatten().copied(),
        )?;
        writer.commit(source_path)?;
        Ok(())
    }
}

pub(crate) struct Session {
    config: Option<Config>,
    decoder: Option<String>,
    fingerprint_ms: f64,
    pub frames: Vec<Value>,
}
impl Session {
    pub(crate) fn new(
        config: Option<Config>,
        base: &Path,
        codes: &EcCodes,
    ) -> Result<Self, String> {
        let mut config = config;
        if let Some(config) = &mut config {
            config.resolve(base)?;
        }
        let start = Instant::now();
        let decoder = config
            .as_ref()
            .map(|_| codes.cache_identity())
            .transpose()?;
        Ok(Self {
            config,
            decoder,
            fingerprint_ms: start.elapsed().as_secs_f64() * 1000.0,
            frames: Vec::new(),
        })
    }
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn frame(
        &mut self,
        source_path: &Path,
        source: &SourceIdentity,
        model: Model,
        cycle: &str,
        hour: u32,
        bbox: Option<[f64; 4]>,
        build: impl FnOnce() -> Result<Data, String>,
    ) -> Result<Data, String> {
        let Some(config) = &self.config else {
            self.frames.push(json!({"hour":hour,"status":"disabled"}));
            return build();
        };
        let start = Instant::now();
        let key_input = json!({
            "contract":KIND,"source":source,"model":model,"cycle_utc":cycle,"hour":hour,"bbox":bbox,
            "engine_source":env!("FIRESKY_ENGINE_SOURCE_ID"),"decoder":self.decoder,
            "architecture":std::env::consts::ARCH,"os":std::env::consts::OS,
        });
        let key: String =
            Sha256::digest(serde_json::to_vec(&key_input).map_err(|e| e.to_string())?)
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
        let root = config
            .directory
            .clone()
            .unwrap_or_else(|| source_path.parent().unwrap().join(".rust-derived-v1"));
        fs::create_dir_all(&root).map_err(|e| e.to_string())?;
        if !fs::symlink_metadata(&root)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_dir()
        {
            return Err("derived cache root must be a nonsymlink directory".into());
        }
        let root = root.canonicalize().map_err(|e| e.to_string())?;
        let _lock = Lock::acquire(
            &root.join(format!("{key}.lock")),
            config.lock_timeout_seconds,
        )
        .map_err(|e| e.replace("GFS cache", "derived cache"))?;
        let target = root.join(&key);
        let existed = match fs::symlink_metadata(&target) {
            Ok(meta) if meta.file_type().is_dir() => true,
            Ok(_) => return Err("derived cache entry must be a nonsymlink directory".into()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
            Err(e) => return Err(e.to_string()),
        };
        let mut invalid_reason = None;
        if existed {
            match Data::read(&target, &key, source, model) {
                Ok(data) => {
                    if source_identity(source_path)? != *source {
                        return Err("derived source changed during cache lookup".into());
                    }
                    self.frames.push(json!({"hour":hour,"status":"hit","key":key,"lookup_ms":start.elapsed().as_secs_f64()*1000.0}));
                    return Ok(data);
                }
                Err(error) => invalid_reason = Some(error),
            }
        }
        let lookup_ms = start.elapsed().as_secs_f64() * 1000.0;
        let start = Instant::now();
        let data = build()?;
        data.validate(model)?;
        let build_ms = start.elapsed().as_secs_f64() * 1000.0;
        let start = Instant::now();
        let staging = tempfile::Builder::new()
            .prefix(".derived-pending-")
            .tempdir_in(&root)
            .map_err(|e| e.to_string())?;
        let bundle = staging.path().join("bundle");
        data.write(&bundle, &key, source_path, source)?;
        sync_dir(&bundle)?;
        if existed {
            // Preserve malformed entries and unknown additions for inspection.
            // Never recursively delete a directory we did not create.
            let quarantine = tempfile::Builder::new()
                .prefix(".derived-invalid-")
                .tempdir_in(&root)
                .map_err(|e| e.to_string())?;
            let quarantine = quarantine.keep();
            fs::rename(&target, quarantine.join(&key)).map_err(|e| e.to_string())?;
            sync_dir(&quarantine)?;
        }
        fs::rename(&bundle, &target).map_err(|e| e.to_string())?;
        sync_dir(&root)?;
        self.frames.push(json!({
            "hour":hour,"status":if existed {"rebuilt"} else {"miss"},"key":key,"source":source,
            "lookup_ms":lookup_ms,"build_ms":build_ms,"publish_ms":start.elapsed().as_secs_f64()*1000.0,
            "invalid_reason":invalid_reason,
        }));
        Ok(data)
    }
    pub(crate) fn metadata(&self) -> Value {
        json!({"contract":KIND,"enabled":self.config.is_some(),"engine_source":env!("FIRESKY_ENGINE_SOURCE_ID"),
            "decoder":self.decoder,"decoder_fingerprint_ms":self.fingerprint_ms,
            "hits":self.frames.iter().filter(|f| f["status"]=="hit").count(),"frames":self.frames})
    }
}
fn sync_dir(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    fs::File::open(path)
        .and_then(|file| file.sync_all())
        .map_err(|e| e.to_string())?;
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn data() -> Data {
        Data {
            latitudes: vec![0., 0., 1., 1.],
            longitudes: vec![100., 101., 100., 101.],
            surface: vec![20.; 4],
            derived: derivation::DerivedGrid {
                levels: vec![300],
                columns: vec![[f32::from_bits(0x7fc01234); 36]; 4],
                profiles: vec![[f32::from_bits(0x7fc05678); 24]; 4],
            },
            boundary_semantics: "unknown".into(),
            ec_proxy_coverage: None,
            ec_native_grid_shape: None,
        }
    }
    fn session(root: &Path) -> Session {
        Session {
            config: Some(Config {
                directory: Some(root.to_owned()),
                lock_timeout_seconds: 0.,
            }),
            decoder: Some("test-decoder".into()),
            fingerprint_ms: 0.,
            frames: Vec::new(),
        }
    }
    #[test]
    fn relative_directory_resolution_is_idempotent_and_does_not_create_it() {
        let cwd = std::env::current_dir().unwrap();
        let temp = tempfile::tempdir_in(&cwd).unwrap();
        let base = temp.path().strip_prefix(&cwd).unwrap();
        let mut config = Config {
            directory: Some("cache".into()),
            lock_timeout_seconds: 0.,
        };
        config.resolve(base).unwrap();
        let expected = temp.path().canonicalize().unwrap().join("cache");
        assert_eq!(config.directory.as_ref().unwrap(), &expected);
        config.resolve(base).unwrap();
        assert_eq!(config.directory.as_ref().unwrap(), &expected);
        assert!(!expected.exists());
    }
    #[test]
    fn request_filename_without_a_parent_uses_current_directory() {
        let mut config = Config {
            directory: Some("cache".into()),
            lock_timeout_seconds: 0.,
        };
        config.resolve(Path::new("")).unwrap();
        assert_eq!(
            config.directory.unwrap(),
            std::env::current_dir()
                .unwrap()
                .canonicalize()
                .unwrap()
                .join("cache")
        );
    }
    #[test]
    fn hit_streams_original_float32_nan_payloads() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("raw");
        fs::write(&source, b"source").unwrap();
        let identity = source_identity(&source).unwrap();
        let mut cache = session(&temp.path().join("cache"));
        cache
            .frame(
                &source,
                &identity,
                Model::Gfs,
                "2026-11-09T00:00:00Z",
                9,
                None,
                || Ok(data()),
            )
            .unwrap();
        let hit = cache
            .frame(
                &source,
                &identity,
                Model::Gfs,
                "2026-11-09T00:00:00Z",
                9,
                None,
                || panic!("unexpected decode"),
            )
            .unwrap();
        assert_eq!(hit.derived.columns[0][0].to_bits(), 0x7fc01234);
        assert_eq!(hit.derived.profiles[0][0].to_bits(), 0x7fc05678);
        assert_eq!(cache.frames[1]["status"], "hit");
    }
    #[test]
    fn changed_source_cannot_publish_and_staging_is_cleaned() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("raw");
        fs::write(&source, b"source").unwrap();
        let identity = source_identity(&source).unwrap();
        let root = temp.path().join("cache");
        let mut cache = session(&root);
        let result = cache.frame(
            &source,
            &identity,
            Model::Gfs,
            "2026-11-09T00:00:00Z",
            9,
            None,
            || {
                fs::write(&source, b"changed").unwrap();
                Ok(data())
            },
        );
        assert!(result.is_err());
        assert!(fs::read_dir(root).unwrap().all(|e| e
            .unwrap()
            .path()
            .extension()
            .is_some_and(|e| e == "lock")));
    }
    #[test]
    fn failed_build_never_becomes_a_hit() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("raw");
        fs::write(&source, b"source").unwrap();
        let identity = source_identity(&source).unwrap();
        let mut cache = session(&temp.path().join("cache"));
        assert!(cache
            .frame(
                &source,
                &identity,
                Model::Gfs,
                "2026-11-09T00:00:00Z",
                9,
                None,
                || Err("failed decode".into())
            )
            .is_err());
        cache
            .frame(
                &source,
                &identity,
                Model::Gfs,
                "2026-11-09T00:00:00Z",
                9,
                None,
                || Ok(data()),
            )
            .unwrap();
        assert_eq!(cache.frames[0]["status"], "miss");
    }
    #[test]
    fn cycle_hour_bbox_and_decoder_have_independent_keys() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("raw");
        fs::write(&source, b"source").unwrap();
        let identity = source_identity(&source).unwrap();
        let mut cache = session(&temp.path().join("cache"));
        for (cycle, hour, bbox, decoder) in [
            ("2026-11-09T00:00:00Z", 9, None, "one"),
            ("2026-11-09T06:00:00Z", 9, None, "one"),
            ("2026-11-09T00:00:00Z", 12, None, "one"),
            ("2026-11-09T00:00:00Z", 9, Some([100., 101., 1., 0.]), "one"),
            ("2026-11-09T00:00:00Z", 9, None, "two"),
        ] {
            cache.decoder = Some(decoder.into());
            cache
                .frame(&source, &identity, Model::Gfs, cycle, hour, bbox, || {
                    Ok(data())
                })
                .unwrap();
        }
        assert!(cache.frames.iter().all(|frame| frame["status"] == "miss"));
        let keys: std::collections::BTreeSet<_> = cache
            .frames
            .iter()
            .map(|f| f["key"].as_str().unwrap())
            .collect();
        assert_eq!(keys.len(), 5);
    }
}
