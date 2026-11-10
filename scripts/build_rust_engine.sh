#!/usr/bin/env bash
# Build the Python-free engine executable. ecCodes is loaded only for GRIB I/O.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cargo build --manifest-path "$ROOT/engine/Cargo.toml" --release --locked
printf 'Rust engine: %s/engine/target/release/firesky-engine\n' "$ROOT"
