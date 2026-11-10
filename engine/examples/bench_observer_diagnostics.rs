//! Synthetic additional-diagnostic reduction; no weather decode, JSON or I/O.
use firesky_native::{observer_diagnostics as diagnostic, NATIONAL_OUTPUT_FIELDS};
use serde_json::json;
use std::time::Instant;

fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let points: usize = args
        .first()
        .map_or(Ok(36_685), |v| v.parse())
        .map_err(|_| "invalid points")?;
    let trials: usize = args
        .get(1)
        .map_or(Ok(5), |v| v.parse())
        .map_err(|_| "invalid trials")?;
    if args.len() > 2 || !(1..=100_000).contains(&points) || !(1..=15).contains(&trials) {
        return Err("usage: bench_observer_diagnostics [POINTS=36685] [TRIALS=5]".into());
    }
    let mut values = vec![0.0; points * 20 * NATIONAL_OUTPUT_FIELDS];
    for (i, row) in values
        .as_chunks_mut::<NATIONAL_OUTPUT_FIELDS>()
        .0
        .iter_mut()
        .enumerate()
    {
        for (j, v) in row.iter_mut().enumerate() {
            *v = ((i * 13 + j * 7) % 97) as f32 / 97.0;
        }
        row[0] = f32::from(i % 7 != 0);
    }
    let offsets: Vec<_> = (0..20).map(|i| -20.0 + i as f64 * 5.0).collect();
    let serial = diagnostic::summarize_many(&values, points, &offsets, false)?;
    let parallel = diagnostic::summarize_many(&values, points, &offsets, true)?;
    if serial
        .iter()
        .flatten()
        .zip(parallel.iter().flatten())
        .any(|(a, b)| a.to_bits() != b.to_bits())
    {
        return Err("serial/parallel diagnostic fields differ".into());
    }
    drop(serial);
    drop(parallel);
    let mut modes = Vec::new();
    for parallel in [false, true] {
        let mut times = Vec::new();
        let mut checksum = 0.0;
        for _ in 0..trials {
            let started = Instant::now();
            let result = diagnostic::summarize_many(&values, points, &offsets, parallel)?;
            times.push(started.elapsed().as_secs_f64() * 1000.0);
            let actual: f64 = result.iter().flatten().sum();
            if checksum != 0.0 && checksum != actual {
                return Err("diagnostic checksum changed".into());
            }
            checksum = actual;
            std::hint::black_box(&result);
        }
        let mut sorted = times.clone();
        sorted.sort_by(f64::total_cmp);
        modes.push(json!({"parallel":parallel,"milliseconds":times,"median_ms":(sorted[(trials-1)/2]+sorted[trials/2])*0.5,"checksum":checksum}));
    }
    println!(
        "{}",
        json!({"scope":"synthetic additional observer diagnostics; no weather/JSON/I/O; not full forecast",
        "points":points,"steps":20,"fields":diagnostic::FIELDS,"trials":trials,"modes":modes,
        "serial_parallel_bits_equal":true,"os":std::env::consts::OS,"arch":std::env::consts::ARCH,
        "debug_assertions":cfg!(debug_assertions)})
    );
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("bench_observer_diagnostics: {e}");
        std::process::exit(1);
    }
}
