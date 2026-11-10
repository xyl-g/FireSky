//! In-memory timeline benchmark. No Python, JSON, tensor I/O, or weather scoring.
use chrono::NaiveDate;
use firesky_native::timeline::{self, ModelTimes, Point, PreparationOptions};
use std::hint::black_box;
use std::time::Instant;

fn main() {
    let date = NaiveDate::from_ymd_opt(2026, 11, 9).unwrap();
    let zone = "Asia/Shanghai".parse().unwrap();
    // Same 145*253 China production rectangle, 0.25-degree spacing.
    let points: Vec<_> = (0..145)
        .flat_map(|row| {
            (0..253).map(move |column| Point {
                lat: 18.0 + f64::from(row) * 0.25,
                lon: 73.0 + f64::from(column) * 0.25,
                elevation_m: 0.0,
                timezone: zone,
            })
        })
        .collect();
    let hours: Vec<_> = (-8..=16).map(|value| f64::from(value) * 3.0).collect();
    let model = Some(ModelTimes {
        cycle_epoch: date.and_hms_opt(0, 0, 0).unwrap().and_utc().timestamp() as f64,
        hours: &hours,
    });
    for (share_ephemeris, parallel) in [(false, false), (true, false), (false, true), (true, true)]
    {
        let options = PreparationOptions {
            share_ephemeris,
            parallel,
        };
        let run = || {
            timeline::prepare_with_options(black_box(&points), date, false, model, options).unwrap()
        };
        black_box(run());
        let mut times = Vec::new();
        let mut checksum = 0.0;
        for _ in 0..7 {
            let start = Instant::now();
            let result = run();
            times.push(start.elapsed().as_secs_f64() * 1000.0);
            checksum = result.rows.iter().map(|row| row.anchor).sum::<f64>();
            black_box(result);
        }
        times.sort_by(f64::total_cmp);
        println!("points={} steps=20 cache={share_ephemeris} parallel={parallel} median_ms={:.3} checksum={:.6} trials_ms={times:?}",
                 points.len(), times[3], checksum);
    }
}
