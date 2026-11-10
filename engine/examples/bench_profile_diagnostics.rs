//! Synthetic selected-column component benchmark, not an end-to-end forecast.
use firesky_native::profile_diagnostics::{FramePair, SelectedSample, Stencil};
use std::hint::black_box;
use std::time::Instant;

fn main() {
    let mut args = std::env::args().skip(1);
    let points: usize = args.next().map(|v| v.parse().unwrap()).unwrap_or(36685);
    let trials: usize = args.next().map(|v| v.parse().unwrap()).unwrap_or(3);
    assert!((1..=100000).contains(&points) && (1..=15).contains(&trials));
    let rows = 192;
    let cols = 192;
    let cells = rows * cols;
    let levels = [
        200.0, 250.0, 300.0, 400.0, 500.0, 600.0, 700.0, 750.0, 800.0, 850.0, 925.0,
    ];
    let frame = |time: usize| -> Vec<f32> {
        (0..cells * levels.len() * 24)
            .map(|i| {
                let level = (i / 24) % levels.len();
                let cell = i / (24 * levels.len());
                let n = ((cell * 13 + level * 7 + time * 11) % 101) as f32;
                match i % 24 {
                    0 => 85.0,
                    1 => 12000.0 - level as f32 * 1000.0,
                    3 => -0.35,
                    6 => {
                        if n > 30.0 {
                            n
                        } else {
                            0.0
                        }
                    }
                    7 | 15 => 90.0,
                    11 | 12 => n * 1e-7,
                    13 => n / 100.0,
                    14 => levels[level],
                    _ => 0.0,
                }
            })
            .collect()
    };
    let first = frame(0);
    let second = frame(1);
    let columns = vec![[0.0; 36]; cells];
    let samples: Vec<_> = (0..points * 20)
        .map(|i| {
            let cell = (i / 20) % cells;
            SelectedSample {
                stencil: Stencil::new(
                    [(cell / cols) as f32 + 0.25, (cell % cols) as f32 + 0.75],
                    [0.0, 0.0],
                    1.0,
                    rows,
                    cols,
                )
                .unwrap(),
                time_weight: (i % 20) as f32 / 20.0,
            }
        })
        .collect();
    let pair = FramePair {
        profiles: [&first, &second],
        columns: [&columns, &columns],
        levels: &levels,
        average_envelope: false,
    };
    let expected = pair.diagnose_many(&samples, false).unwrap();
    let expected_bits: Vec<_> = expected.iter().flatten().map(|v| v.to_bits()).collect();
    let checksum: f64 = expected.iter().flatten().map(|&v| f64::from(v)).sum();
    drop(expected);
    println!(
        "points={points} steps=20 levels={} cells={cells} profile_bytes={} checksum={checksum:.15}",
        levels.len(),
        (first.len() + second.len()) * 4
    );
    for parallel in [false, true] {
        let warm = pair.diagnose_many(&samples, parallel).unwrap();
        assert!(warm
            .iter()
            .flatten()
            .map(|v| v.to_bits())
            .eq(expected_bits.iter().copied()));
        drop(warm);
        let mut times = Vec::new();
        for _ in 0..trials {
            let started = Instant::now();
            let output = black_box(pair.diagnose_many(black_box(&samples), parallel).unwrap());
            times.push(started.elapsed().as_secs_f64() * 1000.0);
            assert!(output
                .iter()
                .flatten()
                .map(|v| v.to_bits())
                .eq(expected_bits.iter().copied()));
        }
        println!("parallel={parallel} trials_ms={times:?}");
        times.sort_by(f64::total_cmp);
        println!(
            "parallel={parallel} median_ms={:.6}",
            times[times.len() / 2]
        );
    }
}
