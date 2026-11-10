//! Warm in-process cache lookup/publication only; no network or GRIB decoding.
use chrono::{TimeZone, Utc};
use firesky_engine::gfs_data::{build_url, Config, GfsClient};
use serde_json::json;
use std::{path::PathBuf, time::Instant};

fn main() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 4 {
        return Err("usage: bench_gfs_data CACHE_DIR find|reuse ITERATIONS EXPECTED_PATH".into());
    }
    let iterations: usize = args[2].parse().map_err(|_| "invalid iteration count")?;
    if iterations == 0 || iterations > 10_000 {
        return Err("iterations must be 1..10000".into());
    }
    let expected = PathBuf::from(&args[3]);
    let client = GfsClient::new(Config {
        cache_dir: args[0].clone().into(),
        resolution: "0p25".into(),
        region: None,
        timeout_seconds: 0.1,
        lock_timeout_seconds: 1.0,
        download_attempts: 1,
    })?;
    let cycle = Utc.with_ymd_and_hms(2026, 11, 9, 0, 0, 0).unwrap();
    let bbox = Some([100.0, 102.0, 1.0, 0.0]);
    let url = build_url(cycle, 9, bbox, "0p25", false)?;
    let run = || -> Result<(), String> {
        let path = match args[1].as_str() {
            "find" => client
                .find(cycle, 9, bbox, Some(&url))?
                .ok_or("cache miss")?,
            "reuse" => {
                let result = client.download(&url, Some(cycle), Some(9), bbox)?;
                if result["status"] != "exact_cache" {
                    return Err("benchmark must not download".into());
                }
                PathBuf::from(result["path"].as_str().ok_or("missing path")?)
            }
            _ => return Err("mode must be find or reuse".into()),
        };
        if path != expected {
            return Err("cache selection changed".into());
        }
        Ok(())
    };
    run()?;
    let started = Instant::now();
    for _ in 0..iterations {
        run()?;
    }
    println!(
        "{}",
        json!({"mode":args[1],"iterations":iterations,
        "ms_per_call":started.elapsed().as_secs_f64()*1000.0/iterations as f64,
        "path":expected})
    );
    Ok(())
}
