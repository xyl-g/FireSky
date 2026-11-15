//! Small, read-only ecCodes adapter. No Python interpreter or Python bindings.

use crate::native::gfs::{self, GfsGrid};
use chrono::{DateTime, Datelike, Timelike, Utc};
use libloading::Library;
use serde::Serialize;
use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_double, c_int, c_long, c_void};
use std::path::Path;
use std::ptr;

type NewHandle = unsafe extern "C" fn(*mut c_void, *mut libc::FILE, *mut c_int) -> *mut c_void;
type DeleteHandle = unsafe extern "C" fn(*mut c_void) -> c_int;
type GetLong = unsafe extern "C" fn(*const c_void, *const c_char, *mut c_long) -> c_int;
type GetSize = unsafe extern "C" fn(*const c_void, *const c_char, *mut usize) -> c_int;
type GetString =
    unsafe extern "C" fn(*const c_void, *const c_char, *mut c_char, *mut usize) -> c_int;
type GetDoubles =
    unsafe extern "C" fn(*const c_void, *const c_char, *mut c_double, *mut usize) -> c_int;
type ErrorMessage = unsafe extern "C" fn(c_int) -> *const c_char;
type GetMessage = unsafe extern "C" fn(*const c_void, *mut *const c_void, *mut usize) -> c_int;

/// Cumulative decoded-value budget for a full (non-subset) GFS frame. Kept
/// at the historical bound: callers that need whole wide domains use the
/// subset path instead.
const GFS_FULL_DECODE_POINT_BUDGET: usize = 10_000_000;
/// Cumulative retained-cell budget for subset decodes. The China production
/// envelope keeps ~72k cells across ~150 retained fields per frame (~11M
/// values); the old 10M full-file budget rejected it even though every
/// decoded value outside the bbox is discarded. Per-message grids stay
/// bounded by `max_points` (5M), so this only raises the cumulative ceiling.
const GFS_RETAINED_DECODE_POINT_BUDGET: usize = 40_000_000;
/// Per-message grid bound for subset decodes. NOMADS serves wide envelopes
/// (e.g. a 2000 km point domain spans ~660k cells at 0.25°); the retained
/// cells after subsetting stay bounded by the caller's `max_points`, so the
/// full-message bound only needs to admit the widest served grid (global
/// 0.25° = 1440×721 ≈ 1.04M cells).
const GFS_SUBSET_MESSAGE_POINT_LIMIT: usize = 1_500_000;

/// Expand a wrapped subset bbox into its as-served 0..360 equivalent when the
/// message longitudes live in that convention. NOMADS files downloaded with
/// rightlon > 180 store longitudes in 0..360 even though the request bbox was
/// normalized to [-180,180]; without this expansion the subset filter would
/// silently drop the 180..360 cells (or keep the complementary span).
fn subset_for_message(mut subset: [f64; 4], longitudes: &[f32]) -> [f64; 4] {
    let west = subset[0];
    let east = subset[1];
    if west > east && longitudes.iter().any(|&lon| lon > 180.0) {
        subset[1] = east + 360.0;
    }
    subset
}

#[cfg(test)]
mod subset_tests {
    use super::subset_for_message;

    #[test]
    fn wrapped_subset_expands_only_for_0_360_messages() {
        let wrapped = [99.25, -168.75, 48.5, 12.25];
        let served_0_360 = [100.0_f32, 150.0, 185.0, 191.0];
        assert_eq!(
            subset_for_message(wrapped, &served_0_360),
            [99.25, 191.25, 48.5, 12.25]
        );
        let normalized = [100.0_f32, 150.0, -175.0, -169.0];
        assert_eq!(subset_for_message(wrapped, &normalized), wrapped);
        let ordinary = [99.25, 141.25, 48.5, 12.25];
        assert_eq!(subset_for_message(ordinary, &served_0_360), ordinary);
    }
}

fn loaded_module_path(address: *const c_void) -> Result<std::path::PathBuf, String> {
    #[cfg(unix)]
    {
        let mut info: libc::Dl_info = unsafe { std::mem::zeroed() };
        if unsafe { libc::dladdr(address, &mut info) } == 0 || info.dli_fname.is_null() {
            return Err("cannot identify loaded decoder module".into());
        }
        let path = unsafe { CStr::from_ptr(info.dli_fname) }
            .to_str()
            .map_err(|e| e.to_string())?;
        Path::new(path).canonicalize().map_err(|e| e.to_string())
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStringExt;
        #[link(name = "kernel32")]
        extern "system" {
            fn GetModuleHandleExW(flags: u32, name: *const u16, module: *mut *mut c_void) -> i32;
            fn GetModuleFileNameW(module: *mut c_void, name: *mut u16, size: u32) -> u32;
        }
        let mut module = ptr::null_mut();
        // FROM_ADDRESS | UNCHANGED_REFCOUNT; the owning Library remains alive.
        if unsafe { GetModuleHandleExW(0x6, address.cast(), &mut module) } == 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        let mut buffer = vec![0u16; 32768];
        let size = unsafe { GetModuleFileNameW(module, buffer.as_mut_ptr(), buffer.len() as u32) }
            as usize;
        if size == 0 || size >= buffer.len() {
            return Err("cannot resolve loaded decoder DLL path".into());
        }
        std::path::PathBuf::from(std::ffi::OsString::from_wide(&buffer[..size]))
            .canonicalize()
            .map_err(|e| e.to_string())
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = address;
        Err("loaded decoder module paths are unsupported on this platform".into())
    }
}

pub struct EcCodes {
    new_handle: NewHandle,
    delete_handle: DeleteHandle,
    get_long: GetLong,
    get_size: GetSize,
    get_length: GetSize,
    get_string: GetString,
    get_doubles: GetDoubles,
    error_message: ErrorMessage,
    get_message: GetMessage,
    // Must outlive all function pointers and message handles.
    _library: Library,
}

impl EcCodes {
    /// Bind derived results to the loaded decoder and the actual definitions
    /// and samples. Hash resources on each cache-enabled request, not just
    /// their version string or mutable directory timestamps.
    pub(crate) fn cache_identity(&self) -> Result<String, String> {
        use sha2::{Digest, Sha256};
        let mut hash = Sha256::new();
        let library_path = loaded_module_path(self.new_handle as *const c_void)?;
        let identity = crate::tensor_bundle::source_identity(&library_path)?;
        hash.update(serde_json::to_vec(&identity).map_err(|e| e.to_string())?);
        let mut modules = std::collections::BTreeMap::new();
        modules.insert(library_path.clone(), identity);
        type ResourcePath = unsafe extern "C" fn(*const c_void) -> *const c_char;
        let mut total = 0u64;
        let mut seen = std::collections::BTreeSet::new();
        let mut files = std::collections::BTreeMap::new();
        fn tree(
            path: &Path,
            hash: &mut Sha256,
            total: &mut u64,
            seen: &mut std::collections::BTreeSet<std::path::PathBuf>,
            files: &mut std::collections::BTreeMap<std::path::PathBuf, u64>,
        ) -> Result<(), String> {
            let metadata = std::fs::symlink_metadata(path).map_err(|e| e.to_string())?;
            let name = path.to_str().ok_or("decoder resource path is not UTF-8")?;
            hash.update((name.len() as u64).to_le_bytes());
            hash.update(name);
            if metadata.file_type().is_symlink() {
                tree(
                    &path.canonicalize().map_err(|e| e.to_string())?,
                    hash,
                    total,
                    seen,
                    files,
                )?;
            } else if metadata.file_type().is_dir() {
                if !seen.insert(path.to_owned()) {
                    return Ok(());
                }
                let mut entries = std::fs::read_dir(path)
                    .map_err(|e| e.to_string())?
                    .map(|entry| entry.map(|v| v.path()))
                    .collect::<Result<Vec<_>, _>>()
                    .map_err(|e| e.to_string())?;
                entries.sort();
                for entry in entries {
                    tree(&entry, hash, total, seen, files)?;
                }
            } else if metadata.file_type().is_file() {
                if files.insert(path.to_owned(), metadata.len()).is_none() {
                    *total = total
                        .checked_add(metadata.len())
                        .ok_or("decoder resource size overflow")?;
                }
                if files.len() > 100_000 || *total > 512 * 1024 * 1024 {
                    return Err("decoder resources exceed cache fingerprint bounds".into());
                }
                hash.update(b"regular-file");
            } else {
                return Err(
                    "decoder cache resources must resolve to regular files/directories".into(),
                );
            }
            Ok(())
        }
        for symbol in [
            b"codes_definition_path\0".as_slice(),
            b"codes_samples_path\0".as_slice(),
        ] {
            let function =
                unsafe { self._library.get::<ResourcePath>(symbol) }.map_err(|e| e.to_string())?;
            let pointer = unsafe { function(ptr::null()) };
            if pointer.is_null() {
                return Err("missing decoder resource path".into());
            }
            let paths = unsafe { CStr::from_ptr(pointer) }
                .to_str()
                .map_err(|e| e.to_string())?;
            hash.update(symbol);
            for path in std::env::split_paths(paths) {
                if matches!(path.to_str(), Some("/MEMFS/definitions" | "/MEMFS/samples")) {
                    type MemfsExists = unsafe extern "C" fn(*const c_char) -> c_int;
                    // ecCodes generates all embedded resources into eccodes_memfs.
                    // Fingerprint that actual module, not a version or virtual name.
                    let symbol =
                        unsafe { self._library.get::<MemfsExists>(b"codes_memfs_exists\0") };
                    let dependency;
                    let exists = if let Ok(symbol) = symbol {
                        *symbol
                    } else {
                        #[cfg(windows)]
                        let filename = "eccodes_memfs.dll";
                        #[cfg(target_os = "macos")]
                        let filename = "libeccodes_memfs.dylib";
                        #[cfg(not(any(windows, target_os = "macos")))]
                        let filename = "libeccodes_memfs.so";
                        let adjacent = library_path
                            .parent()
                            .ok_or("decoder has no parent")?
                            .join(filename);
                        dependency =
                            unsafe { Library::new(adjacent) }.map_err(|e| e.to_string())?;
                        *unsafe { dependency.get::<MemfsExists>(b"codes_memfs_exists\0") }
                            .map_err(|e| e.to_string())?
                    };
                    let probe = if path.ends_with("definitions") {
                        "/MEMFS/definitions/boot.def"
                    } else {
                        "/MEMFS/samples/GRIB2.tmpl"
                    };
                    let probe = CString::new(probe).map_err(|e| e.to_string())?;
                    if unsafe { exists(probe.as_ptr()) } == 0 {
                        return Err("decoder advertises unavailable embedded resources".into());
                    }
                    let module = loaded_module_path(exists as *const c_void)?;
                    if !modules.contains_key(&module) {
                        modules.insert(
                            module.clone(),
                            crate::tensor_bundle::source_identity(&module)?,
                        );
                    }
                    hash.update(b"eccodes-embedded-resources-v1");
                    hash.update(path.to_str().unwrap().as_bytes());
                    hash.update(serde_json::to_vec(&modules[&module]).map_err(|e| e.to_string())?);
                    if !path.exists() {
                        continue;
                    }
                }
                tree(
                    &path.canonicalize().map_err(|e| {
                        format!(
                            "cannot fingerprint decoder resource {}: {e}",
                            path.display()
                        )
                    })?,
                    &mut hash,
                    &mut total,
                    &mut seen,
                    &mut files,
                )?;
            }
        }
        // Resource trees contain thousands of small files. Keep every content
        // checksum, but deduplicate resolved targets and hash with bounded I/O
        // workers; folding in path order keeps keys independent of scheduling.
        let files: Vec<_> = files.into_iter().collect();
        let workers = std::thread::available_parallelism()
            .map_or(1, usize::from)
            .min(4);
        std::thread::scope(|scope| -> Result<(), String> {
            let handles: Vec<_> = files
                .chunks(files.len().div_ceil(workers).max(1))
                .map(|chunk| {
                    scope.spawn(move || -> Result<Vec<_>, String> {
                        chunk
                            .iter()
                            .map(|(path, bytes)| {
                                let identity = crate::tensor_bundle::source_identity(path)?;
                                if identity.bytes != *bytes {
                                    return Err(
                                        "decoder resource changed during fingerprinting".into()
                                    );
                                }
                                Ok((path, identity))
                            })
                            .collect()
                    })
                })
                .collect();
            for handle in handles {
                for resource in handle
                    .join()
                    .map_err(|_| "decoder fingerprint worker panicked")??
                {
                    hash.update(serde_json::to_vec(&resource).map_err(|e| e.to_string())?);
                }
            }
            Ok(())
        })?;
        let mut env: Vec<_> = std::env::vars_os()
            .filter(|(name, _)| {
                name.to_string_lossy().starts_with("ECCODES_")
                    || name.to_string_lossy().starts_with("GRIB_")
            })
            .collect();
        env.sort();
        for (name, value) in env {
            let name = name.to_str().ok_or("decoder environment is not UTF-8")?;
            let value = value.to_str().ok_or("decoder environment is not UTF-8")?;
            hash.update((name.len() as u64).to_le_bytes());
            hash.update(name);
            hash.update((value.len() as u64).to_le_bytes());
            hash.update(value);
        }
        Ok(hash.finalize().iter().map(|b| format!("{b:02x}")).collect())
    }

    pub fn load() -> Result<Self, String> {
        let candidates = if let Some(path) = std::env::var_os("FIRESKY_ECCODES_LIBRARY") {
            vec![path]
        } else {
            #[cfg(target_os = "macos")]
            let names = [
                "/opt/homebrew/lib/libeccodes.dylib",
                "/usr/local/lib/libeccodes.dylib",
                "libeccodes.dylib",
            ];
            #[cfg(target_os = "windows")]
            let names = ["eccodes.dll"];
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            let names = ["libeccodes.so", "libeccodes.so.0"];
            names.iter().map(std::ffi::OsString::from).collect()
        };
        let mut errors = Vec::new();
        for candidate in candidates {
            // Loading ecCodes executes trusted native library initialization.
            let loaded = unsafe { Library::new(&candidate) };
            match loaded {
                Ok(library) => return unsafe { Self::from_library(library) },
                Err(error) => errors.push(format!("{}: {error}", candidate.to_string_lossy())),
            }
        }
        Err(format!(
            "ecCodes unavailable; set FIRESKY_ECCODES_LIBRARY to its shared library. {}",
            errors.join("; ")
        ))
    }

    unsafe fn from_library(library: Library) -> Result<Self, String> {
        unsafe fn symbol<T: Copy>(library: &Library, name: &[u8]) -> Result<T, String> {
            library
                .get::<T>(name)
                .map(|value| *value)
                .map_err(|error| error.to_string())
        }
        Ok(Self {
            new_handle: symbol(&library, b"codes_grib_handle_new_from_file\0")?,
            delete_handle: symbol(&library, b"codes_handle_delete\0")?,
            get_long: symbol(&library, b"codes_get_long\0")?,
            get_size: symbol(&library, b"codes_get_size\0")?,
            get_length: symbol(&library, b"codes_get_length\0")?,
            get_string: symbol(&library, b"codes_get_string\0")?,
            get_doubles: symbol(&library, b"codes_get_double_array\0")?,
            error_message: symbol(&library, b"codes_get_error_message\0")?,
            get_message: symbol(&library, b"codes_get_message\0")?,
            _library: library,
        })
    }

    fn check(&self, code: c_int, context: &str) -> Result<(), String> {
        if code == 0 {
            return Ok(());
        }
        let detail = unsafe { CStr::from_ptr((self.error_message)(code)) }.to_string_lossy();
        Err(format!("{context}: ecCodes {code}: {detail}"))
    }

    pub fn decode_file(
        &self,
        path: &Path,
        include_values: bool,
        max_messages: usize,
        max_points: usize,
    ) -> Result<Vec<GribField>, String> {
        if !(1..=4096).contains(&max_messages) || !(1..=5_000_000).contains(&max_points) {
            return Err("max_messages must be 1..4096; max_points must be 1..5000000".into());
        }
        let mut fields = Vec::new();
        let mut total_points = 0usize;
        self.visit_file(path, max_messages, |message| {
            let count = message.size(b"values\0")?;
            total_points = total_points
                .checked_add(count)
                .ok_or("GRIB point count overflow")?;
            if count > max_points || (include_values && total_points > 10_000_000) {
                return Err("GRIB decoded point limit exceeded".into());
            }
            let values = message.values(count)?;
            let missing_count = values.iter().filter(|value| !value.is_finite()).count();
            let (latitudes, longitudes) = if include_values {
                let latitudes = message.array(b"latitudes\0", count)?;
                let longitudes = message.array(b"longitudes\0", count)?;
                if latitudes
                    .iter()
                    .chain(&longitudes)
                    .any(|value| !value.is_finite())
                {
                    return Err("GRIB coordinates contain nonfinite values".into());
                }
                (Some(latitudes), Some(longitudes))
            } else {
                (None, None)
            };
            fields.push(GribField {
                short_name: message.string(b"shortName\0")?,
                type_of_level: message.string(b"typeOfLevel\0")?,
                level: message.long(b"level\0")?,
                data_date: message.long(b"dataDate\0")?,
                data_time: message.long(b"dataTime\0")?,
                end_step: message.long(b"endStep\0")?,
                grid_type: message.string(b"gridType\0")?,
                point_count: count,
                missing_count,
                values: include_values.then(|| {
                    values
                        .into_iter()
                        .map(|v| v.is_finite().then_some(v))
                        .collect()
                }),
                latitudes,
                longitudes,
            });
            Ok(())
        })?;
        if fields.is_empty() {
            return Err("file contains no GRIB messages".into());
        }
        Ok(fields)
    }

    pub fn decode_gfs_file(
        &self,
        path: &Path,
        max_messages: usize,
        max_points: usize,
    ) -> Result<GfsGrid, String> {
        self.decode_gfs_frame(path, max_messages, max_points, None)
    }

    /// Decode only the surface orography field for the terrain prepass.
    ///
    /// Regional files cover whole domains with ~150 kept fields per message;
    /// the full decode's cumulative point budget (10M) is exceeded long before
    /// the orography message arrives. The prepass only samples terrain, so it
    /// reads that single field and stops at the first matching message.
    pub fn decode_gfs_terrain(
        &self,
        path: &Path,
        max_messages: usize,
        max_points: usize,
    ) -> Result<GfsGrid, String> {
        if !(1..=5_000_000).contains(&max_points) {
            return Err("max_points must be 1..5000000".into());
        }
        let mut grid: Option<GfsGrid> = None;
        self.visit_while(path, max_messages, |message| {
            let name = message.string(b"shortName\0")?;
            let level_type = message.string(b"typeOfLevel\0")?;
            let level =
                i32::try_from(message.long(b"level\0")?).map_err(|_| "GFS level out of range")?;
            if level_type != "surface" || level != 0 || name != "orog" {
                return Ok(true);
            }
            let count = message.size(b"values\0")?;
            if count > max_points {
                return Err("GFS decoded point limit exceeded".into());
            }
            let (latitudes, longitudes) = gfs::coordinates(
                &message.array(b"latitudes\0", count)?,
                &message.array(b"longitudes\0", count)?,
            )?;
            let mut terrain = GfsGrid::new(latitudes, longitudes);
            terrain.insert((level_type, level, name), message.values(count)?)?;
            grid = Some(terrain);
            Ok(false)
        })?;
        grid.ok_or_else(|| "GFS GRIB has no surface orography field".into())
    }

    pub fn decode_gfs_frame(
        &self,
        path: &Path,
        max_messages: usize,
        max_points: usize,
        expected: Option<(DateTime<Utc>, u32)>,
    ) -> Result<GfsGrid, String> {
        self.decode_gfs_frame_inner(path, max_messages, max_points, expected, None)
    }

    /// Frame decode restricted to a spatial subset. Regional files cover whole
    /// domains, but the forecast only consumes `bbox`; reading values outside
    /// it wastes the cumulative point budget (10M) on cells that are discarded
    /// by `subset_gfs` immediately after decoding. Coordinates are still read
    /// per message so the shared-grid and cycle/step contracts stay enforced.
    pub fn decode_gfs_frame_subset(
        &self,
        path: &Path,
        max_messages: usize,
        max_points: usize,
        expected: Option<(DateTime<Utc>, u32)>,
        bbox: [f64; 4],
    ) -> Result<GfsGrid, String> {
        self.decode_gfs_frame_inner(path, max_messages, max_points, expected, Some(bbox))
    }

    fn decode_gfs_frame_inner(
        &self,
        path: &Path,
        max_messages: usize,
        max_points: usize,
        expected: Option<(DateTime<Utc>, u32)>,
        subset: Option<[f64; 4]>,
    ) -> Result<GfsGrid, String> {
        if !(1..=5_000_000).contains(&max_points) {
            return Err("max_points must be 1..5000000".into());
        }
        let mut grid: Option<GfsGrid> = None;
        let mut total_points = 0usize;
        let mut cycle = None;
        self.visit_file(path, max_messages, |message| {
            let name = message.string(b"shortName\0")?;
            let level_type = message.string(b"typeOfLevel\0")?;
            let level =
                i32::try_from(message.long(b"level\0")?).map_err(|_| "GFS level out of range")?;
            if !gfs::keep_field(&level_type, level, &name) {
                return Ok(());
            }
            let count = message.size(b"values\0")?;
            // In subset mode the cumulative budget tracks retained cells only;
            // the full-file count would exhaust the budget on wide regional
            // envelopes before the subset is even selected. The per-message
            // full-grid count is still bounded by max_points.
            if subset.is_none() {
                total_points = total_points
                    .checked_add(count)
                    .ok_or("GFS point count overflow")?;
                if count > max_points || total_points > GFS_FULL_DECODE_POINT_BUDGET {
                    return Err("GFS decoded point limit exceeded".into());
                }
            } else if count > GFS_SUBSET_MESSAGE_POINT_LIMIT {
                return Err("GFS decoded point limit exceeded".into());
            }
            let current_cycle = (
                message.long(b"dataDate\0")?,
                message.long(b"dataTime\0")?,
                message.long(b"endStep\0")?,
            );
            if let Some((expected_cycle, hour)) = expected {
                let date = i64::from(expected_cycle.year()) * 10000
                    + i64::from(expected_cycle.month()) * 100
                    + i64::from(expected_cycle.day());
                let clock = i64::from(expected_cycle.hour()) * 100;
                let seconds_per_unit = match message.long(b"stepUnits\0")? {
                    0 => 60,
                    1 => 3600,
                    2 => 86400,
                    10 => 10800,
                    11 => 21600,
                    12 => 43200,
                    13 => 1,
                    _ => return Err("unsupported GFS forecast-step unit".into()),
                };
                if current_cycle.0 != date
                    || current_cycle.1 != clock
                    || current_cycle.2.checked_mul(seconds_per_unit) != Some(i64::from(hour) * 3600)
                {
                    return Err("GFS actual cycle/step does not match the stored request".into());
                }
            }
            if cycle.is_some_and(|cycle| cycle != current_cycle) {
                return Err("GFS messages use different cycles or forecast steps".into());
            }
            cycle = Some(current_cycle);
            let (latitudes, longitudes) = gfs::coordinates(
                &message.array(b"latitudes\0", count)?,
                &message.array(b"longitudes\0", count)?,
            )?;
            if let Some([west, east, north, south]) =
                subset.map(|b| subset_for_message(b, &longitudes))
            {
                let indices: Vec<usize> = latitudes
                    .iter()
                    .zip(&longitudes)
                    .enumerate()
                    .filter_map(|(i, (&lat, &lon))| {
                        (lat <= north as f32
                            && lat >= south as f32
                            && if west <= east {
                                lon >= west as f32 && lon <= east as f32
                            } else {
                                lon >= west as f32 || lon <= east as f32
                            })
                        .then_some(i)
                    })
                    .collect();
                if indices.is_empty() {
                    return Err("GFS stored bbox has no cells".into());
                }
                let kept = indices.len();
                total_points = total_points
                    .checked_add(kept)
                    .ok_or("GFS point count overflow")?;
                if kept > max_points || total_points > GFS_RETAINED_DECODE_POINT_BUDGET {
                    return Err("GFS decoded point limit exceeded".into());
                }
                let sel = |v: &[f32]| indices.iter().map(|&i| v[i]).collect::<Vec<_>>();
                let latitudes = sel(&latitudes);
                // Normalize retained longitudes back to [-180,180] so grids
                // from 0..360 files share the product coordinate convention.
                let longitudes: Vec<f32> = sel(&longitudes)
                    .into_iter()
                    .map(|lon| if lon > 180.0 { lon - 360.0 } else { lon })
                    .collect();
                let grid =
                    grid.get_or_insert_with(|| GfsGrid::new(latitudes.clone(), longitudes.clone()));
                if grid.latitudes != latitudes || grid.longitudes != longitudes {
                    return Err("GFS messages use different coordinate layouts".into());
                }
                let values = message.values(count)?;
                return grid.insert((level_type, level, name), sel(&values));
            }
            let grid =
                grid.get_or_insert_with(|| GfsGrid::new(latitudes.clone(), longitudes.clone()));
            // Same point count does not prove that fields share a grid.
            if grid.latitudes != latitudes || grid.longitudes != longitudes {
                return Err("GFS messages use different coordinate layouts".into());
            }
            grid.insert((level_type, level, name), message.values(count)?)
        })?;
        grid.ok_or_else(|| "GFS GRIB has no supported shared grid fields".into())
    }

    pub(crate) fn visit_file(
        &self,
        path: &Path,
        max_messages: usize,
        mut visit: impl FnMut(&Message<'_>) -> Result<(), String>,
    ) -> Result<(), String> {
        self.visit_while(path, max_messages, |message| {
            visit(message)?;
            Ok(true)
        })
    }

    pub(crate) fn first_area(&self, path: &Path) -> Result<[f64; 4], String> {
        self.area(path, None)
    }

    pub(crate) fn first_subset_area(
        &self,
        path: &Path,
        bbox: [f64; 4],
    ) -> Result<[f64; 4], String> {
        self.area(path, Some(bbox))
    }

    fn area(&self, path: &Path, bbox: Option<[f64; 4]>) -> Result<[f64; 4], String> {
        let mut area = None;
        self.visit_while(path, 1, |message| {
            let count = message.size(b"values\0")?;
            if count == 0 || count > 5_000_000 {
                return Err("CAMS header point limit exceeded".into());
            }
            let lat = message.array(b"latitudes\0", count)?;
            let lon = message.array(b"longitudes\0", count)?;
            if lat.iter().chain(&lon).any(|v| !v.is_finite()) {
                return Err("CAMS header coordinates are nonfinite".into());
            }
            let lon: Vec<_> = lon
                .iter()
                .map(|v| (v + 180.0).rem_euclid(360.0) - 180.0)
                .collect();
            let mut bounds = [
                f64::NEG_INFINITY,
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
            ];
            for (lat, lon) in lat.into_iter().zip(lon) {
                if bbox.is_some_and(|[w, e, n, s]| lat < s || lat > n || lon < w || lon > e) {
                    continue;
                }
                bounds[0] = bounds[0].max(lat);
                bounds[1] = bounds[1].min(lon);
                bounds[2] = bounds[2].min(lat);
                bounds[3] = bounds[3].max(lon);
            }
            if bounds.iter().any(|v| !v.is_finite()) {
                return Err("GRIB subset contains no coordinates".into());
            }
            area = Some(bounds);
            Ok(false)
        })?;
        area.ok_or("CAMS file has no GRIB messages".into())
    }

    pub(crate) fn visit_while(
        &self,
        path: &Path,
        max_messages: usize,
        mut visit: impl FnMut(&Message<'_>) -> Result<bool, String>,
    ) -> Result<(), String> {
        if !(1..=4096).contains(&max_messages) {
            return Err("max_messages must be 1..4096".into());
        }
        let name = CString::new(path.as_os_str().as_encoded_bytes())
            .map_err(|_| "GRIB path contains NUL")?;
        let stream = unsafe { libc::fopen(name.as_ptr(), c"rb".as_ptr()) };
        if stream.is_null() {
            return Err(format!(
                "cannot open {}: {}",
                path.display(),
                std::io::Error::last_os_error()
            ));
        }
        let stream = FileStream(stream);
        let mut messages = 0usize;
        loop {
            let mut error = 0;
            let handle = unsafe { (self.new_handle)(ptr::null_mut(), stream.0, &mut error) };
            if handle.is_null() {
                // ecCodes may return success or CODES_END_OF_FILE (-1) at EOF.
                if error == 0 || error == -1 {
                    break;
                }
                self.check(error, "reading GRIB message")?;
                return Err("ecCodes returned no handle".into());
            }
            let message = Message {
                codes: self,
                handle,
            };
            self.check(error, "reading GRIB message")?;
            if messages >= max_messages {
                return Err("GRIB message limit exceeded; increase max_messages".into());
            }
            messages += 1;
            if !visit(&message)? {
                break;
            }
        }
        if unsafe { libc::ferror(stream.0) } != 0 {
            return Err(format!("I/O error reading {}", path.display()));
        }
        Ok(())
    }
}

struct FileStream(*mut libc::FILE);

impl Drop for FileStream {
    fn drop(&mut self) {
        unsafe {
            libc::fclose(self.0);
        }
    }
}

pub(crate) struct Message<'a> {
    codes: &'a EcCodes,
    handle: *mut c_void,
}

impl Drop for Message<'_> {
    fn drop(&mut self) {
        unsafe {
            (self.codes.delete_handle)(self.handle);
        }
    }
}

impl Message<'_> {
    pub(crate) fn encoded(&self) -> Result<&[u8], String> {
        let mut pointer = ptr::null();
        let mut length = 0;
        let error = unsafe { (self.codes.get_message)(self.handle, &mut pointer, &mut length) };
        self.codes.check(error, "GRIB encoded message")?;
        if pointer.is_null() || length == 0 || length > 512 * 1024 * 1024 {
            return Err("invalid encoded GRIB message length".into());
        }
        // ecCodes owns the bytes until this message's handle is deleted.
        Ok(unsafe { std::slice::from_raw_parts(pointer.cast(), length) })
    }
    pub(crate) fn values(&self, count: usize) -> Result<Vec<f32>, String> {
        Ok(self
            .values_f64(count)?
            .into_iter()
            .map(|v| v as f32)
            .collect())
    }

    pub(crate) fn values_f64(&self, count: usize) -> Result<Vec<f64>, String> {
        let mut values = self.array(b"values\0", count)?;
        // Declared but unreadable or misaligned bitmaps fail closed.
        if self.long(b"bitmapPresent\0")? != 0 {
            for (value, bit) in values.iter_mut().zip(self.array(b"bitmap\0", count)?) {
                if bit == 0.0 {
                    *value = f64::NAN;
                } else if bit != 1.0 {
                    return Err("GRIB bitmap contains a value other than 0 or 1".into());
                }
            }
        }
        Ok(values)
    }

    pub(crate) fn long(&self, key: &[u8]) -> Result<i64, String> {
        let mut value = 0;
        let error = unsafe { (self.codes.get_long)(self.handle, key.as_ptr().cast(), &mut value) };
        self.codes.check(error, &String::from_utf8_lossy(key))?;
        // C long is 32-bit on Windows, even on 64-bit targets.
        #[allow(clippy::unnecessary_cast)]
        Ok(value as i64)
    }

    pub(crate) fn optional_long(&self, key: &[u8]) -> Result<Option<i64>, String> {
        let mut value = 0;
        let error = unsafe { (self.codes.get_long)(self.handle, key.as_ptr().cast(), &mut value) };
        // CODES_NOT_FOUND/GRIB_NOT_FOUND; all other ecCodes errors remain errors.
        if error == -10 {
            return Ok(None);
        }
        self.codes.check(error, &String::from_utf8_lossy(key))?;
        #[allow(clippy::unnecessary_cast)]
        Ok(Some(value as i64))
    }

    pub(crate) fn size(&self, key: &[u8]) -> Result<usize, String> {
        let mut size = 0;
        let error = unsafe { (self.codes.get_size)(self.handle, key.as_ptr().cast(), &mut size) };
        self.codes.check(error, &String::from_utf8_lossy(key))?;
        Ok(size)
    }

    pub(crate) fn optional_array(
        &self,
        key: &[u8],
        limit: usize,
    ) -> Result<Option<Vec<f64>>, String> {
        let mut size = 0;
        let error = unsafe { (self.codes.get_size)(self.handle, key.as_ptr().cast(), &mut size) };
        if error == -10 {
            return Ok(None);
        }
        self.codes.check(error, &String::from_utf8_lossy(key))?;
        if size > limit {
            return Err("GRIB metadata array exceeds size limit".into());
        }
        if size == 0 {
            return Ok(None);
        }
        let values = self.array(key, size)?;
        if values.iter().any(|v| !v.is_finite()) {
            return Err("GRIB metadata array contains nonfinite values".into());
        }
        Ok(Some(values))
    }

    pub(crate) fn string(&self, key: &[u8]) -> Result<String, String> {
        let mut length = 0;
        let error =
            unsafe { (self.codes.get_length)(self.handle, key.as_ptr().cast(), &mut length) };
        self.codes.check(error, &String::from_utf8_lossy(key))?;
        if length > 4096 {
            return Err("GRIB metadata string is too large".into());
        }
        let mut buffer = vec![0u8; length + 1];
        length = buffer.len();
        let error = unsafe {
            (self.codes.get_string)(
                self.handle,
                key.as_ptr().cast(),
                buffer.as_mut_ptr().cast(),
                &mut length,
            )
        };
        self.codes.check(error, &String::from_utf8_lossy(key))?;
        CStr::from_bytes_until_nul(&buffer)
            .map_err(|error| error.to_string())?
            .to_str()
            .map(str::to_owned)
            .map_err(|error| error.to_string())
    }

    pub(crate) fn array(&self, key: &[u8], expected: usize) -> Result<Vec<f64>, String> {
        let mut size = self.size(key)?;
        if size != expected {
            return Err(format!(
                "{} has {size} entries, expected {expected}",
                String::from_utf8_lossy(key)
            ));
        }
        let mut values = vec![0.0; size];
        let error = unsafe {
            (self.codes.get_doubles)(
                self.handle,
                key.as_ptr().cast(),
                values.as_mut_ptr(),
                &mut size,
            )
        };
        self.codes.check(error, &String::from_utf8_lossy(key))?;
        if size != expected {
            return Err("ecCodes array length changed during decode".into());
        }
        Ok(values)
    }
}

#[derive(Debug, Serialize)]
pub struct GribField {
    pub short_name: String,
    pub type_of_level: String,
    pub level: i64,
    pub data_date: i64,
    pub data_time: i64,
    pub end_step: i64,
    pub grid_type: String,
    pub point_count: usize,
    pub missing_count: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub values: Option<Vec<Option<f32>>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub latitudes: Option<Vec<f64>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub longitudes: Option<Vec<f64>>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn installed_decoder_resources_have_content_identity() {
        let Ok(decoder) = EcCodes::load() else {
            return;
        };
        let identity = decoder.cache_identity().unwrap();
        assert_eq!(identity.len(), 64);
        assert!(identity.bytes().all(|c| c.is_ascii_hexdigit()));
        assert!(loaded_module_path(decoder.new_handle as *const c_void)
            .unwrap()
            .is_file());
    }
}
