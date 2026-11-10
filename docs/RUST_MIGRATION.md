# Rust Engine Migration

## Status

Stages 1 through 54 are an experimental, runnable migration spanning shared
numerical primitives; GFS, EC and CAMS input/derivation paths; point and
regional assessment/materialization; diagnostics and report contracts; cache,
location, and desktop IPC operations; and a substantial HSD nowcast path.
Stage 53 adds Rust multi-scan calibrated retrieval, timeline selection,
provenance and adjustment assembly, and Stage 54 adds the opt-in native batch
per-scan assessment operation over versioned 27-column feature rows. Rust also
owns the nowcast event/instant quality ledger by default. These stages are not
a replacement for the complete forecast engine: production scheduling,
portions of satellite processing, report/application orchestration and several
runtime entrypoints still require Python, and the new batch assessment remains
opt-in with the Python path as the default.

The desktop application and HTTP API still use Python by default. SwiftUI and
WinUI clients have opt-in Rust routes for selected location, inventory and
nowcast operations; their full service lifecycle and the Qt/web interfaces
remain Python-owned.

The overall Python-retirement objective, remaining module inventory and
completion gates are tracked in [RUST_MIGRATION_PLAN.md](RUST_MIGRATION_PLAN.md).
The goal remains incomplete until production workflows no longer require
Python; passing a migration stage is not overall acceptance.
Measured standalone performance and implemented optimizations are recorded
in [RUST_PERFORMANCE.md](RUST_PERFORMANCE.md). They do not substitute for
full-forecast or production Python-retirement acceptance.

For a concise Chinese closeout of migrated, remaining and deliberately
external/platform-owned boundaries, see
[RUST_MIGRATION_STATUS.zh-CN.md](RUST_MIGRATION_STATUS.zh-CN.md).

Implemented:

- Optional direct native-client location routing to a separate bounded Rust
  desktop-protocol process, with original search/metadata payloads, handshake
  validation, cooperative cancellation and no silent Python fallback.
- Complete read-only desktop GFS/EC/CAMS inventory through that direct
  native-client route, preserving cycle/hour/coverage/path/area fields and
  distinct total-versus-indexed counts in one directory traversal per root.
- Opt-in desktop cache checks against resolved event plans, including GFS
  domain/schema coverage, EC cycle/hour admission, fast/strict CAMS bracket
  selection and full independent/paired-model reports. Event planning and
  session/confirmation ownership still require Python.
- Independent fast/full location search, point metadata with local geographic
  timezone/SRTM fallback, shared schema-2 location caches, safe SRTM download
  and raster interpolation, and full pinned offline timezone data. Scalar
  and bounded batch timezone queries run without Python; production location
  consumers/resource packaging have not been switched.
- GFS NOMADS URL generation, shared raw-cache metadata/index lookup, advisory
  locks, bounded retry downloads and explicit-hour resolution in Rust, with
  an opt-in Python bridge.
- Automatic GFS event-hour/domain/cycle and terrain planning, local compact
  CAMS cache selection and an independent cache-resolved single-point file
  workflow. Optional ADS retrieval fills missing CAMS inputs; production
  orchestration remains unfinished.
- CAMS ADS v1 availability/run selection, bounded authenticated job submission,
  polling and asset transfer, multi-lead batching, strict per-lead validation,
  atomic caching and explicit neighboring-lead fallback.
- Public/basic EC Open Data index selection and Range transfers, compatible
  three-part raw bundles/sidecars, strict header-based cache admission and
  404-only event-cycle fallback, with a separate opt-in bridge.
- One-request EC point event/source-domain/CAMS planning, batched strict raw
  admission/download and a Python-free EC-specific report/quality/Web-report
  result. A separate native service export adds full legacy EC provenance fields,
  HTML, profile-aware response assembly and integrity-bound profile lookup;
  immutable response generations now support durable reuse, refresh and
  registered full-profile reads. Production HTTP, bounded retention and
  result/cache task lifecycle remain unfinished.
- Explicit source/decoder/code-bound GFS/EC model-derived caches, preserving
  every coordinate, terrain field and 36/24-field tensor. Solar/CAMS/scoring/
  diagnostic/report computation remains fresh on a derived-cache hit.
- Separate complete EC service snapshots with checksummed arrays, profiles,
  provenance, report and HTML; atomic per-key publication, optional TTL,
  forced refresh and generation-bound profile tokens. Explicit-cycle hits
  can run without raw GRIB files or ecCodes. A hit is a verified historical
  response, not verification of current raw-input freshness.
- Complete theoretical pressure-profile cross-sections, including all field
  matrices, adaptive pressure families, spherical horizon/terrain occlusion,
  illumination and target phase/status. Stored assessments publish a verified
  per-hour source descriptor; this is not the Python combined profile mmap or
  the completed production cross-section route.
- Reuse of `native/` as both a Rust library and the existing C-ABI library.
- Continuous 20-minute event-window selection, stable offset sorting and
  scattering weights back to the caller's order.
- Duration response, calibrated quality response and its inverse.
- Cloud-amount interpolation and quality-level labels in Rust. These two
  functions are not yet routed from the Python application.
- A standalone `firesky-engine` JSON-lines executable.
- GRIB reading through the ecCodes C API: metadata, float32 values,
  latitude/longitude arrays, explicit bitmap masking and owned handle cleanup.
- Differential Python/Rust tests, including synthetic multi-message GRIB
  files with missing pixels.
- GFS canonical grid assembly: pressure/diagnostic/surface/layer field
  selection, float32 coordinate normalization and last-message-wins field keys.
- Rust observer bbox selection, geometry and the 20-field ray-tracer input
  layout, including 300 hPa aggregate high-cloud fallback only for missing
  pressure-level coverage.
- Float32 cloud presence, cloudy-part blocker opacity and effective profile
  amounts, exposed to the existing regional derivation through C ABI.
- Vertical pressure-profile classification: adaptive 400 hPa family assignment,
  relative high-deck structure, deep risk, thickness, nine material classes,
  column material and isolated high/middle/low material signals.
- Resolved cloud-body risk across adjacent connected families and the
  fully connected deep-column material cap.
- Complete canonical-grid derivation in Rust: field alias/provenance handling,
  log-pressure boundary heights, bracketed unsampled-layer recovery, sparse
  high-cloud spatial support, contiguous decks, significant-member geometry,
  family winds, material/body signals and the 36/24-field output contracts.
- Standalone `derive_gfs`, from stored GRIB through both regional tensors,
  without Python preparation or numerical helper calls.
- Separate caches/identities for experimental GFS and cloud backends.
- Bounded NPY tensor bundles for decoded grids and complete derived tensors,
  with source/file SHA-256, strict shapes and a manifest published last.
- EC public/basic input validation, native tensors and explicit TCC/RH proxy
  grid assembly in Rust. No aggregate layers or condensates are invented.
- Independent stored-input EC-basic derivation, including file-backed outputs,
  plus an opt-in Python transport bridge and isolated cache identities.
- Independent EC offline full-catalog tensors, including model-level
  cc/clwc/ciwc, hybrid A/B, lnsp and diagnostic 2t/2d. This is an input
  contract, not model-level forecast scoring or a public Open Data product.
- Compact production CAMS AOD550 and eleven-layer extinction decoding in
  float64, shared spatial sampling, float32 temporal blending and scale-height
  fitting; standalone file-backed model-grid optical sidecars.
- Expanded 31-field CAMS float64 decode, nearest-point selection, spectral
  AOD interpolation, Angstrom exponent, pressure-profile integration,
  middle-layer diagnostics, air mass, direct transmission and approximate
  Henyey-Greenstein phase function. These diagnostics do not feed forecast scores.
- Profiled tensor writing, with at most two concurrent file-sync workers and
  a 64 KiB write buffer; every sync must succeed before manifest publication.
- EC records share unchanged raw/converted values and allocate a private
  converted buffer only for unit conversion or RH clipping.
- Shared Rust geometric solar position, apparent sunrise/sunset events,
  elevation-threshold searches, shadow speed and typical/target glow windows.
- Strict calendar/zoned/UTC parsing, China-facing dates, UTC cycle selection
  and nearest-hour offsets with ties-to-even rounding.
- Regional apparent-event scheduling, timezone/local-date adjustment, twenty
  solar samples and strict model-hour interpolation, shared by C ABI and
  independent file-backed preparation. Bounded shared ephemerides and ordered
  Rayon execution retain the serial output bits.
- Independent stored GFS/EC-basic -> complete cloud columns/profiles -> solar
  samples -> strict CAMS profiles -> all 82 native regional assessment fields.
  Streamed per-frame profile bundles retain diagnostic inputs; parent metadata
  records consumed raw-file identities and explicitly marks the public forecast
  response as incomplete.
- Complete scalar/regional observer-field empirical composition: 75 ordinary
  component keys, two optional scalar ceiling keys and 19 instantaneous
  fields. The stored-input chain now also produces these quality tensors.
  This scorer alone is not the complete public diagnostic/frame payload.
- Additional complete `_fire_cloud_quality` event diagnostics: 20 numeric
  fields plus optics/return-path metadata, 103 ordinary component keys
  (105 with scalar ceilings), exact quality-level labels and empty-result
  behavior. Separate file-backed diagnostic output retains raw provenance.
- Forecast scene, cloud phase, compatible genus, genus proxy, color, cloud
  evolution and quality/score-curve window rules, as independent Rust
  operations. Proxy labels remain diagnostic and do not affect scoring.
- Temporal-then-horizontal selected-column sampling, finite-corner fallback,
  material class, condensate maxima, deep risk and vertical support/uncertainty/
  confidence. Independent stored outputs retain all complete profile children.
- Complete twenty-frame compact encoding, per-observer result dictionaries and
  generated version-8 single-point cores. Results stream one observer at a time
  to a checked JSON companion; existing assessment/diagnostic arrays remain.
- Strict request/core/step validation and complete single-point materialization,
  including evolution, timing windows and fixed-offset step timestamps.
  A separate frozen-input endpoint validates its generated core before publishing.

Still not production-migrated:

- Live-provider acceptance and complete production scheduling/authentication
  for the independently ported GFS/EC/CAMS download paths.
- Production integration of the independently ported EC-basic point path and
  complete GFS/EC regional forecast orchestration.
- Production point/regional/cache orchestration and remaining diagnostic/export
  payloads. Stored-input point materialization does not replace these services.
- Remote HSD discovery/download and raw calibration, compact-cache writes,
  complete production motion/advection orchestration, per-observation
  `assess_timestep` scoring, observation-constrained result/report assembly,
  and end-to-end nowcast acceptance. The Rust multi-scan adjustment and
  event-quality scorer cover only part of this workflow.
- Desktop session, confirmation, cancellation and concurrent-job management.
- HTTP endpoints, SSE and application packaging.

The standalone engine never starts Python. This does **not** mean the complete
FireSky application is Python-free. The GRIB adapter also still needs a native
ecCodes library and its definitions/resources; this is not a zero-C or
single-file deployment.

## Build And Verify

From the repository root:

```bash
bash scripts/build_rust_engine.sh
bash scripts/build_native_variants.sh
cargo test --manifest-path native/Cargo.toml --locked
cargo test --manifest-path engine/Cargo.toml --locked
FIRESKY_REQUIRE_RUST_TESTS=1 .venv/bin/python -m pytest \
  tests/test_rust_migration.py tests/test_rust_cloud_structure.py \
  tests/test_rust_cams.py tests/test_rust_spectral.py tests/test_rust_ec_full.py \
  tests/test_rust_astronomy.py tests/test_rust_timeline.py tests/test_tensor_bundle.py \
  tests/test_rust_assessment.py tests/test_rust_quality.py \
  tests/test_rust_diagnostics.py tests/test_rust_characteristics.py \
  tests/test_rust_profile_diagnostics.py tests/test_rust_frames.py \
  tests/test_rust_point_core.py -q
FIRESKY_EMPIRICAL_BACKEND=rust .venv/bin/python -m pytest tests/test_scoring_invariants.py -q
```

The Python differential tests look for `engine/target/debug/firesky-engine`.
Either build it with `cargo build --manifest-path engine/Cargo.toml --locked`,
or set `FIRESKY_RUST_ENGINE_BIN` to the release executable.
`FIRESKY_REQUIRE_RUST_TESTS=1` makes missing compiled artifacts a failure
instead of a skip.

The initial implementation is locally verified on macOS. Linux and Windows
execution/packaging remain unverified. In particular, Windows ecCodes loading
and non-ASCII file paths need platform testing.

## Standalone Protocol

```bash
engine/target/release/firesky-engine serve
```

Each request is one JSON object on one line:

```json
{"id":"hello","method":"hello"}
{"id":"window","method":"score_window","params":{"values":[0.8,0.8,0.8,0.8,0.1],"active":[true,true,true,true,true],"widths":[3,4,5,6,7]}}
{"id":"grib","method":"decode_grib","params":{"path":"/absolute/path/sample.grib2","include_values":true}}
{"id":"grid","method":"decode_gfs","params":{"path":"/absolute/path/gfs.grib2"}}
{"id":"features","method":"prepare_gfs_features","params":{"path":"/absolute/path/gfs.grib2","observer":[30.25,120.25],"radius_km":300}}
{"id":"derive","method":"derive_gfs","params":{"path":"/absolute/path/gfs.grib2"}}
{"id":"files","method":"derive_gfs_to_files","params":{"path":"/absolute/path/gfs.grib2","output_dir":"/absolute/path/new-gfs-tensors"}}
{"id":"ec","method":"decode_ecmwf_basic_to_files","params":{"path":"/absolute/path/ec.grib2","output_dir":"/absolute/path/new-ec-tensors","bbox":[110,120,40,30],"expected_cycle_utc":"2026-11-07T12:00:00+00:00","expected_forecast_hour":30}}
{"id":"ec-derived","method":"derive_ecmwf_basic_to_files","params":{"path":"/absolute/path/ec.grib2","output_dir":"/absolute/path/new-ec-derived","bbox":[110,120,40,30]}}
{"id":"ec-full","method":"decode_ecmwf_full_to_files","params":{"path":"/absolute/path/offline-l137.grib","output_dir":"/absolute/path/new-ec-full","bbox":[110,120,40,30]}}
{"id":"cams","method":"decode_cams_to_files","params":{"path":"/absolute/path/cams.grib","output_dir":"/absolute/path/new-cams"}}
{"id":"cams-map","method":"map_cams_to_files","params":{"path":"/absolute/path/cams.grib","target_manifest":"/absolute/path/model/manifest.json","output_dir":"/absolute/path/new-cams-map"}}
{"id":"cams-time","method":"interpolate_cams_to_files","params":{"left_path":"/absolute/path/left.grib","right_path":"/absolute/path/right.grib","target_manifest":"/absolute/path/model/manifest.json","target_time":"2026-11-08T13:00:00Z","output_dir":"/absolute/path/new-cams-interpolated"}}
{"id":"spectral","method":"decode_cams_spectral_to_files","params":{"path":"/absolute/path/expanded.grib","output_dir":"/absolute/path/new-spectral","expected_valid_time":"2026-11-09T00:00:00Z"}}
{"id":"spectral-point","method":"diagnose_cams_spectral","params":{"path":"/absolute/path/expanded.grib","observer":[30,120],"wavelengths_nm":[440,500,550,670,865],"solar_elevation_deg":5,"scattering_angle_deg":30}}
{"id":"events","method":"solar_events","params":{"point":{"lat":31.23,"lon":121.47,"elevation_m":50},"date":"2026-11-09","timezone_name":"Asia/Shanghai","phenomenon":"dusk"}}
{"id":"solar","method":"solar_snapshots","params":{"point":{"lat":31.23,"lon":121.47},"times":["2026-11-09T09:00:00Z"],"timezone_name":"Asia/Shanghai"}}
{"id":"crossing","method":"solar_crossing","params":{"point":{"lat":31.23,"lon":121.47},"anchor":"2026-11-09T09:00:00Z","target_elevation_deg":-5,"phenomenon":"dusk"}}
{"id":"speed","method":"solar_shadow_speed","params":{"point":{"lat":31.23,"lon":121.47},"time":"2026-11-09T09:00:00Z"}}
{"id":"glow","method":"solar_glow_window","params":{"point":{"lat":31.23,"lon":121.47,"elevation_m":50},"anchor":"2026-11-09T09:00:00Z","timezone_name":"Asia/Shanghai","phenomenon":"dusk","window":{"kind":"typical","edge_distance_km":80,"cloud_base_m":6000}}}
{"id":"cycle","method":"time_contract","params":{"operation":"choose_cycle","reference":"2026-11-09T10:00:00Z","cycle_hours":[0,6,12,18],"availability_lag_hours":5}}
```

Results use `{"id": "...", "type": "result", "result": {...}}`.
Errors use `{"id": "...", "type": "error", "error": {"code": "...", "message": "..."}}`.
Standard output contains only protocol messages; diagnostics use standard error.

`score_window` consumes already-computed **per-step pre-response indices**,
activity flags and support widths in minutes. It is not a weather predictor:
it only applies window selection, duration response, calibration and the
quality label. Offsets, if supplied, only order the samples; widths already
describe their time support. Inactive gaps remain on the event axis.

`decode_grib` defaults to metadata and missing-value counts. With
`include_values=true`, each field also includes flat row-order arrays of
values, latitudes and longitudes. Missing/nonfinite float32 values become JSON
`null`, never numeric missing-value sentinels. Coordinates retain the native
ecCodes longitude convention. This is not yet the model-specific tensor
layout consumed by the ray tracer.

`decode_gfs` returns normalized float32 coordinates and a list of
`{level_type, level, name, values}` fields compatible with `_DecodedGfsGrid`.
Unsupported messages are ignored. Unlike the Python reference, every retained
message's normalized coordinate layout and cycle/forecast step are checked,
not merely its point count. Mixed layouts or times fail closed. This is a
deliberately stricter malformed-input contract.

`prepare_gfs_features` performs that decode, observer selection and assembly
without Python. It returns `features` (N x 20) and `cloud_optics` (N x 2:
presence, opacity). Optional/missing feature slots remain JSON `null`.
Required selected `gh`/`r` fields must exist and be finite. Diagnostic-only
pressure levels are retained in grids, but never added to production feature
rows. The bbox semantics, pressure order and feature layout match the Python
reference; this does not yet execute a complete forecast.

`derive_gfs` reads the canonical grid and produces normalized coordinates,
`pressure_levels`, `columns` (cells x 36) and `profiles` (cells x levels x 24).
The complete numerical derivation executes in Rust. Missing values use JSON
`null`. Explicit clear samples, instantaneous-versus-average provenance,
aggregate missing sentinels and isolated local-versus-regional sparse support
retain their reference semantics. This endpoint does not download data,
schedule astronomical events or execute complete forecasts.

`decode_gfs_to_files` and `derive_gfs_to_files` return only `manifest_path`.
They write NPY float32 arrays instead of expanding a regional grid into JSON.
The derivation method retains the native 500000-cell limit but is not subject
to the JSON result's 10000000-scalar limit. `decode_ecmwf_basic_to_files`
similarly writes the EC native/selected coordinates, converted pressure arrays,
terrain, TCC, raw fields and time/unit provenance. The derived EC method sorts
the selected regular grid, applies the existing explicit EC-basic TCC/RH proxy
and produces the shared 36/24-field tensors. Neither method is a forecast.

Set `FIRESKY_ECCODES_LIBRARY` to an explicit ecCodes shared-library path when
automatic loading is unsuitable. An explicit path failure is not silently
replaced with another library. Loading is deferred until a GRIB request, so
scoring works without ecCodes.

Limits:

- One request line: 1 MiB. Oversized lines are drained before the next request.
- One event: 4096 samples; arrays must align and window inputs must be finite.
- `max_messages`: 1..4096, default 128. Exceeding it is an error, not truncation.
- `max_points`: 1..5000000 per field, default 5000000.
- Generic GRIB JSON array output: at most 10000000 points across fields.
- GFS grid assembly: at most 10000000 retained field values, and at most
  500000 observer feature rows. GFS decode defaults to 4096 messages.
- Canonical derivation: at most 500000 cells and the supported subset of
  eleven pressure levels. JSON `derive_gfs` output is additionally capped at
  10000000 column/profile scalar values; larger regional workloads use
  `derive_gfs_to_files`.
- EC basic/full: up to 64000000 retained raw field values, plus necessary converted arrays;
  full-message coordinates and values are checked before bbox selection.
  Decode/derive default to 4096 messages. Missing pixels outside the bbox
  also reject the file.
- EC full catalogs default to 137 model levels. Explicit `model_levels`
  must be the complete positive integer sequence 1..N (no booleans/floats).
  The upper bound is 1346: 54 pressure + 4 surface + 3*N required messages
  must fit the common 4096-message budget. Extra messages still count.
  Hybrid coordinate arrays are bounded at 2*(N+1) before allocation.
- Tensor bundles: up to 1 GiB of array/JSON-companion files, 128 arrays and a 256 KiB manifest.
  Individual NPY shapes have 1..3 dimensions, positive extents up to 5000000.
- Compact CAMS: exactly twelve required messages, at most 5000000 source cells
  and dense sampling cells, and at most 500000 target cells. Unusual sparse
  coordinates whose dense Cartesian expansion exceeds the limit are rejected.
- Expanded CAMS: all 31 fields required, `max_messages` default 4096,
  `max_points` default 5000000 and a 100000000-value retained budget including
  the two coordinate channels (33 channels in total). The retained budget
  limits this catalog to 3030303 cells. Diagnostic AOD queries are capped at
  1024; native profile helpers accept at most 256 levels.
- Requests run sequentially. `hello` advertises implemented numerical and
  stored-input methods, including experimental frame/core assembly.
  Unsupported forecast/session/cancellation methods return errors.

The envelope resembles desktop IPC, but capability coverage is deliberately
different. Do not point the existing desktop client at this executable yet.

## Experimental Python Integration

```bash
FIRESKY_EMPIRICAL_BACKEND=rust .venv/bin/python desktop/app.py
```

This routes scalar/batch event-window selection, duration response and forward/
inverse calibration to the Rust shared library. The rest remains Python.
The default is `FIRESKY_EMPIRICAL_BACKEND=python`; reverting the environment
variable returns to the reference path without a code change.

The Rust backend requires rebuilt native libraries and fails clearly if the
new symbols are absent. It does not silently fall back after Rust errors.
Calibration retains NumPy's NaN/infinity behavior; window selection explicitly
rejects nonfinite arrays. Batch selection currently calls the scalar Rust
routine row by row; no speedup claim is made. Differential checks use floating
point tolerances rather than claiming bitwise-identical arithmetic.

An explicit limitation remains for flat or numerically tied objective values:
NumPy/BLAS and Rust can accumulate dot products in different orders and select
different, equally scoring windows. Their window weights, and therefore
component diagnostics, need not match even if their final objective agrees.
Plateau regression tests check objective equality rather than pretending
weight equality. A shared tie-breaking contract and full-payload validation
are required before making Rust the default.

### GFS And Cloud Backends

```bash
FIRESKY_GFS_DECODE_BACKEND=rust FIRESKY_CLOUD_BACKEND=rust \
  .venv/bin/python desktop/app.py
```

Both variables default to `python` and accept only `python` or `rust`.
`FIRESKY_GFS_DECODE_BACKEND=rust` routes canonical grid decode and GFS client
snapshot assembly to the standalone executable. Set `FIRESKY_RUST_ENGINE_BIN`
to override the default `engine/target/release/firesky-engine`.
The current bridge starts a subprocess per request. Grid decode now uses
NPY files, while small feature results still use JSON;
it is a correctness/migration bridge, not a performance optimization.
The decoder's own code never starts Python.

Rust-decoded grids use separate memory and mmap cache namespaces. Model-derived
caches use separate directories and identities for each experimental backend
combination, preventing reference caches from hiding whether the new paths
were exercised or being overwritten during comparison. Default Python cache
namespaces are unchanged; normal source-fingerprint invalidation still applies.
The current GFS backend is `rust-grid-v2`: binary transport preserves infinity
as well as NaN, unlike the old JSON bridge's conversion of all nonfinite values
to null. Its memory/mmap and derived namespaces therefore exclude old v1
results. Python reference behavior and cache namespaces are unchanged.

`FIRESKY_CLOUD_BACKEND=rust` routes `_presence`, `_blocker_opacity`,
`_effective_profile_cloud_fields`, `_vertical_cloud_structure`,
`_resolved_family_body_deep_risks` and `_apply_connected_deep_material_cap`
to `native/`. The cloud FFI accepts contiguous
float32 buffers, including NaNs; it retains the distinctions between missing,
explicit zero and condensate evidence. High RH alone does not invent cloud
coverage, and horizontal cloud fraction is not multiplied by opacity twice.
The effective-profile input layout is exactly 24 fields.

The structure ABI takes cell-major profiles with six float32 fields per level:
amount, ASL height, liquid, ice, omega and explicit-evidence flag. Pressure
levels are a separate finite 1D array of 1..256 entries; optional family masks
and aggregate deep risk retain their reference semantics. Its ten outputs
match `_vertical_cloud_structure`, with class codes converted back to uint8
by the Python bridge. The recursive family-isolated material calculation runs
in Rust too. Invalid shapes fail clearly, with no Python classifier fallback.
Existing exported ABI signatures are unchanged.

The cloud cache backend identity is now `rust-f32-v3`, isolating complete
derivation from Stage 2's `rust-f32-v1` and Stage 3's `rust-f32-v2` caches.
Python cache identities are unchanged.

With the cloud switch enabled, `derive_grid_columns`,
`derive_grid_layer_profiles` and canonical `_derive_grid_tensor_pair` use the
complete Rust derivation. The Python bridge only validates/looks up fields
and packs contiguous raw buffers; it no longer calls `_prepare_grid_derivation`
or Python's numerical derivation helpers. The paired path derives both outputs
in one call. The `_prepared` private optimization remains relevant only to
the Python reference path. Noncanonical external-adapter hooks retain their
existing explicit adapter boundary. EC-specific normalization is now separately
available through the Stage 5 public/basic adapter.

The new additive `firesky_derive_grid_v1` ABI consumes cell-major 9-field raw
pressure rows, 15-field surface/aggregate metadata, coordinates, pressure
levels and three temporal-semantic flags. Existing exported signatures and
the established 36/24-field tensor layouts are unchanged.

### EC Basic Backend

`FIRESKY_ECMWF_DECODE_BACKEND=rust` routes `decode_ecmwf_ifs_basic_tensor`
through the standalone engine. The default remains `python`. The bridge
validates arguments, transports files and reconstructs the existing dataclass;
it does not invoke Python metadata probing, value conversion, coordinate
selection or numeric preparation. Missing Rust binaries/libraries or invalid
inputs fail explicitly. Full-catalog decoding uses the separate contract
below, not a basic proxy or a Python numerical fallback.

The EC contract still requires all six pressure parameters at the nine public
levels, surface geopotential and entire-atmosphere TCC, with `fc`/`oper`,
regular-grid, units, cycle/step/valid-time and duplicate-field checks. Static
surface geopotential may use step zero. Pressure geopotential is converted by
gravity; fractional TCC becomes percent. RH is clipped only in converted
selected arrays, retaining raw values and all three clipping counters.

The basic proxy is `TCC * clip((RH - 45) / 45, 0, 1)` at each pressure level.
This is not model-level cloud evidence. Condensate fields remain NaN and
high/middle/low aggregates remain absent. Optional 2t/2d are still absent,
matching the current reference decoder's required-field filtering. Surface
TCC is not broadened into the entire-atmosphere contract in this migration.

EC tensor cache identities include backend, Rust binary and bridge/transport
hashes. Regional derived caches have a separate `rust-ec-basic-v1` identity.
Existing raw files, sidecars and reference cache entries are not overwritten.

### EC Offline Full Backend

`decode_ecmwf_ifs_full_catalog_tensor` now also honors
`FIRESKY_ECMWF_DECODE_BACKEND=rust`, using `decode_ecmwf_full_to_files`.
It retains the six pressure parameters at the nine public pressure levels,
all requested model-level cc/clwc/ciwc fields, lnsp, surface geopotential and
required diagnostic 2t/2d. Public TCC is not required or synthesized.

The Python reference had required 2t/2d without recognizing them as fields,
making the full contract unreachable. Stage 8 fixes recognition and K units
for full tensors. Standard ecCodes `heightAboveGround/2/2t,2d` and
`hybrid/0/lnsp` (also `hybridLevel/0/lnsp`) normalize to surface contract
slots while preserving original raw keys. Explicit surface-level-zero forms
remain supported. Basic filtering still excludes optional 2t/2d, so this fix
does not silently change the basic cloud/diagnostic contract.

Every retained field shares the cycle, forecast time, grid and unit contract.
Static geopotential may use step zero. Required field coordinates and
finite/bitmap checks precede cropping; physical terrain/temperature/model-cloud
checks and RH clipping apply to the selected cells, matching the reference.
Extra, unrequested model-level fields receive metadata/time/grid-shape/unit/
duplicate/PV checks, but their coordinates and values are not retained/read.

All requested cc messages require one consistent A/B vector of length
2*(N+1). Water/ice messages may omit it, but any supplied vector must match.
Optional split `hybridA`/`hybridB` metadata is accepted when PV is absent.
Present nonfinite metadata arrays fail closed, including water-message PV
that the Python optional reader can ignore. On this ecCodes build, requesting
NaN PV instead round-trips to a finite zero; the tests verify the resulting
PV conflict, not a native nonfinite guard invocation. Converted coefficients
must remain finite float32.
Model cc converts percent to fraction when needed and must lie in 0..1;
liquid/ice must be nonnegative. Raw values are preserved separately.

The `ecmwf-full-v1` bundle contains 21 float32 arrays: selected/native
coordinates, six pressure matrices, terrain/geopotential, lnsp/2t/2d,
three model-level matrices, half-level A/B and packed raw fields. Metadata
binds model levels, raw keys/units, times, RH diagnostics and
`rust-ec-full-v1`. Python reconstructs reference array mutability, without
metadata probing, value conversion or numeric preparation.

Unchanged EC values borrow their raw buffers internally; only unit changes
or RH clipping allocate converted buffers. This preserves raw provenance,
signed zero and on-disk bytes. The full tensor is not sent through the
basic TCC/RH proxy. No model-level heights, aggregate clouds or complete
forecast are invented. Production cache lifecycle, downloads and capability
probe orchestration still require migration.

### Tensor Transport

`firesky.tensor-bundle.v1` is an interchange format, not a replacement for
forecast cache locking/eviction/identity semantics. `output_dir` must be an
absolute, nonexistent directory. The engine never reuses an existing output,
including an empty directory or a symlink. Failed writes remove only files
created by that writer, not unrelated directory contents.

Each array descriptor contains its relative basename, `<f4` or `<f8` dtype,
C-order shape, exact file size and SHA-256. NaNs, infinities and signed zero
remain binary values. Supported writer targets are little-endian. Source
size/SHA-256 is checked before decoding and again before manifest publication.
Array serialization uses one 64 KiB buffer. At most two owned file-sync
workers overlap with subsequent serialization/hashing. Commit joins all
workers before source revalidation and manifest writing; sync failure,
worker panic or thread-start failure prevents publication. Drop joins
remaining workers before cleanup. No sync or checksum is omitted.
Readers accept only a complete `manifest.json`, reject duplicate keys,
symlinks, path traversal, wrong dimensions/dtype/order and trailing payloads,
and check hashes before loading arrays. Python mappings use validated open
file descriptors instead of reopening a path after validation. These hashes
provide integrity checks, not authentication against an actor who can rewrite
both arrays and manifest.

Rust CAMS target readers load only finite, matching 1D latitude/longitude
arrays, using the `npyz` parser, a bounded 4096-byte NPY header and exact
dtype/shape/order/payload extents. Target manifest and coordinate hashes are
revalidated before publishing the output. Other model arrays are described
but not loaded or independently checksum-validated by this coordinate reader.
It is not a complete model-cache validator.

The opt-in GFS decode bridge now consumes this format. Python
`rust_grid.derive_gfs_tensors(path, output_dir)` returns a validated bundle
with read-only mmap arrays by default; the caller owns files/mapping lifetime.
Persistent forecast cache policy and Python-free cache lifecycle remain work
for later stages.

### CAMS Compact Backend

`FIRESKY_CAMS_BACKEND=rust` enables compact production decode, model-grid
mapping, three-hour optical blending and effective aerosol scale-height
fitting. Default `python` behavior remains available. Missing symbols,
executables or rejected inputs do not trigger a Python numerical fallback.
In-memory grid/probe caches and point/regional application result caches
distinguish `rust-cams-v1`; default Python result keys are unchanged.

The compact contract is one surface-level-zero `aod550` and `aerext532`
in `m**-1` at `[1000,950,925,900,850,800,700,600,500,400,300]` hPa.
All messages must share native float64 coordinates and one UTC validity
time. AOD must lie in 0..100; extinction must be finite and nonnegative;
positive AOD with an all-zero profile is rejected. `validityTime` is HHMM:
`6` means 00:06. The Rust decoder explicitly applies bitmaps, rejecting
missing extinction pixels that the old Python loader can misinterpret as
finite sentinel values. This stricter malformed-input behavior is tested.

`decode_cams_to_files` writes native float64 `latitudes`, `longitudes`,
`aod550` and pressure-major `aerext532` (11 x cells).
`map_cams_to_files` reads target coordinates directly from a model tensor
manifest, normalizes longitude for sampling and emits float32 AOD,
cell-major profiles and a 0/1 float32 coverage array. Out-of-domain points
remain uncovered even when their clamped sampled values are finite.
Missing source cells use the reference nearest-cell fallback; duplicate
source coordinates retain last-assignment semantics.

`interpolate_cams_to_files` requires the exact UTC three-hour bracketing
valid times for an aware RFC3339 target (offsets accepted, precision at most
microseconds). Both source grids must cover every target cell. Blending is
`left + (right-left)*float32(weight)`, including at exact brackets, rather
than a different algebraic expression or nonfinite-value shortcut. Model
coordinates, left source and right source are all revalidated before manifest
publication. The output binds both source hashes and target-manifest identity.

One native index matrix is shared by all twelve channels, avoiding twelve
floating-point source matrices. Sampling uses float64 intermediates before
float32 conversion, with reference coverage checks at the same stages.
The legacy scale-height fit uses float64 log/sqrt/power operations and a
1.2 km default, retaining existing thresholds and bounds; differential tests
allow small platform-math differences. This fitting helper is not substituted
for full-profile light-path integration.

Downloads, filename/area selection, production cache lifecycle, full temporal
sample orchestration and point-source formatting still execute in Python
when using the application bridge. The standalone sidecar methods accept
explicit files and target time, not an automatic forecast request. The
expanded `cams_optics.py` spectral/SSA/asymmetry catalog is a separate,
Stage 7 contract with a different pressure set.

### CAMS Expanded Backend

The same `FIRESKY_CAMS_BACKEND=rust` switch now routes expanded decoding and
the numerical helpers in `cams_optics.py` to Rust, without numerical fallback.
Python still performs download/request construction, cache sidecar identity
and checksum validation, and application orchestration.

The expanded catalog requires surface AOD at `[355,440,550,670,865]` nm;
extinction at `[355,532,1064]` nm on `[1000,925,850,700,600,500,400,300]` hPa;
and surface `ssa550`/`asymmetry550`. Unlike the compact profile, it has no
950/900/800 hPa fields. Duplicate, missing, misleveled, mixed-grid/time,
nonfinite and out-of-range fields fail closed. Explicit bitmaps reject
missing extinction pixels rather than accepting finite missing sentinels.
Unrequested fields are ignored, subject to the message limit.

`decode_cams_spectral_to_files` writes eight float64 arrays:
`latitudes`, `longitudes`, `aod` (5 x cells), three `aerext` arrays
(8 x cells), `ssa550` and `asymmetry550`. Normalized coordinates, AOD message
insertion order and writable Python dataclass arrays match the reference.
Raw/offline decoding permits absent validity keys; an optional
`expected_valid_time` requires an actual matching time. Production cache
validation still requires its existing time/identity/checksum contract.

`diagnose_cams_spectral` consumes an explicit file and point, returning
nearest source values, log/log AOD interpolation without extrapolation,
profile optical depths, 2..8 km boundary-interpolated diagnostics, Angstrom
exponent, relative air mass, direct transmission and an approximate phase
function. Clear/insufficient positive AOD produces per-component null/error
results, not fabricated optical values. Nonfinite air mass is null with an
explicit finite flag. Profile geometry uses the reference standard-atmosphere
exponent 0.190263, not the compact scale-height helper's approximation.

Every output retains `score_consumed=false`. The Henyey-Greenstein value is
an approximation from CAMS' asymmetry moment, not an observed angle-resolved
phase function or an empirical forecast score. This is a diagnostic endpoint,
not a complete forecast or automatic source selector.

### Astronomy And Time Backend

`FIRESKY_ASTRONOMY_BACKEND=rust` routes the existing point geometry and time
entrypoints to Rust. The default remains `python`; invalid backend names,
missing artifacts and rejected inputs never cause a numerical fallback.
Solar position, UTC crossings, shadow speed and glow-window calculations use
four additive C ABI symbols in the existing native library (ABI 3 retained).
Events, parsing and cycle/date operations use the standalone debug engine.
These low-frequency Python bridge operations still pay subprocess startup
cost; the Rust engine calls their implementations in-process.

The shared UTC core lives in `native/src/solar_time.rs`; both C ABI and
standalone window outputs use it. Microsecond epochs cross the ABI as i64,
not lossy floating-point timestamps. The geometry contract retains 42 crossing
iterations, 48 illumination-inverse iterations, ties-to-even timedelta halving,
and the reference's second-resolution solar position (subseconds do not change
the position). This is compatibility, not subsecond astronomical accuracy.
The existing target-window max/units order, including top below base, is
preserved; correcting that would be a separate scoring change.

Solar formulae are modified translations of installed Astral 3.2; source
attribution and its Apache-2.0 license are in `third_party/astral/`. These notices
must be included in future release packaging. Point event selection preserves
the full `sun()` precondition: civil dawn, sunrise, sunset and civil dusk must
all exist even if the caller only requests one sunrise/sunset anchor. Polar or
missing-twilight cases are errors, not invented events. Native caller transport
failures remain RuntimeError; rejected astronomical inputs become ValueError.

Chrono-tz 0.10.4 embeds IANA **2025b**, advertised by `hello.astronomy`.
UTC-to-local conversion avoids ambiguous-local-time guessing; historical
second-resolution timezone offsets are preserved in event/snapshot output.
The verified Python reference is **3.15.2**, including exact `24:00:00`
normalization to the next day. Older Python parser compatibility and zones
changed relative to this pinned timezone database remain acceptance gaps;
this does not establish global timezone parity or justify a default switch.

The bounded JSON API rejects unknown fields, invalid coordinates, calendar
overflow, unknown zones and invalid phenomena. Snapshots/offset requests allow
at most 4096 times; crossing searches allow 0..1440 minutes. Cycle hours must
be a nonempty ascending unique subset of 0..23. Zoned timestamps require
seconds, at most six fractional digits and offsets within +/-14h; UTC input
requires literal Z or +00:00 (not -00:00).

Point, regional and in-memory forecast response identities retain their
unchanged Python keys by default. Rust astronomy adds its contract plus engine,
native-library and bridge SHA-256 identities, preventing old Python responses
or different compiled artifacts from hiding a migration test behind a cache hit.
This is result isolation, not a migrated production cache lifecycle.
As with the existing native loader, rebuild artifacts before starting a process;
hot replacement of an already-loaded shared library is not supported.

### Regional Solar Timeline Backend

The same `FIRESKY_ASTRONOMY_BACKEND=rust` switch now routes
`national_batch.prepare_solar_event_timeline` and `prepare_solar_samples`
through `firesky_regional_timeline_v1`. The shared implementation is
`native/src/timeline.rs`; no Python solar preparation or interpolation helper
is called on this path. Existing default behavior remains Python.

The contract deliberately retains the **regional**, not point/full-sun,
semantics:

- Dawn offsets are -75..20 minutes and dusk offsets -20..75, in five-minute
  steps. Observer elevations are rounded to float32 before event calculation.
- Timezone offsets use local noon; local midnight uses ZoneInfo-compatible
  fold=0, including the pre-transition offset for nonexistent clocks. Adjacent
  UTC-date retries and a bounded local-date crossing fallback are preserved.
  A known legacy exception remains: Apia's skipped 2011-12-30 can yield an
  event on the next local date. Migration parity does not prove requested-day
  correctness for nonexistent calendar days; tightening it needs a separate
  contract change.
- Regional hour angles are clamped in polar cases, and the anchor sample is
  forced valid. This legacy policy is explicitly labeled in bundle metadata;
  it is not evidence of a physical sunrise/sunset in the polar night/day.
- Position uses the regional whole-second epoch Julian formula. Evaluation
  timestamps retain the original local-midnight/minute arithmetic order.
- Hours must be nonempty, finite and strictly increasing. Left insertion and
  absolute 1e-7-hour exact-match tolerance retain their asymmetric endpoints.
  Uncovered valid samples fail the entire request; uncovered invalid samples
  remain inactive. All nine float32 sample columns are retained.
- Local AOD **0.12 remains a placeholder**, not observed CAMS data. The complete
  forecast must replace it and attach the extinction sidecar before scoring.

The standalone debug method accepts a bounded **file**, avoiding oversized
JSON-line messages for the 36,685-point production domain:

```json
{"id":"regional","method":"prepare_regional_solar_to_files","params":{"path":"/absolute/request.json","output_dir":"/absolute/new-bundle"}}
```

Request file example:

```json
{"points":[{"lat":31.23,"lon":121.47,"elevation_m":50,"timezone_name":"Asia/Shanghai"}],"date":"2026-11-09","phenomenon":"dusk","cycle_utc":"2026-11-09T00:00:00Z","hours":[0,3,6,9,12,15,18,21,24]}
```

`cycle_utc` and `hours` must be present together or both omitted/null. Requests
allow 1..500,000 points, up to 4096 model hours and 64 MiB of JSON; unknown
fields, symlink inputs and existing output directories are rejected. C ABI
also supports empty point sets for existing in-memory caller compatibility.
The `regional-solar-v1` bundle contains observers (N,3), elevation/azimuth,
valid and evaluation epochs (N,20), anchors (N), and optional samples
(N,20,9). Valid is float32 on disk, bool in the Python in-memory API.
Request SHA-256, array hashes and manifest-last publication use the existing
tensor transport. This identity proves the request bytes, not weather provenance.

Shared ephemerides cover at most 172,800 whole seconds (2.64 MiB), only when
the sample count justifies dense reuse. Sparse or wide-span requests bypass
the table. At least 1024 points enable ordered parallel processing through
the existing Rayon pool; smaller batches remain serial. Execution choices
are exposed to the Rust profiling example, not as a production backend switch.

## Stored Regional Assessment

The experimental debug method `assess_stored_regional_to_files` now connects
the migrated numerical components without a Python subprocess:

```json
{"id":"assessment","method":"assess_stored_regional_to_files","params":{"path":"/absolute/request.json","output_dir":"/absolute/new-result"}}
```

Example request file (paths refer to already available, matching raw files):

```json
{"model":"gfs","cycle_utc":"2026-11-09T00:00:00Z","date":"2026-11-09","phenomenon":"dusk","radius_km":200,"bbox":null,"points":[{"lat":31.23,"lon":121.47,"timezone_name":"Asia/Shanghai","elevation_source":"model-grid"}],"frames":[{"hour":9,"path":"gfs-f009.grib2"},{"hour":12,"path":"gfs-f012.grib2"},{"hour":15,"path":"gfs-f015.grib2"}],"cams":[{"valid_time":"2026-11-09T09:00:00Z","path":"cams-0900.grib"},{"valid_time":"2026-11-09T12:00:00Z","path":"cams-1200.grib"},{"valid_time":"2026-11-09T15:00:00Z","path":"cams-1500.grib"}]}
```

Relative paths resolve against the request file, not the engine's working
directory. `model` is `gfs` or `ecmwf-basic`; the latter retains the basic
TCC/RH proxy and must not be confused with offline full model-level input.
Observer height is either `elevation_source:"observer"` with `elevation_m`,
or `model-grid` without a supplied height. The latter is derived by float64
bilinear sampling of the decoded float32 terrain before solar preparation.
Observers outside the model domain fail rather than inherit an edge cell.

Model frames must have ascending distinct hours, matching actual GRIB cycle/
step, identical coordinates/terrain and common pressure levels. Supported scan
orders are canonicalized into ascending latitude-major cells. The ray tracer
requires a complete rectangular grid with common uniform latitude/longitude
spacing; arbitrary reduced grids and antimeridian-split rectangles are not
supported. Optional `bbox` is `[west,east,north,south]`; EC crops through its
existing bounded raw decoder, while GFS crops **after** bounded raw decoding.
This does not enable arbitrary full-global GFS files beyond decoder limits.

The CAMS catalog is a caller-frozen map of exact three-hour UTC valid times,
not automatic freshest-cycle selection or a migrated production cache.
Every consumed CAMS file's actual valid time must match the catalog, and
AOD550 plus all eleven extinction levels must cover **every model cell**.
Only model frames referenced by valid solar samples need filled optics;
anchor source labels retain their own brackets. Temporal/spatial interpolation
retains float32 arithmetic and replaces valid samples' placeholder AOD.
Absent or invalid CAMS evidence is an error, never a heuristic substitution.

Output `regional-assessment-v1` contains eleven arrays:

| Array | Shape |
| --- | --- |
| `latitudes`, `longitudes` | C |
| `hours` | T |
| `columns` | T,C,36 |
| `aerosol_profiles` | T,C,11 |
| `observers` | N,3 |
| `samples` | N,20,9 |
| `assessment` | N,20,82 |
| `evaluation_epoch` | N,20 |
| `anchor_epoch` | N |
| `aerosol_anchor_brackets` | N,3 |

The last array stores left/right UTC epochs and right weight, permitting the
original per-point `cams-ads ... w=.6f` source label without overflowing a
256 KiB manifest on large point sets. Quality is the constant `cams`.
Globally invalid edge slots are trimmed for native work and restored as
canonical zero rows in the same output allocation. All pressure profiles are
written per frame as `profiles_NNN/manifest.json`, kind
`regional-profiles-v1`, array shape C,L,24. Parent descriptors include child
manifest SHA-256/size; no time chunk is dropped to fit the parent bundle.

Requests are bounded at 64 MiB, 1..500,000 points, 1..64 frames and 1..256
CAMS entries. Raw files must be regular nonsymlink files at most 512 MiB;
individual bundle limits, existing decoder limits and the aggregate parent
1 GiB limit still apply. Consumed files are hashed before/after use and again
before publishing the parent manifest. Failed work removes only owned files,
including provisional profile children; existing output is never replaced.
These are offline artifact integrity checks, not a production cache or a
hostile concurrent-filesystem security guarantee.

Cloud-column derivation now parallelizes independent cells after coherent
spatial-support resolution, using the existing Rayon pool at 1024 cells or more.
Work has at least 128-cell granularity and writes disjoint final output
slices. The explicit serial mode remains available for profiling/bitwise
verification, with ordered error propagation.

The safe native Rust assessment function is reused in-process; its numerical
algorithm and C ABI version are unchanged. Metadata records CPU execution,
IANA 2025b, legacy polar policy and `public_forecast_complete:false`. This
endpoint returns no public forecast JSON, event-duration/window selection,
characteristic labels or complete diagnostic/export payload.

## Complete Empirical Composition

Stage 12 adds two debug capabilities, bringing the advertised total to 25:

```json
{"id":"quality","method":"score_observer_fields","params":{"rows":[],"mode":"regional","apply_duration":true,"local_columns":true,"score_ceiling":false,"radius_km":200}}
{"id":"stored-quality","method":"score_stored_regional_to_files","params":{"path":"/absolute/request.json","output_dir":"/absolute/new-quality"}}
```

The first method accepts events containing samples of exactly 18 float64
fields, in this order:

```text
view, burn, load, material_load, incoming_load, incoming_block,
visibility, vividness, offset_minutes, local_aod550,
local_view, local_burn, local_load, local_material_load, local_incoming_load,
observation_gate, score_ceiling, valid
```

`rows` has shape event x sample x 18; events may have different lengths.
`mode` is `scalar` or `regional` (default). Defaults for the other options are
shown above. Radius must be finite and in (0,20000]. Physical input channels
must be finite; gate/valid must be exactly 0 or 1. The native scorer allows
nonfinite AOD with the reference finite-mean/instant sanitization behavior;
JSON itself cannot represent nonfinite numbers. Unknown fields are rejected.
Debug batches allow at most 4096 events and 32768 total samples, each event at
most 4096 samples; the existing 1 MiB JSON-line limit still applies.

The response contains `events`, `instant` and schema `metadata`. Event results
have an `index` and all 75 ordinary component keys, including exact text,
null local-quality diagnostics when absent, and optional scalar ceiling keys.
An event with no valid samples retains the reference minimal result, not a
fabricated full component dictionary. Instant records retain all 19 fields
and boolean `physically_burnable`. Packed inputs do not expose the Python
private precomputed-instant cache hooks.

Scalar and regional behavior deliberately remain distinct: scalar supports
observation vetoes/ceilings, the inverse-response final ceiling and a zero-width
active-mean fallback. Regional composition retains its own no-active duration
factor, final pre-response clipping and selected-row AOD mean. Optional
`scalar_aod` is a per-event list of original AOD values for scalar finite-mean
semantics when callers have already selected/reordered/duplicated input rows;
it is rejected in regional mode. Local-column quality remains diagnostic and
never replaces the forward-sky index.

The engine enables `serde_json/float_roundtrip`. A regression test checks both
binary64 neighbors of physical thresholds: parsing must not round a value
just below 0.05 onto the visibility floor and thereby change an entire event.

The second method uses the exact stored assessment request/provenance/
cleanup contracts above. It produces `regional-quality-v1`: all eleven
assessment arrays and complete profile children, plus:

| Array | Type | Shape |
| --- | --- | --- |
| `quality` | float64 | N,73 |
| `quality_flags` | float32 | N,4 |
| `instant_quality` | float64 | N,20,19 |

`metadata.quality` defines every column. The 73 numeric slots are the index,
70 ordinary numeric component fields (including two nullable local fields)
and two optional ceiling fields. The five ordinary text components are
constant/source/code expansions from metadata. Flags are `has_samples`,
`local_columns`, `score_ceiling`, `limiting_factor_code`. Consumers must use
these flags to restore minimal empty results, null local values and omitted
ceiling keys; raw numeric slots alone are not the JSON contract. This endpoint
uses regional mode, local columns and duration response, without a scalar
observation ceiling.

The parent preflight adds 3640 bytes per observer to the assessment estimate,
still bounded at 1 GiB. A per-point stack adapter reads the authoritative
82-field float32 ledger without a second full N,20,18 float64 input tensor;
instant results stream to disk rather than retaining another N,20,19 tensor.
Independent event composition uses ordered Rayon execution at 1024 points,
with at least 64 points per task and deterministic first-error reporting.
Repeated field reductions are cached without temporary column vectors;
bitwise tests preserve the previous per-field summation order.

The known NumPy/BLAS near-tied-window selection limitation is **not resolved**.
Metadata explicitly records `numpy_blas_tie_parity_proven:false` and the Rust
serial-f64 window policy. Equal objective values do not establish identical
diagnostic composition. All these outputs remain experimental and
`public_forecast_complete:false`; Python public entrypoints/defaults and the
C ABI version are unchanged.

Still outside this stage: `_fire_cloud_quality` additional ground/optical/
layer diagnostics, `build_national_frame_payloads`, public point-core schemas,
characteristic labels, public time/window payloads, exports and production
cache/service/client integration. A complete empirical component dictionary
is not the complete forecast response.

## Event Diagnostics And Characteristics

Stage 13 adds three methods, for 28 advertised debug capabilities:

```json
{"id":"diagnostics","method":"diagnose_observer_fields","params":{"events":[],"mode":"regional","apply_duration":true,"radius_km":1000}}
{"id":"stored-diagnostics","method":"diagnose_stored_regional_to_files","params":{"path":"/absolute/request.json","output_dir":"/absolute/new-diagnostics"}}
{"id":"phase","method":"forecast_characteristics","params":{"operation":"phase","cloud_ice_kg_kg":0.00001,"cloud_liquid_kg_kg":0.00002}}
```

Each diagnostic event contains `values` (sample x 82 float32 native ledger),
`samples` (sample x 9 float32), `offsets` (sample float64), and optional
`valid_indices`, `burnable_gate` and `score_ceiling`. The default selection is
all rows, not the ledger status; callers supplying a subset must select it
explicitly. Indices may repeat or be unsorted, preserving original selected
row semantics. All ledger/sample values and offsets must be finite. Gates
must be booleans, ceiling lists must have one finite value per original row,
and every selection index must be in bounds. Existing line/event/sample
limits apply (1 MiB line, <=4096 events, <=32768 total samples, <=4096 samples
per event and selected indices).

Scalar mode reproduces the non-precomputed `_fire_cloud_quality` scorer,
including original float32 selected-sample AOD mean before float64 promotion,
vetoes, optional ceilings and local-column diagnostics. Regional mode uses
the existing regional composer as the precomputed score, without a scalar
ceiling or external score veto; the diagnostic active/peak rebuild still
applies the supplied observation gate exactly as the Python wrapper does.
The two paths must not be silently conflated.

Results include `index`, `level`, and all 103 ordinary component keys:
the previous 75, twenty additional numeric diagnostics, seven optics fields
and `observer_return_model`. Scalar ceilings add two keys. The extra numeric
fields cover incoming blocker risk at the first maximum effective burn,
four active-time optical depth/transmission means, three unweighted ground
means and high/middle/low view/burn peaks/means. No valid samples instead
returns index zero, its level and an **empty** component object.

Optical depth/transmission are Beer-Lambert path diagnostics, not radiance or
perceived brightness. Geometry gates are rebuilt before selecting peak/active
samples; raw layer/ground diagnostics are not replaced by the selected score
window. Float32 AOD means use a tested ordered reduction, including lengths
on either side of the eight-lane/128-element boundaries and length 4096.

`diagnose_stored_regional_to_files` retains the Stage 11 input and integrity
rules and all Stage 12 quality arrays. New kind `regional-diagnostics-v1`
adds `quality_diagnostics` (N,20 float64) and `metadata.diagnostics` with
column names, all constant components, quality-level interpretation and
EC-specific source-text replacements. Consumers must combine it with the
quality flags/schema; empty selections must not acquire fabricated fields.
EC component optics retain the original GFS-common fields and three EC
overrides, as the existing wrapper/frame code does. The required source-text
replacement keys and values are explicit in metadata.

Preflight reserves another 160 bytes per observer, within the same 1 GiB
parent bound. Additional diagnostic reductions use the existing Rayon pool
at >=1024 points, minimum 64 points per task, disjoint final output and
deterministic point-ordered errors. The serial mode remains available for
profiling and all-field bitwise verification.

`forecast_characteristics` accepts a tagged `operation` and the owning Python
function's named inputs:

| Operation | Reference |
| --- | --- |
| `level` | `fire_cloud_quality_level` |
| `scene` | `scene_diagnosis` |
| `phase` | `cloud_phase_diagnosis` |
| `genus` | `cloud_genus_diagnosis` |
| `genus_proxy` | `cloud_genus_proxy_diagnosis` |
| `color` | `color_diagnosis` |
| `evolution` | `cloud_evolution_diagnosis` |
| `window` | `score_curve_window` |

Step dictionaries retain unrelated public fields, while unknown outer
operation parameters are rejected. Curves allow <=4096 steps. JSON numeric
inputs must be finite; optional/missing numeric inputs retain the supported
null/default semantics. Time inputs use the bounded ISO calendar contract:
`YYYY-MM-DDTHH:MM:SS[.ffffff]`, either naive or with `Z`/numeric offset. Mixed
naive/aware curves fail. General Python `fromisoformat` extensions outside
this contract have not been accepted as equivalent.

The port preserves stable first-maximum choices, insertion-order genus
candidate lists, proxy/non-probability/no-scoring-effect metadata, quality
override of burn strength, physically-burnable coverage windows, existing
global-best selection even when that instant is inactive, and unmodified
source timestamp text in evolution diagnostics. Crossing times use exact
float-ratio timedelta multiplication with ties-to-even microsecond rounding,
not an intermediate float microsecond count. Naive output stays naive;
aware output retains the crossing's starting offset. Exact `24:00:00`
end-of-day inputs normalize to tomorrow for both naive and aware timestamps.

These independent characteristic operations are not yet wired into complete
public point/frame payload assembly. Complete compact frames, public point-core
validation/materialization, cache/service/client workflows and exports remain
open. Selected-profile sampling was added separately in Stage 14 below.
The NumPy/BLAS near-tied-window caveat and `public_forecast_complete:false`
remain in force; Python application defaults are unchanged.

## Selected Profile Diagnostics

Stage 14 ports the observer-column sampler and `_profile_diagnostic_bundle`.
These sample the observation column independently of the raytracer's remote
cloud target. They must not be replaced with native target material/phase.
The standalone debug operations are:

- `diagnose_profile_columns`: bounded inline profile/aggregate tensors,
  per-point nine-field samples and four-corner coordinate stencils.
- `diagnose_stored_profiles_to_files`: the existing frozen raw-model/CAMS
  request, producing `regional-profile-diagnostics-v1`.

The stored bundle has all fifteen previous diagnostic arrays plus
`profile_diagnostics(N,20,7)`, float32. Its field names are in
`metadata.profile_diagnostics.fields`: class code, cloudy-deck liquid/ice
maxima, deep-cloud risk, vertical support count, uncertainty in meters and
geometry confidence. Codes/counts remain exactly representable float32
integers, not newly quantized compact public frames.

`native/src/profile_diagnostics.rs` preserves:

- Float32 clamped four-corner geometry, including exact-cell and out-of-grid
  edge behavior. Time indices truncate then clamp; time weights clamp to [0,1].
- Per-corner temporal interpolation before spatial interpolation. One finite
  temporal endpoint supplies the fallback regardless of time weight. A
  nonfinite blended corner is excluded; remaining finite corner weights are
  renormalized. No finite support yields zero, not nearest-cell substitution.
- The original `first + (second-first)*weight` operation order, including
  overflow at a zero time weight, and four-term float32 spatial reductions.
- Existing effective cloud amounts and vertical classification. Deck
  condensates use amount >=0.35; geometry support uses amount >=0.005 and
  finite height. Those masks intentionally differ.
- Pressure-depth lookup with ties-to-even rounding/default 700 m, and the
  original pressure-field fallback when inline level metadata has wrong length.
- Aggregate deep risk from independently sampled layer fractions, boundaries
  and precipitation. The stored implementation derives boundary temporal
  semantics from the first raw frame, matching Python preparation.
- Instantaneous/unknown boundaries multiply unresolved uncertainty by 0.5;
  average envelopes use 0.75. Support confidence retains the original
  float64 selection times float32 amount-confidence calculation.

Confidence is metadata only, never an additional quality multiplier.
Selected-column condensate does not prove the phase of a remote burnable
target: metadata explicitly records `confidence_affects_quality:false` and
`target_phase_proven:false`.

### Bounded Execution

The original assessment, quality and event-diagnostic methods/kinds remain
unchanged. The new parent preflight adds N*20*7*4 bytes. Each source profile
child remains complete; no pressure level, time frame or observer is silently
dropped. Samples are grouped by their temporal pair, and the reader retains
at most two full float32 profile frames. Their combined payload is limited
to 1 GiB, in addition to existing parent/child transport bounds. This is a
working-array bound, not a process RSS guarantee.

Profile readers validate the pinned manifest identity, exact kind/shape/dtype,
regular nonsymlink files, bounded NPY headers and payload extents, and full
array SHA-256. NPY values pass through a 64 KiB `BufReader`, avoiding
per-float file reads without changing checks or arithmetic.
Loaded arrays are rehashed, and all profile children are
revalidated before parent publication. These owned local bundles are not a
production shared cache or a hostile concurrent-filesystem guarantee.
Failure retains the existing owned-file cleanup and manifest-last policy.

Sampling/classification uses indexed Rayon work for groups >=1024 samples
and keeps the first error in input order. Task-local profile/classifier-input
buffers are reused without shared mutation; serial mode remains available
for bitwise comparisons. No new C ABI, download, runtime Python invocation,
application default switch or public `forecast` implementation was added.

## Complete Frame And Generated Core Assembly

`build_stored_frames_to_files` uses the same frozen request-file format as
`diagnose_stored_profiles_to_files`, with a required nonempty `name` of at most
256 UTF-8 bytes on every observer. Optional `point_spacing_deg` must be positive
when supplied.
`include_point_core` defaults to false; true requires exactly one observer.
Explicit observer elevations retain float64 precision, while `model-grid`
observers use the existing checked terrain interpolation.

```json
{"id":"frames","method":"build_stored_frames_to_files","params":{"path":"/absolute/path/named-observer-request.json","output_dir":"/absolute/path/new-frames"}}
```

The `regional-frames-v1` parent retains all sixteen assessment, quality and
diagnostic arrays and all complete per-frame profile children. Its
`metadata.frame_payload` describes `forecast.json` by relative filename, byte
length and SHA-256. The JSON companion contains `results`, `point_core` and
`frames`, matching the complete three-value output of Python's
`build_national_frame_payloads`. This is not the materialized public point
forecast returned by `national_point_core.py`.

Covered contracts include:

- All twenty version-103 frames, base64 row-major quantization, float32
  ties-to-even rounding, saturation and explicit little-endian 16-bit fields.
- The separate raw/masked field semantics of invalid samples, selected-column
  diagnostics, complete event components and model-specific optics metadata.
- Earliest best-step selection using float32 instant index, float32 coverage
  and raw load, distinct from float64 event scoring and mean instant index.
- Local IANA timestamps, lead time, original observer elevation, phase/genus/
  color diagnostics, confidence metadata and the version-8 generated core.
- Unresolved burnable-target phase remains unknown. Selected observer-column
  condensate is only a proxy, including the existing legacy source labels.
- No-burn events retain null best times/offsets. An observer with no valid
  samples fails assembly; no successful zero-result forecast is invented.

The assembler retains only a small index/coverage/burnability projection from
the existing instantaneous pass, not another N x 20 x 19 float64 tensor.
Event scores and selected-profile diagnostics are reused. Per-observer result
maps are serialized and released sequentially; the full regional result list
is not materialized in memory. The twenty compact frames are still retained.

JSON output shares the parent's 1 GiB payload budget. Files use exclusive
creation and owned-file cleanup; serialization/size failure poisons publication.
All pending syncs are joined before cleanup. No manifest is published until
JSON sync/hash, array syncs, profile/source revalidation and request checks pass.
This is local transport integrity, not a production cache or hostile-filesystem
TOCTOU guarantee. Consumers must check the companion descriptor themselves;
the generic NPY bundle reader does not silently validate arbitrary JSON files.

For targeted testing, `assemble_observer_frames` consumes already-computed
82-field ledgers, 9-field samples, 36-field columns and optional seven-field
profile diagnostics. It is limited to 64 observers, 20 samples each, 4096 grid
cells and 65536 column values. `quantize_frame_fields` separately tests up to
32768 values with `u8`, `u16`, `i16` or `raw-u8` encoding. These debug adapters
do not replace the raw-file endpoint or the production forecast API.

Frame `performance` values are measurements, not parity numerics. Rust reuses
prior scoring, so `instant_scoring_seconds` is zero; assembly timing covers
preparation through JSON serialization, excluding prior scoring and final
sync/hash. Parent metadata records this scope and a separate assembly/output
duration. Metadata marks the validator/materializer as ported, but
`point_core_validated` remains false for this frame-only endpoint. Validation
is mandatory in the separate stored-point endpoint below. NumPy/BLAS tied-window
parity and complete production forecasts remain unproven.

## Validated Stored Point Forecast

`forecast_stored_point_to_files` accepts the same explicit frozen raw-file
request as frame assembly, but requires exactly one named observer and implies
`include_point_core=true`. It does not search, download, select cycles or use a
production cache.

```json
{"id":"point","method":"forecast_stored_point_to_files","params":{"path":"/absolute/path/one-observer-request.json","output_dir":"/absolute/path/new-point"}}
```

The `point-forecast-v1` parent retains all sixteen NPY arrays, full per-frame
profile children and `forecast.json`. A second companion, `point_forecast.json`,
contains the complete result of Python's `materialize_national_point_core`,
not a reduced summary. Its `metadata.point_forecast` descriptor binds its
filename, byte count and SHA-256. All outputs share the existing byte budget,
exclusive creation, sync/hash, source revalidation and manifest-last publication.
Validation/materialization failure rolls back owned artifacts and never
publishes the parent. Existing destinations and unowned files are preserved.

The validator checks exact root/component/step fields, version/algorithm,
request coordinates/elevation/radius/model/date/cycle, IANA anchor offset and
scalar full-sun event, all twenty canonical offsets, numerical bounds,
loss-chain monotonicity, invalid-row masking, authoritative duplicates, colors,
proxies, scene scores, geometry, float32 best-step selection and active counts.
Existing optional proxy keys and deliberately unvalidated legacy EC/proxy
metadata retain their Python behavior. This is compatibility, not new
meteorological validation or resolution of the NumPy/BLAS near-tie limitation.

Materialization adds complete local/UTC step timestamps, AGL/observer-relative
geometry, target phase, best-step properties, evolution, timing window,
summaries and provenance/capability fields. Burnable-target phase remains
unresolved; selected-column phase/genus remain non-scoring proxies. Parsed
anchor offsets stay fixed across the twenty steps, including DST transitions,
matching Python's parsed datetime behavior. Duration rounding is ties-to-even;
large integer output/support counts are not truncated to i64/u64. Integer and
float representations remain distinct where Python requires actual integers.
Anchor tolerance reproduces Python floating timestamp subtraction, including
the reduced precision of distant calendar years.

`validate_point_core` and `materialize_point_core` are bounded debug methods
with `params={"request":{...},"core":{...}}`. Request accepts the existing
`ForecastRequest` JSON shape, with ISO date/datetime strings and a point object.
Neither method launches Python. Unknown request fields fail; unused supported
request options do not add downloads, satellite observations or nowcast.
The sequential debug line limit remains 1 MiB, and `forecast` remains unsupported.

Descriptors report `point_core_validated=true` and
`stored_point_payload_complete=true`, but explicitly keep
`automatic_input_selection=false`, `numpy_blas_tie_parity_proven=false` and
`public_forecast_complete=false`. The latter records the unfinished production
workflow, not missing fields in this frozen-core point result.

## Next Boundary

Port automatic weather-file/time planning and production cache identities,
integrity, locking and lifecycle around the completed frozen-input point path.
Retain the separate existing EC point workflow rather than silently replacing
it with the explicit regional TCC/RH proxy. Complete remaining regional
service/export schemas, location services, observations/nowcast and production
desktop/HTTP/CLI contracts. Keep Python as the reference and current default
until whole workflow outputs, failures, sessions and packaging are verified.

## Stage 23: EC Provenance And Service Export

The debug executable advertises `ecmwf-provenance-and-export`, 48 capabilities
and unchanged native ABI 3. Three new methods are independent of the existing
point-only workload and production entrypoints:

| Method | Input | Output |
| --- | --- | --- |
| `render_html_report` | `report` dictionary and optional `generated_local` label | `rust-html-report-v1` with rendered HTML |
| `forecast_ecmwf_service_to_files` | Same `path`/`output_dir` request as the EC point planner | Parent `ecmwf-service-forecast-v1` manifest |
| `read_ecmwf_service_cross_section` | Parent manifest path/identity, profile key, matching latitude/longitude and canonical offset | Complete theoretical cross-section response |

The service workflow reuses independent EC event/domain/raw/CAMS planning and
all numerical scoring. It retains the sixteen main arrays, per-hour profile
children, `forecast.json`, `point_forecast.json`, `ecmwf_point.json`,
`input_plan.json` and `cross_section_source.json`. It adds `report.html` and
`ecmwf_service.json`, each described by byte count and SHA-256 in parent metadata.
Existing point-only report shape and its `html_report_path=null` are unchanged;
the service report separately records the HTML artifact path.

### Provenance And Response

The native service retains every legacy EC provenance field: provider/member/
resolution/source/cycle, actual step source times, twenty evaluation times,
raw files/hours/grid/unit metadata, pressure axes, domain geometry, CAMS
semantics/files/descriptions, native versions/quality/dominant target,
capability limitations, confidence semantics and phase timings. It additionally
records the consumed source identities and actual consumed CAMS valid times.
The legacy `cams_valid_times_utc` intentionally remains the union of brackets
for **all** model source hours; it must not be represented as proof that every
listed CAMS time was read. Actual consumed times have their own field.
Native phase timing includes tensor writes and excludes the later service
export, rather than claiming identical Python timing boundaries.

`response` ports the existing EC Web payload's numerical/diagnostic assembly,
dominant-target overrides, source roles, data provenance, default best-time
cross-section, cross-section request and empty map fields. The following are
explicit new contracts, not compatible production-cache substitutions:

- `layer_profile_path=null`: no fabricated Python four-dimensional mmap.
  `native_profile_source` identifies the relative
  `cross_section_source.json`, checksum and
  `verified-per-hour-cell-pressure-24` layout.
- `profile_key="ec-native:<64 lowercase hex>"`: binds source identity,
  observer, timezone database and native ABI; it is not Python's `ec:<digest>`.
- Profile lookup checks parent/service/source identities and fixed local file
  names, validates the key and matching coordinates, then uses the complete
  verified sparse reader. A relocated intact bundle remains readable.
  This is explicit file-backed lookup, not a production registry or HTTP route.
- `html_report_url=null` plus `html_artifact`: no invented `/reports/` mount.
  `response_cache.status="not-integrated"`; `production_http_complete`,
  `response_cache_complete` and `public_forecast_complete` remain false.

### HTML And Publication

`html_report.rs` and the copied `report.css` port the existing
`html_report.py` content, metrics, chart, table, diagnostics and escaping.
The renderer supports dawn/dusk, model/nowcast metrics, empty and single-step
charts, optional duration and the existing score tiers. Same-zone local-wall-time
comparison at DST rollback is retained, including Python's fold equality;
this does not change astronomical scheduling. A pinned generation label allows
byte-for-byte reference tests. Default generation labels use host-local libc
time on Unix; non-Unix callers currently need an explicit label, and the
service's automatic timestamp remains a cross-platform packaging gap.

HTML shares the bounded bundle writer's create-new, hash, flush/sync, byte
budget and owned-file rollback. Parent manifest publication remains last.
Unit tests exercise partial HTML failure poisoning commit and cleanup that
preserves unowned files. The fixed bundled `report.html` filename does not
port the Python standalone writer's slug/date-directory naming policy.

### Stage 23 Verification: 2026-11-09

- Debug targeted regression: **272 passed** in 53.42 s across service,
  EC point, cross-section and migration tests.
- New service/HTML tests cover byte-identical complete HTML, dawn/dusk,
  empty/single-step charts, unknown model labels, escaping, nowcast metrics,
  score thresholds, four IANA timezones, DST fold behavior and nullable duration.
- Actual cached-raw EC/CAMS service tests compare all legacy provenance fields
  except the explicitly different profile layout/timing and separately checked
  dominant-target source. Complete response and cross-section fields are checked
  against the owning Python routines, using identical native profile tensors
  for strict cross-section numerical comparison.
- Profile tests reject wrong identities, contracts, keys, coordinates, offsets,
  symlinks, path substitution and corrupt arrays without breaking the next RPC;
  intact relocated bundles are accepted. Native children have empty
  `PATH`/`PYTHONPATH` and nonexistent `FIRESKY_PYTHON`.
- Engine unit tests after the ADS deadline fix: **36 passed, 1 existing manual benchmark ignored**.
  Native unit tests: **141 passed**, with two warnings in the independently
  modified `native/src/national.rs` test code (duplicate test attribute and
  an unused test function). This stage does not edit that file.

The first correctly invoked full nine-switch release regression observed
**1396 passed, 1 failed** in 328.54 s. The failure was the existing ADS wait
deadline test: a request bounded by the remaining operation budget could
time out on the transport clock just before the `Instant` deadline, returning
a generic redacted network error rather than the required deadline error.
`ads.rs` now distinguishes that budget-limited timeout from an independently
shorter request timeout; ambiguous job submissions are still not retried,
and URLs/bodies remain redacted. A deterministic helper test and delayed-monitor
integration tests cover both timeout causes, single submission and temp cleanup.
The existing failing assertion was not relaxed. The debug service/ADS subset
then observed **72 passed** in 11.12 s.

After rebuilding the release executable with that fix, the complete suite
with all nine Rust backend switches observed **1399 passed, 1 existing
Starlette/httpx deprecation warning** in **330.66 s**. This is one clean full
run, not the combination of the earlier failing run and isolated rechecks.
Engine Clippy (`--all-targets`, `-D warnings`), engine formatting and tracked
whitespace checks passed. Release SHA-256 is
`5a91552da06843ba8b90b26bebca70d7a4b6abd881390538a8a1883b51167c91`.
Verification logs are `/tmp/firesky-stage23-targeted-debug.log`,
`/tmp/firesky-stage23-deadline-debug.log`,
`/tmp/firesky-stage23-engine-final.log` and
`/tmp/firesky-stage23-full-rust-verified.log`. The earlier full failure is
retained in `/tmp/firesky-stage23-full-rust-final.log`.
An initial direct `.venv/bin/pytest` invocation failed desktop collection
because the repository root was absent from its import path; the full runs
above use the documented `.venv/bin/python -m pytest` invocation.

The HTML browser preview was blocked by the browser's local-file URL policy;
no visual screenshot verification is claimed. Renderer byte compatibility is
verified separately. See Stage 23 in `RUST_PERFORMANCE.md` for the measured
new service-output cost and remaining initialization/derived-cache bottlenecks.

This stage does not finish production HTTP/CLI, response/derived-cache reuse,
cross-section routing/registration, combined online EC-plus-ADS acceptance,
progress, cancellation, concurrency, other products or native packaging.
The desktop/HTTP defaults and unsupported generic debug `forecast` are unchanged.

## Stage 22: Verified Profile Cross-Sections

The standalone executable advertises `verified-cross-section` and adds
`build_stored_cross_section` (45 capabilities; C ABI remains version 3).
It ports `national_batch.build_national_cross_section`, not merely the separate
seven-field selected-column diagnostic.

Stored profile/point assessments now publish `cross_section_source.json`.
The parent manifest's `metadata.cross_section_source` records its byte count,
SHA-256, `rust-cross-section-source-v1` contract and explicit
`verified-per-hour-cell-pressure-24` layout. The descriptor contains the model,
terrain source, date/phenomenon/cycle, strict time/pressure axes, selected-grid
geometry and relative child-manifest identities. It intentionally does not
invent a `layer_profile_path` pointing at a three-dimensional array or claim
compatibility with Python's four-dimensional NPY/NPZ mmap.

The RPC parameters are:

```text
source_path
source_identity: {bytes, sha256}       # from the assessment manifest
point: {lat, lon, elevation_m, timezone_name}
offset_minutes
max_distance_km                       # optional; default 1000
distance_step_km                      # optional; default 25
```

Source JSON is limited to 256 KiB. Parameters and source structs reject unknown
fields. The reader checks the descriptor checksum, 1..64 increasing finite
nonnegative model hours, 1..256 increasing pressure levels, geometry bounds,
observer elevation and IANA timezone. The offset must match a canonical event
five-minute sample within 1e-7 minutes. Distances are finite, radius 0..1000 km,
step 0.1..1000 km, at most 4096 samples. The normal 1000/25 path has 41 samples.
These are explicit standalone request limits, not national-grid subsampling.

Only the selected one/two hourly frames are opened. Each child must be a
regular, bounded, non-symlink `manifest.json` in a local child directory, with
the expected identity, `regional-profiles-v1` kind and exact frame-index/hour/
pressure-axis/24-field binding. NPY dtype, C order, shape, payload extent,
bounded header, full size and SHA-256 are verified. The entire selected array
is hashed before/after reads and revalidated before returning; manifest and
source descriptor identities are rechecked too. Unselected hours need not be
loaded or rehashed. This validates processed profile inputs, not raw-weather
freshness or production cache lifecycle.

The returned object preserves all owning Python fields: observer/local time,
float32 distance path, model terrain, ASL/AGL illumination thresholds, all
thermodynamic/cloud/condensate/wind matrices, pressure-family codes, layer
base/top/status matrices and complete theoretical targets. Target diagnosis
retains adaptive/continuous 400-hPa membership, connected-envelope tolerance,
same-distance/same-family phase, spherical tangent horizon, straight-chord
terrain clearance with the 25 m margin, and the original status explanations.
Remote missing terrain is an error, never replaced by station elevation.
The observer origin retains its explicit station altitude.

Horizontal/time interpolation reuses the existing finite-fallback native
stencil. Four-corner edge clamping is preserved, including paths extending
beyond the stored domain: it is compatibility behavior, **not evidence of
actual terrain/weather coverage outside that domain**. Theoretical burnability
still does not evaluate intervening cloud-body occlusion.

An initial whole-frame reader passed the new differential tests. The final
reader collects/deduplicates all path corners first and uses the NPY parser's
seek API to decode only those columns. It retains whole-file integrity checks
and exact interpolation/reduction order. This reduces allocation/decoding,
not spatial or pressure coverage; native-before/after results are identical.
See the Stage 22 performance record for measured costs and remaining limits.

### Stage 22 Verification: 2026-11-09

- New differential/rejection tests: **70 passed** in 14.85 s after sparse
  reading and **70 passed** in 11.28 s on the final release build. They compare
  the complete output at four offsets for dawn/dusk,
  finite/missing/no-cloud material, ice/water/unknown phase, below-sea/high
  stations, terrain ridges, adaptive/continuous/isolated 400-hPa boundaries,
  missing upper pairs, one pressure level and finite temporal fallback.
- Tests also cover noninteger distance increments, zero radius, another IANA
  timezone, one-frame exact-hour and two-frame-only reads, and actual stored
  GFS/EC raw-to-assessment-to-cross-section publication.
- Corrupted/absent arrays, wrong dtype/shape, duplicate array keys, replaced
  hours/pressure axes, symlinks, path escapes, checksum mismatch, unknown
  parameters, invalid observer/time/grid/distance inputs and missing remote
  terrain reject without breaking the following RPC.
- All native children run with empty `PATH`/`PYTHONPATH` and a nonexistent
  `FIRESKY_PYTHON`. No Python fallback or new production switch was added.
- Numeric checks retain the shared strict 3e-11 relative/1e-11 absolute
  tolerance. Only spherical horizon/terrain-clearance distances allow
  2e-9 m absolute error for compensated Python `hypot` versus libm at Earth
  radius. All status/reason/phase/family fields remain exact. NumPy float32
  scalar promotion, pairwise reductions, decimal rounding and JSON float64
  promotion are reproduced explicitly.
- Engine unit tests: **33 passed, 1 existing manual benchmark ignored**.
  Native unit tests: **141 passed**. Engine Clippy (`--all-targets --no-deps`,
  `-D warnings`), both crates' formatting checks and whitespace checks passed.
- Pressure-axis metadata compares at its declared float32 precision, retaining
  exact normalized values instead of rejecting valid short JSON decimals such
  as `249.9`; wrong index/hour/pressure-axis substitutions still reject.

Whole-application runs on this changing checkout:

| Run | Observed result |
| --- | --- |
| Sparse measured release, all nine Rust switches | 1320 passed, 1 existing Starlette/httpx warning, 328.74 s |
| Final debug build, default backends | 1320 passed, same warning, 346.51 s |
| Final release, all nine Rust switches | 1305 passed, 19 setup errors in one shared Swift compiler fixture, same warning, 322.34 s |
| Isolated Swift fixture recheck, same Rust environment | 19 passed in 1.43 s; repeated after the full run: 19 passed in 2.18 s |

The final full run's `swiftc` fixture exited 1; the failure was not reproduced
in either isolated rerun. No Swift/application source was edited in this stage,
and no compiler root cause is established. Other desktop files/tests changed
during these runs; the later suite collected 1324 tests rather than 1320.
Do not combine the separate runs into a claim of one clean final 1324-test run.
The final Rust cross-section tests and all Rust unit checks passed independently.
Logs are `/tmp/firesky-stage22-full-rust.log`,
`/tmp/firesky-stage22-full-default.log`,
`/tmp/firesky-stage22-full-rust-final.log`,
`/tmp/firesky-stage22-targeted-final.log` and
`/tmp/firesky-stage22-native-ui-final.log`.

This stage does not finish EC service provenance, the existing Web response/
HTML/export pipeline, cross-section registration/cache keys, derived-cache
lifecycle, online EC-plus-ADS acceptance, concurrency/cancellation or native
packaging. Production `forecast` remains unsupported in the debug protocol;
desktop/HTTP defaults remain Python.

## Stage 21: Independent EC Point Orchestration

The debug executable advertises `ecmwf-point-orchestration` and adds
`plan_ecmwf_inputs`, `forecast_cached_ecmwf_point_to_files` and
`materialize_ecmwf_point` (44 capabilities, C ABI unchanged).
These are experimental native entrypoints, not replacements for the production
desktop `forecast` method or HTTP service. The nine existing opt-in switches
and Python production defaults are unchanged.

`plan_ecmwf_inputs` takes `{ "path": "/absolute/ec-point.json" }`.
`forecast_cached_ecmwf_point_to_files` additionally takes a new, absolute
`output_dir`; existing destinations are never overwritten. The input file is
bounded to 1 MiB and uses this strict schema:

```json
{
  "date": "2026-11-10",
  "phenomenon": "dawn",
  "point": {
    "name": "Observer",
    "lat": 31.2,
    "lon": 121.5,
    "timezone_name": "Asia/Shanghai",
    "elevation_source": "observer",
    "elevation_m": 50.0
  },
  "radius_km": 200.0,
  "cycle_utc": "2026-11-09T00:00:00Z",
  "ec": { "cache_dir": "/absolute/cache/ecmwf" },
  "cams_cache_dir": "/absolute/cache/cams",
  "allow_ec_download": false,
  "compute_backend": "auto"
}
```

Optional `now_utc` caps automatic cycle selection; omitting `cycle_utc` uses
the Stage 20 seven-hour/00-12Z publication-aware selector. Only 404 permits
trying an older cycle. `ec` accepts the Stage 20 bounded public/basic
configuration. Optional `cams_download` accepts the existing ADS configuration;
relative cache/credential-file paths resolve against the request file's parent.
No GFS, satellite, observation, full-catalog or AOD-only substitute is used.

The observer requires an explicit elevation, as in the existing EC point
application. Only `auto` and `cpu` are supported; unsupported backends fail
before downloads. Solar scheduling retains the canonical twenty evaluation
times, event-aware cycle selection and published three-/six-hour EC source
brackets. The scorer candidate radius is capped at 1000 km, the native solar
trace is `min(1000, candidate + 400)` km and the raw source crop covers their
sum. Events outside the published 360-hour horizon are rejected before raw
downloads. Antimeridian source domains are explicitly unsupported by the
current rectangular scorer, rather than silently dropping one side.
An eastern domain edge of exactly positive 180 degrees retains the existing
longitude-normalization convention and is not misclassified as wraparound.

Raw resolution is one multi-hour call, not one Rust subprocess per hour.
Strict basic header admission and metadata checks precede scoring. CAMS area
selection uses the actual selected model-grid coordinates, padded through
the existing model-bbox area contract. Missing EC inputs leave `cams_area`
unknown and do not trigger speculative CAMS downloads. Missing CAMS inputs
are reported or filled only when ADS is explicitly configured. Scoring
validates actual CAMS message times and full 11-level coverage; a planner
cache selection alone is not that final validation.
All twenty event times still determine the raw EC hours, but CAMS requirements
follow only source hours referenced by valid solar samples, plus the event
anchor bracket. An extra raw hour used only by invalid/nighttime samples must
not create a false CAMS requirement.

The new `rust-ecmwf-input-plan-v1` artifact records event times, source hours,
candidate/trace/source radii, bbox, model/CAMS selections, missing inputs,
download reports and phase times. It retains explicit incomplete production
lifecycle flags. Existing Stage 20 raw-cache limits and validation boundaries
remain applicable. EC inter-frame terrain consistency now follows the owning
application's absolute 2 m tolerance, independently of GFS's existing stricter
check; EC coordinate consistency uses absolute 1e-6 degrees.
One request-scoped ecCodes library handle stays alive through raw admission,
CAMS selection and assessment, preserving its internal definition caches
across phase-local decoder handles. The handle is released when the request
finishes; this is not an unbounded global cache or a Python-side workaround.

All stored EC single-point calculations now additionally publish
`ecmwf_point.json`, with an integrity descriptor under `ecmwf_point_report`.
The `rust-ecmwf-point-report-v1` result contains the complete pre-HTML
`ForecastReport.to_dict()` contract, authoritative `native_quality`, EC
dominant-burnable geometry/genus/phase extraction and the existing
`web_report_payload` serialization. It does not relabel the generic point
materializer as the EC application response.

This preserves the EC diagnostic confidence 0.42 (not statistical confidence),
EC-specific source-time/reason/step fields, native best index, all twenty
quality steps, duration/evolution/window rules and no-burn defaults.
Report anchor time is the original astronomical result; evaluation times keep
their epoch-converted clock, including a possible one-microsecond difference.
The shared core validator still checks scoring/geometry/time identity.
Additional EC checks reject out-of-range/fractional source indices and
brackets/weights that do not cover their evaluation time.

The native result intentionally reports `public_forecast_complete=false`;
its integrity descriptor also reports `provenance_complete=false`.
It does not yet port the EC application's full
provenance/cross-section profile-file contract, its durable derived-cache and
response-cache reuse, HTML/map/export handling, production progress/
cancellation, or native-client/HTTP dispatch. The existing native per-hour
profile bundles must not be presented as the Python application's combined
profile mmap. No production default is changed by this stage.

## Stage 21 Verification: 2026-11-09

`tests/test_rust_ecmwf_point.py`: **77 passed in 21.03 s** on the final
request-scoped-library implementation. Coverage includes sixteen complete
EC report/quality/dominant/Web-report differentials (dawn/dusk, valid/invalid,
no-burn/no-family, blocked, tied and family-wrapping cases), malformed core/
sample/backend rejection, twenty-four location/radius/phenomenon event-domain
plans, published six-hour sources, exact positive-180 normalization,
publication-aware automatic cycles/404 fallback and 403/500 fail-closed
behavior, bounded invalid requests without network calls, and the EC 2 m
terrain consistency boundary/rollback.

Two complete dawn/dusk integrations perform actual loopback EC index/Range
downloads, seed only the CAMS brackets referenced by valid native samples
plus the anchor, then run the native planned point workflow with Python
unavailable. Complete reports, native quality, dominant geometry and Web-report
serialization are checked against both the same-core owning Python
materializers and the actual `run_ecmwf_basic_point_forecast` workflow from
the same raw files. Source downloads/cache reuse must not call the Python SDK
or an external provider during native execution.

The actual Python-workflow native-quality comparison retains only the
previous Stage 16 upstream EC float32 tolerances: two ground-area quality
means and two per-step ground-area fields use `rtol=3e-6, atol=3e-8`.
All other quality fields and complete reports retain the strict shared
differential comparator. Same-core EC report/quality/Web-report comparisons
do not introduce those upstream tolerances. Performance before/after native
reports additionally have an exact canonical identity, recorded separately
in `RUST_PERFORMANCE.md`.

Final optimized release, with all nine existing switches set to `rust`:
**1250 passed, 1 existing Starlette/httpx deprecation warning in 311.20 s**.
Final optimized debug build with default production backends:
**1250 passed, the same 1 warning in 333.99 s**, using
`env FIRESKY_REQUIRE_RUST_TESTS=1 .venv/bin/python -m pytest -q`.
The whole-checkout count includes unrelated concurrent desktop tests; it is
not a count of newly ported workflows.

```sh
env FIRESKY_REQUIRE_RUST_TESTS=1 \
  FIRESKY_RUST_ENGINE_BIN="$PWD/engine/target/release/firesky-engine" \
  FIRESKY_EMPIRICAL_BACKEND=rust FIRESKY_GFS_DECODE_BACKEND=rust \
  FIRESKY_CLOUD_BACKEND=rust FIRESKY_ECMWF_DECODE_BACKEND=rust \
  FIRESKY_CAMS_BACKEND=rust FIRESKY_ASTRONOMY_BACKEND=rust \
  FIRESKY_GFS_DATA_BACKEND=rust FIRESKY_CAMS_DATA_BACKEND=rust \
  FIRESKY_ECMWF_DATA_BACKEND=rust .venv/bin/python -m pytest -q
```

Final engine Rust tests: **33 passed, 1 existing manual benchmark ignored,
0.13 s**. Native Rust tests: **140 passed, 3.23 s** (native source unchanged
by Stage 21). Debug/release builds, Clippy all-targets/no-deps with
`-D warnings`, formatting and whitespace checks pass. A release `hello`
with empty PATH/PYTHONPATH and unavailable Python reports 44 capabilities,
`ecmwf-point-orchestration` and ABI 3; exact production `forecast` still
returns the explicit not-migrated error.

The local six-cell report benchmark compares the complete native reports and
raw-file identities before/after request-scoped library residency. Median
batched cold latency improves 1017.61 -> 682.12 ms and warm latency
826.88 -> 476.85 ms; Python's resident warm derived-cache path remains much
faster at 52.35 ms. See the performance document for raw trials, topology,
output-I/O differences, fingerprints and limitations. Neither these timings
nor the tests establish full-domain/live EC-plus-ADS acceptance, profile
cross-section/provenance/service/packaging completion or Python retirement.

## Stage 20: EC Public/Basic Data Access

The sequential debug protocol now advertises `ecmwf-basic-data-access`,
41 capabilities and native ABI 3. `ecmwf_data` implements the owning
`EcmwfIfsAdapter`'s public/basic raw-data contract. It does not replace the
separate EC point algorithm with GFS, and never downloads the offline full
model-level catalog. The exact production `forecast` IPC remains unimplemented.

Example params:

```json
{
  "operation": "resolve",
  "config": {
    "cache_dir": "/cache/ecmwf",
    "source": "ecmwf",
    "resolution": "0p25",
    "timeout_seconds": 90.0,
    "lock_timeout_seconds": 300.0,
    "request_attempts": 3,
    "retry_seconds": 1.0
  },
  "cycle_utc": "2026-11-09T00:00:00Z",
  "forecast_hours": [6, 9, 12],
  "allow_download": true
}
```

`source` retains the existing `ecmwf`, `aws` and `google` routes, without an
implicit mirror switch. Only the existing `0p25` public/basic resolution is
accepted. Optional `config.base_url` routes controlled tests to a local
server; it must be HTTPS, or loopback HTTP, with no URL credentials, query
or fragment. Use a separate cache directory for overrides: the compatible
Python sidecar does not record a custom base URL.
Redirects are bounded to ten, require HTTPS or loopback HTTP and
never attach an application API token. No ADS/EC credentials are needed.
The HTTP client retains its proxy-environment behavior; local tests and
benchmarks set `NO_PROXY=*` rather than exercising deployment proxy routing.

Operations:

- `plan` reports source hours/brackets and deterministic raw paths/URLs,
  without decoding or creating raw files. Explicit cycles require no HTTP.
- `probe` HEADs the specified index files: 2xx is available, only 404 is
  absent, and other HTTP/network failures are errors.
- `resolve` and `download` resolve the listed hours through strict compatible
  caches, optionally filling misses when `allow_download:true`. Both support
  multiple hours; the Python single-file entrypoint requests one.

An omitted cycle enables event planning with `reference_utc`,
`evaluation_utcs` and optional `now_utc`/`max_cycles` (default eight, maximum
64). Selection starts at the earlier of event/now, subtracts seven hours,
floors to 00/12Z and tries previous 12-hour cycles only after genuine 404s.
The highest uncached required source step is probed, matching the existing
ascending-publication assumption, not independently proving every step.
As in the Python selector, nonempty local basic bundles bypass publication
probes without being declared contract-validated. Subsequent resolution
still validates sidecars and GRIB headers. `use_cache_for_cycle:false`
disables that selector-only cache shortcut.

Source brackets retain three-hour cadence through f144 and six-hour cadence
through f360. The existing helper clamps later times to f360; planning
reports `within_public_horizon:false` instead of claiming these are valid
future forecasts. Explicit cycles preserve the installed SDK's URL stream
boundary for 06/18Z before/after 2026-05-12. Header admission still requires
the existing `type=fc` / `stream=oper` basic contract; historical `scda`
capability is not newly asserted.

Three request parts retain the same ordering and raw bytes as the owning
Python adapter: pressure fields at the requested hour, TCC at that hour,
then surface `z`/optional `2t`/`2d` at f000. NDJSON rows are filtered by
parameter/type/step/level type/pressure level and sorted by offset.
Adjacent selected ranges are coalesced, not reordered or padded across
unselected bytes. Overlapping ranges and integer overflow fail closed.
Responses must be HTTP 206 with exact Content-Range, consistent declared
length, identity encoding and complete streamed bytes. Retries reset the
current output offset before replacing a partial range.
No ETag-pinned remote snapshot or provider checksum is asserted; the returned
SHA-256 identifies the assembled raw bytes.

Indexes are limited to 32 MiB, 100,000 records and 64 KiB per line; raw
bundles to 512 MiB. Typed index records and shared `Arc` views replace
deep-cloned JSON trees. The request-local LRU accounts for vector capacity,
retained strings and entry storage, evicting above a 64 MiB estimate.
This is not a process RSS cap; raw response buffers, allocator overhead,
HTTP state, ecCodes and active parsing have separate costs. Per-HTTP request,
retry and lock waits are finite. A whole-request deadline, production
cancellation and remote task lifecycle remain unfinished.

Cache names and JSON sidecars match Python exactly, including UTC `Z`
spelling and source/contract/resolution/field lists. Rust and Python acquire
the same per-file advisory lock. New bundles undergo metadata-only strict
basic capability checks before durable staged publication; complete value
and coordinate validation remains the decoder/scorer's responsibility.
Missing/invalid existing sidecars, wrong clocks/units/fields, duplicates
and nonregular destinations fail explicitly rather than being silently
replaced by a different model or contract. Corrupt preexisting nonempty raw
files are preserved; repair/eviction policy remains future lifecycle work.

Raw and metadata files are individually atomic, not a two-file transaction.
Metadata is published before the validated raw file, followed by Unix
parent-directory sync. A crash can leave a sidecar without raw data; it
cannot turn that orphan into a cache hit. Valid earlier hours may remain
cached when a later hour fails. Normal errors clean owned temporaries;
SIGKILL/power-loss orphan cleanup is not implemented.

`FIRESKY_ECMWF_DATA_BACKEND=rust` routes strict basic adapter downloads,
publication probes and the EC point event-cycle selector through Rust,
without SDK fallback. It is independent of the EC numeric-decoder switch.
Defaults remain Python. The bridge currently starts a process per operation;
the batched native RPC's measured speedup must not be attributed to this
single-file bridge. Native complete EC point orchestration, the legacy
research downloader, other product/capability probes, production cache/task
lifecycle, IPC and packaging remain outstanding.

Protocol and byte comparisons use the actual installed
`ecmwf-opendata 0.3.35` / `multiurl 0.3.9` against a local HTTP server.
No live public weather-provider download or production mirror/proxy
acceptance is established. Performance measurements are in
`RUST_PERFORMANCE.md`; ecCodes remains a C dependency.

## Stage 20 Verification: 2026-11-09

On this local macOS/aarch64 checkout:

| Check | Observed Result |
| --- | --- |
| New EC transport/planning/integration cases | 77 passed, 46.09 s |
| Native Rust unit tests | 140 passed, 3.64 s |
| Engine Rust unit tests | 33 passed, 1 component benchmark intentionally ignored |
| Complete Python suite, default backends/debug engine | 1172 passed, 302.07 s |
| Complete Python suite, all nine Rust switches/release engine | 1172 passed, 283.56 s |
| Engine Clippy, all targets/no-deps, warnings denied | Passed |
| Engine formatting and diff whitespace checks | Passed |
| Locked debug/release builds and Python-unavailable release hello | Passed; 41 capabilities, ABI 3 |

The 77 added cases cover actual-SDK raw bytes at f000/f006/f144/f150/f360;
sidecars/cache admission in both Python-to-Rust and Rust-to-Python directions;
missing/invalid required field headers, clocks/type, ranges and HTTP statuses; bounded
indexes/records/lines; partial-body retry/reset; existing-file preservation;
Python/Rust advisory locking, cross-process deduplication and symlink
destinations; the historical SDK URL stream boundary; event cadence,
future/past cycle caps, 404-only fallback and explicit outage errors; separate
full-catalog namespaces and provider/selector bridges forbidding SDK/Python
probe calls.

Two composed dawn/dusk integration cases resolve/download EC raw inputs
through Rust, then feed the existing stored EC-basic point endpoint.
All sixteen parent arrays match the independently prepared original-input
Rust path bitwise. Complete point JSON is strictly compared with Python
materialization of the same core and independently compared with Python's
frame/core pipeline using the existing Stage 16 EC-only float32 tolerances
for two ground means/two per-step fields. Other point fields retain strict
comparison; no blanket tolerance was added. These are composed controlled
input cases, not the unimplemented automatic native EC point orchestration.
Engine children run with empty PATH/PYTHONPATH and a nonexistent Python path.

Build and test commands:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --bin firesky-engine
cargo build --manifest-path engine/Cargo.toml --locked --release \
  --bin firesky-engine
cargo test --manifest-path native/Cargo.toml --locked
cargo test --manifest-path engine/Cargo.toml --locked
cargo clippy --manifest-path engine/Cargo.toml --locked \
  --all-targets --no-deps -- -D warnings
cargo fmt --manifest-path engine/Cargo.toml -- --check
FIRESKY_REQUIRE_RUST_TESTS=1 .venv/bin/python -m pytest -q
env FIRESKY_REQUIRE_RUST_TESTS=1 \
  FIRESKY_RUST_ENGINE_BIN="$PWD/engine/target/release/firesky-engine" \
  FIRESKY_EMPIRICAL_BACKEND=rust FIRESKY_GFS_DECODE_BACKEND=rust \
  FIRESKY_CLOUD_BACKEND=rust FIRESKY_ECMWF_DECODE_BACKEND=rust \
  FIRESKY_CAMS_BACKEND=rust FIRESKY_ASTRONOMY_BACKEND=rust \
  FIRESKY_GFS_DATA_BACKEND=rust FIRESKY_CAMS_DATA_BACKEND=rust \
  FIRESKY_ECMWF_DATA_BACKEND=rust .venv/bin/python -m pytest -q
```

Whole-suite counts include the current workspace's other desktop tests,
not only this stage's 77 new cases. The existing Starlette/HTTPX deprecation
warning remains. No live ECMWF publication/transfer, production mirror/proxy
setup, cross-platform package or whole-application Python retirement was
accepted. Production defaults remain Python.

## Stage 19: CAMS ADS Data Access

At Stage 19 the debug engine advertised `cams-ads-data-access` and added `cams_data`.
The transport matches the installed `cdsapi 0.7.7` /
`ecmwf-datastores-client 0.6.0` v1 processing flow:
process discovery, `constraints`, `execution`, monitor polling and
`asset.value` results. Testing uses local HTTP servers and the actual installed
Python SDK, not a live ADS account. No real credentials were used or printed.

Example params:

```json
{
  "operation": "resolve",
  "cache_dir": "/cache/cams",
  "area": [40.0, 110.0, 20.0, 120.0],
  "target_utcs": ["2026-11-09T09:00:00+00:00"],
  "allow_download": true,
  "require_exact_valid_time": true,
  "config": {
    "timeout_seconds": 120.0,
    "wait_timeout_seconds": 7200.0,
    "poll_initial_seconds": 1.0,
    "poll_max_seconds": 5.0,
    "request_attempts": 3,
    "lock_timeout_seconds": 300.0
  }
}
```

`area` is `[north,west,south,east]`. Its numeric JSON types, integral float
suffixes, negative zero and small-exponent spelling participate in the
Python-compatible area hash. Datetimes must be timezone-aware. Exact profile
targets must fall on a three-hour UTC boundary. At most 256 targets are
accepted; duplicates are deduplicated.

Operations preserve separate existing behaviors:

- `batch` groups missing targets by selected run, downloads one bundle per
  group, and returns only valid per-lead files. Group errors/missing targets
  are reported for the caller's fallback.
- `download` takes exactly one target and performs the existing ordered
  lead, +3, -3, +6 fallback when optical profiles are invalid.
- `resolve` performs the batch phase followed by single-target fallback for
  missing entries.

Rows retain both `requested_valid_time` and actual filename `valid_time`,
an `exact` flag, run/lead, path and SHA-256 identity. A neighboring fallback
is never mislabeled as the requested exact time. Consumers needing strict
brackets must reselect the requested times; the GFS point planner does so
and fails closed when a required bracket is still absent.
`allow_download:false` permits cache-only resolution without credentials.

Availability ranking follows `choose_best_cams_selection`: dates within the
target's preceding five days, profile-only day constraints, nearest valid
time, then youngest eligible run and the existing tuple tie order.
Day constraints are memoized once per distinct date within the request,
not queried again for every target. Refresh bypasses older exact-time hits
only when the refresh cycle can apply; historical files are not removed.
Nonexact cache lookup retains its legacy two-hour/header-only policy;
strict brackets retain complete profile probing. Cache-hit clocks are not
falsely declared verified.

Credentials are read only on a network miss, from `CDSAPI_KEY` and
`CDSAPI_URL`, falling back to `CDSAPI_RC` or `~/.cdsapirc`; optional
`config.api_url` and `config.credentials_file` override endpoint/file routing.
The RPC never accepts a key value. Credential files are bounded to 64 KiB.
TLS verification cannot be disabled. URLs require HTTPS except loopback
HTTP used by tests; URL credentials/fragments are rejected. Authenticated
API links must stay within the configured processing origin/path, and API
redirects are rejected. Asset redirects are bounded to ten and use a separate
credential-free client. Both clients ignore proxy environment variables,
as the existing provider's `Session.trust_env=False` does.

HTTP requests/polling have finite configured timeouts/deadlines and bounded
retries. An ambiguous execution POST is never automatically retried, to
avoid duplicate remote jobs. API bodies and signed asset URLs are redacted
from errors. JSON responses are bounded to 4 MiB; assets must match their
declared length and remain within 16 bytes..512 MiB.
Remote jobs are not automatically deleted, matching the installed SDK's
default; production cancellation/resume remains unimplemented.

The existing 1..4-worker environment limit is process-local; per-run/area
advisory locks additionally coordinate Rust writers across processes and
recheck valid existing outputs after locking. Python does not acquire these
new run locks. Multi-lead splitting retains every encoded GRIB message,
uses bounded message/lead limits, closes append handles promptly and rejects
unrequested leads. Each staged file must pass full compact-profile decoding
and actual GRIB valid-time checks before atomic publication.
File and Unix parent-directory syncs are retained. Corrupt regular targets
are replaced only after a valid replacement is ready; failures preserve
previous files and clean owned temporaries. Valid subsets from a batch may
remain cached when another target fails; this is per-file atomicity, not a
multi-file transaction. SIGKILL/power-loss orphan cleanup is not implemented.

`FIRESKY_CAMS_DATA_BACKEND=rust` routes the existing provider's single/batch
download entrypoints through this engine, with no Python SDK/network fallback.
It is separate from the optical decoder's `FIRESKY_CAMS_BACKEND`.
The default remains `python`; the subprocess bridge does not retire Python.
Empty batch requests retain the existing no-op result.

The GFS planning recipe additionally accepts optional `cams_download`
containing the config above. Its presence explicitly enables online CAMS
resolution; absence remains cache-only. A relative credentials file resolves
against the recipe directory. `input_plan.json` records transfer counts,
phase timings and readiness, never tokens or signed asset URLs.
Complete point scoring still independently checks raw hashes, clocks and
coverage. Publication probes, complete cache/task lifecycle, other aerosol
providers/corridor-reading orchestration, separate EC point workflows,
production IPC/HTTP/CLI and packaging remain outstanding.

The actual-SDK comparison exposed an existing Python splitter bug: handles
were stored as three-tuples but unpacked as two-tuples on subsequent messages.
The annotation/unpacking is corrected so the reference batch path retains
complete multi-message profiles instead of silently failing into fallback.
Performance comparisons use this corrected reference, not the broken path.
Request-local ecCodes reuse and timing evidence are documented in
`RUST_PERFORMANCE.md`; ecCodes remains C and no zero-C claim is made.

## Stage 19 Verification: 2026-11-09

On this local macOS/aarch64 checkout:

| Check | Observed Result |
| --- | --- |
| Native Rust unit tests | 140 passed, 2.81 s |
| Engine Rust unit tests | 32 passed, 1 component benchmark intentionally ignored |
| Complete Python suite, default backends/debug engine | 1069 passed, 252.51 s |
| Complete Python suite, all eight Rust switches/release engine | 1069 passed, 235.29 s |
| Strengthened online-CAMS point cases, debug/release | 2 passed each, 6.15 / 6.01 s |
| Engine Clippy, all targets/no-deps, warnings denied | Passed |
| Engine formatting and diff whitespace checks | Passed |
| Final debug and release engine builds, locked dependencies | Passed |
| Release hello without Python available | Passed; 40 capabilities, native ABI 3 |

The 77 added cases exercise the ADS processing protocol against independent
local API/asset servers, including the actual installed Python SDK. Coverage
includes run selection, six numeric area identities, strict/nonexact cache
policies, refresh, credentials precedence, token-free assets, bounded
retries/deadlines, hostile links, malformed results, partial/invalid profiles,
actual GRIB clock mismatch, preserved corrupt destinations, symlink rejection,
cross-process run-lock deduplication, step-range splitting, complete
multi-message Python splitting and original DST-fold datetime keys.
Provider bridge tests forbid Python SDK calls on the Rust path.

Two online-CAMS-to-point cases (dawn/dusk) compare all sixteen parent arrays
bitwise and complete point JSON against the frozen-input Rust path, then
compare complete point JSON against the independent Python frame/core
preparation and materializer. The bitwise array assertions were added after
the full-suite runs and then verified with both debug and release engines;
no runtime code changed after those full runs.
Source identities, companion metadata and the explicit
`GFS-event-hours-and-CAMS-ADS` scope are checked. Engine children have empty
PATH/PYTHONPATH and a nonexistent Python executable; Python remains the
external test/reference harness, not a subprocess called by Rust.

Build and test commands:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --bin firesky-engine
cargo build --manifest-path engine/Cargo.toml --locked --release \
  --bin firesky-engine
cargo test --manifest-path native/Cargo.toml --locked
cargo test --manifest-path engine/Cargo.toml --locked
cargo clippy --manifest-path engine/Cargo.toml --locked \
  --all-targets --no-deps -- -D warnings
cargo fmt --manifest-path engine/Cargo.toml -- --check
FIRESKY_REQUIRE_RUST_TESTS=1 .venv/bin/python -m pytest -q
env FIRESKY_REQUIRE_RUST_TESTS=1 \
  FIRESKY_RUST_ENGINE_BIN="$PWD/engine/target/release/firesky-engine" \
  FIRESKY_EMPIRICAL_BACKEND=rust FIRESKY_GFS_DECODE_BACKEND=rust \
  FIRESKY_CLOUD_BACKEND=rust FIRESKY_ECMWF_DECODE_BACKEND=rust \
  FIRESKY_CAMS_BACKEND=rust FIRESKY_ASTRONOMY_BACKEND=rust \
  FIRESKY_GFS_DATA_BACKEND=rust FIRESKY_CAMS_DATA_BACKEND=rust \
  .venv/bin/python -m pytest -q
```

The Python suite retains the existing Starlette/HTTPX deprecation warning.
No live ADS account, licence acceptance, public-network queue or real forecast
download was exercised. This stage does not establish production IPC,
cancellation, full cache/task lifecycle, packaging or overall Python
retirement. Defaults remain Python.

## Stage 18: Automatic GFS Input Planning

At Stage 18 the debug engine advertised `planned-cached-gfs-point` and added:

- `plan_gfs_inputs`, with params `{"path":"/absolute/recipe.json"}`.
- `forecast_cached_gfs_point_to_files`, with params
  `{"path":"/absolute/recipe.json","output_dir":"/new/output"}`.

A recipe replaces the manually supplied forecast-hour/CAMS-file lists:

```json
{
  "date": "2026-11-09",
  "phenomenon": "dusk",
  "points": [{
    "name": "example",
    "lat": 0.25,
    "lon": 100.25,
    "timezone_name": "Asia/Shanghai",
    "elevation_source": "observer",
    "elevation_m": 50.0
  }],
  "radius_km": 200.0,
  "cycle_utc": "2026-11-09T00:00:00+00:00",
  "gfs": {"cache_dir": "/cache/gfs", "resolution": "0p25"},
  "cams_cache_dir": "/cache/cams",
  "allow_gfs_download": false,
  "batch_min_points": 64
}
```

`cycle_utc` is optional. Without it, cycle selection uses the earliest finite
regional solar anchor, clipped to `now_utc` (optional; system UTC otherwise),
minus four hours, rounded down to a six-hour GFS cycle. This reproduces the
owning prefetch route's heuristic, not a provider publication probe.
`publication_verified` remains false. Optional `bbox` uses
`[west,east,north,south]`; otherwise the existing radius/padding/domain rules
apply. Antimeridian-crossing source domains are rejected rather than silently
truncated. Relative cache paths resolve against the recipe's parent.

Observer elevation must be explicit and finite in [-500,9000] m.
For `elevation_source:"model-grid"`, omit `elevation_m`: a verified GFS
terrain prepass adjusts solar samples, recomputes requirements and, when
necessary, repeats once using the new first hour. The prepass must belong to
the final plan. Missing terrain input leaves a provisional plan with
`terrain_pending:true`; it cannot produce a point result.

The scalar path preserves full-sun validation, local wall-clock/DST fold-zero
arithmetic, dense one-minute event samples with five-minute outer samples,
solar-elevation filtering and the forced anchor. At the explicit batch
threshold, the existing twenty-sample regional policy applies instead.
Scalar negative offsets clamp to zero; regional negative brackets fail.
One-hour/three-hour cadence and the regional nearest-hour tolerance remain
distinct. Regional timeline work is chunked in groups of at most 1024.
Recipes are bounded to 64 MiB, 500000 observers, 64 GFS hours and 256 CAMS
valid times; unknown fields, invalid zones and malformed inputs fail.

GFS resolution reuses Stage 17's shared cache, locks, download validation and
retry contracts. Downloads require explicit `allow_gfs_download:true`;
failure is an error, not a successful incomplete-plan response. Otherwise
missing GFS hours are reported without network access. CAMS remains strictly
local-cache-only at Stage 18; Stage 19 adds the explicit optional online config.

Compact CAMS selection reproduces the production policy: required three-hour
brackets, newest run first, exact area-key preference within a run, stable
traversal ties, full profile decoder validation and half-cell coverage
tolerance for nonexact areas. A newer filename whose internal GRIB time is
wrong is still selected, as in Python; actual scoring must then fail closed,
not silently use an older run. Selection does not prove filename/GRIB clock
agreement. Directory traversal is bounded and does not follow directory
symlinks.

A process-local LRU stores only up to 256 CAMS validation/coverage probes,
including failures, keyed by canonical path, byte length and modification
time. Ordinary file changes invalidate probes. This is not cryptographic
identity or protection against adversarial same-signature replacements.
The scoring path still rereads, hashes, decodes and checks raw input clocks,
grids and coverage; probe hits never substitute for those checks.

`inputs_located` means all paths were selected and terrain planning is final,
not that every GFS file has passed weather decoding. The plan explicitly
reports `model_cache_hits_validated:false`,
`cams_filename_grib_time_verified:false`,
`production_input_lifecycle_complete:false` and
`public_forecast_complete:false`. Selected CAMS candidates have decoder
validation, subject to the file-version memo described above.

The point method requires exactly one named observer and feeds the typed
plan directly into the existing raw-input point pipeline, without a Python
adapter or synthetic intermediate request. It retains all sixteen parent
arrays, complete pressure-profile children, `forecast.json` and
`point_forecast.json`. An additional hashed `input_plan.json` companion
records selection and timing evidence; the original recipe remains the
bundle's source identity. Existing outputs are not overwritten, and owned
partial artifacts are cleaned on failure. Point metadata limits automatic
selection to `GFS-event-hours-and-local-CAMS`.

No Python production default or native-client launcher changed. At this stage
separate EC point orchestration, CAMS authentication/downloads, publication probes,
production cache/task lifecycle, persistent/concurrent desktop IPC, HTTP/CLI,
observations/nowcast, exports and Python-free packaging were outstanding.
Stage 19 adds optional CAMS retrieval; the other production
gates remain unfinished. ecCodes is still C; no zero-C claim is made.

Performance measurements and remaining terrain-decode costs are in
`RUST_PERFORMANCE.md`. This stage is not whole-application acceptance.

## Stage 18 Verification: 2026-11-09

On this local macOS/aarch64 checkout:

| Check | Observed Result |
| --- | --- |
| Native Rust unit tests | 140 passed |
| Engine Rust unit tests | 31 passed, 1 component benchmark intentionally ignored |
| New automatic-planning differential/integration cases | 75 passed; final debug rerun 20.49 s |
| Complete Python suite, default backends/debug engine | 992 passed, 198.98 s |
| Complete Python suite, all seven Rust switches/release engine | 992 passed, 176.47 s |
| Engine Clippy, all targets/no-deps, warnings denied | Passed |
| Engine formatting, changed native timeline formatting, diff whitespace checks | Passed |
| Release hello without Python available | Passed; 39 capabilities, native ABI 3 |

The 75 new cases cover scalar/regional thresholds, sunrise/sunset,
one-/three-hour cadence, historical/future cycle selection, worldwide
domains and CAMS area identities, terrain correction and final-hour binding,
newest/exact/covering/corrupt CAMS candidates, stable ties, live file
mutation/deletion/recovery, relative cache roots, DST folds/gaps, polar
full-sun failures, invalid requests and preserved existing output.
Download failure is tested through a local rejecting HTTP CONNECT proxy,
without contacting the public provider.

Four complete point cases (dawn/dusk, observer/model-grid elevation) compare
all sixteen parent arrays and complete point JSON with the frozen Rust path,
then independently compare complete point fields to Python's existing
frame/core preparation and point materializer. Input-plan companion hashes,
recipe identity, metadata limits and no-overwrite behavior are asserted.
Native children in these tests have empty PATH/PYTHONPATH and a nonexistent
Python executable path; Python is only the test/reference harness.
This is controlled-fixture workflow evidence, not live-provider,
national-production or packaging acceptance.

Build and test commands:

```bash
cargo build --manifest-path native/Cargo.toml --locked --release
cargo build --manifest-path engine/Cargo.toml --locked --bin firesky-engine
cargo build --manifest-path engine/Cargo.toml --locked --release \
  --bin firesky-engine --example bench_input_plan
cargo test --manifest-path native/Cargo.toml --locked
cargo test --manifest-path engine/Cargo.toml --locked
cargo clippy --manifest-path engine/Cargo.toml --locked \
  --all-targets --no-deps -- -D warnings
FIRESKY_REQUIRE_RUST_TESTS=1 .venv/bin/python -m pytest -q
env FIRESKY_REQUIRE_RUST_TESTS=1 \
  FIRESKY_RUST_ENGINE_BIN="$PWD/engine/target/release/firesky-engine" \
  FIRESKY_EMPIRICAL_BACKEND=rust FIRESKY_GFS_DECODE_BACKEND=rust \
  FIRESKY_CLOUD_BACKEND=rust FIRESKY_ECMWF_DECODE_BACKEND=rust \
  FIRESKY_CAMS_BACKEND=rust FIRESKY_ASTRONOMY_BACKEND=rust \
  FIRESKY_GFS_DATA_BACKEND=rust .venv/bin/python -m pytest -q
```

The Python suite retains one existing Starlette/HTTPX deprecation warning;
native tests retain their existing duplicate-attribute/dead-code warnings.
Whole-native-crate formatting still reports pre-existing differences in
`lib.rs`/`national.rs`; those files were not reformatted as part of this
stage. The changed timeline module and engine pass their formatting checks.
Production defaults, native client launchers and the overall retirement
completion gates are unchanged.

## Stage 17: GFS Data Access

`gfs_data` is a standalone JSON-lines method; `hello` reports migration stage
`gfs-data-access` at that stage (now `ecmwf-point-orchestration`). Its
operations are `url`, `find`, `resolve` and `download`.
Requests reject unknown fields. For example, a no-network explicit catalog:

```json
{"id":"gfs","method":"gfs_data","params":{"operation":"resolve","cache_dir":"/absolute/gfs-cache","cycle_utc":"2026-11-09T00:00:00Z","forecast_hours":[9,12,15],"bbox":[100,102,1,0],"allow_download":false}}
```

The caller must supply a cycle and ascending unique hours (1..64 entries).
Results report missing hours instead of treating a partial catalog as complete.
An error partway through a download batch may leave earlier complete cache
files, but does not return a successful complete catalog.
This is not automatic event/weather planning. The new Rust module does not
invoke Python, curl or other download subprocesses.

`FIRESKY_GFS_DATA_BACKEND=rust` explicitly routes `GfsNomadsClient` URL
generation, covering-cache lookup and download through `rust_gfs_data.py`.
Default remains `python`, invalid flags fail, and there is no silent fallback.
`FIRESKY_RUST_ENGINE_BIN` selects the executable. This transport switch is
separate from `FIRESKY_GFS_DECODE_BACKEND`; neither switch retires Python
orchestration, forecast caches or desktop services.

### Cache And Transfer Contracts

- Exact 0p25/0p50 product selectors, pressure/layer/surface variables,
  diagnostic extras, canonical bbox rounding, negative-longitude mapping and
  query ordering match the Python builder on the differential fixtures.
- Raw paths retain URL SHA-256's first twenty hex characters, existing
  date/cycle/region/resolution partitions and the undated fallback.
- Both runtimes read/write `.grib2.json` and the existing
  `.gfs-metadata-index-v1/index.json`. Reads do not replace a valid Python
  index. Legacy cache-root filename fallback remains supported.
- Lookup retains lexical-first selection, cycle/hour matching, nonempty
  files, query-schema identity, global-coverage admission and bbox containment.
  The inherited query identity excludes spatial selectors and ignores URL
  origin/path; it is not source authentication. A cache hit does not prove
  model fields, freshness or valid GRIB contents.
- Index directory stamps and matching metadata size/mtime detect ordinary
  changes; malformed indexes rebuild under the shared index lock. Bounded
  process-local reuse retains at most four roots and 64 MiB of serialized-index
  weights, not a 64 MiB RSS guarantee. Filesystem I/O occurs outside its mutex.
  Incremental publication rereads the changed metadata, refreshes directory
  stamps and publishes the compatible sorted index atomically.
- Target and index locks interoperate with Python `flock` on the tested
  macOS host. Lock waits default to 300 seconds. Retries default to four,
  with 1/2/4/... seconds of backoff capped at twelve; attempts are bounded
  to 1..32, network operation timeout to 0.001..3600 seconds.
- HTTP uses Rust reqwest/rustls, certificate verification, identity encoding,
  the existing User-Agent and at most ten redirects. Only HTTP(S) without
  embedded credentials or fragments is admitted. Network timeout applies
  per blocking operation, not to the whole transfer. The Python bridge has
  no whole-download subprocess deadline; production cancellation is not ported.
- Downloads stream through a 1 MiB buffer into an exclusively owned temporary
  file, capped at 512 MiB. Length, minimum sixteen bytes and GRIB/7777 boundary
  markers are checked before sync and non-clobbering publication. Returned
  SHA-256 binds downloaded bytes; `model_fields_validated` stays false.
  Boundary markers alone are not meteorological or complete GRIB validation.
- Existing nonempty exact targets remain reusable without revalidation,
  explicitly reporting `content_verified:false`. Empty placeholders are
  replaceable; symlink/nonregular final targets are rejected. Metadata failure
  after successful raw publication leaves the complete raw file recoverable
  without another HTTP request. Normal failures remove only owned temporaries.

Metadata is bounded to 256 KiB, index JSON to 64 MiB/100000 entries and cache
walks to one million entries/depth 64. Region tags must be single components;
bboxes must be finite, geographically bounded and south <= north. Malformed
paths and configurations can therefore be rejected more strictly than Python.
Walks do not follow directory symlinks; this is not an adversarial-filesystem
race or hostile-cache security guarantee. SIGKILL/power-loss temporary cleanup,
cache eviction/refresh and full lifecycle management remain open.

The tests use a local HTTP fixture, not live NOAA. Synthetic marker-only bodies
exercise transport failures without claiming GRIB validity. Separate ecCodes
fixtures test downloaded real GRIB through Rust decoding, plus cache-resolved
real GFS/CAMS frozen inputs through complete dawn/dusk point materialization.
The harness still supplies hours, CAMS files and frozen request assembly;
those integration tests do not prove an automatic production forecast.

## Stage 17 Verification: 2026-11-09

Local macOS/aarch64, rebuilt debug/release engine and unchanged application
defaults:

| Check | Observed Result |
| --- | --- |
| Native Rust unit tests | 140 passed |
| Engine Rust unit tests | 30 passed; one component benchmark ignored |
| Complete Python suite, default backends/debug engine | 917 passed, 177.03 s |
| Complete Python suite, all seven Rust switches/release engine | 917 passed, 153.52 s |
| Added GFS data tests | 79 cases |
| Engine Clippy, warnings denied; Rust formatting; whitespace | Passed |

GFS cases cover exact selectors and URL formatting; bidirectional Python/Rust
index interoperability; global/diagnostic/resolution admission; legacy root
fallback and numeric metadata; live invalidation and malformed index recovery;
normal, chunked, no-length, redirected and retried HTTP responses; bad
markers/lengths, oversize headers, server errors and header/body timeouts.
Six competing processes issue one successful HTTP request under the shared
target lock. Python-held target/index locks enforce bounded failure; a fully
published raw file survives a later metadata failure and recovers without
another transfer. Tests also reject invalid configuration/traversal, preserve
unowned files and nonempty exact targets, replace empty placeholders, and
reject final-target symlinks.

All four operations run with empty PATH/PYTHONPATH and a nonexistent Python
path in the differential suite. A dedicated opt-in bridge test disables the
Python URL canonicalizer and network/download functions. Real-GRIB tests
separately cover local HTTP -> Rust decode and cache-resolved frozen GFS/CAMS
-> complete dawn/dusk point output compared with Python. Local synthetic
HTTP, cached fixtures and native decode do not prove live provider behavior.

Measured final warm cache lookup is 0.046086387 ms in Rust versus
0.155739583 ms in Python on the 1000-record fixture. The current subprocess
bridge costs 9.961158350 ms, so it is not a lookup speed improvement.
Metadata/index publication remains slower: 27.2565 ms Rust versus
17.5307542 ms Python, with unequal durability contracts. Scope, all trials,
initial-measurement limitations and reproduction commands are in
[RUST_PERFORMANCE.md](RUST_PERFORMANCE.md).

The suites retain the existing FastAPI/HTTPX deprecation; native tests retain
their previous duplicate-test/dead-code warnings. Automatic complete planning,
other provider downloads/authentication, cache lifecycle, separate EC point
orchestration, observations/nowcast, production IPC/HTTP/CLI and Python-free
packaging remain incomplete. No production default or overall-completion
claim follows from this stage.

## Stage 16 Verification: 2026-11-09

Local macOS/aarch64 evidence, with rebuilt engine/native CPU artifacts and
unchanged production defaults:

| Check | Observed result |
| --- | --- |
| Native Rust unit tests | 140 passed |
| Engine Rust unit tests | 30 passed; one component benchmark ignored |
| Complete Python suite, default backends/debug engine | 838 passed, 161.69 s |
| Complete Python suite, all six Rust switches/release engine | 838 passed, 139.04 s |
| Engine Clippy, warnings denied; Rust formatting; whitespace | Passed |
| Added point-core tests | 98 cases, including batched field-by-field mutations |
| Frozen raw GFS and EC-basic point workflows | Complete materialized point fields and intermediate outputs matched at documented tolerances |
| Runtime independence | All new engine methods exercised with empty PATH/PYTHONPATH and nonexistent Python path |

The point tests compare all returned fields, not only indices or labels.
They cover GFS/EC, dawn/dusk, valid/invalid/no-burn/no-family/no-profile,
blocked and tied events; exact required/optional fields and types; numerical
bounds, authoritative duplicates, loss chains, color/proxy/scene consistency;
large integer support counts, ties-to-even duration output up to 1e308,
float32 key overflow, Unicode/Python whitespace, microseconds, DST fixed-offset
steps and the one-second anchor tolerance in 2026 and 2400.

Eight raw integration combinations cover both model inputs, phenomena and
observer/model-grid elevations. Materialization from the same core uses strict
structural/numerical comparisons. Independent raw EC comparisons separately
retain the existing float32 ground-area amount/load tolerances; these do not
weaken same-core parity. The frozen real benchmark's 82 assessment fields are
exact on both pinned inputs, not a universal bitwise claim.

Failure tests reject unnamed/multiple observers, preserve existing successful
destinations and exercise a late scalar full-sun failure after successful
regional frame assembly. Owned point artifacts are removed and no parent
manifest is published. This preserves the distinction between the regional
event policy and the scalar full-sun contract; it is not observational validation.

The optimized warm fixed-core component falls from 1.496634084 to
0.411704168 ms/point, with identical Rust JSON byte count/hash. Its Python
reference median is 1.319862666 ms/point. This is not a full-forecast speedup.
Frozen raw workflow medians including durable Rust output are 1231.484917 ms
for GFS and 4003.562209 ms for EC-basic; Python's memory-only references are
760.875292 and 5455.280833 ms. Full scope, trial values and reproduction commands
are in [RUST_PERFORMANCE.md](RUST_PERFORMANCE.md).

The full suites retain one existing FastAPI/HTTPX deprecation; native tests
retain their pre-existing duplicate-test/dead-code warnings. The existing
NumPy/BLAS near-tied-window parity gap, pinned timezone-database limitations,
production caches/planning, separate EC point orchestration, observations,
desktop/HTTP/CLI and Python-free packaging remain incomplete. Passing this
stage does not complete Python retirement.

## Stage 15 Verification: 2026-11-09

Local macOS/aarch64 evidence, with unchanged application defaults:

| Check | Observed result |
| --- | --- |
| Native Rust unit tests | 140 passed |
| Engine Rust unit tests | 28 passed; one component benchmark ignored by default |
| New frame/core/codec and failure checks | 84 passed |
| Complete Python suite, Python defaults and release engine | 740 passed, 118.96 s |
| Complete Python suite, all six Rust switches and release engine | 740 passed, 117.69 s |
| Engine strict Clippy, all targets | Passed |
| Rust formatting and diff whitespace checks | Passed |
| Frozen real GFS / EC-basic frame/core workflows | Complete builder payloads and compact bytes matched; all 82 assessment fields exact |
| Controlled 36,685-point result serialization | Identical byte count/SHA-256 in streamed and materialized modes |

The raw tests cover GFS/EC-basic, dawn/dusk, explicit float64 and model-grid
observer elevations, one-point cores and multi-observer results. Generated
cores additionally pass the existing Python validator; this is not a Rust
validator implementation. Rust raw-file subprocesses run with empty
PATH/PYTHONPATH and a nonexistent Python path.

Strict assembly comparisons use identical assessment, column, timestamp and
profile-diagnostic inputs for both builders. Existing upstream tensor checks
remain separate: assessments use rtol 4e-5 / atol 5e-4 and columns rtol 4e-6 /
atol 5e-5; profile fields retain their Stage 14 per-field tolerances. This
separation matters: an EC fixture has about 2e-9 of ground-area diagnostic
mean difference through independently prepared raw inputs, not an assembly
schema difference. Raw-chain compact-frame bytes also match the independently
prepared Python path, without a quantization tolerance.

Synthetic complete-builder comparisons cover invalid/low-visibility steps,
no burn, no family, absent profiles, incoming blocking, first-winner ties,
uint8 family-code wrapping, original observer elevation precision, microsecond
timestamps and multiple IANA zones. Every valid sample count 1..20 is checked.
Unit/codec tests cover nonfinite saturation, ties and adjacent float32 values,
little-endian output, bounded malformed input, JSON byte-budget failures,
publication poisoning, sync joining and preservation of unowned files.

The controlled Rust serialization baseline peaks at 1757184000 bytes RSS
versus 612089856 bytes for streaming, about 65.2% less, with overlapping
timings and identical serialized results. This is a component memory result,
not a production or Python-versus-Rust speed claim.
Frozen raw workflow medians are GFS 765.13 ms Python / 1223.60 ms Rust and
EC-basic 5533.13 ms Python / 4005.94 ms Rust, with unequal durable I/O.
See [RUST_PERFORMANCE.md](RUST_PERFORMANCE.md) for trials, scope and commands.

Both Python suites retain one existing FastAPI/httpx deprecation warning.
Native tests retain the existing duplicate-test/dead-code warnings. No C ABI,
production default, desktop, HTTP or packaging switch is made here.
Core validation/materialization, NumPy/BLAS near-tied-window parity and the
remaining retirement gates are still open.

## Stage 14 Verification: 2026-11-09

Local macOS/aarch64 evidence, not complete-forecast or deployment acceptance:

| Check | Observed result |
| --- | --- |
| Native Rust unit tests | 140 passed |
| Standalone engine unit tests | 24 passed |
| New selected-profile differential cases | 24 passed within the complete suite |
| Complete Python suite, default backends and release engine | 656 passed |
| Complete Python suite, all six Rust switches and release engine | 656 passed |
| Engine Clippy, all targets, warnings denied | Passed |
| Native library strict Clippy audit | 65 existing errors; none in the new profile module |
| Formatting and diff whitespace checks | Passed |
| Raw GFS / EC-basic with selected profiles | Seven new fields and all previous diagnostics matched; assessments exact |
| Synthetic 36,685 observers x 20 rows | All seven outputs serial/parallel bit identical |

Tests cover exact/off-grid/clamped stencils, missing corners and temporal
endpoints, all-missing/no-cloud/one-level columns, unknown/fallback pressure
levels, fractional/clamped sample indices, deep profiles and both boundary
temporal semantics. Sampled full profiles and aggregate fields are compared
bitwise; class/support/uncertainty outputs are exact, while condensates and
deep/confidence outputs have separate magnitude-appropriate tolerances.
The raw tests cover GFS/EC-basic dawn/dusk without Python on the engine path,
plus instantaneous/average GRIB boundaries and failed-chain cleanup.

Reader tests cover pinned manifest identities, shape/level bounds, changed
arrays, symlinks and rehashed corrupt NPY headers. Native tests cover
finite-blend overflow, missing-endpoint fallback at zero weight, fresh versus
reused scratch, every output bit under one/four threads and first-error order.
Debug and release executables and native CPU variants were rebuilt.
Both complete suites have one existing FastAPI/httpx deprecation warning;
native unit builds retain the two existing test warnings.

The component median is 773.990 ms serial / 158.799 ms parallel, about
4.87x, excluding weather, ray tracing, JSON and I/O. Task-local buffer reuse
reduces the parallel median by 8.9%. Buffered NPY decoding fixes the newly
observed GFS profile-load bottleneck without weakening validation.
Complete trials, real-file timings and remaining optimization candidates
are in [RUST_PERFORMANCE.md](RUST_PERFORMANCE.md).

The public response remains incomplete; near-tied NumPy/BLAS window selection,
full point/frame schemas, services/cache/client workflows and Python-free
packaging remain open. Python defaults and the native C ABI are unchanged.

## Stage 13 Verification: 2026-11-09

Local macOS/aarch64 evidence, not production acceptance:

| Check | Observed result |
| --- | --- |
| Native Rust unit tests | 136 passed |
| Standalone engine unit tests | 22 passed |
| New diagnostic/characteristic differential cases | 65 passed within the complete suite |
| Complete Python suite, default backends and release engine | 632 passed |
| Complete Python suite, all six Rust switches and release engine | 632 passed |
| Engine Clippy, all targets, warnings denied | Passed |
| Native crate strict Clippy audit | Same 65 existing errors; none in the new diagnostic module |
| Formatting and diff whitespace checks | Passed |
| Raw GFS / EC-basic with complete event diagnostics | All 103 components and 19 instant fields matched; assessments exact; all columns/profile children matched |
| Synthetic 36,685 observers x 20 rows | Every additional diagnostic field serial/parallel bit identical |

Tests include empty and all-invalid selections, inactive events, visibility/
external gates, blocker caps, repeated/unsorted selected rows, zero widths,
scalar ceilings and float32 AOD means. Characteristic checks cover threshold
neighbors, candidate order, unknown/null evidence, no-scoring-effect proxy
labels, legacy/quality curves, global peak selection, mixed timestamps,
naive/aware midnight normalization and exact microsecond crossings across
both five-microsecond and thousands-of-years spans. A failing frozen CAMS
catalog also proves cleanup of provisional diagnostic profile bundles.

The new component benchmark measured 54.717 ms serial / 13.042 ms parallel
medians (about 4.20x) for the twenty additional diagnostics, excluding
weather, ray tracing, JSON and file output. Complete stored-file timings and
every trial are in [RUST_PERFORMANCE.md](RUST_PERFORMANCE.md). No complete
public frame/point-core performance claim follows from these measurements.

Python defaults and application entrypoints remained unchanged at Stage 13.
Selected-profile diagnostics were still open then and are addressed in Stage
14 above. Full public assembly, broader parser acceptance, production services/
cache/client integration and Python-free release packaging remain open, as
does the NumPy/BLAS near-tied-window diagnostic selection gate.

## Stage 12 Verification: 2026-11-09

Local macOS/aarch64 evidence:

| Check | Observed result |
| --- | --- |
| Native Rust unit tests | 133 passed |
| Standalone engine unit tests | 22 passed |
| Complete event/instant differential cases | 45 passed, including scalar/regional thresholds, ceilings, empty/invalid samples, weak tails, missing local diagnostics, text and raw GFS/EC pipelines |
| Complete Python suite, default backends and release engine | 567 passed |
| Complete Python suite, all six Rust switches and release engine | 567 passed |
| Engine Clippy, all targets, warnings denied | Passed |
| Native crate Clippy, library, warnings denied | Not clean: 65 lint errors outside the new quality module |
| Rust formatting and diff whitespace checks | Passed |
| Stored raw GFS, 3 x 24,843 cells / 1 observer | All 82 assessment fields exact; all 75 event components and 19 instant fields matched |
| Stored raw EC-basic, 3 x 841 cropped cells / 1 observer | Same complete comparison passed, assessment maximum error zero |
| Synthetic 36,685-point x 20-sample event core | Serial/parallel every numeric field and flag bit identical |

The new tests do not use score-only acceptance: keys, text, null/omitted
fields and complete component values are compared. The real raw-input
benchmarks also verify columns and every profile child. The native library
retains two pre-existing test warnings; the Python suite retains the existing
FastAPI/HTTPX deprecation warning.

The additional strict native-crate Clippy audit reported 50 errors in
`national.rs`, 11 in `lib.rs`, two in `cloud.rs`, and one each in `cams.rs`
and `cloud_structure.rs`; none were in `quality.rs`. These include existing
argument-count, safety-documentation and loop/style lint debt. The passing
engine `--no-deps` check does not imply the entire native dependency is lint
clean. No broad unrelated refactor or lint suppression was applied.

The final component benchmark measured 289.869 ms original serial,
245.256 ms cached serial and 56.178 ms ordered parallel medians. This is
about 5.16x versus the original Rust serial composer, not full-forecast
or Python-versus-Rust speedup. Final-build details, all trial samples,
stored-input timings and unequal-I/O caveats are in
[RUST_PERFORMANCE.md](RUST_PERFORMANCE.md).

No production defaults, frontend sessions, HTTP routes or packaging were
switched. GRIB still depends on ecCodes C; overall Python retirement and the
near-tied-window payload gate remain incomplete.

## Stage 11 Verification: 2026-11-09

On this macOS/aarch64 checkout with rebuilt CPU libraries, debug/release
engine and Python 3.15.2:

| Check | Observed result |
| --- | --- |
| New stored-assessment differential/transport tests | 21 passed in the full suite |
| Native Rust tests | 130 passed |
| Rust engine tests | 20 passed |
| Complete Python suite, default backends | 522 passed |
| Complete Python suite, all six Rust switches and release engine | 522 passed |
| Engine all-target Clippy with warnings denied | Passed |
| Engine/changed native-module formatting and `git diff --check` | Passed |
| Stored raw GFS, 3 x 24,843 cells / 1 observer | All 82 assessment fields exactly matched |
| Stored raw EC-basic, 3 x 841 cropped cells / 1 observer | All 82 assessment fields exactly matched |
| All real per-frame pressure profiles | Within rtol 4e-6 / atol 5e-5, preserving NaN positions |

The new integration cases compare both GFS and EC-basic, dawn and dusk, mixed
timezones, explicit/model-grid heights, off-grid/exact-grid observers, relative
paths and bbox cropping. A separate non-unit-spacing case guards the
reference harness against accidentally assuming 1-degree spacing; real inputs
use 0.25 degrees. Full 36-column tensors, 24-field profiles, samples, epochs,
assessment tensors and reconstructed CAMS source labels are compared.
Restricted child-process tests have empty PATH/PYTHONPATH and a nonexistent
`FIRESKY_PYTHON`; no interpreter is needed by the engine.

Failure cases cover missing cloud evidence, incorrect actual forecast step,
shifted coordinates, changing terrain, missing/wrong-time/insufficient-coverage
CAMS, out-of-domain observers, invalid elevation/bbox, unknown fields,
duplicate model/CAMS times, source symlinks and output collisions. Partial
profile work is cleaned without replacing existing output. Two new Rust
engine tests verify geometry reordering/rejection and every possible
nonempty trim span over multiple observers. The new native test compares
every serial/parallel output bit over one/four-thread pools, threshold-sized
grids and single/gapped/full pressure sets.

The real fixtures and their source SHA-256 identities are reproducible with
`scripts/bench_assessment_migration.py`. GFS cloud derivation parallelism
reduces its stored three-frame Rust workflow from approximately 1.17 to
0.96 seconds while retaining exact assessment output; it remains slower than
the approximately 0.73-second Python in-memory workflow, whose I/O contract
differs. EC measured approximately 3.97 seconds Rust / 5.80 seconds Python
before the derivation change and passed another final-build comparison.
See `RUST_PERFORMANCE.md` for all samples, phase scope and the separate
3.42x pure-Rust derivation measurement.

These are local stored-input tensor/host results, not whole public forecast
equivalence, whole-country scoring/performance acceptance, deployment or
Python retirement. Defaults remain unchanged. The prior tied-window
selection limitation, public response/diagnostic assembly, automatic input/
cache/download selection and production desktop/HTTP workflows remain open.
Existing native duplicate/unused-test warnings and the Python FastAPI/HTTPX
deprecation warning remain unchanged.

## Stage 10 Verification: 2026-11-09

On this macOS/aarch64 checkout, rebuilt CPU native variants, debug/release
engines and Python 3.15.2:

| Check | Observed result |
| --- | --- |
| New regional timeline differential/transport tests | 80 passed |
| Native Rust tests | 129 passed |
| Rust engine tests | 18 passed |
| Complete Python suite, default backends | 501 passed |
| Complete Python suite, all six Rust switches and release engine | 501 passed |
| Engine all-target Clippy with warnings denied | Passed |
| Engine/changed native-module formatting and `git diff --check` | Passed |
| Standalone tensor preparation with empty PATH/PYTHONPATH and nonexistent `FIRESKY_PYTHON` | Passed |
| Pure-Rust in-memory preparation under the same restricted environment | Passed |

The differential suite covers 42 location/date/phenomenon combinations,
512 mixed-zone points, empty in-memory batches, float32 observer-height
rounding, historical second-offset zones, DST/midnight transitions, dateline
adjustment, legacy polar/forced-anchor behavior and the skipped-Apia-date
compatibility exception. Both dawn and dusk also compare all samples over
the complete **36,685-point** production grid, without reducing the domain.
Scalar, nonfinite, duplicate, unordered and empty model-hour input is rejected.
Uncovered valid samples fail; invalid slots do not create false coverage
requirements. Python numerical helpers patched to fail cannot block the Rust
sample path.

The file-backed API verifies source/array identities, all seven output arrays,
the optional six-array no-model variant, unknown-field/resource/shape/timezone
rejection, oversized/symlink inputs and output collision safety. Its restricted
child process never needs a Python executable. The existing C ABI version
remains 3; only an additive regional symbol is introduced.

Five regional Rust tests cover FFI dimension rejection before pointer access,
fold/gap offsets, asymmetric hour-axis exact matching, bounded/sparse cache
bypass and bitwise equality of every output across serial/cached/parallel
execution for both phenomena. Existing native tests still emit two prior
duplicate/unused-test warnings; Python retains one FastAPI/HTTPX deprecation
warning.

The full-suite Rust run sets empirical, GFS, cloud, EC, CAMS and astronomy
switches together. Differential tests deliberately switch back to Python for
reference calculations; this is integration coverage, not a Python-free full
forecast or a production-default acceptance.

On the warm production-domain component fixture, the public caller improves
from roughly 212 to 50 ms (4.20x); the same-executable Rust core improves from
180 to 15.4 ms (11.69x) with caching/parallelism. Full methods, individual-trial
reproduction and limitations are in [RUST_PERFORMANCE.md](RUST_PERFORMANCE.md).
This stage completes solar/model-time input preparation, not weather selection,
CAMS attachment, scoring/diagnostic assembly, application IPC or full forecasts.
Production defaults and the overall retirement objective remain unchanged.

## Stage 9 Verification: 2026-11-09

On this macOS/aarch64 checkout, Python 3.15.2, rebuilt CPU native variants
and the release engine:

| Check | Observed result |
| --- | --- |
| New astronomy/time differential tests | 105 passed |
| Native Rust tests | 124 passed |
| Rust engine tests | 18 passed |
| Complete Python suite, default backends | 421 passed |
| Complete Python suite, all six Rust switches in the environment | 421 passed |
| Engine all-target Clippy with warnings denied | Passed |
| Engine and changed native-module format, `git diff --check` | Passed |
| Standalone snapshots with empty PATH/PYTHONPATH and nonexistent `FIRESKY_PYTHON` | Passed |
| Same restricted child: events, glow window, crossing and cycle selection | Passed |
| Warm pure-Rust position/window example under the same restricted environment | Passed |

The full-suite Rust run includes empirical, GFS, cloud, EC, CAMS and astronomy
switches; differential tests deliberately toggle back to Python for reference
calculations. This is integration coverage, not a Python-free full forecast.
Both Python suites retain one existing FastAPI/HTTPX deprecation warning;
native tests retain two pre-existing duplicate/unused-test warnings.

Differential checks cover 30 dawn/dusk anchors across 15 location/date/zone
cases, the complete five-event dictionary including historical second-offset
zones, 521 random/polar solar snapshots, both event directions, missing
crossings, valid/ineligible typical and remote windows, and shadow speed.
Position tolerances are 2e-10 degrees elevation / 2e-9 azimuth; event tolerance
is 5 microseconds and threshold/window timestamps 2 microseconds. These are
implementation comparisons, not astronomical accuracy claims.

Tests also cover strict malformed calendar/offset input, exact end-of-day
normalization, UTC spelling, fractional precision, ties-to-even half-hour
offsets, cycle lag/day rollover, China calendar dates, resource/unknown-field
rejection, calendar overflow, missing artifacts and backend/cache isolation.
The C ABI and standalone remote-window dictionaries match exactly on the
explicit fixture. Python numerical helpers patched to raise do not prevent
the Rust position/event/window paths from executing.

The measured bridge optimization and separate in-process Rust timings are
recorded in [RUST_PERFORMANCE.md](RUST_PERFORMANCE.md). Remaining work still
includes regional timelines, full forecasts and every production retirement
gate; pinned timezone and Python parser-version differences are not resolved
by this stage.

## Stage 8 Verification: 2026-11-09

| Check | Observed result |
| --- | --- |
| Native Rust tests | 120 passed |
| Rust engine tests | 16 passed |
| New full-catalog differential tests | 37 passed |
| Complete Python suite, default backends | 316 passed |
| Complete Python suite, all five Rust switches and release engine | 316 passed |
| Engine Clippy, no-deps, warnings denied | Passed |
| Rust formatting and Git whitespace checks | Passed |

Full-catalog tests use actual ecCodes-encoded synthetic files, not an observed
IFS model-level weather bundle. They cover 1, 2, 4, 137 and 138 explicitly
requested levels; default L137; exact arrays, raw provenance, mutability,
geopotential conversion, RH clipping, antimeridian cropping and required
2t/2d/lnsp aliases. Valid paths also succeed with Python metadata/numerical
decoder helpers patched to raise.

Failure coverage includes missing/duplicate model and surface fields,
conflicting/missing/wrong-length/corrupted A/B, mixed grids/cycles/steps,
out-of-bbox bitmap gaps, invalid cloud/condensate values, invalid level lists,
UTC/forecast-hour expectations, output collisions and resource bounds.
Missing water PV and extra-level metadata follow the reference rules.
PV-corruption tests account for the actual codec's NaN normalization, as
described above.

Default L137 standalone output passes with empty `PATH`/`PYTHONPATH` and
unavailable `FIRESKY_PYTHON`, retaining 469 raw fields. A 6161-cell synthetic
L137 benchmark compares 489 tensor/raw arrays bitwise with Python. Before/
after record-storage optimization, full manifests/NPY files are byte-identical
for that input and the pinned real EC-basic decode/derivation workflows.
Performance/RSS evidence is in `RUST_PERFORMANCE.md`. Full weather validation,
forecast retirement and platform deployment remain unproven.
The existing FastAPI/HTTPX deprecation and native duplicate/unused test warnings
remain unchanged.

## Stage 7 Verification: 2026-11-09

| Check | Observed result |
| --- | --- |
| Native Rust tests | 120 passed |
| Rust engine tests | 15 passed |
| Expanded spectral differential tests | 33 passed |
| Complete Python suite, default backends | 279 passed |
| Complete Python suite, all five Rust switches and release engine | 279 passed |
| Engine Clippy, no-deps, warnings denied | Passed |
| Rust formatting and Git whitespace checks | Passed |

Expanded tests use ecCodes-encoded synthetic GRIB messages, including shuffled
catalogs, minute-level times, dateline coordinates, clear columns and malformed
fields/levels/time/grid/bitmaps. Float64 arrays match the reference
bitwise. Randomized/threshold tests compare scalar, spectral and profile
helpers within 3e-12 absolute/relative tolerance, including nonfinite inputs,
nearest ties and interpolated integration boundaries. Native ABI bounds are
checked before pointer access.

Standalone diagnostics run with empty `PATH`/`PYTHONPATH` and unavailable
`FIRESKY_PYTHON`, matching reference point diagnostics. No real expanded
31-field weather input was available locally, so this is not a real-CAMS or
meteorological validation claim.

The common writer's serial-sync baseline and optimized release produce
identical manifest and NPY bytes for six workflows: pinned real GFS/EC-basic
derivation, real compact CAMS decode/map/time blend, and synthetic expanded
CAMS decode. Failure tests cover sync errors, worker panic, already-observed
errors, the two-worker bound, source changes and joining before cleanup.
Measured performance and input hashes are in `RUST_PERFORMANCE.md`.

## Stage 6 Verification: 2026-11-09

| Check | Observed result |
| --- | --- |
| Native Rust tests | 116 passed |
| Rust engine tests | 12 passed |
| Migration/transport differential tests | 121 passed |
| Complete Python suite, default backends | 241 passed |
| Complete Python suite, all five Rust switches and release engine | 241 passed |
| Engine Clippy, no-deps, warnings denied | Passed |
| Rust formatting and Git whitespace checks | Passed |

Compact CAMS synthetic tests cover exact double-precision decode, minute-level
validity times, output mutability, backend cache isolation, invalid/duplicate/
missing fields, mismatched grids/times, bitmaps and value limits. Spatial
tests include randomized scan order, dateline normalization, holes,
duplicates, degenerate axes, nonfinite targets/channels and empty targets.
Temporal float32 output is exact against NumPy; scale-height tests cover
physical thresholds, defaults and multidimensional inputs. Production
`_apply_cams_aerosol_profiles` outputs match across backends, including
local AOD, profile tensors and source provenance.

Malformed target files (hash, shape, symlink, duplicate array key,
Fortran-order header, trailing bytes) fail closed. Over-500000 target
requests are rejected before CAMS decode. Existing output directories are
not overwritten.

Real compact files from the 2026-11-06 12Z run, leads 48/51, contain
61311 source cells each. Float64 decoded arrays match Python bitwise.
Using the pinned GFS-derived 6241-cell model bundle, the standalone
2026-11-08 13:00Z interpolated AOD and (6241,11) extinction output match
NumPy exactly, with full coverage. The process ran with empty
`PATH`/`PYTHONPATH` and `FIRESKY_PYTHON=/no-python`; this proves the explicit
sidecar workflow, not complete Python retirement or zero C dependencies.
Component and standalone timings are recorded in `RUST_PERFORMANCE.md`.

## Stage 5 Verification: 2026-11-09

On this macOS checkout:

| Check | Observed result |
| --- | --- |
| Native Rust tests | 113 passed |
| Rust engine tests | 10 passed |
| Migration/transport differential tests | 89 passed |
| Complete Python suite, default backends | 209 passed |
| Complete Python suite, all four Rust switches and release engine | 209 passed |
| Engine Clippy, no-deps, warnings denied | Passed |
| GFS 36000-cell / 11-level derived file output | Passed; 10800000 scalar values, beyond JSON limit |
| Synthetic EC tensors | Exact arrays, metadata, raw provenance and mutability |
| Invalid EC fields/times/grids/bitmap and limits | Explicit rejection |
| Source changes / damaged NPY / symlinks / duplicate manifest keys | Explicit rejection |
| EC tensor cache roundtrip and backend isolation | Passed |

The real stored input was
`.cache/ecmwf/2026-11-07/12Z/ifs-basic-v2-2026110712-f030.grib2`,
with native shape 721 x 1440. For bbox `[110,120,40,30]` (1681 cells),
every EC tensor array, raw field and metadata attribute matched the Python
reference exactly, including array mutability. Standalone release
`derive_ecmwf_basic_to_files` then matched columns `(1681,36)` and profiles
`(1681,9,24)` exactly, including NaNs and sorted coordinates, at 0.25-degree
spacing. The process ran with empty `PATH`/`PYTHONPATH` and
`FIRESKY_PYTHON=/nonexistent-python`.

The full suite still emits the existing FastAPI/HTTPX deprecation warning;
native tests retain the existing duplicate/unused test warnings. These are
local stored-input and host-test results, not complete forecast, performance,
operational weather-accuracy, Windows/Linux deployment or production
Python-retirement acceptance. Production defaults remain Python.

## Stage 1 Verification: 2026-11-08

On this macOS checkout:

| Check | Observed result |
| --- | --- |
| Native Rust tests | 96 passed |
| Rust engine tests | 5 passed |
| Complete Python test suite, reference backend | 127 passed |
| Complete Python test suite, Rust empirical backend | 127 passed |
| Engine Clippy with warnings denied, excluding dependency lints | Passed |
| Real GFS cache | 120 fields / 748920 points: all values, masks and coordinates matched |
| Real CAMS cache | 12 fields / 7800 points: all values, masks and coordinates matched |
| Real EC cache | 56 fields / 58141440 points: metadata, point counts and missing counts matched |

The EC check did **not** compare complete numerical arrays or coordinates.
The decoder comparisons use the same ecCodes backend through separate
language interfaces; they verify adapter behavior, not independent
meteorological correctness or complete forecast equivalence.

Existing test warnings remain: a FastAPI/HTTPX deprecation and duplicate/
unused test declarations in the pre-existing native national scorer tests.

## Stage 2 Verification: 2026-11-08

On this macOS checkout, after rebuilding the release engine and native libraries:

| Check | Observed result |
| --- | --- |
| Native Rust tests | 103 passed |
| Rust engine tests | 5 passed |
| Complete Python suite, default backends | 135 passed |
| Complete Python suite, all three Rust backends and release engine | 135 passed |
| Engine Clippy, changed Rust files' formatting, whitespace checks | Passed |
| Real canonical GFS grid | 115 fields / 6241 cells: all float32 values, masks and coordinates exactly matched |
| Real observer inputs | 4851 x 20 features matched with rtol 1e-12, atol 1e-9 |
| Regional integration | 6241 x 36 columns and 6241 x 11 x 24 profiles matched with rtol/atol 3e-6 |
| Effective profile fields | All five outputs matched the Python reference with rtol/atol 3e-6 |

The real input was the stored GFS cache
`.cache/gfs/2026-11-07/18Z/0p25/fd5bf3073c2648e2cf2c.grib2`.
Comparisons preserve NaN positions. Synthetic tests additionally cover an
antimeridian grid, missing fraction fallback, explicit zero cloud, diagnostic
level retention, shifted same-size grids, mixed forecast steps, missing
vertical priors, absent engine failure and backend cache isolation.

The regional checks still execute Python's remaining derivation logic with
Rust helpers. They do not establish a Python-free forecast, full diagnostic
equivalence, independent meteorological validation or a performance gain.
The Stage 1 tied-window limitation still applies. Defaults remain Python.

## Stage 3 Verification: 2026-11-08

On this macOS checkout, using rebuilt native libraries and release engine:

| Check | Observed result |
| --- | --- |
| Native Rust tests | 107 passed |
| Rust engine tests | 5 passed |
| Complete Python suite, default backends | 145 passed |
| Complete Python suite, all three Rust backends | 145 passed |
| Engine Clippy, changed Rust formatting and whitespace checks | Passed |
| Real GFS regional columns | 6241 x 36: maximum absolute difference 0 |
| Real GFS profiles | 6241 x 11 x 24: matched with rtol/atol 3e-6 |
| Real profile classification | 6241 profiles: all class codes exact, ten outputs within rtol 3e-6 / atol 3e-7 |

The stored input is the same 115-field GFS cache used in Stage 2. Regional
derivation also succeeded with Python's deep-structure and material classifiers
patched to raise, confirming that the Rust path did not silently use them.

New differential tests cover all nine class codes, float32 threshold neighbors,
adaptive family assignment, family-mask overrides, recursive isolated
materials, empty/noncontiguous inputs, NaN/infinite inputs, connected-body
risk, and the inclusive 900 m connection tolerance. Synthetic comparisons
use rtol 3e-6 / atol 3e-7, preserving NaN locations and requiring exact codes.
Exact regional output on this one real cache is not a general bitwise-equivalence
claim. Full forecasts, other platforms, performance and deployment remain
unverified; Python remains the default/reference implementation.

## Stage 4 Verification: 2026-11-09

On this macOS checkout, using rebuilt native libraries and release engine:

| Check | Observed result |
| --- | --- |
| Native Rust tests | 112 passed |
| Rust engine tests | 5 passed |
| Complete Python suite, default backends | 165 passed |
| Complete Python suite, all three Rust backends and release engine | 165 passed |
| Engine Clippy, changed Rust formatting and whitespace checks | Passed |
| Real GFS through C ABI | 6241 x 36 columns and 6241 x 11 x 24 profiles exactly matched, including NaN locations |
| Same real GFS through standalone release `derive_gfs` | Both arrays exactly matched the Python reference |
| Runtime independence for tensor derivation | Standalone endpoint passed with empty PATH and nonexistent `FIRESKY_PYTHON` |

The stored input is the same 115-field GFS cache used in Stages 2 and 3.
The C ABI integration also passed with Python preparation disabled.
Synthetic tests forbid Python's numerical preparation, recovery, geometry
conversion, classification and optics helpers while exercising the paired
Rust path. They compare 96 randomized canonical grids over single/gapped/full
pressure sets and instantaneous/averaged aggregates, plus targeted recovery,
bitmap gaps, sparse spatial patches, local-only support, nonfinite fields and
temporal-provenance cases. Standalone tests check GRIB-to-tensor parity too.

These synthetic comparisons use rtol 3e-6 / atol 3e-5 with matching NaN
locations. Exact equality on one real cache does not establish general
bitwise equivalence, meteorological accuracy or a completed point/regional
forecast. Production defaults, downloads, EC/CAMS normalization, complete
forecast payloads, nowcast, desktop IPC, HTTP/CLI and Python-free packaging
remain unfinished. Large JSON tensor transport remains bounded as documented.

## Stage 24: Verified Native Derived Frames

The stored regional/point endpoints and independently planned GFS/EC point
and EC service endpoints accept an optional request field:

```json
{
  "derived_cache": {
    "directory": "native-derived",
    "lock_timeout_seconds": 30
  }
}
```

Missing or `null` means disabled. `{}` places `.rust-derived-v1` beside each
raw model file. Explicit relative directories resolve against the request
file, not the engine working directory. Planning validates this configuration
without creating cache directories. The feature is opt-in; no production
backend/client or HTTP response-cache default has changed.
Directory resolution is idempotent, including when the request filename
itself is relative or has no parent component.

Each content-addressed `native-derived-frame-v1` entry retains canonical
latitude/longitude and terrain arrays, `[cell,36]` columns, all
`[cell,pressure,24]` profiles, pressure levels, boundary temporal semantics,
and EC proxy coverage/native source shape. There is no source-domain crop
beyond the original requested bbox, field reduction, diagnostic suppression,
or lossy dtype conversion. This is a new Rust format, not a Python cache alias.
Existing Python caches are neither imported nor removed. Native reuse currently
requires the exact bbox; Python's larger-domain/smaller-bbox reuse, combined
profile mmap interoperability and the remaining production cache lifecycle
are not established by this stage.

Keys bind the complete raw-file size/SHA-256, model, canonical UTC cycle/hour,
exact bbox, target OS/architecture, compile-time engine/native source and
dependency-lock fingerprint, loaded ecCodes library contents, effective
definition/sample resource contents, and ecCodes/GRIB environment settings.
The source fingerprint also binds compiler identity, optimization and Rust
flags. Decoder resource symlinks are resolved and hashed; directory traversal
is bounded and repeated targets are not traversed indefinitely. Fingerprinting
is performed once per cache-enabled request, not skipped on cache hits.

Readers require the exact five-array set, native float32 dtype and shapes,
bounded regular nonsymlink manifests/arrays, unique metadata/descriptor keys,
canonical rectangular coordinates, allowed ordered pressure levels, valid
terrain/model metadata, NPY headers and full-file checksums. Rows stream
directly into the final fixed-width float32 allocation, preserving NaN payload
bits without a second flat full-profile buffer. Files/manifests are revalidated
after reading and the raw source is checked again.

Per-key inter-process locks cover lookup, computation and publication.
New entries are built in owned staging directories, synced and atomically
renamed only after the source-bound manifest commits. A malformed existing
directory is rebuilt from raw inputs and moved to an explicit
`.derived-invalid-*` quarantine, preserving unknown files. Entry/root/lock
symlink collisions fail closed. Failed builds do not publish a hit or retain
owned pending bundles. Quarantine retention/eviction remains a lifecycle task.

Only pre-CAMS model derivation is cached. Every request still assembles and
validates current CAMS, solar sampling, scoring, quality and profile diagnostics,
frames, reports, HTML and cross-sections. Complete independently hashed
per-hour profile children are copied into each output, never hard-linked to
cache files. EC source paths/times are rebuilt from the current request rather
than replayed from cached provenance. Parent metadata exposes cache status and
timings; EC service provenance exposes `native_derived_cache`. This does not
claim an HTTP response-cache hit or production registration.

The cache is trusted local application storage. SHA-256/shape checks detect
incomplete, stale or accidentally changed artifacts; they do not authenticate
an attacker who can rewrite both arrays and descriptors. Runtime decoder
resources must remain stable during a request. Full transitive C-library
packaging identity, non-Unix decoder fingerprints, clean-machine releases,
eviction policy and production concurrency/cancellation acceptance remain
unfinished. The core still uses the ecCodes C decoder.

### Stage 24 Verification

Final verification release SHA-256:
`ea40abe1320407fff93c28458e1ffd7672bc9a39efd08dc780ffbf80bf004f83`.
The performance section pins its earlier measurement binaries separately;
no performance gain is attributed to the subsequent path-resolution repair.

| Check | Observed Result |
| --- | --- |
| Full suite, all nine Rust selectors enabled, final release | 1436 passed in 411.96 s |
| Native-cache integration coverage within that suite | 37 passed |
| Engine Rust unit tests | 42 passed, 1 existing manual benchmark ignored |
| Native Rust unit tests | 141 passed |
| Engine Clippy `--all-targets -- -D warnings` | Passed |
| Engine formatting and tracked/new-source whitespace checks | Passed |

The 37 cache cases cover GFS/EC complete main/profile bit parity, 19 corrupt
descriptor/metadata/array variants with preserved unknown files, filesystem
collisions and lock timeout, two-process single-builder behavior, source-byte
invalidation, moved identical raw files, actual decoder-resource content
invalidation even with preserved timestamps, invalid configuration, independent
GFS planning with absolute/relative request paths, and EC point-to-service
reuse with report/quality/dominant/HTML/cross-section parity. Native engine
requests execute with empty PATH/PYTHONPATH and unavailable `FIRESKY_PYTHON`.
Additional Rust unit cases cover original float32 NaN payloads, source changes
before commit, failed-build cleanup, independent key axes and idempotent
request-relative directory resolution.

An initial full pass on the measured release passed 1435 tests in 415.34 s.
The final pass above adds the relative-request regression and uses the rebuilt
release. Logs are `/tmp/firesky-stage24-full-rust-{final,verified}.log`,
`/tmp/firesky-stage24-engine-final.log` and
`/tmp/firesky-stage24-native-final.log`.

The Python suite retains one existing Starlette/httpx deprecation warning.
The native tests retain the existing duplicate-test/unused-function warnings
in `native/src/national.rs`; that independently modified file was not edited.
These local tests do not establish production HTTP/desktop integration,
cache eviction, live-provider, cancellation, cross-platform or clean-machine
release acceptance, and do not complete the overall migration goal.

## Stage 25: Immutable EC Service Response Cache

The independent EC service now has a durable snapshot layer, distinct from
both Python's gzip response/mmap caches and Stage 24's model-only derived
cache. It stores the complete existing native service bundle, not a selected
score or a reduced-domain replacement: all sixteen main arrays, all
per-hour `[cell, pressure, 24]` profile tensors, six JSON companions, report
HTML, provenance, diagnostics and complete theoretical cross-sections.

### Native Contracts

Two debug RPC methods are added; the existing export/read methods remain
unchanged:

```json
{"id":"forecast","method":"forecast_ecmwf_service_cached","params":{"path":"/absolute/path/ec-request.json","cache_dir":"/absolute/path/native-responses","force_refresh":false,"max_age_seconds":3600,"lock_timeout_seconds":30}}
{"id":"profile","method":"read_cached_ecmwf_service_cross_section","params":{"cache_dir":"/absolute/path/native-responses","response_key":"ec-response:<identity-key>:<generation>:<receipt-sha256>","lat":31.2,"lon":121.5,"offset_minutes":0}}
{"id":"selected","method":"read_cached_ecmwf_service_cross_section","params":{"cache_dir":"/absolute/path/native-responses","response_key":"ec-response:<identity-key>:<generation>:<receipt-sha256>","lat":31.3,"lon":121.6,"elevation_m":80,"timezone_name":"Asia/Shanghai","offset_minutes":0}}
```

`path` consumes the existing independent EC request recipe. `cache_dir`
must be absolute; it is separate from the raw and derived caches.
`force_refresh` defaults to false; missing/null `max_age_seconds` imposes no
age expiry, matching the legacy persistent response cache. A finite age is
bounded to 0..365 days; zero recomputes. The per-key inter-process lock
defaults to 30 seconds and accepts 0..3600 seconds.

The forecast result is `rust-ecmwf-cached-service-v1`, containing the full
`service`, immutable manifest path/identity, a registered `response_key` and
lookup-specific `response_cache` metadata. Status is `refresh` or `hit`;
refresh reasons distinguish `miss`, `forced`, `expired` and `invalid`.
Metadata retains both the generation's recipe checksum and the current
request checksum; reformatting a recipe does not falsely claim a recomputation.
The returned service view attaches current lookup metadata and rebases its
report path after relocation. `service_view_is_contextual=true` distinguishes
that view from the unchanged, checksummed on-disk service JSON.

For the original observer, profile requests can omit elevation/timezone and
must match its coordinates. A newly selected point must provide **both** its
own bounded elevation and IANA timezone. It uses the complete retained source
domain/profile matrices, not the original observer's terrain or timezone.
Exact canonical five-minute offsets, time coverage and terrain support
remain mandatory. Changing timezone can move a local-date solar event beyond
the stored hours; the reader fails explicitly rather than clamping to another
day. Point-to-timezone/elevation lookup itself is not yet ported.

### Identity And Freshness

The native identity covers EC/basic/model/member contract, canonical cycle,
target date/phenomenon, observer name/location/elevation/timezone, requested
radius and backend, absolute raw EC/CAMS directories, provider/resolution/URL,
complete engine/native build-source fingerprint, native ABI, IANA database
and architecture/OS. Unsupported resolutions/backends remain rejected.
Transport retry/download settings and the optional derived-cache directory
do not change numerical response identity. ADS credentials/environment
values are neither read on an explicit-cycle hit nor included in the receipt.

An explicit cycle can identify an existing snapshot without opening raw
GRIB files or loading the ecCodes library. Automatic selection still follows
the real native publication/cache-cycle policy before lookup: it cannot
silently pin an older cycle simply to return a hit.

A hit verifies the complete stored generation, but intentionally does **not**
revalidate its historical raw EC/CAMS inputs. This matches the owning
Python response-cache behavior and differs from a derived-cache hit.
Every result says
`source_freshness=immutable-snapshot-not-current-raw-validation`.
Force refresh performs the full current raw/CAMS admission and computation;
there is no cached-output shortcut masquerading as new source validation.

### Publication And Integrity

The layout is:

```text
<cache>/<identity>.lock
<cache>/<identity>/index.json
<cache>/<identity>/<generation>/receipt.json
<cache>/<identity>/<generation>/bundle/{manifest.json,...}
```

One advisory lock covers lookup/build/publication for each request identity.
A fresh, owned random generation is built without replacing an existing
bundle. All arrays, NPY headers, child manifests, exact fixed artifact paths,
SHA-256 descriptors, JSON bounds and the complete file inventory are checked.
The bounded receipt binds the request identity and every published file.
File and directory syncs precede atomic replacement of `index.json`.
The generation directory does not move, so absolute generated report paths
remain valid. Failed computation cleans only its owned pending generation,
leaving the previous published index usable.

The registered token binds namespace, identity, immutable generation and
receipt checksum. Refresh publishes a new token while old readers retain
their original generation. A corrupted index/generation is rebuilt; the bad
index is archived and unknown old files are preserved. Index/lock/key-root
symlink collisions fail closed; symlinked snapshot content is never followed.
Fixed child paths and strict token syntax reject traversal. This is local
integrity/registration, **not** a remote authentication or authorization token.

Old generations are intentionally retained. Bounded eviction/reader leases,
storage quotas, cancellation, production concurrency/task management and
HTTP authorization/route registration are outstanding. Do not expose this
debug file protocol as an untrusted remote filesystem API.

### Verification

Final release SHA-256:
`4212140a79e5bccf98328465563306978daa057866d99dfdcb57cefc7996d2fe`.
The standalone `hello` advertises stage `immutable-service-response-cache`
and 50 debug capabilities; it still rejects the unimplemented generic
production `forecast` method.

| Check | Final Result |
| --- | --- |
| Full Python suite with all nine Rust selectors enabled | 1505 passed in 539.91 s |
| New response-cache cases included above | 69 passed |
| Rust engine unit tests | 44 passed, 1 existing manual benchmark ignored |
| Native Rust unit tests | 141 passed |
| Cargo format, Clippy all targets with warnings denied, whitespace checks | Passed |

`tests/test_rust_response_cache.py` covers complete cold/hit outputs, offline
reads with both raw caches deleted and ecCodes/Python unavailable, old tokens
after refresh, failed-refresh cleanup, TTL, semantic-versus-byte identity,
automatic cycle advancement, concurrent single-builder publication, lock
timeout, corrupt arrays/JSON/HTML/receipts/indexes, rebound malicious
descriptors, duplicate keys, symlink collisions, relocation, selected-point
Python cross-section parity and invalid inputs with a live RPC afterward.
Two additional complete requests download EC raw fields and ADS/CAMS data
together, publish the full service, reuse strict raw caches on refresh and
then hit the durable response without provider credentials or raw inputs.
These are simulated local providers, not live-account acceptance.

The initial full run had only the old 48-capability snapshot assertion
failure; that test was updated for the two explicit new methods. A subsequent
run was stopped before completion to rebuild a zero-TTL clock-skew correction.
The final full result above uses the rebuilt, measured release throughout.
Zero TTL now recomputes even if generation time is slightly ahead of the
host clock; Rust unit coverage retains bounded clock-skew behavior for
ordinary/no age limits.

Final logs: `/tmp/firesky-stage25-full-rust-verified.log`,
`/tmp/firesky-stage25-engine-final.log` and
`/tmp/firesky-stage25-native.log`. The full suite retains the existing
Starlette/httpx deprecation warning. The independently modified
`native/src/national.rs` was not edited and retains its two existing
duplicate-test/unused-function warnings.

`scripts/bench_response_cache_migration.py` measures complete fresh-child
refresh/hit/profile operations and verifies every main/profile tensor bit
outside the timed interval. See Stage 25 in `RUST_PERFORMANCE.md` for measured
scope and limitations.

`native_response_cache_available=true` reports this independent capability.
The original stored service's `response_cache_complete=false` and the
production/public completion flags remain false: native durable snapshots do
not establish a completed production cache/application lifecycle.

## Stage 26: Native Location Services

The independent `location` debug RPC follows `src/firesky/location.py`, not a
fixed timezone or longitude approximation. Its operations are:

| Operation | Contract |
| --- | --- |
| `search` | Trimmed query, schema-2 disk cache first, one-attempt Nominatim candidate then Open-Meteo fallback; candidate timezone/elevation may be null |
| `resolve` | Cache first, Open-Meteo then Nominatim plus point metadata; complete `LocationResult` fields, best-effort disk publication |
| `metadata` | Python-compatible four-decimal rounding, single Open-Meteo attempt, then actual local timezone plus SRTM |
| `timezone` | One offline coordinate, same rounding, explicit geographic manifest |
| `timezones` | 1..4096 ordered `[lat,lon]` pairs, same results/rounding, one dataset acquisition per batch |
| `elevation` | Explicit local HGT or locked SRTM cache/download |
| `tile` | Legacy floor-based tile naming and SRTM coverage bounds |

Queries, finite coordinates, timeouts, retry/lock settings and operation-specific
fields are validated. Provider JSON, compressed/decompressed raster sizes,
location labels and memo counts are bounded. HTTPS is required except loopback
HTTP fixtures; redirects and environment proxy discovery are disabled. One
resident HTTP client pools connections. Debug configuration accepts explicit
provider/cache paths; it is not an authenticated public HTTP API.

The independent bounded LRU caches fast searches and rounded metadata. A
`force_refresh` bypasses these and the resolved-location disk lookup.
`allow_download=false` permits cache/local reads without provider requests.
Memory hits are explicitly labeled `memoized-result`, not fresh provider data.

### SRTM And Location Cache

Both 1201- and 3601-square signed big-endian HGT formats are accepted.
All four samples use the reference bilinear operation order; partial voids
use the valid-sample arithmetic mean, and all-void samples fail. Elevation
remains finite signed raster data, not the narrower forecast observer range.
Tile download coverage remains `-56 <= latitude < 60`.

Per-tile advisory locking prevents duplicate publication across processes.
Transfers are size-bounded; gzip CRC, truncation, dimensions and trailing
bytes are checked before owned temporary-file publication, file/directory
sync and no-clobber rename. Failed downloads leave no HGT/partial temp file.
Existing incompatible or symlinked tiles are preserved and rejected.
Local raster reads validate shape and file size while reading the same file
descriptor; this is not whole-tile SHA-256 immutability.

Location cache format remains `_schema_version:2`, keyed by trimmed query,
with the existing complete result fields. Read errors are misses. Writes
hold a per-file lock, preserve other queries and publish atomically.
Write failures never turn a successful provider lookup into failure.
Unlike the legacy writer, malformed/incompatible or symlinked existing
caches are preserved rather than silently replaced.
The new lock coordinates native writers; the legacy Python location writer
does not participate in it. Mixed-writer lost-update prevention is not
established by the two-native-process test.

### Complete Geographic Data

`scripts/export_timezone_data.py` prepares a version-pinned
`firesky-timezone-source-v1` dataset from timezonefinder 9.2.0 / shortcut
layout 3. Preparation uses isolated Python; Rust execution does not spawn
or import it. No geometry simplification or invented ocean/overlap policy
is applied. The verified fixture contains all 444 timezone names, 1356
polygons and 792 holes from data package 4.2026.6 / dataset 2026f.

The manifest records exact array axes/dtypes/bytes/SHA-256, integer coordinate
scale, root/child grid shape, original candidate ordering and early-stop
indices, plus copied code/data license identities. Ten standard C-order NPY
arrays retain the full geometry. The loader checks bounds, offset/ref axes,
candidate stop semantics, checksums and both licenses before admitting data.
Fortran-order files are deliberately rejected; the exporter explicitly emits
contiguous C-order coordinates.

Ordinary containment uses `geo`'s polygon implementation. Points reported
exactly on a boundary use the owning reference's half-open integer convention,
with MIT attribution retained in `third_party/timezonefinder-LICENSE`.
Poles, dateline aliases, holes, overlapping zones, original shortcut candidate
order and last-zone fallback are preserved.

One immutable resident dataset is keyed by absolute resource root and manifest
identity. Each acquisition rereads the bounded manifest; changing its identity
reloads and fully verifies the arrays. Once admitted, subsequent queries use
verified resident bytes, not fresh revalidation of external array files on
every point. Results expose `immutable-verified-resident-dataset` and explicit
manifest/data-version provenance. `force_refresh` of location results does
not reinterpret mutable arrays under an unchanged geographic manifest.

Reproduce resource preparation into a new destination:

```sh
uv run --no-project --isolated --python 3.14 \
  --with timezonefinder==9.2.0 --with timezonefinder-data==4.2026.6 \
  python scripts/export_timezone_data.py --output /tmp/firesky-timezones-new
```

Actual code and data licenses are copied separately, including the data
package's ODbL notice. Resource redistribution obligations and release
packaging remain explicit review gates, not completed legal acceptance.
The normal project virtualenv was not modified to install timezonefinder.
The 65 MiB prepared resource currently lives under `/tmp`, not in an installer.

### Verification And Scope

The native location suite has 97 cases: complete geocoding/reference parity,
fallback ordering, exact rounding, offline metadata, holes/edges/poles,
both raster sizes, interpolation/void behavior, download and location-cache
concurrency, bad gzip/CRC/redirect cleanup, cache preservation, symlinks,
invalid batches, malformed geographic arrays/manifests and oversized labels.
The separate full-world benchmark checks 20,000 pinned points covering cities,
all polygon/hole samples, overlap regions, grid lines, poles, dateline and
seeded random coordinates. Scalar and batched outputs must both match the
reference exactly. See Stage 26 in `RUST_PERFORMANCE.md`.

Standalone benchmark and `invoke`-based workflow probes explicitly remove
Python/ecCodes availability from the native child.
Provider tests are simulated loopback servers, not live geocoding/elevation
availability or rate-limit acceptance. No Python desktop/HTTP location
consumer, production metadata/profile registration or startup resource
discovery has been switched. `production_location_complete=false` remains
explicit. The full application retirement goal is still incomplete.

Final macOS/aarch64 release SHA-256:
`84eeb1df306430656358dc99c2b36cc57d696d466fc2aeafc87073e60817effd`.
`hello` now reports `native-location-services` and 51 debug capabilities.

| Check | Final Result |
| --- | --- |
| Full Python suite with all nine Rust selectors enabled | 1609 passed in 563.76 s |
| New native location suite | 97 passed in 19.97 s |
| Native engine unit tests | 45 passed, 1 existing manual benchmark ignored |
| Native numerical library unit tests | 141 passed |
| Full-world scalar/batch timezone differential, 3 trials | 20,000 points per trial, zero differences |
| Cargo format, Clippy all targets with warnings denied, whitespace checks | Passed |

Logs: `/tmp/firesky-stage26-full-rust.log`,
`/tmp/firesky-stage26-location-final-tests.log`,
`/tmp/firesky-stage26-engine.log`, `/tmp/firesky-stage26-native.log`
and `/tmp/firesky-stage26-clippy-final.log`. Existing Starlette/httpx and
independently modified `native/src/national.rs` warnings remain; that source
was not edited during this stage. The same release hash was used for the
full regression and the subsequent isolated benchmark.

## Stage 27: Native Desktop Location Transport

The new `firesky-engine desktop-location [--config PATH]` command is private
JSON-lines IPC for the real native clients' location methods. It opens no
listening socket and never forwards a request to Python. It is separate
from `serve`, the sequential migration/debug protocol.

`hello` identifies `firesky-rust-location`, protocol 1, and advertises only
`search`, `metadata` and `cancel`. `production_desktop_complete=false` remains
explicit. Unported inventory, preflight/session, preparation, publication,
forecast and credential operations fail as unsupported rather than returning
fabricated results.

### Real Client Contract And Routing

The adapter follows the owning `desktop_rpc.py` composition:

- Desktop `search` first obtains the fast candidate with a 6-second timeout,
  then always resolves its metadata with an 8-second timeout. A complete
  cached/Open-Meteo candidate does not skip that metadata step. The result
  contains only `name`, `lat`, `lon`, `timezone_name` and `elevation_m`.
- Desktop `metadata` retains the original caller coordinates in its result.
  Only the underlying provider/geographic lookup is rounded to four decimals.
  The result contains only `lat`, `lon`, `timezone_name` and `elevation_m`.
- Queries must be strings with 1..200 trimmed Unicode scalar characters.
  The new typed wire validation deliberately rejects accidental nonstring
  queries instead of Python's arbitrary-object string coercion. Native
  clients already send strings. Numbers/numeric strings for coordinates
  must still decode to finite values within geographic bounds.

Swift and C# `EngineClient` support an explicit
`FIRESKY_LOCATION_ENGINE_BIN` executable path and optional
`FIRESKY_LOCATION_CONFIG` startup file. During the normal `hello`, they
validate the separate child's engine identity, version and required methods.
Only search/metadata calls route to this child. Task/cancellation-token
cancellation follows the transport instance owning that request, and
reconnection/stop disposes both children. Location diagnostics have a
separate `location-engine.log`; the optional `FIRESKY_NATIVE_LOG_DIR` is
useful for isolated probes.

Configured native failures never silently retry through Python. Leaving the
environment switch unset retains the original client path. This opt-in
therefore removes Python from those client requests, not from the application:
the main child, connection prerequisites, forecasts, data management and
credentials still require Python. Qt/HTTP paths are unchanged.

### Admission, Concurrency And Cancellation

The server has four fixed worker threads and at most sixteen admitted active
requests by default, including queued requests. Startup settings allow 1..8
workers and workers..64 active requests. The queue and active registry are
bounded; extra work receives `busy`. Hello/cancel remain available under
full admission, and completion may be out of order.

Both native protocols share the same tested byte-bounded framing: one MiB
per line, oversized-line draining before the next frame, generic parse
errors, validated IDs/method/params and serialized JSON output. Duplicate
active IDs are rejected; completed IDs may be reused. Completion and cancel
are linearized under the request registry lock.

Cancellation is cooperative. Queued cancelled work skips dispatch entirely.
Running work checks cancellation between provider operations, during retry
waits and advisory-lock acquisition, and before cache/SRTM publication.
EOF marks all remaining requests cancelled, drains the queue and joins workers.
The existing cache lock gains a checked acquisition API; its original
callers retain the same behavior.

An in-flight blocking HTTP send/read still waits for its bounded timeout or
completion. `cancel` acknowledges a request, not immediate hard interruption
or transactional rollback of a cache write that already committed. The
terminal reply becomes `cancelled` when the worker observes/completes the
operation; no false cancellation-complete progress event is emitted.

Client stop/reconnect still terminates old child processes. A hard termination
during a file write can leave an owned temporary file, even though final
cache publication is atomic. Crash recovery/temporary-file retention is
remaining lifecycle work; cooperative-cancel cleanup tests are not hard-kill
or power-loss acceptance.

### Configuration And Remaining Gates

The optional startup file is a regular nonsymlink JSON file bounded at
64 KiB. Unknown fields and invalid limits fail before serving. Its `location`
object uses the Stage 26 provider/cache/resource settings; `allow_download`
defaults true. Relative paths resolve in the app workspace. Example and
client launch instructions are in `apps/README.md`.

The complete offline timezone resource still requires explicit preparation
and delivery. Online-only defaults do not fabricate an offline fallback.
This command does not implement the forecast confirmation/frozen-plan/session
state machine, credential storage, resource installer or HTTP service.
Production default switching and full Python retirement remain unaccepted.

The new tests use interactive native processes, not EOF-closed shortcuts that
would cancel work before completion. They cover the exact owning desktop
payloads, provider composition, rounding/original coordinates, offline raster
lookup, fixed workers/admission, out-of-order replies, duplicate IDs, saturation,
cancelled queued work, retry/lock/transfer cancellation, EOF cleanup, malformed/
oversized frames, startup bounds and Unicode limits.

The actual Swift `EngineClient.swift` is compiled into a host probe. A
test-only legacy engine records every call, proving search/metadata and their
cancellation do not reach Python when enabled, including native failures.
Other scenarios cover opt-out behavior, stop/reconnect, invalid executable/
configuration and a wrong-protocol child. No real credential module or file
is used by those probes. Swift app compilation is host evidence, not GUI
interaction or clean-machine release acceptance.

Windows routing is implemented in source but this Mac has no .NET SDK.
No Windows compilation, WinUI interaction or Windows runtime acceptance is
claimed. Existing native numerical source changes were preserved.

Client requests also check serialized UTF-8 size before writing. Previously
an oversized frame could receive an uncorrelated null-ID server error and
leave the client's continuation pending. Swift/C# now reject such requests
locally; a real Swift probe verifies rejection followed by a successful
ordinary request on the same connection.

### Verification

Final macOS/aarch64 release SHA-256:
`30e56fe712f0d789932d917c057669ff66256ec17690dc8f13515e87c56b81de`.
Debug `hello` reports `desktop-location-transport`, retaining the existing
51 migration capabilities. The separate child has its explicit subset
handshake described above.

| Check | Result |
| --- | --- |
| Full Python suite, all nine Rust selectors enabled | 1660 passed in 580.53 s |
| Final desktop-location/client + native-preparation/UI contract tests | 93 passed in 31.96 s |
| Desktop-location cases included in the final focused run | 52 |
| Rust engine units | 45 passed, 1 existing manual benchmark ignored |
| Native numerical library units | 141 passed |
| Final Swift application build | Passed, existing SDK linker search-path warnings |
| Rust format / Clippy all targets with warnings denied / whitespace | Passed |
| Complete pinned geographic scalar/batch differential, final release | 20,000 points per trial, 3 trials, zero differences |

The broad run was already in progress when the final client-side byte-limit
guard and its extra probe case were added. That final delta was verified by
the subsequent 93-case focused run and fresh Swift build, not represented as
a second full-suite run or an invented 1661-pass result. Rust source/binary
was unchanged throughout both runs and subsequent benchmarks.

Logs: `/tmp/firesky-stage27-full-rust.log`,
`/tmp/firesky-stage27-client-final-tests.log`,
`/tmp/firesky-stage27-engine-verified.log`,
`/tmp/firesky-stage27-native-verified.log`,
`/tmp/firesky-stage27-swift-final.log` and
`/tmp/firesky-stage27-clippy-final.log`.
Existing Starlette/httpx and native numerical-test warnings remain.
`native/src/national.rs` was not edited.

See Stage 27 in `RUST_PERFORMANCE.md` for equal-wire local-provider measurements.
The benchmark's reference-only Python child delegates to the owning
`DesktopService` for location calls and excludes real credentials from its
hello; the Rust runtime never launches that reference adapter.

## Stage 28: Complete Native Desktop Inventory

The existing private native-client child now implements the actual desktop
`inventory` method. Swift and C# route it directly with search/metadata when
`FIRESKY_LOCATION_ENGINE_BIN` is set. The historical command name
`desktop-location`, engine identity `firesky-rust-location` and environment
variable remain compatible. Its handshake now advertises/requires `inventory`
as well. This is an expanded subset, not the final desktop engine.

### Original Payload And Counting Rules

The owning contract is `desktop_data.inventory` and the three local scanners
in `web_server.py`. The result retains the complete original
`{"datasets":[...]}` payload in GFS, EC, CAMS order:

- `source`, resolved `path`, raw `total_file_count` and `total_storage_mb`.
  Raw totals include both `.grib` and `.grib2`, including empty/unrecognized
  files. JSON/index/lock/derived files are not raw-storage totals.
- `file_count`, rounded `total_size_mb`, `latest_cycle`, complete `cycles`
  and `unindexed_file_count`. The latter subtracts cycle-grouped files from
  all raw files, not the looser positive-size GFS file count.
- Every cycle retains deduplicated/sorted `hours`, `files`, unrounded `mb`
  and `label`, plus source-specific `coverage`, `paths` or `area_keys`.
- `area_bounds`, including explicit nulls for unknown CAMS areas.

GFS positive `.grib2` files count even without usable sidecars. Only parseable
cycle/hour/coverage sidecars contribute cycle entries. Global coverage remains
null; absent bbox remains null, and duplicate-hour coverage lists remain
ordered. Cycle keys remain the original strings; labels do not convert a
non-UTC offset to UTC.

EC admits nonempty, exactly named `ifs-basic-v2-YYYYMMDDHH-fHHH.grib2`
files. Legacy `ifs-v3` and differently named bundles remain raw totals only.
As with the owning inventory, filename admission is not complete GRIB/header
validation and does not itself validate the calendar encoded in the name.
Duplicate-hour paths retain the last visited eligible file.

CAMS admits the compact `cams-optics-v2` filename grammar, retains run minutes,
lead groups and area-key sets, and excludes spectral/older names from compact
cycle indexing. An empty compact file still contributes its first area-key
lookup attempt but not an available cycle/hour. Generated filenames use the
producer's ASCII digit grammar; arbitrary Unicode-digit filename variants
are not admitted by the native index.

Counts and filename grouping are **not** ready/verified/publication flags.
Inventory does not prove model fields, source freshness, requested-domain
coverage or successful forecast execution. Preflight/confirmation still
requires its independent strict checks.

### First-Message CAMS Areas

The scanner reads the existing `.cams-area-index-v1.json` only when the
entry's exact nanosecond mtime and byte size match the scanned file, with
four finite area values. Otherwise it uses the existing native ecCodes
first-message coordinate reader, normalizing longitude to `[-180,180)`,
and reports `[north,west,south,east]`. Only the first matching raw file for
each area key determines that inventory response's bound, as in Python.
Missing ecCodes or invalid/empty GRIB yields a null bound, not a fabricated
area or failure of the entire byte/count inventory.

No optical values need decoding for this operation. The existing five-million
coordinate bound remains enforced by the area reader. Tests include actual
GRIB, prime-meridian/dateline normalization and malformed later bytes that
must not affect the first-message bound.

A process-local LRU retains at most 4096 successful area results keyed by
resolved path/size/nanosecond mtime; Python uses its own larger cache. This
is metadata-keyed reuse, not whole-file SHA-256 proof. File size/mtime is
rechecked around a newly obtained area. Each inventory rescans the filesystem,
so added/deleted/resized raw files and cycle membership are not hidden by
a cached complete response. Sidecar-only edits under unchanged raw metadata
need not invalidate an already resident area, matching the legacy reuse
semantics.

The native inventory does not write raw caches or area sidecars. One bounded
directory sidecar is retained while scanning, avoiding accumulation of all
parsed directory indexes. A native inventory with valid area sidecars runs
with both Python and ecCodes unavailable.

### Traversal, Bounds And Ordering

One traversal per root collects raw files and reuses each file's metadata for
both raw totals and cycle aggregation. The owning Python inventory performs
separate local-scanner and all-raw traversals, plus repeated area metadata
lookups. Native processing still returns every original cycle/coverage/path
field; the optimization does not replace the payload with summary counts.

Traversal retains the current reference environment's Python 3.15 `rglob`
order: sibling directories' direct matches are emitted before descent through
a LIFO directory stack. A simple recursive DFS changed EC duplicate-lead
path selection in the larger differential fixture; it was corrected and
retained as a regression case. Output ordering is not a portable filesystem
sort policy; older Python versions may have different glob traversal.

Root symlinks resolve for the displayed root path, regular file links count,
and directory symlinks inside roots are not recursed. Relative paths retain
the original EC path representation, including `.`/separator normalization;
displayed root paths resolve even when final components do not exist.

Per root, at most one million visited entries and 128 directory levels are
accepted. Configured roots require nonempty UTF-8 paths up to 4096 bytes.
Sidecar reads require nonsymlink regular files and are bounded at 16 MiB.
Over-limit/malformed GFS metadata is ignored for grouping while the raw file
remains counted. Special raw entries, including FIFOs and directories
masquerading as GRIB files, fail explicitly instead of blocking or
underreporting. These are deliberate admission guards; they do not reproduce
every permissive/coercing Python behavior for malformed metadata.

Cancellation checks run throughout directory enumeration and per-file
aggregation. Work uses the existing bounded desktop queue and serialized
completion/cancel rules. An in-flight ecCodes/read/stat operation is not
asynchronously interrupted. A scan is not a transactional filesystem
snapshot; concurrent changes can cause failure or a changing report, as
with the original inventory. There is no newly invented ready flag.

### Integration And Remaining Work

The startup file gains an `inventory` object with `gfs_cache_dir`,
`ecmwf_cache_dir` and `cams_cache_dir`, defaulting to the existing `.cache`
locations. Client requests cannot select arbitrary roots; roots are supplied
by the local startup configuration. `apps/README.md` records the full example.

Real compiled Swift-client probes exercise data-page inventory through the
native child, including a native error, while recording every call to a
test-only Python main child. Inventory never arrives at Python and does not
silently fall back. The same routing is implemented in C# source; Windows
compilation/runtime remain unverified on this Mac without .NET.

Default startup remains Python. When opted in, the main service, preflight,
publication, preparation, forecast sessions and credentials still use Python;
Qt/HTTP inventory remains on its original path. Production resource delivery,
full task lifecycle and complete Python retirement remain unfinished.

### Verification And Performance

Final macOS arm64 release SHA-256:
`337d38e2b123ab15a6836177283ae26f7f01847f5e37ac1b08bcde72feced865`.

| Check | Observed Result |
| --- | --- |
| Full suite with all nine Rust backend selectors required | 1730 passed in 580.25 s; one dependency deprecation warning |
| Focused location, desktop transport and inventory suite | 218 passed, including 69 inventory cases |
| Engine unit tests | 47 passed; one existing manual benchmark ignored |
| Native calculation unit tests | 141 passed; two existing test warnings |
| Engine Clippy, all targets, warnings denied | Passed |
| Swift package build and real client inventory probes | Passed |
| Windows build/runtime | Not verified; .NET unavailable on this host |
| Formatting, Python benchmark compilation and whitespace | Passed |

The inventory differential suite covers full cycle/coverage/path/area payloads,
duplicate-hour traversal, live additions/deletions/resizes, relative paths,
symlinks, malformed/bounded sidecars, special files, cancellation, startup
configuration, first-message real GRIB areas and actual Swift routing/error
behavior without Python fallback.

After regression finished, a separate three-trial/five-scan benchmark compared
complete responses over identical persistent JSON-lines transports. All 30
responses per fixture matched after normalizing only unordered area-key sets.
For 12,000 synthetic raw files, median resident scans were 298.531 ms in Rust
and 934.393 ms in Python (about 3.13 times as fast). On the existing 141-file
workspace cache they were 3.795 ms and 15.954 ms (about 4.20 times as fast).
Existing-cache filenames, sizes and nanosecond mtimes matched before/after;
this is not whole-content hash verification.

The native child ran with Python unavailable in both modes, and ecCodes also
unavailable for the matching-sidecar synthetic fixture. Real GRIB area decoding
still uses the native ecCodes C library when necessary. These measurements are
not evidence of a C-free engine or full-application performance. Detailed
startup, first-scan, memory and CPU results and reproduction commands are in
Stage 28 of `RUST_PERFORMANCE.md`.

Final traces: `/tmp/firesky-stage28-full-rust.log`,
`/tmp/firesky-stage28-inventory-synthetic-verified.json` and
`/tmp/firesky-stage28-inventory-real-verified.json`.

## Stage 29: Resolved Desktop Plan Cache Checks

`check_desktop_data` implements the cache-checking part of
`desktop_data.check_model_data` independently in Rust. The explicit
`FIRESKY_DESKTOP_DATA_BACKEND=rust` bridge connects this to the existing desktop
service and Qt data panel. `FIRESKY_RUST_ENGINE_BIN` selects the executable.
Python remains the default; a configured native failure never triggers a
Python cache-check fallback.

This is a resolved-plan boundary, not a port of `model_context` or the desktop
session protocol. Python still normalizes raw requests, expands regional
points, resolves geographic timezones/source domains/cycles, builds solar
windows, resolves terrain and performs publication/refresh I/O. A shared
`_refresh_model_context` retains the original EC artifact-availability check
and the rule that a new cycle cannot start after the event window.

The bridge sends one or two independent plans, preserving model cycle, event
start, point count, bbox, area, required model hours, terrain status and notes.
It transfers only the unique required CAMS brackets, not every regional
observer's solar sample. This bounds transport without dropping a requirement:
the owning checker already reduces its times to exactly that union.

### Native Checks And Complete Reports

GFS uses the existing native covering-cache implementation and original
resolution/request-schema URL. A positive filename count cannot substitute
for the requested cycle/hour/domain. Existing metadata-index locks,
publication and invalidation remain in use, so this operation can update
derived GFS index files; it is not the read-only inventory method.

EC retains the owning checker's cycle-label/hour admission for positive
`ifs-basic-v2` files. It does not silently turn preflight into a full EC header
or field validation. Likewise, GFS covering metadata is not proof that all
forecast values decode.

CAMS candidates are parsed once per check and shared across both model plans.
The checker filters required valid times before reading headers, then selects
newest runs with exact-area priority and stable traversal ties. Fast mode
retains exact-area metadata-only admission; a nonexact area needs first-message
coverage, with the existing 0.2-degree-plus-epsilon boundary tolerance.
Matching size/mtime area sidecars can supply that bound without ecCodes.

Strict mode additionally performs the existing full native compact-optics
validation. A malformed newest run is skipped in favor of an eligible older
run; missing ecCodes or invalid optical data produces missing CAMS requirements,
not invented values. Regular file links remain eligible. Newly selected
files are re-statted around checks, and process-local reuse remains keyed by
file version. This is not whole-content hash verification.

Every original report field is retained: cycle/local/publication labels,
source counts, missing hours/valid times/area keys, readiness, terrain status,
notes, event starts and independent model cycles. Paired reports preserve
their per-model `requirements`, readiness conjunction and area-aware missing
count. The original outer grouped row's first-part `missing_keys` field is
retained even though its count uses the union; no unrelated schema correction
is hidden in this migration.

The existing bounded inventory walker is shared for source scans without
performing inventory-only area lookups. Raw/sidecar admission guards remain.
Plans accept at most two distinct models, 100,000 points, 256 model-hour
entries and 256 exact three-hour CAMS targets each. Invalid plans fail before
cache scanning. Model hours are bounded to 0..10,000. The checker does not infer
that a supplied plan matches a raw
frontend request; the eventual native event planner must establish that link.

### Integration And Remaining Boundary

The bridge validates engine identity, report shape, readiness/missing-count
consistency and model-cycle identity before returning the original report.
It uses one engine call for a paired check, rather than calling an engine for
each required hour. The bridge reuses one serialized native subprocess,
retaining file-version probe reuse without caching whole readiness reports.
Up to eight active/queued bridge calls are admitted. Requests and responses
are bounded to 1 MiB; stderr is drained into a bounded 8 KiB tail.

Changing the executable's file identity or the environment reconnects before
the next independent request. Transport failure fails the current call and
disposes the child; only a new check can reconnect. Operation errors retain a
healthy connection. Idle explicit close and Python process exit join/reap the
child. Close waits for an active serialized request. A Python fork closes the
connection before creating independent
parent/child transports. This avoids inherited pipe-reader locks. These
mechanics do not add an asynchronous interrupt of a running native operation.

The persistent transport addresses the observed repeated strict-check cost:
fresh processes reloaded ecCodes and discarded validation metadata for every
check. The performance harness separately retains the original transient
bridge as a benchmark-only baseline, rather than a production fallback.

The private `desktop-location` child and Swift/C# method routing are unchanged.
In particular, this debug command does not advertise the production desktop
`check`, produce a confirmation token or take ownership of a session. The
existing Python service continues to freeze payloads, pin cycles, require
confirmation, perform its final check and own preparation/forecast locks.

Native event/terrain planning from original point/regional payloads,
publication probes, nowcast checks, session expiry/single-use confirmation,
preparation, forecast task lifecycle and complete Python retirement remain
unfinished. The unit cancellation gate does not establish end-to-end desktop
cancellation: an active decode or existing index-lock wait is not interrupted.

### Verification And Performance

Current macOS arm64 release SHA-256:
`7694c321a27ce3afb6d77d8a72bac0726286babfd07add18ce8bf2c51e177885`.

| Check | Observed Result |
| --- | --- |
| Final full suite with all ten Rust backend selectors required | 1835 passed in 583.51 s |
| New preflight and persistent transport cases | 90 + 15, all covered by the final full run |
| Earlier focused inventory/GFS/CAMS/protocol/preflight suite | 373 passed |
| Engine unit tests | 48 passed; one existing manual benchmark ignored |
| Engine Clippy, all targets, warnings denied | Passed |
| Python source/benchmark compilation, Rust formatting and whitespace | Passed |

The full run emits two warnings: the existing FastAPI/Starlette dependency
deprecation and Python's warning from the intentional multithreaded fork
lifecycle test. The latter verifies independent connections and child cleanup;
it is not advice to fork a GUI/forecast process.

Coverage includes real compact GRIB and invalid newest-run fallback, exact/
nonexact areas and edge tolerance, metadata-only fast checks without ecCodes,
GFS covering/global/schema/stale admission, different model areas and missing
keys, publication/refresh gates, real point/regional planning integration,
malformed plans/responses and no Python checking fallback. Transport tests
cover framing, concurrent IDs/admission, external exits, reconnect rules,
large stderr, executable/environment changes, exit/fork cleanup and changed
raw files within one persistent child.

After the first complete regression and the transport-focused checks, separate
uncontended three-trial benchmarks compared all four paths over identical
frozen plans and complete original reports. Every one of 60 reports per fixture
matched. In the 12,006-file fast fixture, resident optimized-bridge checks were
247.945 ms versus 471.045 ms in Python. In the 306-file real-CAMS strict fixture,
they were 8.774 ms versus 17.867 ms in Python and 194.884 ms through the original
transient bridge. Reuse is about 22.21 times as fast as the transient bridge
for repeated unchanged-file strict checks; its first strict check still costs
193.919 ms. These are cache-check subset results, not complete application
performance or proof of Python retirement.

See Stage 29 in `RUST_PERFORMANCE.md` for initialization, child-only memory/CPU,
file-admission limits, bridge-overhead comparisons and reproduction commands.
Final traces: `/tmp/firesky-stage29-full-final.log`,
`/tmp/firesky-stage29-preflight-fast-verified.json` and
`/tmp/firesky-stage29-preflight-strict-verified.json`.

### Stage 30: Raw Desktop Event Context

The desktop bridge now has a separate `desktop_context` Rust operation for raw
point and regional model payloads. It resolves explicit observers, China
nearest-grid point identities, generated named/custom grids, the checked-in
China city catalog, model/CAMS domains, independent model cycles, event
evaluation times, model hours, terrain-dependent replanning and the final
context shape consumed by `check_model_data`. The operation writes a bounded,
hashed JSON context file so a 36,685-point China plan does not cross the
1 MiB line protocol limit.

The Python `model_context` entry point delegates to this operation only when
`FIRESKY_DESKTOP_DATA_BACKEND=rust`; the default remains Python. The Rust
operation does not call Python, download data or claim that cache metadata is a
forecast. It uses the existing geographic timezone resource when configured,
with the owning longitude-based IANA `Etc/GMT` fallback for uncovered/open
coordinates. Non-China production packaging still needs to ship and configure
the verified timezone manifest.

Differential coverage now includes a China grid-normalized point, an explicit
regional point, a custom regional grid, and a guard proving the native raw
planner does not invoke the Python implementation. These tests cover context
planning only; publications, nowcast/satellite availability, confirmation
sessions, preparation, forecast execution and complete Python retirement remain
open lifecycle gates.

Stage 30 verification: the full suite with all ten Rust backend selectors and
the raw-context tests passed **1839 tests** in 583.79 seconds. Engine Clippy
with warnings denied and the existing desktop preflight/transport regression
set also passed.

### Stage 31: Nowcast Context And Cache Gate

Nowcast desktop checks now use Rust for the raw nowcast context and the GFS/CAMS
cache gate when `FIRESKY_DESKTOP_DATA_BACKEND=rust`. The native context resolves
the solar anchor, four-hour future guard, live or retrospective satellite
lookup cutoff, GFS cycle including bounded NOMADS probing, the five-minute
event window, operational GFS bbox, geographic region and CAMS area. HSD
compact-frame discovery remains the existing satellite provider contract and
is combined with the native report before readiness is returned.

The Python path remains the default reference. Publication queries and HSD
byte/cache validation are still Python-owned boundaries. Nowcast preparation
and scoring remain Python-owned, so this stage does not claim a complete
satellite or forecast lifecycle migration.

Stage 31 verification: the full suite with all ten Rust backend selectors
passed **1842 tests** in 583.62 seconds. The added nowcast context/cache
differentials and Rust engine tests passed; Clippy remained clean with
warnings denied.

### Stage 32: Nowcast Preparation Plan

The Rust nowcast context now also exposes the exact CAMS three-hour bracket
set, and a separate `desktop_nowcast_gfs_plan` operation resolves the GFS
prior bbox, pinned cycle and deduplicated forecast hours for both event
samples and the two HSD frame times. With `FIRESKY_DESKTOP_DATA_BACKEND=rust`,
`prepare_nowcast_data` uses this native plan before invoking the existing
Python GFS/HSD/CAMS providers. The Python provider remains the byte/cache and
network execution boundary; this stage moves deterministic preparation
planning without changing download or scoring behavior.

Focused verification passed the Rust desktop context, desktop preparation and
desktop RPC tests (**42 passed**), plus the Rust migration protocol cases
(**98 passed** in the combined focused selection), Rust formatting, Clippy and
Python compilation. The complete suite with all Rust backend selectors then
passed **1844 tests** in 574.33 seconds, with the existing two dependency/fork
warnings. Nowcast scoring, publication ownership, provider orchestration and
the Python desktop service lifecycle remain open.

### Stage 33: Native HSD Compact Cache Gate

The Rust engine now exposes `desktop_nowcast_hsd_cache`. When
`FIRESKY_DESKTOP_DATA_BACKEND=rust`, `check_nowcast_data` discovers recent HSD
compact NPZ frames and validates them in Rust before composing the native
GFS/CAMS readiness report. The gate rejects symlinks and oversized files,
obsolete or unknown metadata, wrong point/radius identity, invalid UTC
timestamps, projection-hash or band/unit mismatches, malformed float32
C-order arrays, nonfinite coordinates, infinite or out-of-range brightness
temperatures, and insufficient finite coverage. Candidates are scanned in the
same newest-first filename order as Python; future or older-than-window frames
are skipped so a stale newest file cannot hide an eligible older frame.

This is a cache-read migration only. Remote HSD listing/range download,
calibration and compact-cache writes, frame construction, motion/advection,
nowcast scoring, publication policy, confirmation/session ownership and the
desktop task lifecycle remain Python-owned. The Python backend remains the
reference default, and this stage does not claim a Python-free nowcast
workflow or native provider/network implementation.

Focused verification passed the Rust desktop context, desktop preparation,
desktop RPC and migration protocol selection (**99 passed**), Rust unit tests
(48 passed; one existing manual benchmark ignored), Clippy with warnings
denied, release build, formatting and the stale-frame Python/Rust differential.
The complete suite with all Rust backend selectors passed **1845 tests** in
586.02 seconds, with the existing FastAPI/Starlette and intentional fork
warnings. This is the final regression result for this stage.

### Stage 34: Native HSD Frame Export

The Rust engine now exposes `desktop_nowcast_hsd_export`. It reuses the
strict compact-cache reader from Stage 33, decodes the two-dimensional
latitudes/longitudes and calibrated B07/B13/B15 float32 arrays, and commits
each source NPZ to the existing `firesky.tensor-bundle.v1` transport as
`hsd-compact-frame-v1`. The bundle metadata repeats the HSD schema,
projection, units, quality, footprint and valid/ready times; the manifest
source identity is the complete NPZ file identity. The native path rejects
source changes before publication.

`RustHsdCompactCacheProvider` consumes the bundle through the existing
validated tensor reader and constructs the same `CalibratedSatelliteFrame`
contract used by the Python provider. Set `FIRESKY_HSD_BACKEND=rust` to route
the cached nowcast analyzer through this provider. The default remains
Python. Remote HSD listing/range downloads, Satpy calibration, compact-cache
writes, motion/advection, GFS-prior orchestration, cloud-column retrieval,
scoring and desktop session ownership remain Python-owned.

The export path has two bounded resident caches: Rust decoded arrays and
Python validated frame objects, each capped at 128 MiB and invalidated by
source/bundle file version. Source-keyed bundles are retained below the
compact-cache `.rust-bundles` directory so repeated requests do not rewrite
the same tensor files. This is a derived cache, not proof of source freshness
beyond the existing size/mtime contract.

Focused verification for this stage passed the Rust desktop context/export,
desktop preparation, desktop RPC, persistent transport and migration protocol
selection (**115 passed**), Rust unit tests (**48 passed; one existing manual
benchmark ignored**), formatting/Clippy and the source-replacement array
differential. The complete suite with all Rust backend selectors then passed
**1846 tests** in **585.70 seconds**, with the existing FastAPI/Starlette and
intentional fork warnings. The release binary used for this stage has SHA-256
`eb88a40835e55b0aaa272919f06e668d32b36ba8be9e18aadd1f91e96ccc654a`.

### Stage 35: Native HSD Motion Diagnostics

The Rust engine now exposes `desktop_nowcast_hsd_motion`. It reads the
source-bound `hsd-compact-frame-v1` manifests produced by Stage 34 and
implements the Python motion contract over B13: finite-pixel overlap,
common finite mean fill, mean removal and population standard deviation,
Hanning-windowed two-dimensional FFT, normalized cross-power, row-major peak
selection with wrapped displacement, 20% displacement rejection, selected
pixel correlation and the `strong`/`unknown` velocity gate. The result keeps
the exact previous/latest UTC identities and age diagnostics.

`RustHsdCompactCacheProvider.estimate_motion` routes paired frames through
this operation, and `JmaNowcastAnalyzer` selects that method only when the
native HSD provider is active. The native operation verifies both complete
tensor bundles before the first calculation and retains at most 64
manifest-version/threshold results. The default Python/NumPy motion path is
unchanged.

Focused verification passes the native frame export and motion differential
(12 tests), Rust unit tests (48 passed; one existing manual benchmark ignored),
RustFFT/Clippy, formatting and Python compilation. The complete suite with
all Rust backend selectors passed **1847 tests** in **588.36 seconds**, with
the existing FastAPI/Starlette and intentional fork warnings. The release
binary used for this stage has SHA-256
`46d33ad1fa8dff4112fe4fee3d75e5dede655ff3e38850fa2047e5773756f9c9`.
Remote HSD I/O, calibration, compact writes, advection/interpolation,
cloud-column retrieval, GFS-prior orchestration, scoring and desktop session
ownership remain Python-owned.

### Stage 36: Native HSD Advection

The Rust engine now exposes `desktop_nowcast_hsd_advection`. It reads a
validated `hsd-compact-frame-v1` bundle, applies the same target-time
translation and non-wrapping bilinear interpolation used by Python, preserves
latitudes/longitudes and HSD metadata, and writes a derived source-bound
tensor bundle. The derived source string keeps the existing
`|advection=<reason>|horizon=<hours>h` provenance contract. Rust supports the
continuous B07/B13/B15 HSD path; categorical HCAI nearest-neighbor fields
remain Python-owned.

`RustHsdCompactCacheProvider.advect` is selected by `JmaNowcastAnalyzer` only
for the opt-in `FIRESKY_HSD_BACKEND=rust` provider. The Python path remains
the default. Rust avoids materializing source arrays when a validated derived
bundle already exists, and Python keeps a 128 MiB bounded projection cache
with manifest/array-version invalidation. Remote HSD listing/range downloads,
calibration, compact-cache writes, cloud-column retrieval, GFS-prior
orchestration, scoring and desktop session ownership remain open.

Focused verification covered the Rust HSD export, motion and advection
differentials, including derived-bundle reuse, source replacement and
float32 interpolation semantics. Rust unit tests passed **48 tests** with one
existing manual benchmark ignored; formatting, Clippy with warnings denied,
Python compilation and the complete suite passed. The full suite with all
Rust backend selectors passed **1848 tests** in **658.38 seconds**, with the
existing FastAPI/Starlette and intentional fork warnings. The release binary
used for this stage has SHA-256
`c6af4c6dcc70c26bdf51db8f5dcb8be01a5a61224fbe6fee2ffff20d144f3f6f`.

### Stage 37: Native HSD Cloud-Column Retrieval

The Rust engine now exposes `desktop_nowcast_hsd_retrieval`. It validates the
source-bound `hsd-compact-frame-v1` manifest and owns the deterministic HSD
B07/B13/B15 posterior: cloud-presence probability, conservative clear-sky
evidence, GFS-supported candidate layers, layer weighting, base-height
posterior, conditional height profiles and cross-layer probability
conservation. HSD phase, optical depth and cloud type remain explicitly
unknown; the HCAI categorical retrieval is not routed through this operation.

When `FIRESKY_HSD_BACKEND=rust` is active and the frame contains exactly
B07/B13/B15 with a native manifest, `calibrated_satellite_cloud_retrieval`
samples the fan in Python, batches the validated samples and vertical priors
into one native call, then adapts the result back to the existing
`SatelliteCloudColumn`/`GridCellFeature` contracts. The Python backend remains
the default, and a native retrieval error is not silently converted into a
different algorithm. The v2 compact response transfers only posterior fields;
frame/prior provenance is reconstructed from the already validated request.

The Rust child keeps a bounded process-local frame-contract cache keyed by the
manifest and all five tensor-array file versions. The first version of a
bundle still runs the complete manifest/array verification; replacement or
mtime/size changes evict the entry and force verification again. This cache is
an integrity-preserving resident optimization under the existing file-version
contract, not a freshness guarantee for mutable source files.

Focused verification passed the native HSD retrieval differential for cloudy
and conservative-clear scenes, the full sampled fan/feature adapter,
persistent transport and migration protocol checks (**85 passed; one existing
fork warning**), Rust unit tests (**48 passed; one existing manual benchmark
ignored**), formatting, Clippy with warnings denied and Python compilation.
The complete suite then passed **1850 tests** in **656.47 seconds**, with the
existing FastAPI/Starlette and fork warnings. The release binary used for the
benchmark has SHA-256
`f809474bbc08aebcbfa4fccd5416f2ee2eddb2177fd92ba82979a52236081528`.
This stage still does not claim that the whole nowcast workflow is Python-free.

### Stage 38: Production Desktop Inventory Bridge

`desktop_data.inventory` now keeps the original implementation as
`_python_inventory` and selects the Rust implementation when
`FIRESKY_DESKTOP_DATA_BACKEND=rust`. The production `DesktopService.inventory`
route therefore uses the persistent Rust `serve` child instead of Python's
three separate scanner traversals. The default remains Python, and a Rust
transport or response-contract failure is surfaced rather than silently
falling back.

The new `desktop_inventory` operation returns the explicit
`rust-desktop-inventory-v1` identity and the complete original
`{"datasets": [...]}` payload. The bridge passes cache-root strings without
pre-resolving them: this preserves Python's EC cycle-path spelling on macOS
while Rust continues to resolve only the displayed dataset root. Existing
native inventory tests remain the source of full GFS coverage, EC path,
duplicate-hour and CAMS area semantics.

Focused verification passed the inventory differential, production
`DesktopService` routing, persistent transport and migration protocol tests
(**126 passed**), Rust unit tests (**48 passed; one existing manual benchmark
ignored**), formatting, Clippy with warnings denied and Python compilation.
The complete suite then passed **1851 tests** in **660.64 seconds**, with the
existing FastAPI/Starlette and fork warnings. The release binary used for the
benchmark has SHA-256
`851c0e65f4baf7eaf9fd23430e05e38c998a03812f6f42c023083c18f11948b9`.
This stage does not remove the Python desktop service or route search,
metadata, checks, preparation, forecasting, credentials or sessions.

### Stage 39: Production Desktop Location Bridge

`DesktopService.search` and `metadata` now use the persistent Rust `serve`
child when `FIRESKY_DESKTOP_DATA_BACKEND=rust`. The new
`desktop_location` operation calls the existing Rust
`location::desktop_dispatch`, transfers the configured provider/cache
settings, and returns the explicit `rust-desktop-location-v1` identity. The
adapter preserves the original desktop payloads and raises native failures
through the existing JSON-RPC error path; it does not call the Python
geocoding or metadata functions as a fallback.

Focused location, inventory, transport and protocol verification passed
**195 tests** with the existing fork warning. The release binary used for the
location benchmark has SHA-256
`299fbe88542d6c6f806db952d4018ca32a0829a5c87e861bf27425eef16efab2`.
The complete suite then passed **1853 tests** in **660.68 seconds**, with the
existing FastAPI/Starlette and fork warnings. The default backend remains
Python, and the main desktop session, publication, preparation, forecast,
credential and cancellation lifecycle is still Python-owned.

### Stage 40: Native Desktop Session Authority

The Rust engine now exposes `desktop_session` through the persistent `serve`
child. For the opt-in `FIRESKY_DESKTOP_DATA_BACKEND=rust` path,
`DesktopService` gives each service instance a private owner identity and Rust
holds the session token, expiry, 32-session bound, dismissed state and
single-use consumption state. Owner mismatches, expired tokens and tokens
consumed by a previous forecast are rejected in the native child. A child
restart invalidates its in-memory session store; the Python mirror cannot
silently revive those tokens.

The Python service still keeps its report/payload mirror and operation locks
for compatibility with the existing UI and test contracts. `create`, `update`
and `consume` synchronize complete snapshots. Normal entry into
`publications`, `prepare` or `forecast` uses the lighter native `validate`
operation, which transfers only the token and model kind instead of copying
the complete report and payload back to Python. Native failures are surfaced
through the existing JSON-lines error path; unknown `dismiss` calls retain
the previous idempotent response.

Focused verification passed the native session contract, production service
route, persistent transport, migration protocol and desktop RPC tests
(**33 session/RPC/transport tests**, plus the existing HSD differential).
Rust unit tests passed (**50 passed; one existing manual benchmark ignored**),
formatting, Clippy with warnings denied and Python compilation. The release
binary used for this stage has SHA-256
`a375c972739318c39655e5008839921ffcfe1fddaee01f674ddea30a29eabcb6`.
The complete Python suite then passed **1855 tests** in **661.62 seconds**,
with the existing FastAPI/Starlette and fork warnings.
This is a lifecycle-authority migration, not a claim that the complete
desktop forecast workflow is Python-free or that bridge latency has vanished.

### Stage 41: Native Desktop Publication Probing

The Rust engine now exposes `desktop_publications`. It keeps a bounded
five-minute process-local cache keyed by source and UTC hour, probes the
concrete GFS `f006` product and the three ECMWF Open Data `.index` mirrors,
and uses the existing ADS constraint-selection logic to identify the CAMS
run. Requested sources run in bounded scoped workers so provider timeouts do
not add serially. The output is the explicit `rust-desktop-publications-v1` contract with
`runtime_python_used=false`, the original `cycle`/`label` fields and the
provider-versus-heuristic `verified` flag. Missing credentials, network
errors and non-published artifacts retain the same heuristic cycle with
`verified=false`; they never claim provider verification.

When `FIRESKY_DESKTOP_DATA_BACKEND=rust` is active, `DesktopService.publications`
and the Rust-backed model/nowcast preflight path use this RPC. The Python
`published_cycles` function remains the default backend/reference
implementation. Focused validation covers provider URL/cycle boundary
helpers, invalid response rejection, native service routing, desktop
preflight parity and the protocol capability list. This stage migrates
publication I/O ownership only; raw planning, cache downloads, preparation,
forecast scoring, progress/cancellation and the Python desktop process remain
unmigrated.

The final release engine used for this stage has SHA-256
`52c3c2e2456f4a83cfd1ad300c3093d1451ce3833ed38e5d0924e478279e9081`.

### Stage 42: Native Desktop Model Preparation

The Rust engine now exposes `desktop_prepare`. It consumes the same bounded
desktop request file as `desktop_context`, reuses the native event/space
planner, downloads a first model frame when model-grid terrain is pending,
reruns the context for terrain-aware event hours, and then resolves all GFS or
ECMWF files plus exact CAMS optical-profile brackets. CAMS `resolve` results
are checked for an empty `missing_valid_times` set before the operation can
return `ready`; a native failure is not routed through a Python provider.

When `FIRESKY_DESKTOP_DATA_BACKEND=rust` is active,
`desktop_data.prepare_model_data` uses this operation. The adapter preserves
the existing `model_source="both"` shape by issuing separate native GFS and EC
requests, and reports the same coarse progress envelope. The persistent Rust
transport admits `desktop_prepare`; its response is explicitly identified by
`rust-desktop-prepare-v1` and `runtime_python_used=false`.

Focused verification passed the native preparation bridge, raw-context,
desktop RPC/data and Rust protocol tests; Rust unit tests passed **53 tests**
(one existing manual benchmark ignored), formatting, Clippy with warnings
denied and Python compilation. A live upstream download was not used as a
deterministic test fixture, so provider throughput, rate limits and complete
forecast latency remain unclaimed. The release engine used for this stage has
SHA-256
`16385122abeeecff186b8470b701ab572b8251c53ac97806804157395ea61d74`.

The complete Python suite then passed **1863 tests** in **661.32 seconds**
with the existing FastAPI/Starlette and intentional fork warnings. The suite
includes a local ecCodes-backed native GFS/CAMS preparation fixture and a
normal-path preflight guard proving Rust context is used without a Python
planner fallback.

This stage migrates model-input preparation only. Nowcast remote HSD
retrieval/download, forecast scoring, fine-grained progress/cancellation and
the Python desktop process remain open completion gates.

### Stage 43: Native Nowcast Data Preparation

The Rust engine now exposes `desktop_nowcast_prepare`. The operation consumes a
bounded normalized nowcast request, resolves the native nowcast context, checks
the two recent compact HSD frames already written by the provider, expands the
GFS hour set with those frame timestamps, and resolves/downloads the native GFS
files plus exact CAMS profile brackets. CAMS responses must contain an explicit
empty `missing_valid_times` list before the operation can return `ready`.

When `FIRESKY_DESKTOP_DATA_BACKEND=rust` is active,
`desktop_data.prepare_nowcast_data` still lets the existing Python provider
discover and write remote HSD compact frames. It then hands the rest of the
preparation to Rust and preserves the existing point/valid-time response shape.
The persistent Rust transport and hello capability list include
`desktop_nowcast_prepare`; native failures do not fall back to Python GFS/CAMS
providers.

The deterministic local fixture uses real ecCodes CAMS files, native HSD
compact-cache validation and GFS cache metadata. It passed without network
access and without entering the Python GFS/CAMS provider paths. Remote HSD
listing/range retrieval, calibration/cache writes, nowcast scoring, forecast
execution, fine-grained progress/cancellation and the Python desktop service
remain open. This stage therefore moves the nowcast input-provider boundary,
not the complete nowcast workflow.

Focused desktop/native verification passed **146 tests**; Rust release unit
tests passed **53 tests** with one existing manual benchmark ignored.
Formatting, Clippy with warnings denied and Python compilation passed. The
release engine used for this stage has SHA-256
`dbfb340baea8a50556ca216d005133885c5cc23ed74a33ef2cebf60bce4ba5fe`.
The complete Python suite then passed **1866 tests** in **661.70 seconds**,
with the existing FastAPI/Starlette and fork warnings.

### Stage 44: Native Desktop Point Forecast Execution

The opt-in Rust desktop backend now routes point forecasts through the native
engine for both GFS and EC. The native path resolves the desktop context,
builds the frozen input plan, validates cached model/CAMS inputs, decodes and
scores the native fields, materializes the point payload and verifies the
cross-section source identity. Python remains an IPC/result adapter only for
this path.

The two model artifacts have distinct contracts. GFS uses
`rust-stored-point-forecast-v1` and returns `point_forecast.json`. EC uses
`rust-ecmwf-point-report-v1`, validates `ecmwf_point.json`, and promotes its
`web_report_payload` to the desktop result while retaining native quality and
dominant-burnable diagnostics. Neither artifact claims the complete public web
forecast contract; both report `public_forecast_complete=false`.

Desktop point requests now opt into the native content-addressed derived-frame
cache under the configured model cache. The Python tensor bridge keeps full
manifest/source/hash validation but mmap-maps arrays to avoid copying large NPY
payloads into Python memory. Region forecasts, nowcast scoring/retrieval,
HTML/map publication and the Python desktop service remain open migration
boundaries.

Focused verification passed **5 native point bridge tests**, including a real
EC ecCodes fixture, plus **59 existing desktop/RPC/process tests**. Rust
release unit tests passed **53 tests** with one existing manual benchmark
ignored; formatting and Clippy with warnings denied passed. The complete
Python suite then passed **1871 tests** in **657.33 seconds**, with the
existing FastAPI/Starlette and fork warnings. The release engine hash remains
`dbfb340baea8a50556ca216d005133885c5cc23ed74a33ef2cebf60bce4ba5fe`; the
default backend remains Python.

### Stage 45: Native Desktop Regional Frames and HSD Selection

The opt-in Rust desktop backend now routes regional GFS/EC frame execution
through `build_stored_frames_to_files`. The Python adapter translates the
native desktop context and `desktop_prepare` output into the frozen
`regional-frames-v1` request, verifies the output manifest and `forecast.json`
identity, and restores the existing regional result shape before writing the
internal cache. Context/preparation cycle and forecast-hour mismatches fail
closed.

Regional cache reads require
`rust-desktop-regional-forecast-v1` with `runtime_python_used=false`, so a
Python-generated cache cannot hide the native route. The public array-v6
summary path recognizes the internal artifact's
`public_forecast_complete=false` marker and refreshes through the public
owner instead of returning incomplete data.

When `FIRESKY_DESKTOP_DATA_BACKEND=rust` is selected and
`FIRESKY_HSD_BACKEND` is unset, desktop nowcast scoring uses the Rust compact
HSD provider, native motion/advection and the Rust HSD posterior. An explicit
HSD backend override still wins. This is a calibrated satellite-core
migration. Later stages add native vertical-prior construction and event
scoring; remote HSD warming, HTML/map output and the public web contract remain
outside that boundary.

The regional bridge has focused tests for the frozen request, output identity,
cache boundaries and a real local Rust GFS frame artifact. The default backend
remains Python. The complete Python suite then passed **1877 tests** in
**661.93 seconds**, with the existing FastAPI/Starlette and fork warnings.

### Stage 46: Native Public Regional Model Route

`FIRESKY_REGIONAL_DATA_BACKEND=rust` now routes public GFS/EC model-region
requests through the native regional frame executor. The route uses the
separate `rust-public-regional-forecast-v1` contract. It validates the native
point fields against the existing array-v6 summary projection before writing
the durable full cache and public summary.

When the selector is active, a Python full cache and Python summary cache are
ignored. A native public cache is served only when
`runtime_python_used=false` and `public_forecast_complete=true`. The default
backend remains Python, and nowcast/public provider transport remains on its
existing owner.

The real local Rust GFS frame fixture now passes both the internal summary
validator and the public contract path. This stage moves regional numerical
execution; it does not claim a Python-free HTTP server or complete nowcast.

The regional frame request leaves the point-oriented derived-frame cache off.
The cache remains enabled for native point forecasts; regional frames already
write complete profile children, and the measured warm regional path was
slower when it revalidated and reloaded those children through the cache.
The complete Python suite then passed **1879 tests** in **658.60 seconds**,
with the existing FastAPI/Starlette and fork warnings.

### Stage 47: Native HSD Horizontal Metrics

The native `desktop_nowcast_hsd_retrieval` operation now accepts an explicit
metrics request and returns
`rust-desktop-nowcast-hsd-retrieval-v3`. Rust performs the same horizontal
sample de-duplication and probability aggregation as Python, including
near/far/horizon ratios, texture, uniformity, blanket, horizon clearance and
composition. Empty and unknown-column cases preserve the Python reference
semantics.

`calibrated_satellite_cloud_retrieval(..., return_metrics=True)` validates and
passes the native metrics to the nowcast analyzer. Existing v1/v2 native
retrieval calls and the Python backend remain available. The focused cloudy
and clear fixtures compare native columns, sets and all metric fields against
the Python implementation. GFS vertical-prior construction, final event
scoring, remote HSD warming, HTTP/CLI ownership and a Python-free nowcast
workflow remained open at this stage. Later stages add native prior and
compacted final-scoring contracts; Stage 51 records the current boundary. The
complete Python suite then passed **1879 tests** in **677.79 seconds**, with
the same two existing warnings.

### Stage 48: Opt-In Native GFS Vertical Prior

The Rust engine now exposes `desktop_nowcast_prior` with the
`rust-desktop-nowcast-gfs-prior-v1` contract. It owns the deterministic
left/right GFS feature interpolation, nearest horizontal column selection,
terrain cutoff and ordered `VerticalPriorLevel` construction. Python still
owns cache access and decoded-grid extraction, and the bridge reconstructs
the existing `VerticalPrior` object after strict response validation.

`FIRESKY_GFS_PRIOR_BACKEND=rust` enables this path. It remains opt-in after a
same-fixture benchmark measured a **0.488 ms** Rust RPC median versus
**0.031 ms** for local Python construction. The stage now also exposes
`desktop_nowcast_prior_batch` with the
`rust-desktop-nowcast-gfs-prior-batch-v1` contract, and the satellite retrieval
path sends all sample points in one frame through that batch request. The batch
measurement is **0.278 ms per prior**, versus **0.419 ms** for separate RPCs
(1.51x faster), but remains **8.91x** slower than local Python because the
feature arrays are still repeated in JSON. This is transport amortization, not
an end-to-end nowcast speedup. A compact decoded-grid/array contract is the
next optimization target; the full Python nowcast workflow remains open.

### Stage 49: Shared GFS Prior Batch

`desktop_nowcast_prior_shared_batch` separates frame-level GFS feature arrays
from sample-level target/terrain metadata. Rust performs the left/right feature
interpolation once and builds all requested priors from that shared result.
The Python provider selects it when both decoded snapshots have matching
coordinate/pressure domains and falls back to the previous batch contract for
irregular domains.

The 24-sample warm fixture measured **6.697 ms** for the previous batch and
**3.185 ms** for the shared batch, or **0.279 ms -> 0.133 ms per prior**
(**2.10x**). JSON request size fell from **29,810** to **4,372** bytes
(**6.82x** reduction). Local Python remained **0.031 ms per prior**, so the
Rust selector stays explicit and the complete cached nowcast path remains
unverified. The complete Python suite then passed **1881 tests** in
**673.03 seconds**, with the same two existing warnings.

### Stage 50: Cached Nowcast Batch Boundary

The normal 900 km satellite footprint can contain 150 sample points, which
exceeds the native batch limit and can also exceed the 1 MiB line protocol
limit when every point carries local decoded-grid features. The provider now
splits shared calls at 128 targets and 2,400 unique coordinate/pressure
features, with the old batch contract retained as an irregular-domain
fallback. A cached 150-point fixture completed successfully through the
shared Rust path: GFS prior construction was **747 ms** in Rust versus
**796 ms** in Python, and complete calibrated retrieval was **1771 ms** versus
**1849 ms**. This is a local warm-cache measurement and does not close the
Python-free production workflow gate.

### Stage 51: Compact Single-Call Nowcast Quality Ledger

`score_nowcast_fields` now computes the event composition, all per-step
instant values and the nowcast projection in one native call under the
`rust-desktop-nowcast-quality-v2` contract. The event component dictionary is
returned once instead of being repeated for every instant event. Instant
values use the declared 19-value matrix, and the per-step event projection uses
the declared 10-value matrix. Every projection field is validated against
Rust-reported metadata before the Python adapter reconstructs the original
step dictionary.

The Rust path supports parallel instant-event composition for batches of at
least 64 rows; serial and parallel results have a dedicated exact parity test.
The old two-call adapter remains the benchmark reference only. The normal
production scoring selector defaults to this one-call Rust path, while an
explicit `FIRESKY_NOWCAST_SCORING_BACKEND=python` retains the Python formula.

On the release engine, a warm synthetic score-only fixture measured:

| Steps | Previous two calls | Compact one call | Speedup |
| ---: | ---: | ---: | ---: |
| 1 | 0.693 ms | 0.343 ms | 2.02x |
| 5 | 1.412 ms | 0.526 ms | 2.68x |
| 20 | 3.739 ms | 1.011 ms | 3.70x |
| 60 | 9.515 ms | 2.120 ms | 4.49x |
| 128 | 18.437 ms | 3.738 ms | 4.93x |
| 150 | 21.003 ms | 4.238 ms | 4.96x |

The serialized 128-step response fell from **862,511** to **65,673** bytes
(13.1x). This is a local score-bridge microbenchmark. It excludes HSD frame
decode, GFS prior sampling, observation constraints, report assembly and HTTP
transport, so it does not establish an equivalent end-to-end nowcast latency
improvement. Release SHA-256 is
`8806ac3e216d7beee0ce69489d42f305414d0c863b6bebe2dccf560d17acaeb0`.

### Stage 52: Unified Calibrated Retrieval RPC

`desktop_nowcast_calibrated_retrieval` now runs one calibrated HSD scan from
sampling geometry through decoded GFS prior construction, HSD pixel selection,
cloud-column posterior and horizontal metrics in Rust. The response uses
`rust-desktop-nowcast-calibrated-retrieval-v1`; the command-line line protocol
receives only a bounded file receipt for
`rust-desktop-nowcast-calibrated-retrieval-file-v1`, and Python verifies the
file byte count and SHA-256 before parsing it. This removes the 1 MiB
line-response limit from the 150-point result without changing the request or
ordinary RPC response limits.

Rust keeps bounded, file-versioned caches for validated HSD frames and decoded
GFS sidecars. A cold request still validates the complete bundle; a warm hit
checks the manifest and member identities before reusing arrays. GFS sampling
uses decoded sidecars without repeating Python NumPy feature extraction for
every sample. HSD nearest-pixel lookup uses a latitude-bucket index, wraps
longitude correctly across the date line, rejects targets outside the frame
footprint and preserves storage-order tie behavior.

The Python bridge validates every contract field and reconstructs the existing
`GridCellFeature`, `SatelliteCloudColumn`, `SatelliteCloudColumnSet`,
`VerticalPrior` and metrics objects. `calibrated_satellite_cloud_retrieval`
returns priors in sample order; explicit `None` entries remain `None` rather
than being mistaken for an empty batch. The differential fixture covers
cloudy, clear, non-finite channel, no-retrieved-column and outside-footprint
results against the Python implementation. A nowcast-adjustment regression
verifies that GFS prior source and height reach the final adjusted vertical
profile.

The path is selected when the calibrated frame has a native manifest and both
the HSD and decoded-prior conditions are available; otherwise the established
Python fallback remains explicit. This stage covers only one calibrated
retrieval request. HSD download/cache warming, multi-frame selection,
motion/advection orchestration, `NowcastAdjustment` construction, final event
scoring, HTTP/UI and packaging still involve Python ownership. It is not a
claim of a pure-Rust application or complete production-nowcast latency.

The 9-trial 150-point real-cache benchmark measured a **30.20 ms** file RPC
and **50.54 ms** wrapper versus **1862.57 ms** for the Python posterior, or
**61.67x** and **36.85x** respectively. Python object reconstruction accounts
for about **20.58 ms**. These are warm local retrieval-only measurements; see
[RUST_PERFORMANCE.md](RUST_PERFORMANCE.md#stage-52-unified-calibrated-retrieval)
for the complete table and exclusions.

### Stage 53: Multi-Scan Rust Nowcast Adjustment

`calibrated_only_nowcast_adjustment` now has a complete opt-in native path for
one to four calibrated HSD scans. The operation owns UTC deduplication,
five-minute timeline selection, per-scan calibrated retrieval, nearest-time
primary selection, multi-scan provenance aggregation, sample coverage,
confidence, cache identity and `NowcastAdjustment` payload assembly. Each scan
still retains its own observed features, source time, cloud-column set and
vertical-prior provenance.

The display/advected frame remains a separate identity input. Public image
metrics and the positive-coverage gate use the nearest scoring scan, while
`image_valid_time_utc`, range diagnostics and
`satellite_vertical_profile.valid_time_utc` use the display frame. Exact time
ties select the earlier UTC scan, matching the Python loop's strict
`distance < primary_distance` replacement rule.

Native scan retrieval is ordered and bounded to four workers, so a typical
two-frame pair runs concurrently without unbounded fan-out. Rust rejects
independent scans that collapse onto one nowcast offset and rejects a primary
scan with zero usable calibrated coverage; a stale display frame cannot become
a score.

The Python bridge verifies the request-file SHA-256, output byte count and
SHA-256, fixed response identities and every nested cloud-column field before
constructing the existing `NowcastAdjustment`. Derived AGL fields are checked
as part of that payload and then removed before reconstructing
`SatelliteCloudColumn`. New regressions cover complete model reconstruction,
all receipt identities, fixed algorithm/source/channel/schema identities,
window fail-closed behavior, absence of per-frame Python retrieval on the
native path, explicit fallback only when native context is absent, and the
rule that a contract failure after Rust execution is not hidden by fallback.

This stage does not make the production nowcast Python-free. HSD/CAMS/GFS
downloads and cache warming, motion estimation, advected-frame creation,
final event scoring, HTTP/CLI/UI ownership, packaging and clean-machine
cross-platform validation remain outside the migrated boundary. Nor is this
a complete request-latency claim: the benchmark starts from warm local HSD
and GFS caches.

On the pinned 2026-11-07 fixture, seven warm trials measured **55.23 ms** for
one scan and **75.13 ms** for two scans through the complete Python wrapper.
The previous serial two-scan wrapper median was **82.17 ms**, and the current
Python reference path measured **3063.54 ms** for two scans. The two-scan
wrapper is therefore **40.78x** faster than that reference in this measured
scope. See
[RUST_PERFORMANCE.md](RUST_PERFORMANCE.md#stage-53-multi-scan-nowcast-adjustment)
for the full breakdown. Release SHA-256 is
`39726d44dcdc57c488170410d5eb8ede4e24ee2c9ecbaf40acd07453443ff412`.

### Stage 54: Native Batch Nowcast Assessment

`desktop_nowcast_assessment` is a new opt-in engine operation that scores one
to sixteen observation scans in a single call. Each scan carries its own
`hsd-assessment-feature-row-v2` rows — the legacy 20-column buffer extended by
`surface_temperature_k`, `surface_dewpoint_k`, the satellite footprint geometry
and `horizontal_cloud_probability` / `cloud_probability_given_cloud`; absent
cells travel as NaN and are never backfilled to 0 — plus its solar geometry,
radar fields and per-scan satellite material evidence. Inside one call the
engine builds the cloud columns (conditional-probability aware), the
`FeatureSpatialIndex` query family, `candidate_signals` with the dual-segment
aerosol path and dawn branch, the local-deck / remote-boundary / cloud-hole
signals, `diagnose_solar_inflow_blockage`, both weather penalties and the full
solar-path cross section with profile samples, then the `assess_timestep`
composition (regime, scene, color, phase, duration, coverage, structure gate,
reasons, map cells) and the radar constraints with the same field semantics as
`apply_nowcast_observation_constraints`.

The operation emits, per scan, the complete constrained `TimeStepAssessment`
field set, and for the whole batch the 18-value ledger rows in
`native/src/quality.rs` `INPUT_NAMES` order, constructed with the same float32
quantization chain as `fire_cloud_quality_from_assessments`, plus the canonical
event/instant ledger over those rows. Event `best_index` selection and the
core-window rules stay in Python. The bridge
(`nowcast_assessment` / `nowcast_assessment_as_model`) validates the request
SHA-256, output byte count and SHA-256, both contract identities and every
nested payload field before rebuilding full `TimeStepAssessment` objects.
`FIRESKY_NOWCAST_ASSESSMENT_BACKEND` selects the path per process (default
`python`); an invalid value raises, and a native failure after Rust execution
propagates instead of falling back to Python.

Differential coverage on the pinned 2026-11-07 two-scan fixture and synthetic
batteries: complete `TimeStepAssessment` field equality against
`assess_timestep` + `apply_nowcast_observation_constraints` (scores, counts,
all four signal tops, boundary uncertainty, cross section with profile and
summary strings, solar inflow blockage, selected AODs, forward/observer
coverage fields, color diagnosis, every reason line); rows equality against
the `national_batch` float32 oracle at `rtol=3e-11`; missing-prior,
no-usable-column, radar-absent and radar-low-coverage semantics; candidate
stable-sort ties and boundary-merge ties; empty scan sets; and receipt
tampering (contract, bytes, SHA-256, request SHA-256) rejected. The
`reused_nowcast_diagnostics` regression is unchanged.

This stage does not make the production nowcast Python-free: the backend
selector defaults to `python`, HSD/CAMS/GFS downloads and cache warming, main
scan selection, final event `best_index`/window rules, HTTP/CLI/UI ownership,
packaging and clean-machine validation remain outside the migrated boundary.
The per-scan timing in
[RUST_PERFORMANCE.md](RUST_PERFORMANCE.md#stage-54-nowcast-assessment-baseline)
is the pre-existing Python baseline table; the new
"Stage 54: Native Batch Nowcast Assessment" section there measures this
operation against that baseline with the same pinned fixture — the two tables
share the Stage 54 name but measure different paths. Release SHA-256 of the
measured binary is
`aebb2fd7f6968d60788ac41b9cf579b5dd74b61f665789516040c76a6ef0c50b`.
