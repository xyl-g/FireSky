//! Synthetic full-event composition component; excludes weather, JSON and I/O.
use firesky_native::quality::{self, Mode, Options, Row};
use serde_json::json;
use std::time::Instant;

fn fixture(points: usize) -> Vec<Row> {
    let mut rows = Vec::with_capacity(points * 20);
    for point in 0..points {
        for step in 0..20 {
            let view = 0.25 + 0.5 * (point % 37) as f64 / 36.0;
            let burn = view * (0.1 + 0.8 * ((point * 7 + step * 11) % 97) as f64 / 96.0);
            let material = burn * 2.5 * 0.72;
            let incoming = material * 0.85;
            let load = incoming * 0.8;
            rows.push([
                view,
                burn,
                load,
                material,
                incoming,
                0.2,
                0.8,
                0.7,
                -20.0 + step as f64 * 5.0,
                0.15,
                view * 0.6,
                burn * 0.5,
                load * 0.5,
                material * 0.5,
                incoming * 0.5,
                1.0,
                2.5,
                1.0,
            ]);
        }
    }
    rows
}

fn trial(rows: &[Row], options: Options, parallel: bool) -> Result<(f64, f64), String> {
    let started = Instant::now();
    let scores = quality::score_many(rows, rows.len() / 20, 20, options, parallel)?;
    let millis = started.elapsed().as_secs_f64() * 1000.0;
    let checksum: f64 = scores
        .iter()
        .flat_map(|s| s.values)
        .filter(|v| v.is_finite())
        .sum();
    std::hint::black_box(&scores);
    Ok((millis, checksum))
}

fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let points: usize = args
        .first()
        .map_or(Ok(36_685), |s| s.parse())
        .map_err(|_| "invalid point count")?;
    let trials: usize = args
        .get(1)
        .map_or(Ok(5), |s| s.parse())
        .map_err(|_| "invalid trial count")?;
    if args.len() > 2 || !(1..=100_000).contains(&points) || !(1..=15).contains(&trials) {
        return Err("usage: bench_quality [POINTS=36685] [TRIALS=5]".into());
    }
    let rows = fixture(points);
    let options = Options {
        mode: Mode::Regional,
        apply_duration: true,
        local_columns: true,
        score_ceiling: false,
        radius_km: 200.0,
    };
    let serial = quality::score_many(&rows, points, 20, options, false)?;
    let parallel = quality::score_many(&rows, points, 20, options, true)?;
    for (a, b) in serial.iter().zip(&parallel) {
        if a.flags() != b.flags()
            || a.values
                .iter()
                .zip(b.values)
                .any(|(a, b)| a.to_bits() != b.to_bits())
        {
            return Err("serial/parallel quality bits differ".into());
        }
    }
    drop(serial);
    drop(parallel);
    let mut modes = Vec::new();
    for parallel in [false, true] {
        let mut times = Vec::new();
        let mut checksum = 0.0;
        for _ in 0..trials {
            let (millis, actual) = trial(&rows, options, parallel)?;
            if checksum != 0.0 && checksum != actual {
                return Err("quality checksum changed".into());
            }
            checksum = actual;
            times.push(millis);
        }
        let mut sorted = times.clone();
        sorted.sort_by(f64::total_cmp);
        modes.push(json!({"parallel":parallel,"milliseconds":times,
            "median_ms":(sorted[(trials-1)/2]+sorted[trials/2])*0.5,"checksum":checksum}));
    }
    println!(
        "{}",
        json!({
            "scope":"synthetic complete event composition; no weather/JSON/I/O; not full forecast",
            "points":points,"steps":20,"trials":trials,"modes":modes,
            "serial_parallel_bits_equal":true,
            "os":std::env::consts::OS,"arch":std::env::consts::ARCH,
            "debug_assertions":cfg!(debug_assertions),
        })
    );
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("bench_quality: {error}");
        std::process::exit(1);
    }
}
