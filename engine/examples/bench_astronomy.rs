//! Warm in-process UTC components, excluding startup, IPC and result assembly.
use chrono::{Duration, TimeZone, Utc};
use firesky_native::solar_time::{glow_window, position, Point};
use serde_json::json;
use std::{hint::black_box, time::Instant};

fn measure(mut run: impl FnMut() -> f64) -> serde_json::Value {
    black_box(run());
    let mut trials = Vec::new();
    let mut checksum = 0.0;
    for _ in 0..7 {
        let start = Instant::now();
        checksum = black_box(run());
        trials.push(start.elapsed().as_secs_f64() * 1000.0);
    }
    let mut ordered = trials.clone();
    ordered.sort_by(f64::total_cmp);
    json!({"median_ms":ordered[3],"trials_ms":trials,"checksum":checksum})
}

fn main() {
    let point = Point {
        lat: 31.23,
        lon: 121.47,
        elevation_m: 50.0,
    };
    let start = Utc.with_ymd_and_hms(2026, 11, 9, 8, 0, 0).single().unwrap();
    let times: Vec<_> = (0..10000)
        .map(|i| start + Duration::seconds(i % 7200) + Duration::microseconds(123456))
        .collect();
    let positions = measure(|| {
        times
            .iter()
            .map(|&time| {
                let values = position(black_box(point), black_box(time));
                values[0] + values[1]
            })
            .sum()
    });
    // Event anchor is constructed by the same Rust event API, outside timing.
    let anchor = firesky_engine::astronomy::events(firesky_engine::astronomy::EventsParams {
        point: firesky_engine::astronomy::Point {
            lat: point.lat,
            lon: point.lon,
            elevation_m: point.elevation_m,
        },
        date: "2026-11-09".into(),
        timezone_name: "Asia/Shanghai".into(),
        phenomenon: firesky_engine::astronomy::Phenomenon::Dusk,
    })
    .unwrap();
    let anchor =
        firesky_engine::time_contract::parse_utc(anchor["anchor_time_utc"].as_str().unwrap())
            .unwrap();
    let windows = measure(|| {
        (0..10)
            .map(|_| {
                let window = glow_window(
                    black_box(point),
                    black_box(anchor),
                    false,
                    0,
                    black_box([80.0, 6000.0, 0.0, 50.0]),
                )
                .unwrap()
                .unwrap();
                window.duration_minutes + window.shadow_speed + window.maximum_distance
            })
            .sum()
    });
    println!(
        "{}",
        json!({"scope":"warm in-process components, not full forecast",
        "samples":10000,"windows":10,"positions":positions,"glow_windows":windows})
    );
}
