//! Warm validated point materialization; parsing, serialization and I/O excluded.
use firesky_engine::point_core::{self, Request};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::time::Instant;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Fixture {
    request: Request,
    core: Value,
}

fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let iterations: usize = args
        .get(1)
        .map_or(Ok(500), |s| s.parse())
        .map_err(|_| "iterations")?;
    let trials: usize = args
        .get(2)
        .map_or(Ok(5), |s| s.parse())
        .map_err(|_| "trials")?;
    if !(1..=3).contains(&args.len())
        || !(1..=10000).contains(&iterations)
        || !(1..=15).contains(&trials)
    {
        return Err("usage: bench_point_core FIXTURE [ITERATIONS=500] [TRIALS=5]".into());
    }
    let path = std::path::Path::new(&args[0]);
    if !std::fs::symlink_metadata(path)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_file()
    {
        return Err("fixture must be a regular file".into());
    }
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 1024 * 1024 {
        return Err("fixture exceeds 1 MiB".into());
    }
    let fixture: Fixture = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    let expected = point_core::materialize(&fixture.request, &fixture.core)?;
    let encoded = serde_json::to_vec(&expected).map_err(|e| e.to_string())?;
    let mut times = Vec::new();
    for _ in 0..=trials {
        let started = Instant::now();
        for _ in 0..iterations {
            let result = point_core::materialize(&fixture.request, &fixture.core)?;
            std::hint::black_box(result);
        }
        times.push(started.elapsed().as_secs_f64() * 1000.0 / iterations as f64);
        if point_core::materialize(&fixture.request, &fixture.core)? != expected {
            return Err("materialized output changed".into());
        }
    }
    times.remove(0);
    let mut sorted = times.clone();
    sorted.sort_by(f64::total_cmp);
    let hash = Sha256::digest(&encoded)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    println!(
        "{}",
        json!({"scope":"warm validated point materialization; excludes input decode, serialization and I/O",
        "iterations":iterations,"trials":trials,"ms_per_point":times,
        "median_ms":(sorted[(trials-1)/2]+sorted[trials/2])*0.5,
        "output_bytes":encoded.len(),"output_sha256":hash,
        "debug_assertions":cfg!(debug_assertions),"os":std::env::consts::OS,"arch":std::env::consts::ARCH})
    );
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("bench_point_core: {error}");
        std::process::exit(1);
    }
}
