//! Complete cached-input planning, excluding process startup/output encoding.
use firesky_engine::input_plan::{execute, Params};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, path::PathBuf, time::Instant};

fn stable(value: &mut Value) {
    value.as_object_mut().unwrap().remove("elapsed_ms");
    value.as_object_mut().unwrap().remove("timings_ms");
}
fn main() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 2 {
        return Err("usage: bench_input_plan REQUEST ITERATIONS".into());
    }
    let path = PathBuf::from(&args[0]);
    let iterations: usize = args[1].parse().map_err(|_| "invalid iterations")?;
    if !(1..=1000).contains(&iterations) {
        return Err("iterations must be 1..1000".into());
    }
    let prime = Instant::now();
    let mut expected = execute(Params { path: path.clone() })?;
    let first_call_ms = prime.elapsed().as_secs_f64() * 1000.0;
    if expected["inputs_located"] != true {
        return Err("benchmark needs complete cached inputs".into());
    }
    stable(&mut expected);
    let encoded = serde_json::to_vec(&expected).map_err(|e| e.to_string())?;
    let hash: String = Sha256::digest(&encoded)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let mut ms = 0.0;
    let mut phases = BTreeMap::<String, f64>::new();
    for _ in 0..iterations {
        let start = Instant::now();
        let mut result = execute(Params { path: path.clone() })?;
        ms += start.elapsed().as_secs_f64() * 1000.0;
        for (key, value) in result["timings_ms"]
            .as_object()
            .ok_or("missing phase timing")?
        {
            *phases.entry(key.clone()).or_default() +=
                value.as_f64().ok_or("invalid timing")? / iterations as f64;
        }
        stable(&mut result);
        if result != expected {
            return Err("planning result changed".into());
        }
    }
    println!(
        "{}",
        json!({"ms_per_plan":ms/iterations as f64,"first_call_ms":first_call_ms,"iterations":iterations,
        "phase_mean_ms":phases,"plan_identity":{"bytes":encoded.len(),"sha256":hash},
        "measurement":"warm-input-planning-only","network":false})
    );
    Ok(())
}
