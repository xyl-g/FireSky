//! Warm offline L137 decode/output, not full forecast performance.

use firesky_engine::{ecmwf, grib::EcCodes, tensor_bundle::source_identity};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

struct Directory(PathBuf);
impl Drop for Directory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn elapsed(start: Instant) -> f64 {
    start.elapsed().as_secs_f64() * 1000.0
}

fn trial(codes: &EcCodes, path: &Path, output: &Path) -> Result<Value, String> {
    let total = Instant::now();
    let start = Instant::now();
    let source = source_identity(path)?;
    let hash_ms = elapsed(start);
    let start = Instant::now();
    let tensor = ecmwf::decode_full(
        codes,
        path,
        (1..=137).collect(),
        ecmwf::DecodeOptions {
            bbox: None,
            expected_cycle_utc: None,
            expected_hour: None,
            max_messages: 4096,
            max_points: 5_000_000,
        },
    )?;
    let decode_ms = elapsed(start);
    let start = Instant::now();
    let (_, timings) = tensor.write(output, source)?.commit_profiled(path)?;
    let write_ms = elapsed(start);
    Ok(json!({
        "cells": tensor.latitudes.len(), "source_hash_ms": hash_ms, "decode_ms": decode_ms,
        "write_verify_ms": write_ms, "serialization_ms": timings.serialization_ms,
        "flush_sync_ms": timings.flush_sync_ms, "array_hash_ms": timings.array_hash_ms,
        "source_manifest_ms": timings.source_manifest_ms, "sync_worker_ms": timings.sync_worker_ms,
        "total_ms": elapsed(total),
    }))
}

fn peak_rss_bytes() -> Option<u64> {
    #[cfg(unix)]
    {
        let mut usage = std::mem::MaybeUninit::<libc::rusage>::uninit();
        if unsafe { libc::getrusage(libc::RUSAGE_SELF, usage.as_mut_ptr()) } != 0 {
            return None;
        }
        let bytes = unsafe { usage.assume_init() }.ru_maxrss as u64;
        #[cfg(target_os = "macos")]
        return Some(bytes);
        #[cfg(not(target_os = "macos"))]
        return Some(bytes * 1024);
    }
    #[cfg(not(unix))]
    None
}

fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 2 {
        return Err("usage: bench_ec_full L137_GRIB ITERATIONS".into());
    }
    let iterations: usize = args[1].parse().map_err(|_| "invalid iterations")?;
    if !(1..=20).contains(&iterations) {
        return Err("iterations must be 1..20".into());
    }
    let path = Path::new(&args[0]);
    let codes = EcCodes::load()?;
    let root = std::env::temp_dir().join(format!(
        "firesky-full-bench-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_nanos()
    ));
    std::fs::create_dir(&root).map_err(|e| e.to_string())?;
    let root = Directory(root);
    let mut samples = Vec::new();
    for index in 0..=iterations {
        let output = root.0.join(format!("trial{index}"));
        let result = trial(&codes, path, &output)?;
        if index > 0 {
            samples.push(result);
        }
        std::fs::remove_dir_all(output).map_err(|e| e.to_string())?;
    }
    let mut medians = serde_json::Map::new();
    for phase in [
        "source_hash_ms",
        "decode_ms",
        "write_verify_ms",
        "serialization_ms",
        "flush_sync_ms",
        "array_hash_ms",
        "source_manifest_ms",
        "sync_worker_ms",
        "total_ms",
    ] {
        let mut times: Vec<_> = samples.iter().map(|v| v[phase].as_f64().unwrap()).collect();
        times.sort_by(f64::total_cmp);
        medians.insert(
            phase.into(),
            json!((times[(iterations - 1) / 2] + times[iterations / 2]) * 0.5),
        );
    }
    println!("{}", serde_json::to_string_pretty(&json!({
        "scope": "warm offline L137 decode plus f32 tensor output, no Python/IPC/startup/cleanup, not forecast",
        "source": source_identity(path)?, "model_levels": 137, "iterations": iterations,
        "os": std::env::consts::OS, "arch": std::env::consts::ARCH,
        "debug_assertions": cfg!(debug_assertions), "peak_rss_bytes": peak_rss_bytes(),
        "median_ms": medians, "samples": samples,
    })).map_err(|e| e.to_string())?);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("bench_ec_full: {error}");
        std::process::exit(1);
    }
}
