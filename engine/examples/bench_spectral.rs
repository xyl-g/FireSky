//! Expanded CAMS phase timing; not full-forecast performance.

use firesky_engine::{grib::EcCodes, spectral, tensor_bundle::source_identity};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

struct OwnedDirectory(PathBuf);
impl Drop for OwnedDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn elapsed(start: Instant) -> f64 {
    start.elapsed().as_secs_f64() * 1000.0
}

fn trial(codes: &EcCodes, path: &Path, output: &Path, observer: [f64; 2]) -> Result<Value, String> {
    let total = Instant::now();
    let start = Instant::now();
    let source = source_identity(path)?;
    let hash_ms = elapsed(start);
    let start = Instant::now();
    let optics = spectral::decode(codes, path, 4096, 5_000_000, None)?;
    let decode_ms = elapsed(start);
    let start = Instant::now();
    let result = optics.diagnose(observer, &[440., 500., 550., 670., 865.], 5., 30.)?;
    std::hint::black_box(result);
    let diagnose_ms = elapsed(start);
    let start = Instant::now();
    let writer = optics.write(output, source)?;
    let (_, timings) = writer.commit_profiled(path)?;
    let write_ms = elapsed(start);
    Ok(
        json!({"cells": optics.latitudes.len(), "source_hash_ms": hash_ms,
        "decode_ms": decode_ms, "diagnose_ms": diagnose_ms,
        "write_verify_ms": write_ms, "serialization_ms": timings.serialization_ms,
        "flush_sync_ms": timings.flush_sync_ms, "array_hash_ms": timings.array_hash_ms,
        "sync_worker_ms": timings.sync_worker_ms,
        "source_check_manifest_ms": timings.source_manifest_ms, "total_ms": elapsed(total)}),
    )
}

fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 4 {
        return Err("usage: bench_spectral GRIB ITERATIONS LAT LON".into());
    }
    let iterations: usize = args[1].parse().map_err(|_| "invalid iterations")?;
    if !(1..=20).contains(&iterations) {
        return Err("iterations must be 1..20".into());
    }
    let observer = [
        args[2].parse().map_err(|_| "invalid latitude")?,
        args[3].parse().map_err(|_| "invalid longitude")?,
    ];
    let path = Path::new(&args[0]);
    let codes = EcCodes::load()?;
    let directory = std::env::temp_dir().join(format!(
        "firesky-spectral-bench-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_nanos()
    ));
    std::fs::create_dir(&directory).map_err(|e| e.to_string())?;
    let directory = OwnedDirectory(directory);
    let mut samples = Vec::new();
    for index in 0..=iterations {
        let output = directory.0.join(format!("trial{index}"));
        let result = trial(&codes, path, &output, observer)?;
        if index > 0 {
            samples.push(result);
        }
        std::fs::remove_dir_all(output).map_err(|e| e.to_string())?;
    }
    let mut medians = serde_json::Map::new();
    for phase in [
        "source_hash_ms",
        "decode_ms",
        "diagnose_ms",
        "write_verify_ms",
        "serialization_ms",
        "flush_sync_ms",
        "array_hash_ms",
        "sync_worker_ms",
        "source_check_manifest_ms",
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
        "scope": "warm expanded CAMS source hash/decode/one-point diagnostics/f64 tensor output; no Python or IPC; not full forecast",
        "source": source_identity(path)?, "observer": observer, "iterations": iterations,
        "os": std::env::consts::OS, "arch": std::env::consts::ARCH,
        "debug_assertions": cfg!(debug_assertions), "median_ms": medians, "samples": samples,
    })).map_err(|e| e.to_string())?);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("bench_spectral: {error}");
        std::process::exit(1);
    }
}
