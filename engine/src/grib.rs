//! Small, read-only ecCodes adapter. No Python interpreter or Python bindings.

use chrono::{DateTime, Datelike, Timelike, Utc};
use firesky_native::gfs::{self, GfsGrid};
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
    #[cfg(unix)]
    pub(crate) fn cache_identity(&self) -> Result<String, String> {
        use sha2::{Digest, Sha256};
        let mut hash = Sha256::new();
        #[cfg(unix)]
        {
            let mut info: libc::Dl_info = unsafe { std::mem::zeroed() };
            if unsafe { libc::dladdr(self.new_handle as *const c_void, &mut info) } == 0
                || info.dli_fname.is_null()
            {
                return Err("cannot identify loaded ecCodes library for derived cache".into());
            }
            let path = unsafe { CStr::from_ptr(info.dli_fname) }
                .to_str()
                .map_err(|e| e.to_string())?;
            let identity = crate::tensor_bundle::source_identity(
                &Path::new(path).canonicalize().map_err(|e| e.to_string())?,
            )?;
            hash.update(serde_json::to_vec(&identity).map_err(|e| e.to_string())?);
        }
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
                tree(
                    &path.canonicalize().map_err(|e| e.to_string())?,
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

    #[cfg(not(unix))]
    pub(crate) fn cache_identity(&self) -> Result<String, String> {
        Err("derived decoder fingerprint is not implemented on this platform".into())
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

    pub fn decode_gfs_frame(
        &self,
        path: &Path,
        max_messages: usize,
        max_points: usize,
        expected: Option<(DateTime<Utc>, u32)>,
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
            total_points = total_points
                .checked_add(count)
                .ok_or("GFS point count overflow")?;
            if count > max_points || total_points > 10_000_000 {
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

    fn visit_while(
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
