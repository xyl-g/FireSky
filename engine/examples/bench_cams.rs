//! Complete stored compact CAMS sidecar timing, without Python or IPC.
use firesky_engine::{cams, tensor_bundle::source_identity};
use serde_json::json;
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

struct OwnedDirectory(PathBuf);
impl Drop for OwnedDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 5 {
        return Err(
            "usage: bench_cams LEFT_GRIB RIGHT_GRIB MODEL_MANIFEST TARGET_RFC3339 ITERATIONS"
                .into(),
        );
    }
    let iterations: usize = args[4].parse().map_err(|_| "invalid iteration count")?;
    if !(1..=20).contains(&iterations) {
        return Err("iterations must be 1..20".into());
    }
    let directory = std::env::temp_dir().join(format!(
        "firesky-cams-bench-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_nanos()
    ));
    std::fs::create_dir(&directory).map_err(|e| e.to_string())?;
    let directory = OwnedDirectory(directory);
    let mut samples = Vec::new();
    let mut cells = 0;
    for trial in 0..=iterations {
        let output = directory.0.join(format!("trial-{trial}"));
        let start = Instant::now();
        let manifest = cams::interpolate_files(
            Path::new(&args[0]),
            Path::new(&args[1]),
            Path::new(&args[2]),
            &args[3],
            &output,
        )?;
        let elapsed = start.elapsed().as_secs_f64() * 1000.0;
        if trial != 0 {
            samples.push(elapsed);
        }
        let manifest: serde_json::Value =
            serde_json::from_slice(&std::fs::read(manifest).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
        cells = manifest["arrays"]["aod550"]["shape"][0]
            .as_u64()
            .ok_or("missing output cells")?;
        std::fs::remove_dir_all(output).map_err(|e| e.to_string())?;
    }
    let mut sorted = samples.clone();
    sorted.sort_by(f64::total_cmp);
    println!("{}", serde_json::to_string_pretty(&json!({
        "scope": "warm compact CAMS decode/map/blend/file sync+hash+source/coordinate revalidation; no Python, no IPC, not full forecast",
        "left": source_identity(Path::new(&args[0]))?, "right": source_identity(Path::new(&args[1]))?,
        "model_manifest": source_identity(Path::new(&args[2]))?,
        "target_time": args[3], "cells": cells, "iterations": iterations,
        "os": std::env::consts::OS, "arch": std::env::consts::ARCH,
        "debug_assertions": cfg!(debug_assertions), "samples_ms": samples,
        "median_ms": (sorted[(iterations-1)/2] + sorted[iterations/2]) / 2.0,
    })).map_err(|e| e.to_string())?);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("bench_cams: {error}");
        std::process::exit(1);
    }
}
