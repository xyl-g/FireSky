//! Stored-input benchmark, not a full forecast or cross-language speed claim.

use firesky_engine::{
    ecmwf,
    grib::EcCodes,
    tensor_bundle::{source_identity, BundleWriter},
};
use firesky_native::derivation;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

struct OutputDirectory(PathBuf);

impl Drop for OutputDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn millis(start: Instant) -> f64 {
    start.elapsed().as_secs_f64() * 1000.0
}

fn trial(
    codes: &EcCodes,
    model: &str,
    path: &Path,
    bbox: Option<[f64; 4]>,
    parallel_derivation: bool,
) -> Result<Value, String> {
    let total = Instant::now();
    let start = Instant::now();
    let source = source_identity(path)?;
    let hash_ms = millis(start);
    let start = Instant::now();
    let (grid, decode_ms, adapt_ms) = if model == "ec" {
        let tensor = ecmwf::decode_basic(codes, path, bbox, None, None, 4096, 5_000_000)?;
        let decode_ms = millis(start);
        let start = Instant::now();
        let (grid, _) = tensor.native_grid()?;
        (grid, decode_ms, millis(start))
    } else {
        (
            codes.decode_gfs_file(path, 4096, 5_000_000)?,
            millis(start),
            0.0,
        )
    };
    let start = Instant::now();
    let raw = derivation::RawGrid::from_gfs(&grid)?;
    let pack_ms = millis(start);
    let start = Instant::now();
    let derived = derivation::derive_with_parallelism(&raw, parallel_derivation)?;
    let derive_ms = millis(start);
    // Reserve an owned parent before handing its nonexistent child to the writer.
    let directory = std::env::temp_dir().join(format!(
        "firesky-bench-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_nanos(),
    ));
    std::fs::create_dir(&directory).map_err(|e| e.to_string())?;
    let directory = OutputDirectory(directory);
    let start = Instant::now();
    let cells = grid.latitudes.len() as u64;
    let mut writer = BundleWriter::create(
        &directory.0.join("tensors"),
        "benchmark-derived-v1",
        source,
        json!({"pressure_levels": derived.levels}),
    )?;
    writer.f32("latitudes", &[cells], grid.latitudes.iter().copied())?;
    writer.f32("longitudes", &[cells], grid.longitudes.iter().copied())?;
    writer.f32(
        "columns",
        &[cells, derivation::COLUMN_FIELDS as u64],
        derived.columns.iter().flatten().copied(),
    )?;
    writer.f32(
        "profiles",
        &[
            cells,
            derived.levels.len() as u64,
            derivation::PROFILE_FIELDS as u64,
        ],
        derived.profiles.iter().flatten().copied(),
    )?;
    writer.commit(path)?;
    let write_ms = millis(start);
    let total_ms = millis(total);
    Ok(json!({
        "cells": cells, "levels": derived.levels.len(),
        "source_hash_ms": hash_ms, "decode_ms": decode_ms, "adapt_ms": adapt_ms,
        "pack_ms": pack_ms, "derive_ms": derive_ms, "write_and_verify_ms": write_ms,
        "total_ms": total_ms,
    }))
}

fn peak_rss_bytes() -> Option<u64> {
    #[cfg(unix)]
    {
        let mut usage = std::mem::MaybeUninit::<libc::rusage>::uninit();
        if unsafe { libc::getrusage(libc::RUSAGE_SELF, usage.as_mut_ptr()) } != 0 {
            return None;
        }
        let usage = unsafe { usage.assume_init() };
        let bytes = usage.ru_maxrss as u64;
        #[cfg(target_os = "macos")]
        return Some(bytes);
        #[cfg(not(target_os = "macos"))]
        return Some(bytes * 1024);
    }
    #[cfg(not(unix))]
    None
}

fn run() -> Result<(), String> {
    let mut args: Vec<_> = std::env::args().skip(1).collect();
    let parallel_derivation = if args.last().is_some_and(|v| v == "--serial-derivation") {
        args.pop();
        false
    } else {
        true
    };
    if ![3, 7].contains(&args.len()) || !["gfs", "ec"].contains(&args[0].as_str()) {
        return Err("usage: bench_stored gfs|ec GRIB ITERATIONS [WEST EAST NORTH SOUTH] [--serial-derivation]".into());
    }
    let iterations: usize = args[2].parse().map_err(|_| "invalid iteration count")?;
    if !(1..=20).contains(&iterations) || (args[0] == "gfs" && args.len() != 3) {
        return Err("iterations must be 1..20; only EC accepts a bbox".into());
    }
    let bbox = if args.len() == 7 {
        let mut bbox = [0.0; 4];
        for (slot, value) in bbox.iter_mut().zip(&args[3..]) {
            *slot = value.parse().map_err(|_| "invalid bbox")?;
        }
        Some(bbox)
    } else {
        None
    };
    let path = Path::new(&args[1]);
    let identity = source_identity(path)?;
    let codes = EcCodes::load()?;
    trial(&codes, &args[0], path, bbox, parallel_derivation)?; // One unreported warmup.
    let mut samples = Vec::with_capacity(iterations);
    for _ in 0..iterations {
        samples.push(trial(&codes, &args[0], path, bbox, parallel_derivation)?);
    }
    let mut medians = serde_json::Map::new();
    for phase in [
        "source_hash_ms",
        "decode_ms",
        "adapt_ms",
        "pack_ms",
        "derive_ms",
        "write_and_verify_ms",
        "total_ms",
    ] {
        let mut times: Vec<_> = samples.iter().map(|s| s[phase].as_f64().unwrap()).collect();
        times.sort_by(f64::total_cmp);
        let median = (times[(times.len() - 1) / 2] + times[times.len() / 2]) * 0.5;
        medians.insert(phase.into(), json!(median));
    }
    println!("{}", serde_json::to_string_pretty(&json!({
        "scope": "stored tensors, warm filesystem/ecCodes, no Python, no IPC, not full forecast",
        "model": args[0], "source": identity, "bbox": bbox, "iterations": iterations,
        "os": std::env::consts::OS, "arch": std::env::consts::ARCH,
        "available_parallelism": std::thread::available_parallelism().map(|n| n.get()).ok(),
        "rayon_num_threads": std::env::var("RAYON_NUM_THREADS").ok(),
        "debug_assertions": cfg!(debug_assertions),
        "parallel_derivation": parallel_derivation,
        "peak_rss_bytes": peak_rss_bytes(), "median_ms": medians, "samples": samples,
    })).map_err(|e| e.to_string())?);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("bench_stored: {error}");
        std::process::exit(1);
    }
}
