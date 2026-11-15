# Rust Engine Performance

## Scope

Measured locally on 2026-11-09, macOS/aarch64, 10 available logical CPUs,
Cargo release builds. This measures a standalone Rust process producing
stored-input tensors, compact frames/generated point cores and, in Stage 16,
validated/materialized single-point results.
Stage 17 separately measures synthetic GFS cache access and its Python
subprocess bridge, not download throughput.
Stage 18 measures automatic event input planning, including a bounded CAMS
validation-probe optimization and the remaining terrain-prepass cost.
Stage 19 measures an offline ADS raw-cache-miss workflow, request reduction
and request-local decoder reuse against the corrected Python batch path.
Stage 20 measures offline EC public/basic raw transfers, typed bounded index
reuse and the difference between native batching and per-hour bridge calls.
Stage 21 measures independent EC point report workflows, cold/warm cache
behavior, split versus one-request native execution and request-scoped
ecCodes library residency.
Stage 22 measures full verified profile cross-sections and sparse-column
decoding. Stage 23 adds full EC provenance/HTML/cross-section service-output
costs against the existing Python Web service's forced recomputation.
Individual sections distinguish component work, process startup and durable
output. None establishes complete production forecasts, desktop IPC, live-provider downloads,
or deployed-system performance.

The measured process does not execute Python, but GRIB decoding still uses
the ecCodes C library. "Python-free Rust engine" is not "zero C dependencies".
The production application has not completed Python retirement.

`engine/examples/bench_stored.rs` reuses the actual decode, canonical packing,
derivation and bounded tensor writer. It reports source identity, dimensions,
phase wall times, individual samples, medians and process peak RSS. Each run
uses one unreported warmup followed by measured trials. Inputs and ecCodes/
filesystem caches are warm; measurements were run sequentially without
parallel builds/tests. This is not a cold-disk benchmark.

For the initial `bench_stored` measurements, the file phase includes NPY
serialization, flush/sync, array hashes and source revalidation before manifest
publication. Cleanup and final destruction
of result buffers are outside the timer. Phase medians need not sum exactly
to the median total. RSS is the whole benchmark process high-water mark, not
per-stage allocation or a production RPC memory bound.
The GFS/EC results below precede Stage 7's common writer optimization and
Stage 8's EC record-storage change; their current full-workflow timings/RSS
have not been remeasured.

## Inputs

| Input | Stored File | SHA-256 |
| --- | --- | --- |
| GFS | `.cache/gfs/2026-11-07/18Z/0p25/fd5bf3073c2648e2cf2c.grib2` | `fc34aca8ce2f75b46c3719722d02f629fa8459f6279070f99dddb9e41032c991` |
| EC | `.cache/ecmwf/2026-11-07/12Z/ifs-basic-v2-2026110712-f030.grib2` | `d7fd52af64d2ccb7395a586bafb497c3e701a40353e7bbe41498601bf953a38c` |

GFS: 968947 input bytes, 6241 selected cells, 11 pressure levels, five
measured trials per version. EC: 28257965 bytes, 721 x 1440 native grid,
bbox `[110,120,40,30]`, 1681 selected cells, nine pressure levels, three
measured trials per version.

## Observed Results

All times are median milliseconds. These are before/after Rust comparisons,
not Rust-versus-Python speed ratios.

| Phase | GFS Before | GFS After | EC Before | EC After |
| --- | ---: | ---: | ---: | ---: |
| Source hash | 0.564 | 0.536 | 15.065 | 15.187 |
| GRIB decode/validation | 68.611 | 67.558 | 1179.446 | 1131.965 |
| EC proxy grid adaptation | n/a | n/a | 0.162 | 0.168 |
| Canonical raw tensor packing | 75.138 | 1.170 | 16.725 | 0.213 |
| Cloud derivation | 24.314 | 24.344 | 4.905 | 4.895 |
| File output/verification | 52.770 | 52.989 | 49.035 | 48.963 |
| Total | **221.556** | **146.878** | **1266.272** | **1201.466** |
| Process peak RSS, bytes | 33505280 | 32735232 | 79806464 | 68501504 |

GFS total elapsed time decreased about 33.7% (1.51x throughput for this
particular sequential workload). Its packing phase improved about 64x;
that is not a 64x application speedup. EC total decreased about 5.1%,
with an observed 14.2% lower process peak RSS. The small sample sizes and
single-machine warm-cache conditions do not establish general speedups or
memory guarantees.

## Implemented Optimizations

1. Prebound canonical field views in `native/src/derivation.rs`. Previously,
   every cell/level/channel constructed string keys and searched the field
   map. Keys and aliases are now resolved once, then cell packing reads
   borrowed slices. The first-finite alias policy, final nonfinite fallback,
   missing fields, explicit zero, aggregate temporal provenance and
   boundary-sentinel semantics are unchanged.
2. A Cartesian-grid proof in `engine/src/ecmwf.rs`. Normal row-major scans
   are checked directly against unique row latitudes and first-row
   longitudes, avoiding a global coordinate-pair set. Unusual scan orders
   retain the original exact set-based validation. Duplicate/missing cells,
   nonfinite coordinates, cross-message grid comparisons and bitmap/value
   checks are not skipped.

No thread-pool or arithmetic changes were needed for these gains. The cloud
calculation phase remains effectively unchanged, which also identifies where
the measured improvement came from.

Correctness checks include all 720 permutations of a six-cell EC grid
(including alternate declared dimensions and duplicate-cell rejection),
explicit alias/NaN/infinity/sentinel regressions and the existing randomized
differential tests. Optimized standalone outputs for the pinned GFS and EC
inputs matched the reference coordinates, columns and profiles exactly,
including NaNs. Standalone proof runs used an empty `PATH` and an unavailable
Python path.

## Reproduce

From the repository root:

```bash
cargo build --manifest-path engine/Cargo.toml --release --locked --example bench_stored
engine/target/release/examples/bench_stored gfs \
  "$PWD/.cache/gfs/2026-11-07/18Z/0p25/fd5bf3073c2648e2cf2c.grib2" 5
engine/target/release/examples/bench_stored ec \
  "$PWD/.cache/ecmwf/2026-11-07/12Z/ifs-basic-v2-2026110712-f030.grib2" \
  3 110 120 40 30
```

The named stored inputs are local evidence, not bundled benchmark fixtures.
If unavailable, use equivalent pinned inputs and record their reported hashes.
Do not compare unlike domains, pressure sets, build profiles or cache states.
The harness accepts 1..20 trials, rejects existing output collisions and
removes only its owned temporary output directory after each trial.

## Remaining Work

- EC remains dominated by GRIB decoding and full-message validation. Profile
  individual ecCodes value/coordinate calls before introducing geometry reuse;
  a metadata shortcut must not weaken scan-order or out-of-bbox missing-value
  validation.
- GFS file output/verification took roughly 36% of the earlier measured total.
  Stage 7 profiles and optimizes the common writer on expanded CAMS below;
  GFS/EC output byte parity passed, but their new latency/RSS remain unmeasured.
  Preserve bounded memory, SHA-256 and manifest-last publication in further work.
- Cell-level parallel cloud derivation may help larger grids, but this
  benchmark does not establish its net benefit after pool startup, concurrent
  jobs and memory usage. Measure those costs and bitwise/output-order
  correctness before enabling it.
- Full forecast performance is unknown until the remaining CAMS orchestration, astronomy, time
  interpolation, diagnostics and orchestration run together in Rust.
  Acceptance still needs representative point/regional/national workloads,
  cold and warm runs, startup/IPC latency, P50/P95, CPU/peak memory and
  Windows/Linux measurements.
- Existing numerically tied event-window diagnostic differences remain a
  separate migration gate. These packing/coordinate optimizations do not
  resolve that contract.

## Compact CAMS: 2026-11-09

The production compact profile now has a Rust implementation distinct from
the expanded spectral diagnostics. A real 61311-cell input was used:
`.cache/cams/2026-11-06/12Z/china/cams-optics-v2-2026-11-06-1200-048-0af35cdc1e57.grib`,
2208480 bytes, SHA-256
`ff349b3a7fc55131c78e8c74cac6ee5021bf66a54deb554089251d662f8f4b2e`.

In-memory measurements use one warmup, seven measured trials per backend,
the existing Python/NumPy implementation and the release native C ABI,
on the same local macOS/aarch64 host. Source decoding, startup, IPC, file
output and memory/RSS measurement are outside this comparison. Grid mapping
targets bbox `[80,135,55,10]` at 0.25 degrees: 40001 fully covered cells.
The scale-height fit processes all 61311 source columns.

| Component | Python/NumPy Median ms | Rust Native Median ms | Observed Ratio |
| --- | ---: | ---: | ---: |
| Twelve-channel spatial mapping | 18.761 | 4.938 | 3.80x |
| Effective scale-height fit | 7.438 | 3.129 | 2.38x |

Mapping output and coverage matched exactly. The scale-height maximum
absolute error was zero on this input; randomized/threshold tests allow
3e-12 absolute/relative error for platform math. The mapping implementation
builds one bounded integer cell-index matrix and shares geometry across the
twelve channels, rather than repeatedly materializing floating-point
source matrices. No relaxed math, new thread pool or coverage shortcut was
used. These numbers are component comparisons, not full application speedups
or before/after comparisons of two Rust versions.

A separate standalone Rust benchmark covers both source files, decode,
target-coordinate file validation, spatial mapping, float32 temporal blend,
NPY sync/hash output and source/target revalidation. It uses lead 48 above
and lead 51 (same bytes; SHA-256
`4ee41dd76e81746e8c298094e8cebacce4554dc10c12183e25080fef0f2680af`),
the pinned GFS-derived 6241-cell bundle, and target `2026-11-08T13:00:00Z`.
Five warm trials took `[136.569,136.688,136.729,138.072,138.143]` ms,
median **136.729 ms**. The child process had empty `PATH`/`PYTHONPATH` and
an unavailable Python path. This benchmark has no Python comparator and
excludes process startup, IPC, output cleanup and full forecast execution.
GRIB still uses ecCodes C. It does not establish cold-cache, tail-latency,
CPU/RSS or deployment performance.

Reproduce the component comparison:

```bash
bash scripts/build_native_variants.sh
.venv/bin/python scripts/bench_cams_migration.py \
  .cache/cams/2026-11-06/12Z/china/cams-optics-v2-2026-11-06-1200-048-0af35cdc1e57.grib \
  --iterations 7
```

For the standalone comparison, first obtain a GFS model bundle with
`derive_gfs_to_files`, then run:

```bash
cargo build --manifest-path engine/Cargo.toml --release --locked --example bench_cams
engine/target/release/examples/bench_cams \
  /absolute/path/lead048.grib /absolute/path/lead051.grib \
  /absolute/path/model/manifest.json 2026-11-08T13:00:00Z 5
```

Further optimization should profile per-message ecCodes decoding, source/
coordinate hashing and NPY sync separately. The 4.94 ms mapping microbenchmark
is not evidence that mapping dominates the 136.73 ms standalone workflow.
Preserve complete grid/time/bitmap validation before considering geometry
reuse. Persistent cache policy and concurrent-job memory costs remain open.

## Expanded CAMS And Shared Writer: 2026-11-09

No actual expanded 31-field CAMS weather file was available locally. This
benchmark instead generates an ecCodes GRIB with the complete expanded
catalog, deterministic spatial variation and 24-bit `grid_simple` packing:
61311 cells (191 x 321), 5707472 bytes, SHA-256
`a2745d4ab560686760365e3e92ce19a2911dc4c57208dff34915e1e143b41755`.
It is a synthetic codec/workload test, not meteorological validation.

The in-memory comparison selects the nearest point from that grid, evaluates
five AOD wavelengths, three profile integrals, Angstrom exponent, direct
transmission, approximate HG phase and middle-layer diagnostics. Seven warm
trials gave median **0.417 ms Python/NumPy versus 0.217 ms Rust C ABI**
(1.92x for this component including bridge overhead). Maximum absolute
diagnostic difference was zero on this fixture; randomized tests permit
3e-12 absolute/relative error. This excludes decoding, IPC and file output.

The standalone benchmark hashes the input, decodes all 31 messages,
evaluates one point and writes/verifies the full float64 bundle. Profiling
the serial writer identified file sync as the largest output cost. The
implemented change uses a single 64 KiB serialization buffer and at most
two concurrent file-sync workers, overlapping sync with subsequent writes
and hashes. All workers must complete successfully before source revalidation
and manifest publication. No checksum, source check or sync is removed.

Before/after release runs were sequential, with no parallel builds/tests,
one unreported warmup and seven measured trials per version. Child processes
had empty `PATH`/`PYTHONPATH` and `FIRESKY_PYTHON=/no-python`; ecCodes C remains
required. The latest repeated pair gave these median milliseconds:

| Phase | Serial Sync / 8 KiB | Bounded Sync / 64 KiB |
| --- | ---: | ---: |
| Source hash | 3.208 | 3.125 |
| GRIB decode/validation | 51.661 | 52.058 |
| One-point diagnostics | 0.167 | 0.160 |
| Serialization, including I/O stalls | 20.648 | 42.434 |
| Caller flush/sync wait | 49.847 | 4.664 |
| Array hashes | 9.219 | 13.260 |
| Source check and manifest | 8.253 | 8.179 |
| Complete output/verification | **88.820** | **68.646** |
| Total stored-input workflow | **145.013** | **124.311** |

Total decreased about **14.3%**, output/verification about **22.7%** in this
pair. Before totals were `[145.993,145.013,146.254,144.160,142.527,141.509,146.344]`;
after totals were `[124.107,125.247,123.067,124.311,122.748,125.298,125.950]` ms.
An earlier seven-trial serial/final pair had medians 141.108/125.239 ms
(11.2% lower total). An intermediate concurrent-sync version retaining the
8 KiB buffer measured 133.009 ms. These small warm-cache runs indicate local
benefit, not a universal speedup or evidence of an optimal worker count.

Sync work did not disappear: the final workers' summed wall-time median was
55.647 ms, **not additive** to caller phases. Overlap also increased write
and hash wall times through filesystem contention; only the complete critical
path establishes the net improvement. These timings do not measure cold I/O,
CPU, new peak RSS, concurrent-job load, startup, IPC or full forecasts.

Serial and optimized releases produced byte-identical manifests and NPY
files across six workflows: expanded synthetic decode, pinned real GFS and
EC-basic derivation, and pinned compact CAMS decode/map/time blend. New
writer tests verify the two-worker bound, injected sync errors, worker panic,
persistent failure after a worker was already joined, and joining all workers
before cleanup. These are host fault-path tests, not power-loss testing.

Reproduce the current implementation:

```bash
bash scripts/build_native_variants.sh
cargo build --manifest-path engine/Cargo.toml --release --locked --example bench_spectral
.venv/bin/python scripts/bench_spectral_migration.py --synthetic --iterations 7
# A real expanded input can be supplied instead:
.venv/bin/python scripts/bench_spectral_migration.py --grib /absolute/path/expanded.grib --iterations 7
```

For comparisons across writer versions, build/save the example executable
before editing and pass it with `--rust-benchmark /absolute/path/bench_spectral`.
The harness reports every sample, phase medians, source hash and build
profile. It excludes output cleanup and uses only its owned temporary
directories. Expanded diagnostic output remains `score_consumed=false`;
neither the phase approximation nor this benchmark changes forecast scoring.

## EC Offline Full Catalog: 2026-11-09

No actual complete IFS model-level weather bundle was available locally.
The reproducible synthetic ecCodes fixture contains the full L137 contract:
54 pressure fields, 411 model cloud fields and four surface/diagnostic fields,
6161 cells (61 x 101), 24-bit `grid_simple` packing, 9214195 bytes, SHA-256
`1e5f13459c00daf30eea4f6cc0315920b2b75592f69fe706f67946f96510e29f`.
It is a codec/adapter workload, not real-weather or forecast validation.

Five warm trials per version, plus one unreported warmup, gave:

| Measurement | Eager Raw/Converted Copies | On-Demand Converted Copies |
| --- | ---: | ---: |
| Source hash, median ms | 4.858 | 4.850 |
| Complete Rust decode/validation, median ms | 329.708 | 333.373 |
| Tensor output/verification, median ms | 163.889 | 163.321 |
| Total standalone workflow, median ms | 498.553 | 500.770 |
| Process peak RSS, bytes | 57229312 | 43630592 |

The change reduced observed peak RSS by **23.8%**. Total latency increased
about 0.4% and decode latency about 1.1% in this pair; there is **no measured
speedup claim** for this optimization. Eliminating redundant retained copies
is the benefit. Unchanged fields now borrow raw values, unit conversion is
selected once per message, and RH allocates a private converted buffer only
when clipping is needed. Original raw values and signed zero are unchanged.
All validation, source/file hashes, syncs and manifest-last publication remain.

Before total samples were `[498.553,497.682,574.511,505.861,497.116]`;
after samples were `[500.770,497.227,502.956,505.271,500.633]` ms. The baseline
contains one longer decode trial. These small sequential samples on one
macOS/aarch64 machine do not establish tail latency, concurrent-job memory
bounds, cold-cache results or a general memory reduction percentage. Peak
RSS is a whole-process high-water mark, including warmup, decode and output.

For the same final run, Python's complete decode/validation median was
**751.003 ms**, versus **333.373 ms** for Rust's decode phase (2.25x for this
adapter workload). Both read the same warm input; Python includes its normal
metadata-probe and record-read passes. Compare decode with decode, not with
Rust's hash/output total. This is not a full application speedup. Startup,
IPC and output cleanup are excluded; the child process has Python unavailable
but still uses ecCodes C.
Python materializes its dataclass matrices during decode; Rust retains
validated records and streams those matrices at output. Their internal
allocation/layout work differs, so the ratio is not an equal-layout kernel
benchmark.

The comparison verifies all 489 tensor/raw arrays bitwise, including
mutability and scalar metadata. The eager/optimized Rust releases also emit
byte-identical full manifests/NPY arrays. Pinned real EC-basic decode and
derived output retain byte parity across the same storage change.

Reproduce:

```bash
cargo build --manifest-path engine/Cargo.toml --release --locked --bins --example bench_ec_full
.venv/bin/python scripts/bench_ec_full_migration.py --synthetic --iterations 5
# Or provide a complete L137 offline bundle:
.venv/bin/python scripts/bench_ec_full_migration.py --grib /absolute/path/offline-l137.grib --iterations 5
```

`--rust-benchmark /absolute/path/bench_ec_full` selects a separately saved
before/after example executable. The harness reports individual samples,
source identity, decode/output subphases, build profile and process RSS.
The largest remaining phase is decoding; per-call ecCodes profiling is
needed before changing geometry handling. Do not reuse coordinates on
metadata resemblance alone or omit full-message bitmap/value checks.

## Astronomy And Time: 2026-11-09

Warm component measurements on the same macOS/aarch64 host, Python 3.15.2,
release CPU native library and release engine. No weather files are decoded:
the fixed point is 31.23 N / 121.47 E, 50 m MSL, Asia/Shanghai, 2026-11-09.
Each timed batch contains 10,000 solar snapshots or ten valid typical-deck
windows (80 km edge, 6000 m base, dusk). Seven trials follow an untimed warmup.
Builds and test suites were finished before timing; trials were sequential.

The initial bridge measured 39.909 ms per snapshot batch and **46.603 ms**
per window batch: starting one debug-engine subprocess per window overwhelmed
the numerical work and was much slower than Python (4.465 ms for that batch).
This was an intermediate bridge result, not a Rust-kernel regression.

Implemented optimization:

- Keep position, crossings, shadow speed and complete UTC window calculations
  in the existing native library via additive C ABI symbols.
- Both the standalone engine and C ABI use the same Rust UTC core; no
  duplicated Python numerical implementation or persistent subprocess pool.
- Cross epochs as i64 microseconds. Avoid converting a Python datetime to ISO
  text and parsing it again for every native position call.
- Preserve every crossing iteration, geometric threshold and output field.
  Named-zone/dataclass assembly remains in Python for this bridge benchmark.

Latest paired seven-trial medians:

| Timed caller workflow | Python ms | Rust C ABI bridge ms | Ratio |
| --- | ---: | ---: | ---: |
| 10,000 public solar snapshot calls, including dataclasses/local times | 92.355 | 30.258 | 3.05x |
| Ten complete typical glow-window calls, including dataclasses/local times | 4.504 | 0.347 | 12.98x |

A preceding optimized run measured 92.148/30.735 ms for snapshots and
4.614/0.354 ms for windows (3.00x/13.04x). Maximum position difference in the
10,000-sample fixture was 7.11e-14 degrees; the complete window dataclass
matched exactly. Snapshot bridge time fell about 23% from its initial
39.909 ms measurement after removing the datetime text round-trip. These
observations do not establish full forecast speedup, CPU utilization or RSS.

The separate `bench_astronomy` release executable times only in-process Rust
UTC position/window cores, excluding process startup, JSON, named-zone
conversion and payload/dataclass assembly. It executes successfully with
empty PATH/PYTHONPATH and `FIRESKY_PYTHON=/nonexistent/python`.
Two seven-trial runs measured respectively:

| Rust core workload | First median ms | Second median ms |
| --- | ---: | ---: |
| 10,000 positions | 3.189 | 2.175 |
| Ten typical windows | 0.336 | 0.255 |

Both runs retain identical checksums (positions 2502707.5259345034;
windows 3072.089962898599). These very short core timings vary materially
between runs and are not directly comparable to the richer Python caller
workflows above. They are not a cold/P95/concurrency/RSS baseline.

Reproduce:

```bash
bash scripts/build_native_variants.sh
cargo build --manifest-path engine/Cargo.toml --release --locked --bins --example bench_astronomy
FIRESKY_RUST_ENGINE_BIN="$PWD/engine/target/release/firesky-engine" \
  .venv/bin/python scripts/bench_astronomy_migration.py --trials 7
env PATH='' PYTHONPATH='' FIRESKY_PYTHON=/nonexistent/python \
  engine/target/release/examples/bench_astronomy
```

The script verifies numerical/window parity before timing and prints every
trial. Event/time-parser Python bridges still start a subprocess per operation;
that overhead is not claimed optimized here. At Stage 9, regional solar
timelines, complete stored-input forecasts and representative cold/warm
end-to-end performance acceptance remained unfinished.

## Regional Solar Preparation: 2026-11-09

Warm component measurements on the same macOS/aarch64 host, Python 3.15.2,
release CPU native library and release Rust profiling example. This fixture
uses the application's complete China grid: **36,685 points**, 18..54 N,
73..136 E at 0.25 degrees, zero model-grid placeholder elevation,
Asia/Shanghai, 2026-11-09 dusk. Twenty solar slots per point, UTC cycle at
midnight, model hours -24..48 at three-hour spacing. Seven measured trials
follow an untimed warmup, sequentially with no simultaneous builds/tests.
No weather file decoding, CAMS attachment, scoring or diagnostic export is
included.

The first uncached serial implementation measured 221.985 ms Python/NumPy
versus 215.817 ms through the Rust C ABI bridge, only **1.03x**. Its separate
in-process Rust core measured 175.490 ms. Merely replacing vectorized NumPy
with scalar Rust was not a useful improvement.

Implemented optimizations:

- Share exact whole-second solar ephemerides across points/slots rather than
  recalculate them for all 733,700 samples. Bound the dense table at 172,800
  seconds, and bypass it when fewer than two samples reuse each table slot.
- Parallelize ordered event preparation, shared ephemeris construction, solar
  positions and sample interpolation via the existing Rayon pool for batches
  of at least 1024 points. Preserve input/error ordering and all arithmetic.
- Stream row arrays directly into the existing tensor writer for the file
  API, rather than build additional fully flattened output arrays.

Same-executable, seven-trial pure-Rust component medians:

| Core execution | Median ms |
| --- | ---: |
| Serial, no shared ephemeris | 180.007 |
| Serial, shared ephemeris | 101.137 |
| Parallel, no shared ephemeris | 23.262 |
| Parallel, shared ephemeris (default) | 15.393 |

The cache alone improves the core by **1.78x**; cache plus parallelism improves
it by **11.69x** against the same executable's serial mode. All modes retain
anchor checksum 65820893149426.578125. A Rust test also compares every output
bit for both phenomena, including model samples, across all four execution
choices. Checksum agreement alone is not used as an equivalence proof.

The optimized public Python caller workflow measured **211.982 ms Python
versus 50.428 ms Rust**, **4.20x**. This includes Python coordinate/timezone
transport preparation, output allocation and C ABI copies, unlike the
15.393 ms Rust-core figure. On this fixture all float32 sample values and
evaluation timestamps match the reference exactly. Other differential cases
retain explicit small tolerances, especially ill-conditioned polar azimuth.
These are component/warm latency results, not full forecast speedup,
cold-start, P95, concurrency, CPU-efficiency or peak-memory acceptance.
A preceding optimized run measured 211.727/51.359 ms for the public workflow
and 181.524/15.154 ms for the serial/default core. Final sample assembly writes
directly into the result vector and reduces coverage extrema without a second
whole-batch intermediate sample array; this reduces avoidable allocation,
but no total-RSS reduction is claimed without measurement.

Reproduce:

```bash
bash scripts/build_native_variants.sh
cargo build --manifest-path engine/Cargo.toml --release --locked --example bench_timeline
.venv/bin/python scripts/bench_timeline_migration.py --trials 7
env PATH='' PYTHONPATH='' FIRESKY_PYTHON=/nonexistent/python \
  engine/target/release/examples/bench_timeline
```

The bridge script uses the actual production point generator, verifies all
returned arrays and reports every timing sample. The Rust example constructs
the same rectangle directly and never launches Python; it excludes JSON/file
I/O. Thread counts use the existing Rayon configuration. Shared ephemeris
memory is bounded, but total process RSS and complete forecast performance
still require separate measurement.

## Stored Regional Assessment: 2026-11-09

Stage 11 joins stored GFS/EC-basic raw decoding, complete pressure-profile
derivation, model-grid observer terrain, solar/model-time interpolation,
strict compact CAMS binding and the existing CPU ray tracer in the standalone
Rust process. This is **not** a complete public forecast or a zero-C program:
ecCodes remains a native C dependency.

Before the derivation optimization, warm local-filesystem measurements on the same macOS/aarch64 host,
Python 3.15.2 and release engine, one warmup then three sequential paired
trials, with no concurrent builds/tests:

| Frozen workload | Python median ms | Rust median ms | Python / Rust |
| --- | ---: | ---: | ---: |
| GFS, 24,843 cells, 3 frames, 1 observer | 733.825 | 1165.815 | 0.63x |
| EC-basic, 841 cropped cells, 3 frames, 1 observer | 5800.783 | 3969.262 | 1.46x |

For GFS, Python trials were 732.102/743.683/733.825 ms and Rust
1172.207/1165.815/1163.130 ms. For EC-basic they were
5800.783/5776.265/5867.140 ms and 3969.262/3951.864/3969.867 ms.
Both fixtures use Shanghai 31.23 N, 121.47 E with model-derived elevation,
dusk and 200 km rays. The GFS cycle is 2026-11-07 18Z, hours 14/15/16,
domain [99.25,141.25,48.5,12]; the EC cycle is 2026-11-07 12Z,
hours 42/45/48, cropped domain [118,125,35,28]. Dates are respectively
November 8/9. Compact CAMS comes from the stored November 6 12Z China run,
hours 42/45/48 or 66/69/72.

**The I/O contracts differ.** Python returns the reference in-memory
assessment and profiles; Rust also hashes/revalidates raw files and durably
writes all per-frame profiles plus eleven parent arrays with checksums and
manifest-last publication. Python timing includes the existing native CPU
ray tracer too, not a Python reimplementation of that core. The Rust timer
includes process startup and JSON parsing but excludes the Python verifier's
subsequent file loading/comparison. These figures do not establish equal-I/O
language speedups. In particular, this GFS path is currently **slower**, and
the EC result cannot be generalized to a complete forecast or national batch.

On both real fixtures, all 82 assessment fields, including inactive slots,
matched exactly (maximum absolute difference 0). Samples/timestamps and
36-column tensors are also checked before retaining timings. The reproducible
script additionally validates every C,L,24 profile child and its manifest
identity. Comparisons otherwise use rtol 4e-5 / atol 5e-4 for assessments and
rtol 4e-6 / atol 5e-5 for columns/profiles with identical NaN positions;
exactness on these two fixtures is not a general bitwise-equivalence claim.

Initial Rust phase medians were approximately 936 ms GFS / 3737 ms EC for
model loading, profiles and solar preparation, 117/111 ms for CAMS assembly,
and 9.5/7.4 ms for native scoring. Parent writing and final source verification
are outside these phase counters but inside total Rust time. Subsequent
instrumentation splits raw decode/source hash, packing/derivation and profile
writing/verification; investigate those phases before changing the numerical
scorer, which is not the bottleneck in these one-observer workloads.

That instrumentation measured **311.608 ms** for GFS packing/derivation,
410.453 ms raw decoding/source hashing and 209.555 ms profile writing/
verification. EC's corresponding phases were 8.253/3611.326/115.104 ms.
The GFS per-cell derivation was a material, independently parallelizable
hotspot; the much smaller EC crop is dominated by global raw decoding.

Implemented a bounded-granularity parallel derivation using the existing
Rayon pool, after the shared spatial-support calculation. At least 1024 cells
enable parallel work, with a minimum 128-cell task granularity; smaller grids
remain serial. Each worker writes disjoint slices of the one final column/
profile allocation, and collected errors retain serial cell ordering.
`derive_with_parallelism` preserves an explicit serial reference/profiling
mode; neither numerical formulae nor the C ABI contract change.

Same release executable, same first GFS frame, five warm trials per mode:

| Pure-Rust `derive` component | Median ms |
| --- | ---: |
| Explicit serial | 98.897 |
| Default parallel | 28.954 |

This is **3.42x** for derivation alone; decoding, packing, output checks and
I/O are excluded from that component figure. Full stored-tensor example
medians were 302.704/233.988 ms. Its whole-process peak RSS was respectively
79.5/83.0 MB, not an RSS improvement and not the full assessment process.
One- and four-thread Rust tests compare every column/profile bit with the
serial path, including NaN bits, 1023/1024/4096-cell threshold cases,
single/gapped/full pressure levels and mixed temporal semantics.

The final three-frame GFS workflow measured **729.769 ms Python /
959.290 ms Rust** (Python trials 727.072/737.828/729.769; Rust
967.768/959.290/953.538). Packing/derivation fell to 101.845 ms, while raw
decode remained 406.889 ms and profile writing 208.805 ms. Against the
instrumented 1174.373 ms pre-optimization Rust total, elapsed time fell
about **18%**. All real assessment fields still match exactly and full
profile comparisons pass. The final Rust workflow still trails the Python
in-memory workflow; the unequal I/O caveat continues to apply.
The final executable also reran EC with one warmup and one measured trial:
5462.335 ms Python / 3960.497 ms Rust, all assessment fields exact and all
pressure profiles within tolerance. Its 841-cell crop stays on the serial
branch; this single repeat is a final-build confirmation, not a new
multi-trial performance baseline.

Allocation/I/O controls implemented for the new path:

- Stream each time's complete pressure profiles into a separately bounded
  child bundle, rather than retain a second stacked profile tensor or omit
  temporal chunks to fit one parent file.
- Reuse mapped CAMS fields by exact valid time across model and anchor
  brackets, preserving full-domain coverage checks.
- Expand trimmed assessment rows backwards into the final allocation, avoiding
  a second N,20,82 output buffer. Exhaustive span tests preserve point ordering
  and canonical zero slots.
- Store per-point source brackets numerically rather than repeating large
  strings in a bounded manifest.

The allocation/I/O controls are bounded-memory/transport design choices, not
separately measured RSS or latency improvements. The derivation improvement
above is measured independently. Cold/P95,
multi-observer/national scoring, concurrency and total peak-memory acceptance
remain outstanding.

Reproduce with the existing pinned cache files (no network/downloads):

```bash
cargo build --manifest-path engine/Cargo.toml --release --locked --bins
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model gfs --trials 3
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model ecmwf-basic --trials 3
cargo build --manifest-path engine/Cargo.toml --release --locked --example bench_stored
engine/target/release/examples/bench_stored gfs .cache/gfs/2026-11-07/18Z/0p25/9df16f80b96890d0feef.grib2 5 --serial-derivation
engine/target/release/examples/bench_stored gfs .cache/gfs/2026-11-07/18Z/0p25/9df16f80b96890d0feef.grib2 5
```

The harness uses pytest and Python only for reference/verification. Each timed
engine child has empty PATH/PYTHONPATH and a nonexistent `FIRESKY_PYTHON`; the
engine itself does not launch Python. `--request /absolute/request.json`
accepts another frozen stored-input case, resolving relative raw paths against
that file. Every run prints all timing samples and consumed source SHA-256
identities. No production default, download/cache lifecycle or desktop/HTTP
runtime is switched by this benchmark.

## Complete Empirical Quality: 2026-11-09

Stage 12 adds the complete empirical observer-field event composer, all
instantaneous fields and their connection to the stored raw-input chain.
This is not `build_national_frame_payloads`, public forecast JSON or
production Python retirement. Existing ecCodes remains a C dependency.

### Measured Event-Core Optimization

Release macOS/aarch64, deterministic synthetic 36,685 observers x 20 samples,
regional duration response and local-column diagnostics enabled. This
benchmark excludes weather decoding, ray tracing, JSON and disk I/O. It warms
serial/parallel execution and then runs five sequential trials per mode:

| Implementation | Trials, ms | Median, ms |
| --- | --- | --- |
| Original serial composer | 290.114208, 289.868750, 291.333042, 289.169917, 289.446125 | 289.868750 |
| Cached reductions, serial | 246.135000, 244.993375, 245.256292, 243.677292, 245.514750 | 245.256292 |
| Cached reductions, ordered Rayon | 56.177583, 55.741458, 55.304959, 58.078000, 60.521333 | 56.177583 |

Repeated full/active/max reductions now share cached results, avoiding
temporary per-field vectors while retaining the original summation order.
This reduced serial latency by about 15%. Independent event composition uses
Rayon at >=1024 observers, at least 64 observers per task, final disjoint
output slots and point-ordered errors. Parallel execution is about 4.37x as
fast as the optimized serial mode and 5.16x as fast as the original serial mode.
These are same-language component comparisons, not a Python speedup claim.

All three runs had checksum `16762347.03544205`. More importantly, the
benchmark compares every numeric field and flag of every serial/parallel
observer before timing, bit for bit; unit tests also compare cached
reductions against the previous summation and preserve first-error ordering.
A checksum alone would not establish this equivalence. These timings use
the final implementation, including ignored local-input handling when local
diagnostics are disabled. An earlier optimized build measured 242.424 ms
serial / 54.823 ms parallel; it is not substituted for this final rerun.

The stored-chain adapter projects each observer's existing float32 assessment
onto a small stack array instead of allocating a second complete N,20,18
float64 input tensor (about 106 MB at this synthetic point count). Instant
records stream directly to the bounded tensor writer instead of retaining a
second N,20,19 float64 tensor. These are avoided allocations, not a measured
process RSS reduction. Full regional-chain peak memory remains unmeasured.

### Stored GFS Quality

Same pinned GFS/CAMS files and one Shanghai observer as Stage 11, three model
frames x 24,843 cells, with a warmup and three sequential measured trials.
The final executable includes both the core optimization and exact binary64
JSON parsing (`serde_json/float_roundtrip`); boundary tests found that the
default fast parser could move a value just below the visibility floor onto
the threshold. The fix preserves inputs, not a looser scoring tolerance.

| Workflow | Trials, ms | Median, ms |
| --- | --- | --- |
| Python reference, in-memory result | 731.398667, 733.939917, 733.159959 | 733.159959 |
| Independent Rust, durable complete bundles | 989.134042, 983.664166, 984.426459 | 984.426459 |

Rust phase samples are approximately 406..419 ms model decode/source hashing,
93..110 ms packing/derivation, 212..216 ms profile writing/verification,
117..121 ms CAMS assembly and 9.1..9.5 ms native assessment. Complete
single-observer event composition costs 0.014..0.036 ms; instant records and
quality-array output cost another 13.8..15.5 ms (includes shared pending
writer work, excludes final parent publication). Some reported aggregate
phase timings include subphases and must not be added twice.

The score-core improvement is intentionally not presented as an end-to-end
gain: this one-observer workflow remains dominated by raw data and complete
durable profile output, and remains slower than the unequal-I/O Python path.
Every trial verifies all 82 assessment fields (maximum error zero), all 75
event components including text/nulls, all 19 instantaneous fields and all
columns/profile children. This is a fixed local fixture, not national public
forecast acceptance.

### Stored EC-Basic Quality

Same pinned EC-basic/CAMS files and Shanghai observer as Stage 11, three
global basic input frames cropped to 841 cells. One warmup, three sequential
trials, same durable-output versus in-memory-result caveat:

| Workflow | Trials, ms | Median, ms |
| --- | --- | --- |
| Python reference | 5475.860667, 5460.361167, 5457.373583 | 5460.361167 |
| Independent Rust, durable complete bundles | 3986.192708, 3999.106833, 4061.897500 | 3999.106833 |

All 82 assessment fields were exact; all 75 event components, 19 instantaneous
fields and complete profile children passed the same comparisons. Decode
and source hashing dominate at 3621..3697 ms; this small crop stays below the
parallel-derivation threshold. Event composition costs 0.021..0.024 ms.
The EC result retains the explicit TCC/RH basic proxy, not a full hybrid
model-level scorer or a complete public forecast.

After the final disabled-local-diagnostic input fix and both complete
567-test regression configurations, one warmup/one measured repeat per model
confirmed the final executable: GFS 730.749 ms Python / 977.909 ms Rust;
EC-basic 5469.438 ms Python / 3990.973 ms Rust. Every component, instant field,
column and profile comparison passed again, with exact assessments. These
single repeats confirm the final build; they do not replace the three-trial
baseline above.

Reproduce without downloads:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --release --bins --example bench_quality
env PATH= PYTHONPATH= FIRESKY_PYTHON=/no/python engine/target/release/examples/bench_quality
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model gfs --trials 3 --quality
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model ecmwf-basic --trials 3 --quality
```

The benchmark uses Python/pytest for reference checks only; every engine
child has empty PATH/PYTHONPATH and a nonexistent Python path. No child starts
Python. The NumPy/BLAS near-tied-window diagnostic selection gap remains
explicitly unproven in output metadata. Full public payloads, cold/P95,
national multi-observer raw workflows, concurrent requests and total RSS
remain separate acceptance gates.

## Event Diagnostic Composition: 2026-11-09

Stage 13 adds the complete extra `_fire_cloud_quality` components and
independent forecast-characteristic rules. The stored path produces all
103 ordinary event component keys (105 with scalar ceilings in the debug
scalar path), not just final indices. Complete public frame/point-core
assembly remains unported; characteristic operations are not yet integrated
into that full response. Existing ecCodes is still a C dependency.

### Additional Diagnostic Core

Release macOS/aarch64, deterministic 36,685 observers x 20 native ledger
rows (82 float32 fields), all twenty added diagnostic columns. Weather
decoding, ray tracing, JSON and disk I/O are excluded. Warm serial/parallel
verification precedes five sequential trials per execution mode:

| Execution | Trials, ms | Median, ms |
| --- | --- | --- |
| Serial | 54.365833, 54.185375, 54.938500, 55.224542, 54.716583 | 54.716583 |
| Ordered Rayon | 13.034792, 13.448792, 12.834541, 13.042458, 13.071667 | 13.042458 |

Parallel composition is about 4.20x as fast as the same Rust serial
implementation. Every diagnostic field of every observer is compared bit for
bit before measurement; both modes and all trials produced checksum
`452681.7444514723`. Unit tests additionally compare one-/four-thread
execution and first-invalid-event reporting. The existing Rayon pool is used
at >=1024 points with >=64 points per task and disjoint final output.

The 20-column diagnostic output is about 5.9 MB at this point count, without
another full input ledger copy. No process-RSS reduction is claimed. This
component timing must not be added to the previous component benchmark and
presented as a measured full national forecast.

### Stored Raw Workflow

Same pinned local raw model/CAMS files as Stages 11/12, one Shanghai observer,
one warmup followed by three sequential trials. Rust writes/hashes/syncs all
columns, pressure-profile children, assessment, quality, instant-quality and
extra-diagnostic arrays. Python returns in-memory objects. This remains an
unequal-I/O workflow comparison, not a controlled language benchmark:

| Model / Workflow | Trials, ms | Median, ms |
| --- | --- | --- |
| GFS Python reference | 732.399083, 731.114166, 731.481459 | 731.481459 |
| GFS independent Rust | 956.608541, 982.896250, 990.140042 | 982.896250 |
| EC-basic Python reference | 5480.888083, 5506.798375, 5472.390792 | 5480.888083 |
| EC-basic independent Rust | 3993.285334, 3978.996583, 3989.638042 | 3989.638042 |

GFS uses three frames x 24,843 cells; EC-basic crops three global input
frames to 841 cells and retains its TCC/RH basic proxy. Every trial compares
all 103 event components, all 19 instantaneous fields, every column and
complete profile child; all 82 assessment fields match exactly.

Additional diagnostics plus their file output cost 4.6..5.3 ms for GFS and
4.3..5.1 ms for EC-basic. Those phase measurements include bounded writer
queue/sync work, not just single-observer arithmetic, and exclude final
parent publication. GFS remains dominated by decoding, profile writing and
CAMS assembly; EC-basic remains dominated by global basic-file decoding.
The new diagnostic-core parallelism does not establish an end-to-end gain
for a one-observer case.

After the final naive-midnight parser change and both complete 632-test
regression configurations, one warmup/one measured repeat per model confirmed
the final executable: GFS 731.355 ms Python / 982.017 ms Rust; EC-basic
5464.096 ms Python / 3980.309 ms Rust. All diagnostic/instant/column/profile
comparisons passed again and assessments remained exact. These repeats
confirm the final build, not a new multi-trial performance baseline.

Reproduce from existing frozen files:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --release --bins --example bench_observer_diagnostics
env PATH= PYTHONPATH= FIRESKY_PYTHON=/no/python engine/target/release/examples/bench_observer_diagnostics
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model gfs --trials 3 --diagnostics
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model ecmwf-basic --trials 3 --diagnostics
```

The Python harness is reference/verification only. Timed Rust children
have empty PATH/PYTHONPATH and a nonexistent Python path. Source identities
are printed for every consumed file; no downloads or production cache hits
are involved. Full payload parity, near-tied NumPy/BLAS selection, cold/P95,
national raw scoring, concurrent service workflows, clean-machine packaging
and total peak memory remain open gates.

## Stage 14: Selected Profile Diagnostics

Local macOS/aarch64 release measurements, 2026-11-09. This stage adds the
seven selected observer-column material/geometry diagnostics, not complete
public frame/point-core assembly. Confidence remains metadata only.

### Compute And Allocation Reuse

Synthetic two-frame data: 36,685 observers x 20 samples, 11 levels, 36,864
model cells and 77,856,768 profile-input bytes. Stencils are off-grid;
profiles vary by cell/level/time. Aggregate columns are zero in this
component workload; aggregate-envelope cases are separately covered by
Python differentials and raw-file tests.

One warmup per mode and three sequential trials; every timed result checks
every output bit against serial, not only the dominant class or checksum.
This excludes raw decoding, source validation, ray tracing, event scoring,
JSON and file I/O.

| Implementation | Trials, ms | Median, ms |
| --- | --- | --- |
| Initial serial | 837.667042, 837.511208, 837.307000 | 837.511208 |
| Initial indexed Rayon | 178.648709, 169.929250, 174.369750 | 174.369750 |
| Reused-buffer serial | 775.110750, 773.990208, 773.840625 | 773.990208 |
| Reused-buffer indexed Rayon | 159.199709, 155.087333, 158.799417 | 158.799417 |

The final serial/parallel ratio is about 4.87x. Task-local reuse of sampled
profiles, effective-amount vectors and classifier-input rows reduces the
parallel median by about 8.9%, without a shared mutable cache. Internal
cloud-structure allocations remain unchanged. The output checksum is
819581777.952733993530273 in both versions/modes; unit tests additionally
compare fresh versus reused scratch and one-/four-thread execution bitwise.
No total-process memory or full national forecast speedup is inferred.

### Buffered Reader Bottleneck

The first real GFS run exposed a transport regression: the standard NPY
parser was attached directly to `File`, causing small reads for individual
floats. For three complete profile children, the load-and-verify trials
were 8833.393042, 8745.442167 and 8752.481501 ms, median 8752.481501 ms.

A 64 KiB `BufReader` preserves the same standard parser, float32 values,
SHA-256 checks before/after reading, header/extent checks and final child
revalidation. The same load-and-verify phase becomes 199.003333,
195.707750 and 195.058667 ms, median 195.707750 ms, about 2.2% of the initial
phase time (44.7x faster).
This is a before/after reader optimization within the new stage, not a
speedup over the previous stage, which did not compute these diagnostics.
The initial full Rust median was 9779.100750 ms; the corrected workflow is
1217.592708 ms. All original/new fields are compared in both runs.

### Stored Raw Workflow

Same pinned local raw-model/CAMS identities as Stages 11-13, one Shanghai
observer, one warmup and three sequential trials. The harness includes
Python's selected-profile diagnostics in the timed reference. Rust writes,
hashes and syncs all sixteen parent arrays plus complete per-frame profile
children, then revalidates those children. Python returns in-memory
objects. This is still an unequal-I/O workflow comparison.

| Model / Workflow | Trials, ms | Median, ms |
| --- | --- | --- |
| GFS Python reference | 751.195042, 759.093750, 748.495750 | 751.195042 |
| GFS independent Rust | 1236.617583, 1210.484875, 1217.592708 | 1217.592708 |
| EC-basic Python reference | 5511.014667, 5498.802583, 5494.288750 | 5498.802583 |
| EC-basic independent Rust | 4003.316667, 4006.761209, 4000.282167 | 4003.316667 |

GFS retains three frames x 24,843 cells; EC-basic retains three cropped
frames x 841 cells from global basic inputs. Every measured trial compares
all seven selected-profile fields, 103 event components, 19 instantaneous
fields, all columns and full profile children; all 82 assessment fields
are exact. GFS boundary provenance is average-envelope-not-instantaneous;
EC-basic boundaries are unknown, without invented aggregate evidence.

The GFS profile diagnostic composition/output phase is 195.4..199.4 ms,
of which sampling/classification is only 0.049..0.053 ms. EC-basic takes
7.4..10.3 ms including output; its sampling/classification is 0.024..0.030 ms.
These phases do not include final parent publication/child revalidation.
Both load each of the three frames once, retaining at most two simultaneously.

GFS's durable Rust path remains slower than the in-memory Python reference.
The EC-basic difference remains dominated by global input decoding; it is
not evidence that the selected-profile kernel made the full forecast faster.
Potential later optimizations include bounded selective profile-row access
for sparse observers and measured reuse inside the shared classifier.
They must retain complete source profiles, finite-fallback arithmetic,
checksums and the current bitwise tests. No such further optimization is
claimed here.

Reproduce with existing frozen local inputs:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --release --bin firesky-engine --example bench_profile_diagnostics
env PATH= PYTHONPATH= FIRESKY_PYTHON=/no/python engine/target/release/examples/bench_profile_diagnostics 36685 3
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model gfs --trials 3 --profile-diagnostics
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model ecmwf-basic --trials 3 --profile-diagnostics
```

The Python harness is verification only. Timed Rust children have empty
PATH/PYTHONPATH and a nonexistent Python path. Full payload parity, existing
NumPy/BLAS near-tied-window selection, cold/P95, concurrent workflows,
national raw scoring, total peak memory and Python-free packaging remain
open. No application defaults were switched.

## Stage 15: Frame/Core Assembly And Memory

Date: 2026-11-09, same local macOS/aarch64 checkout. This stage adds complete
compact frames and generated point cores to the frozen-input workflow, not
point-core validation/materialization, production APIs or Python-free packaging.
ecCodes remains a C library used for input decoding.

### Controlled Serialization Comparison

The ignored engine unit benchmark uses 36,685 observers x 20 samples, complete
82-field ledgers and already-computed event/instant/profile diagnostics. Both
modes prepare all twenty compact frames. The timed output is the complete
regional `results` JSON array, buffered into a SHA-256/counting sink.
It excludes source decoding, astronomical scheduling, native scoring, the
frame-envelope serialization, disk output and core generation.

One warmup and three trials run in each independent process. `materialized`
is a controlled Rust allocation baseline which retains every result `Value`
before serialization, not the previous Python implementation or a released
Rust application. `streaming` builds, serializes and releases one observer map
at a time. Both reuse identical event scores and small instantaneous marks.

| Mode | Trials, ms | Median, ms | Process Maximum RSS, bytes |
| --- | --- | --- | --- |
| Materialized Rust baseline | 2242.686000, 2172.075959, 2102.113792 | 2172.075959 | 1757184000 |
| Streaming Rust | 2071.943125, 2147.013084, 2100.878625 | 2100.878625 | 612089856 |

Process maximum RSS falls by about 65.2% in this workload. The timings overlap;
the measured result supports a memory improvement, not a robust general speedup.
The process measurements include common input tensors and retained compact
frames, not only result maps. No whole-application peak-memory claim follows.

All warmup/timed result arrays in both processes have exactly 401751901 bytes
and SHA-256
`afb79fe57ae7849ee7a8e995d3e5ec8abe1ee5ece87c8200c82fab53942ef48b`.
This verifies serialized bytes, not just a numerical checksum.

The retained instant projection is 11739200 bytes (16 bytes per sample), versus
111522400 bytes for another complete 19-field float64 instant tensor.
The existing full instantaneous tensor is still streamed to the durable bundle;
no output fields are removed. The projection avoids retaining a second full
copy solely for frame selection/encoding.

Reproduce, running the two modes sequentially without other benchmark jobs:

```bash
cargo test --manifest-path engine/Cargo.toml --locked --release --lib --no-run
# Use the executable path printed by Cargo; the hash can change after rebuilding.
/usr/bin/time -l env PATH= PYTHONPATH= FIRESKY_PYTHON=/no/python \
  FIRESKY_BENCH_MODE=streaming engine/target/release/deps/firesky_engine-<hash> \
  frames::tests::bench_frame_assembly --ignored --exact --nocapture
/usr/bin/time -l env PATH= PYTHONPATH= FIRESKY_PYTHON=/no/python \
  FIRESKY_BENCH_MODE=materialized engine/target/release/deps/firesky_engine-<hash> \
  frames::tests::bench_frame_assembly --ignored --exact --nocapture
```

`FIRESKY_BENCH_POINTS` and `FIRESKY_BENCH_TRIALS` can select bounded smaller
workloads. The benchmark is ignored in ordinary unit-test runs.

### Frozen Raw Workflow With Generated Core

The same pinned raw GFS/EC-basic/CAMS inputs and Shanghai observer from
Stages 11-14 are used here, with model-grid elevation. One warmup and three
sequential measured trials include the complete frame/core builder. The
Python reference computes the public builder once; extra tensor projections
used only for verification are outside its timer.

| Model / Workflow | Trials, ms | Median, ms |
| --- | --- | --- |
| GFS Python reference | 765.134958, 760.790916, 766.380167 | 765.134958 |
| GFS independent Rust | 1210.981250, 1228.648416, 1223.604666 | 1223.604666 |
| EC-basic Python reference | 5508.618375, 5533.129917, 5561.316833 | 5533.129917 |
| EC-basic independent Rust | 4005.941541, 4004.822917, 4012.832000 | 4005.941541 |

Rust includes process startup, source checks, decode, scheduling, native
assessment, all sixteen parent arrays, complete profile children, the JSON
companion, hashes, syncs and final revalidation. Python returns memory objects
without those durable outputs. This is not an equal-I/O language comparison.
Neither path in this benchmark performs point-core validation/materialization
into the complete public point forecast.

All measured trials check complete JSON structures and values, exact compact
frame bytes, all nineteen instantaneous fields, 103 event components, seven
selected-profile diagnostics, complete columns/profiles and all assessment
fields. The 82-field assessments are exact for these pinned inputs. Timing/rate
fields are intentionally excluded from numerical parity, not omitted from the
outputs. The JSON companion is about 125237 bytes for GFS and 133062 bytes for
EC-basic; actual hashes change with performance measurements.

The new assembly/serialization/sync/hash phase is 8.83..10.65 ms for GFS and
10.01..10.29 ms for EC-basic. GFS still spends about 0.41 s on model decode/hash,
0.21 s writing/verifying profile children and 0.20 s loading/verifying selected
profiles. EC-basic spends about 3.61 s on global model decode/hash. These
remain more consequential than single-observer JSON assembly. GFS is still
slower than the memory-only reference; this stage makes no contrary claim.

```bash
cargo build --manifest-path engine/Cargo.toml --locked --release --bin firesky-engine
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model gfs --trials 3 --frames
.venv/bin/python scripts/bench_assessment_migration.py --pinned-model ecmwf-basic --trials 3 --frames
```

Timed Rust children have empty PATH/PYTHONPATH and a nonexistent Python path.
The synthetic benchmark supports keeping streamed result serialization and
small retained instant marks. Further profile I/O, global EC decode and
production-cache optimizations need separate measurements with unchanged
integrity/failure contracts. Cold/P95, concurrent complete national raw
forecasts, public core materialization and production peak-memory acceptance
are still open.

## Stage 16: Validated Point Materialization

Date: 2026-11-09, local macOS/aarch64 release builds. The frozen-file workflow
now includes request/core validation and the complete materialized single-point
result. It still excludes automatic file/cycle selection, downloads, production
caches, desktop sessions, HTTP/CLI and Python-free application packaging.
ecCodes remains C; this is a Python-free Rust workflow, not a zero-C program.

### Measured Component Optimization

The fixed fixture is the Python-generated point core from the same pinned GFS/
CAMS/Shanghai inputs used below. Request/core JSON is parsed before timing.
One warmup batch and five measured batches each perform 500 complete
validation/materialization calls. Output serialization, source decoding,
process startup and disk I/O are excluded. The Python comparison uses its
reference astronomy/empirical paths. Full structures are compared separately.

The initial Rust version was slightly slower than Python. Two measured
allocation/search costs were removed:

- Exact object validation now checks required keys once and proves no extra
  keys by cardinality, instead of scanning the entire schema for every key.
  Unit tests check required/optional schema uniqueness and disjointness.
- Evolution/window computation borrows one small typed curve from the existing
  twenty steps, instead of cloning and deserializing both complete step maps.

| Implementation | Per-point trials, ms | Median, ms |
| --- | --- | --- |
| Initial Rust | 1.494019750, 1.494505584, 1.509383666, 1.496998416, 1.496634084 | 1.496634084 |
| Optimized Rust | 0.411261916, 0.414111750, 0.411704168, 0.413841666, 0.411013584 | 0.411704168 |
| Python, final comparison run | 1.319862666, 1.302630750, 1.323972000, 1.326410166, 1.316509332 | 1.319862666 |

Rust component latency falls about 72.5% against its initial implementation;
the final component is about 3.21x faster than this Python reference run.
This is one warm, single-point fixture, not a complete-forecast speed ratio.
The initial comparison's Python median was 1.346533834 ms.

Initial and optimized Rust outputs from the same fixed fixture both serialize
to exactly 82155 bytes with SHA-256
`f5663326879b23795c108489c985aec327d446e1bbce80e99b0beb1eec52e8a0`.
The hash is checked outside the timed loop; output equality is also checked
after each batch. It proves unchanged Rust output for this fixture, not
Python/Rust byte-for-byte JSON formatting or meteorological correctness.

Python integer compatibility requires serde_json arbitrary-precision numbers:
support counts remain integers beyond u64, and rounded durations do not
saturate to i64. These semantics are covered by differential tests. JSON
allocation and national peak RSS were not remeasured under this feature;
the Stage 15 RSS numbers describe its earlier build, not a new Stage 16
memory-acceptance result.

### Frozen Raw Inputs Through Point Files

The pinned GFS/EC-basic/CAMS files, source identities, three forecast frames
and Shanghai observer are unchanged from Stages 11-15. One warmup precedes
three sequential measured trials. Python now also validates and materializes
the core once inside its timer; verification-only projections remain outside.

| Model / Workflow | Trials, ms | Median, ms |
| --- | --- | --- |
| GFS Python reference | 761.452708, 760.875292, 760.685167 | 760.875292 |
| GFS independent Rust | 1231.484917, 1240.791458, 1226.603959 | 1231.484917 |
| EC-basic Python reference | 5451.243958, 5455.280833, 5476.496958 | 5455.280833 |
| EC-basic independent Rust | 3991.846292, 4003.562209, 4008.288125 | 4003.562209 |

Rust includes startup, source checks, decode, scheduling, native scoring,
sixteen parent arrays, full profile children, both JSON companions, durable
syncs/hashes and final revalidation. Python returns memory objects. The I/O
contracts differ; GFS remains slower at this scope, and EC's lower elapsed
time is not proof of a general Rust language advantage.

All 82 assessment fields are exact on these pinned inputs. Complete point
JSON structures/values, frame codecs and intermediate tensors are compared
on every trial using the existing stage tolerances. Point outputs are stable:

| Model | Point JSON Bytes | SHA-256 |
| --- | --- | --- |
| GFS | 82151 | `fbfa05af21b4ea2ba7ea8bd9aa4624c2f2655760c027666c0b36d1799cb49c94` |
| EC-basic | 91263 | `fb2f70902cb607cd08f96cb948e01dbcdb6d3222a7a99cc12088d86e16c1a84a` |

Validation/materialization inside the raw workflow takes 0.50..0.66 ms for
GFS and 0.60..0.64 ms for EC-basic. Including the point companion's
serialization/sync/hash takes 8.40..10.92 ms and 8.43..9.82 ms respectively.
These phases are nested within the broader frame preparation/output timing,
not independent values to sum.

The more consequential remaining costs are GFS model decode/hash
401..407 ms, profile write/verification 210..212 ms and selected-profile
load/verification 196..198 ms. EC-basic global decode/hash is 3593..3606 ms.
Further optimization should address measured raw input/profile/cache costs
without weakening source integrity or confusing EC basic proxies with its
separate existing point/full-catalog paths. No persistent production-cache
optimization is implied by this stage.

Reproduce sequentially, without concurrent benchmarks/builds/tests:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --release \
  --bin firesky-engine --example bench_point_core
.venv/bin/python scripts/bench_assessment_migration.py \
  --pinned-model gfs --trials 3 --point-forecast \
  --core-fixture /tmp/firesky-point-core-gfs.json
.venv/bin/python scripts/bench_assessment_migration.py \
  --pinned-model ecmwf-basic --trials 3 --point-forecast
.venv/bin/python scripts/bench_point_core_migration.py \
  --fixture /tmp/firesky-point-core-gfs.json --iterations 500 --trials 5
```

The Python/pytest harness supplies reference checks, not timed Rust computation.
Each Rust child has empty PATH/PYTHONPATH and a nonexistent Python path.

## Stage 17: GFS Cache Data Access

Date: 2026-11-09, local macOS/aarch64 release engine. This measures only
metadata/cache operations, not downloads, GRIB decoding, forecasts or complete
production workflows. ecCodes is still C in the broader engine.

The synthetic fixture has 1000 metadata records across 25 directories and
an initially 1173387-byte shared index. Raw bytes deliberately are not weather
messages. Every measured call checks the same selected path. A cache-only
download call must return `exact_cache`; no HTTP request is allowed.

Two avoidable costs were found and removed:

- Warm lookup reparsed and hashed the full index on every call. A bounded
  process-local Arc cache now reuses it when its size/mtime and directory
  stamps still match. Matching raw metadata is still checked, and ordinary
  changes, malformed indexes and stale rows retain rebuild behavior.
- Cache metadata publication reread every raw metadata JSON. It now replaces
  only the changed index entry, refreshes directory stamps and republishes
  the sorted compatible index under the existing shared lock. Metadata and
  index file syncs and parent-directory syncs remain in the Rust path.

The initial diagnostic run observed Rust medians of 4.807297083 ms for lookup
and 62.6965625 ms for metadata reuse/publication. That run overlapped part
of the local HTTP test run, so it is not an isolated before/after acceptance
baseline. Its lookup trials were 4.818939027, 4.807235833, 4.763740557,
4.821398473 and 4.807297083 ms; publication trials were 62.6965625,
61.8995042, 62.7859125, 61.96165 and 69.2939625 ms.

The final comparison below ran without simultaneous builds/tests/benchmarks.
One untimed warmup precedes each batch. There are five batches, with 300
in-process lookups, ten reuse/publication calls and twenty bridge calls per
batch. Rust component timing excludes process startup and result serialization;
the bridge includes a fresh Rust process and protocol/validation per call.

| Operation | Python Median, ms | Rust In-Process Median, ms | Python-to-Rust Bridge Median, ms |
| --- | --- | --- | --- |
| Covering-cache lookup | 0.155739583 | 0.046086387 | 9.961158350 |
| Exact-cache reuse + metadata/index publication | 17.530754200 | 27.256500000 | 38.141239600 |

Final per-call trial values, in milliseconds:

| Operation / Implementation | Five Trials |
| --- | --- |
| Lookup Python | 0.155510140, 0.155739583, 0.156400137, 0.153196527, 0.156112500 |
| Lookup Rust | 0.069263333, 0.046701947, 0.045520140, 0.045854167, 0.046086387 |
| Lookup bridge | 10.713875000, 10.168183300, 9.912579150, 9.961158350, 9.830820801 |
| Reuse Python | 17.518120800, 17.530754200, 18.271504200, 17.327312499, 17.931379098 |
| Reuse Rust | 27.221100000, 27.578475000, 27.256500000, 27.955950000, 26.755033300 |
| Reuse bridge | 38.435058350, 38.141239600, 39.540845850, 38.084089600, 37.554664600 |

Warm Rust lookup is about 3.38x faster than the Python reference on this fixture,
but the current subprocess bridge is about 64x slower than Python lookup.
Do not enable `FIRESKY_GFS_DATA_BACKEND=rust` as a lookup speed optimization.
Persistent production IPC/batching is still needed to exploit in-process
reuse without changing current runtime behavior.

Publication remains slower in Rust despite eliminating full metadata rereads.
The Rust and Python durability contracts differ: Rust syncs both published
JSON files and parent directories, whereas the Python reference index writer
does not. This is not an equal-I/O language comparison. Further phase-level
profiling, persistent IPC and production lifecycle integration are needed;
no network throughput, complete-forecast, cold/P95 or peak-RSS improvement
is claimed here.

Reproduce the final run sequentially:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --release \
  --bin firesky-engine --example bench_gfs_data
PYTHONPATH=src .venv/bin/python scripts/bench_gfs_data_migration.py \
  --cache-dir /tmp/firesky-stage17-cache-bench \
  --rust-example engine/target/release/examples/bench_gfs_data \
  --engine-bin engine/target/release/firesky-engine
```

The harness creates/reuses only its synthetic cache directory. It forces the
Python reference data backend, runs native children with empty PATH/PYTHONPATH
and a nonexistent Python path, and checks that all paths remain identical.
Transport and real-GRIB/point-result parity are covered separately by the
Stage 17 tests, not by this marker-only cache microbenchmark.
Cold/P95 latency, production caches, concurrent complete workflows, national
raw scoring/peak memory, near-tied NumPy/BLAS parity and application packaging
remain unaccepted. Production defaults are unchanged.

## Stage 18: GFS Event Input Planning

Date: 2026-11-09, macOS/aarch64 release engine. This measures automatic
event-hour/domain planning and local GFS/CAMS cache selection, not weather
scoring, a complete forecast, production IPC or network throughput. Fixture
creation and Python reference checks are outside the Rust timer. Each fresh
Rust process has empty PATH/PYTHONPATH and a nonexistent Python path.
ecCodes remains a C dependency for decoding.

The observer-elevation fixture is one named point, dusk, a 200 km radius,
an explicit 2026-11-09 00Z cycle and real synthetic GFS/compact CAMS GRIB
files. Five independent processes each perform one untimed prime followed
by thirty measured in-process plans. No benchmarks, builds or tests ran
concurrently with either measurement.

Profiling found that repeat compact CAMS validation/decoding dominated the
initial planner. It is now memoized in a process-local LRU of at most 256
validation/coverage records, keyed by canonical path, size and mtime.
No optical tensors are retained. Ordinary changes and failures invalidate
or populate the same versioned probes; actual scoring still hashes,
decodes and checks raw files independently. The cache does not provide
cryptographic source identity, nor speed up a fresh process's first decode.

| Observer-Elevation Planner | Five Warm Trials, ms/plan | Median, ms |
| --- | --- | --- |
| Before validation-probe memo | 174.097630500, 174.261038900, 174.576201500, 174.575702833, 174.461895833 | 174.461895833 |
| After validation-probe memo | 0.190773533, 0.185491700, 0.253831933, 0.203766833, 0.195319367 | 0.195319367 |

The roughly 893x change applies only to this repeated warm planning scope.
Before optimization CAMS selection took 173.814..174.284 ms per plan;
afterward it took 0.0380..0.0566 ms. GFS resolution took
0.0757..0.0978 ms and solar requirements 0.0309..0.0340 ms afterward.
Every trial before and after produced the same 2087-byte stable plan
projection, SHA-256
`d39fbcd249c2a193b1e37e0b66d38d1fb68a7c8a62285ce15a6e6670392c9f04`.
The projection excludes only elapsed/phase timing fields.

First-call measurements after optimization were 197.599750, 175.862333,
179.691083, 180.361667 and 174.693708 ms. These are fresh-process
application-cache misses against an OS-warm fixture, not cold-disk
latencies. Startup and result serialization are outside these timers.
The optimization depends on process reuse; spawning a new engine for every
request cannot realize the warm result. No Python-versus-Rust planner
speedup is claimed by this before/after Rust measurement.

A separate model-grid-elevation run uses the real terrain prepass and
replanning. Five processes each prime once and then measure five plans:

| Model-Grid Planner | Five Warm Trials, ms/plan | Median, ms |
| --- | --- | --- |
| After CAMS memo | 126.981658200, 126.715491600, 127.805166600, 126.301025000, 126.492600000 | 126.715491600 |

Terrain/replanning alone takes 125.962..127.470 ms; CAMS selection is
0.0843..0.0947 ms. First calls are 298.930..319.348 ms. All five stable
projections are 2198 bytes, SHA-256
`da40e159ebadc4fc2eed083d89cce124dc5f86445db7fe6fbe9c9c99c2e05b4c`.
The terrain source is still decoded and revalidated on each plan; this is
a remaining measured cost, not hidden by the observer-only result.
Any future terrain cache/selective-decoding optimization must retain source
identity, grid/clock validation and final-hour binding.

Reproduce sequentially:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --release \
  --bin firesky-engine --example bench_input_plan
PYTHONPATH=src .venv/bin/python scripts/bench_input_plan_migration.py \
  --cache-dir /tmp/firesky-stage18-plan-bench \
  --rust-example engine/target/release/examples/bench_input_plan
PYTHONPATH=src .venv/bin/python scripts/bench_input_plan_migration.py \
  --cache-dir /tmp/firesky-stage18-terrain-bench \
  --rust-example engine/target/release/examples/bench_input_plan \
  --model-grid --iterations 5 --trials 5
```

This does not establish complete forecast P95, throughput, peak RSS,
publication freshness, authenticated CAMS downloads or production
Python-retirement acceptance. Existing defaults remain Python.

## Stage 19: ADS Retrieval and Decoder Lifetime

Date: 2026-11-09, macOS/aarch64 release engine. This is a local HTTP simulation
of ADS v1 processing and asset servers, not a live authenticated-provider,
queue-latency or complete forecast benchmark. Seven valid times across two
selected runs use real synthetic 3x2-cell, twelve-message compact GRIB files.
Payload construction is outside the timer. The fixture injects 10 ms into
every constraints response and marks jobs successful immediately.

The Python side runs the owning provider and actual installed `cdsapi 0.7.7` /
`ecmwf-datastores-client 0.6.0`. The existing Python three-tuple/two-tuple
splitter bug was corrected before measurement: comparing Rust's working
batch path to Python's previously empty batch result would not be meaningful.
Python returns paths in the existing process; Rust includes a fresh child,
protocol encoding/decoding, hashes, complete file validation and durable raw
publication. Native children have empty PATH/PYTHONPATH and a nonexistent
Python executable. The Python test HTTP server/harness is external to the
Rust engine, not a runtime adapter called by it.

Each trial starts with a new application-cache directory. Five trials run
sequentially, Python then Rust, without concurrent tests/builds/benchmarks.
OS filesystem caches and the host Python/ecCodes process are warm.
This is not an equal-startup or equal-durability language comparison.

The request-scoped date-constraint memo removes repeated availability
queries without changing the selected run/lead or raw bytes:

| HTTP Operation | Python | Rust |
| --- | ---: | ---: |
| Catalogue messages | 1 | 0 |
| Process discovery | 3 | 1 |
| Constraints | 15 | 3 |
| Execution | 2 | 2 |
| Monitor | 4 | 2 |
| Results | 2 | 2 |
| Asset transfer | 2 | 2 |

These are observed physical HTTP counts with no retries. Reported engine
constraint counters count logical operations; retry attempts can add calls.
Reducing availability calls is not evidence of lower live-provider latency
or fewer queued ADS jobs: both sides still submit two jobs.

An initial unprofiled run observed medians of 322.967708 ms Python and
501.419209 ms Rust. Phase instrumentation then found 347.046..385.629 ms
inside Rust profile decoding, within 446.351..511.489 ms of local
lock/decode/split/publication work. Releasing/reloading ecCodes between the
two run groups repeatedly discarded its initialized decoding state.
The optimized request now lazily owns one decoder across all groups and
fallbacks, with no cross-request or global decoded-data cache.
Actual file validation, clock checks, syncs and publication are unchanged.

| Profiled Run | Five End-to-End Trials, ms | Median, ms |
| --- | --- | ---: |
| Python before optimization | 416.963250, 315.353250, 314.797875, 314.099667, 323.663916 | 315.353250 |
| Rust before optimization | 3651.424834, 574.759958, 523.297584, 507.953166, 509.418958 | 523.297584 |
| Python after optimization | 422.141125, 326.336000, 327.947625, 313.120958, 317.980709 | 326.336000 |
| Rust after optimization | 890.497458, 367.825458, 358.570333, 358.974959, 358.114750 | 358.974959 |

All first-trial outliers are retained. In particular, the first profiled
Rust external measurement is 3651 ms while its measured internal phases
sum to roughly 519 ms; the difference is unattributed startup/harness time,
not proved to be decoder or network work. It is not silently discarded.

The Rust median decreases about 31.4% between the profiled runs.
Afterward profile decode is 203.272..209.857 ms, inside
293.422..329.359 ms of local work; constraints take 37.921..39.801 ms and
job/poll/transfer 15.074..19.818 ms. Decode/load timings are nested within
local work, not additive independent phases. Remaining local work includes
split I/O, locks and sync/publication. The engine's phase timers exclude
process startup and final protocol/result work.

Rust remains slower than Python's median on this fixture, despite fewer
HTTP calls. First-use ecCodes initialization, raw durability and subprocess
startup remain relevant costs; persistent production resource/service
integration is still needed. No general Rust-over-Python advantage is
claimed, and the opt-in bridge should not be enabled as a warm-cache lookup
speed optimization.

Every trial's seven filenames, raw byte lengths and SHA-256 values match,
before and after optimization and across Python/Rust. The sorted compact
file-identity projection is 1023 bytes, SHA-256
`c0187fd51ca38e72a57b0b99785ff5ba4933dcd882c1c655bf7f0db6bb5ab270`.

Reproduce with a new cache directory; the harness refuses to overwrite
existing trial directories:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --release \
  --bin firesky-engine
.venv/bin/python scripts/bench_cams_data_migration.py \
  --cache-dir /tmp/firesky-stage19-cams-new-run \
  --engine-bin engine/target/release/firesky-engine \
  --trials 5 --constraint-delay 0.01
```

This does not establish live ADS account/licence acceptance, large-grid
throughput, cold-disk/P95 latency, peak RSS, full-forecast performance or
production Python retirement. ecCodes remains C. Defaults remain Python.

## Stage 20: Public/Basic EC Raw Transport

Date: 2026-11-09, macOS/aarch64 release engine. The benchmark runs the owning
`EcmwfIfsAdapter` and actual installed `ecmwf-opendata 0.3.35` /
`multiurl 0.3.9` against a local HTTP index/Range server. Seven source hours
(f006, 009, 012, 015, 018, 021, 024) each produce a complete three-part raw
bundle: 54 pressure messages, TCC and f000 surface z/2t/2d. The synthetic
regular grid has only six cells. Each index includes 10,000 unselected
records, and each index response has an injected 10 ms delay. Source
construction is outside the timer; repeated server JSON encoding is not.

Five cache-miss trials run sequentially, Python then Rust, with no concurrent
tests, builds or other benchmarks. Each uses a new raw-cache directory.
The Python process and OS/ecCodes caches are warm; Rust includes a fresh
child, JSON-lines protocol, hashes, strict header admission and file/metadata/
Unix directory syncs. This is an architectural component comparison, not
equal-startup/equal-I/O evidence that the language alone is faster.
Both paths retain identical raw message ordering and byte identities.

An initial harness attempt failed with a proxy-mediated local HTTP 502 and
is not a timing sample. Successful runs explicitly set `NO_PROXY=*` for the
loopback endpoints. No public ECMWF mirror was contacted or accepted.

Physical HTTP counts per seven-hour trial:

| Operation | Existing Python Adapter | Native Batch | Per-Hour Native Bridge Shape |
| --- | ---: | ---: | ---: |
| Index GET | 21 | 8 | 14 |
| Artifact HEAD | 21 | 0 | 0 |
| Ranged GET | 28 | 28 | 28 |

The batch reuses the target-hour index between pressure/TCC queries and the
f000 index across all seven bundles. Static surface ranges are still fetched
again for each bundle. These counts do not establish live-provider latency,
remote snapshot consistency or fewer forecast products requested.

The measured initial index phase took 351.095..368.141 ms per batch.
The first implementation retained `serde_json::Value` trees and deeply
cloned whole indexes for every part. Typed records now retain only fields
used for selection; `Arc` shares their immutable contents. The LRU evicts
above 64 MiB of accounted records/strings/entry storage. This is an estimate,
not a process memory cap. Selection order, validation and durability are
unchanged.

| Run | Five End-to-End Trials, ms | Median, ms |
| --- | --- | ---: |
| Python before optimization | 1504.971083, 1430.855291, 1456.778166, 1427.880416, 1461.028750 | 1456.778166 |
| Rust batch before optimization | 1005.195084, 997.516875, 1002.485584, 985.104958, 998.261584 | 998.261584 |
| Python after optimization | 1538.278125, 1422.542792, 1413.228375, 1423.281292, 1425.798250 | 1423.281292 |
| Rust batch after optimization | 1351.946334, 897.808666, 867.310958, 867.871209, 861.464792 | 867.871209 |

The Rust batch median decreases about 13.1%. Its index phase becomes
312.386..333.829 ms; header probing remains 374.686..381.121 ms. Other work
includes filtering, cache/lock operations, hashing, syncs and destruction.
Index timing does not include every downstream selection/deallocation
cost, so the total reduction is not attributed entirely to that phase.
All first-trial outliers remain included. The first optimized Rust external
measurement is 1352 ms versus 885 ms inside the engine; the difference is
unattributed startup/harness time, not proved to be GRIB or network work.

The existing Python bridge starts a new process for each hour. A separate
five-trial run deliberately measures that shape:

| Mode | Five End-to-End Trials, ms | Median, ms |
| --- | --- | ---: |
| Python adapter | 1649.543625, 1471.269209, 1465.743750, 1478.509959, 1479.562000 | 1478.509959 |
| Seven native processes | 2267.629750, 2161.536208, 2184.230583, 2186.583000, 2122.563250 | 2184.230583 |

The bridge shape is about 47.7% slower here. Its summed header-probe time is
1319.534..1370.560 ms, versus about 375..381 ms in one native batch.
Repeated native/ecCodes initialization and lost request-scoped reuse remain
costs. Do not enable the bridge as a download speed optimization or claim
that its current callers receive the batch result. Complete native EC point
orchestration and persistent service/resource ownership remain outstanding.

A separate three-trial native-batch run uses macOS `/usr/bin/time -l`:
peak engine RSS is 58,851,328 / 58,114,048 / 58,392,576 bytes
(55.42..56.13 MiB). The engine includes ecCodes; the external Python SDK/
HTTP harness is excluded. This is not a Python memory comparison, a
before/after RSS comparison, or a production maximum. End-to-end latencies
with that wrapper are 908.701208 / 863.523041 / 915.603292 ms. The final
accounted index cache is 21,418,455 bytes in every batch, not the peak RSS.

All seven raw filenames, sizes and SHA-256 values match before/after and
across SDK/native/per-hour/RSS runs. The sorted file-identity projection is
904 bytes, SHA-256
`6c8f092c414aaed81b7a8ad00a390014e4ebc3f06c5adfa387a9a1f2d2646d30`.

Reproduce separately with new cache directories:

```bash
cargo build --manifest-path engine/Cargo.toml --locked --release \
  --bin firesky-engine
.venv/bin/python scripts/bench_ecmwf_data_migration.py \
  --cache-dir /tmp/firesky-stage20-batch-new \
  --engine-bin engine/target/release/firesky-engine \
  --trials 5 --extra-index-rows 10000 --index-delay 0.01
.venv/bin/python scripts/bench_ecmwf_data_migration.py \
  --cache-dir /tmp/firesky-stage20-single-new \
  --engine-bin engine/target/release/firesky-engine \
  --trials 5 --extra-index-rows 10000 --index-delay 0.01 --single
.venv/bin/python scripts/bench_ecmwf_data_migration.py \
  --cache-dir /tmp/firesky-stage20-rss-new \
  --engine-bin engine/target/release/firesky-engine \
  --trials 3 --extra-index-rows 10000 --index-delay 0.01 --measure-rss
```

`--measure-rss` is currently macOS-only. None of these runs establishes
full-globe throughput, cold disk/P95, real ECMWF publication/mirror/proxy
acceptance, complete point-forecast latency or overall Python retirement.
Defaults remain Python and ecCodes remains C.

## Stage 21: Independent EC Point Report Workflow

`scripts/bench_ecmwf_point_migration.py` measures the actual
`run_ecmwf_basic_point_forecast` Python path, split native per-hour raw
resolution plus stored-point scoring, and the one-request
`forecast_cached_ecmwf_point_to_files` path. The latter performs its own
event/domain/hour planning, batched strict raw resolution, cached CAMS
selection and complete independent EC report/quality/Web-report assembly.
This is not the full HTTP/desktop forecast, provenance/cross-section/export
or production cache lifecycle.

The fixture is dawn on 2026-11-10, cycle 2026-11-09 00Z, six model cells,
nine pressure levels, twenty event samples and source hours 21/24.
An actual local HTTP server provides NDJSON indexes/Range responses, with
10,000 unselected index records and 10 ms index delay. All eleven CAMS
levels are present in real synthetic ecCodes GRIB fixtures, preseeded
outside timing; selection, decode and scoring are inside timing.
Source construction is outside timing; server index JSON encoding is inside.
No live provider or credentials are used.

Five sequential trials were run for each backend, each with an initially
empty raw/derived cache followed by a repeat against that backend's warm
cache. Each Rust invocation starts a fresh child with empty PATH/PYTHONPATH
and an unavailable Python path. Split native execution starts one child
per model hour and another for assessment; batched execution starts one
child for the complete operation. Runs were separated from builds/tests.
"Cold" here means new application raw/derived caches, not a cold operating
system page cache or a fresh Python process.

### Measured Bottleneck And Implemented Change

Before optimization, merging the operations into one native process did not
by itself remove repeated ecCodes initialization: phase-local `EcCodes`
objects loaded and then released the last dynamic-library reference.
The warm batched plan in trial 2 spent 235.09 ms in raw resolution
(231.15 ms header probing), 195.28 ms in CAMS selection/coordinate discovery,
then roughly 230 ms in assessment's model decode/hash phase.

`ecmwf_plan::Prepared` now owns a request-scoped library handle from raw
admission through CAMS selection and the final assessment. Phase-local
decoder objects can release their handles without unloading the library and
discarding its internal definition caches. The handle is dropped at the end
of the request. No global unbounded cache, skipped header validation, cached
forecast substitution or Python memoization is introduced.

Warm raw admission still costs 228.92..234.36 ms after the change, including
the first definition initialization in each fresh process. CAMS selection
falls to 16.24..20.43 ms and model decode/hash to 68.79..71.40 ms.
Those phase reductions explain most of the observed wall-time improvement;
the remaining phases still include profile writes, hashing, sync and report
assembly/serialization.

The final version also corrects CAMS requirements to follow only valid solar
sample source indices plus the anchor, while raw EC hours still cover all
twenty evaluations. Both source hours are referenced in this dawn benchmark,
so that correctness fix does not remove any benchmark inputs, HTTP requests
or CAMS files. The new positive-180 longitude edge fix is likewise not
exercised by this fixture.

### Observed Times

End-to-end milliseconds, five-trial medians:

| Implementation | Before Cold | After Cold | Before Warm | After Warm |
| --- | ---: | ---: | ---: | ---: |
| Python, warm host process | 706.98 | 706.64 | 55.65 | 52.35 |
| Rust, split native children | 1067.33 | 1063.46 | 812.19 | 804.27 |
| Rust, one native request | 1017.61 | 682.12 | 826.88 | 476.85 |

The batched Rust path improves by **33.0% cold** and **42.3% warm** against
its own pre-residency baseline. Against the final split-child topology it
improves by 35.9% cold and 40.7% warm. Merely observing fewer HTTP calls was
not sufficient evidence of a faster whole operation.

Before batched cold samples:
`1014.727, 1214.946, 1013.059, 1020.099, 1017.614`.
Before batched warm samples:
`820.602, 826.877, 834.782, 821.897, 829.954`.
After batched cold samples:
`734.054, 660.755, 682.123, 674.074, 712.432`.
After batched warm samples:
`474.147, 476.845, 478.480, 474.903, 493.756`.
First split-cold outliers (1431.95/1490.26 ms before/after) are retained.
No specific external cause was established or subtracted.

Cold request counts are unchanged across native versions:
Python 6 index GETs, 6 HEADs, 8 Range GETs; split native 4 index GETs,
no HEADs, 8 Range GETs; one-request native 3 index GETs, no HEADs,
8 Range GETs. Warm trials issue no HTTP requests.

### Fidelity And Remaining Performance Work

Every trial compares the complete EC report to the Python workflow using
the existing strict differential comparator, and raw filenames/sizes/SHA-256
identities must match. The canonical reference-report/raw-identity projection
is 51,318 bytes, SHA-256
`722ac262328da40cd1d728a122401d35b1ac4505cf03a866c0784de753c1dca1`,
unchanged across runs.

Separately, `jq -cS '.report'` over all forty before/after, cold/warm,
split/batched native output files yields exactly one unique report.
Its canonical newline-terminated SHA-256 is
`164cf482dec184211c4d103fa416eaf58753f2f48b654872911d44f2a6e43cda`.
The optimized release binary is
`3df8f19613f7a6f2638aec5471c03d77b2d3ee535a70bcaf90b5dbdaeade6dad`;
the locally retained baseline binary is
`990b58040963a990fde812654d10d2a9220f850dee884d9609f3c8376b879e3f`.

Python's warm path remains much faster: it reuses durable derived mmaps and
a resident interpreter/ecCodes context. The native path still revalidates
and redecodes raw inputs in a fresh child, creates complete tensor/profile
bundles and writes/hashes/syncs JSON outputs, whereas Python returns a report
in memory. The small cold difference versus Python is not a general language
performance result, and the warm gap must not be hidden by comparing only
download counts. Durable native derived caches, complete source identities,
profile reuse and a bounded persistent production worker remain real work.

This fixture does not establish full-domain throughput, representative CPU/
peak-memory bounds, cold-disk or P95 latency, live EC/ADS behavior, combined
online EC-plus-ADS acceptance, production cancellation/concurrency or complete
Python retirement. ecCodes remains a C dependency.

Reproduce with a new directory:

```sh
cargo build --release --manifest-path engine/Cargo.toml
.venv/bin/python scripts/bench_ecmwf_point_migration.py \
  --cache-dir /tmp/firesky-stage21-point-new \
  --engine-bin engine/target/release/firesky-engine \
  --trials 5 --extra-index-rows 10000 --index-delay 0.01 \
  > /tmp/firesky-stage21-point-new.json 2> /tmp/firesky-stage21-point-new.log
```

The local baseline artifact `/tmp/firesky-stage21-engine-before` can be passed
as `--engine-bin` with another new cache directory. It is a local measurement
artifact, not a packaged release or a committed dependency.

## Stage 22: Verified Cross-Section Reading

Measured on 2026-11-09 on this macOS/aarch64 checkout. This is a complete
theoretical cross-section micro-workflow, not full-forecast performance.
`scripts/bench_cross_section_migration.py` constructs four deterministic
128x128-cell, 11-pressure-level, 24-field float32 profiles and an equivalent
Python combined NPY outside timing. Each query samples the two needed frames
at 41 distances over 1000 km and returns all matrices/targets as JSON.

Seven trials alternate backend execution order. Python runs in a warm host
process with the original mmap reader and full JSON encode/decode. Rust runs
as a fresh child with empty `PATH`/`PYTHONPATH`, nonexistent `FIRESKY_PYTHON`,
source/child/header/whole-array checksum validation and JSON decode included.
Rust RSS is `/usr/bin/time -l`'s maximum resident set size in bytes, including
its runtime. The already-written inputs are OS-cache-warm, not cold-disk.
Python process RSS is not compared because it includes fixture construction.

| Implementation | Python median ms | Rust median ms | Rust median peak RSS bytes |
| --- | ---: | ---: | ---: |
| Initial two whole-frame vectors | 19.181 | 128.177 | 43,696,128 |
| Verified sparse columns | 18.911 | 75.236 | 7,143,424 |

The native implementation improves its own median elapsed time by **41.3%**
and median peak RSS by **83.7%**. It does not beat Python's warm mmap reader.
Rust continues to hash complete selected arrays before/after reading and
before returning, and pays fresh-process startup; Python's owning reader does
not perform those whole-file checks. The residual gap is not a language-only
comparison. More production cache/worker profiling is needed before choosing
any persistent verified-read cache policy.

Observed Rust trial times (ms, all retained):

```text
whole frames: 602.959, 127.514, 130.179, 126.341, 128.964, 128.177, 128.008
sparse:       429.466,  75.160,  75.236,  73.591,  76.317,  81.487,  75.216
```

The first native execution of each binary was markedly slower; these values
are retained rather than called cold-disk latency or removed as outliers.
No cause attribution or P95 conclusion is established by seven trials.

The optimization first builds the existing four-corner stencils for the
whole path, sorts/deduplicates their cells, and seeks with the NPY parser to
decode those complete pressure/24-field columns. Stencils remap to the compact
buffer without changing weights, finite fallback or reduction order.
At 41 distances this decodes at most 164 cells per frame rather than 16,384;
the two float32 column buffers are at most 346,368 bytes for 11 levels.
Whole arrays are still hashed, including bytes outside the requested columns.
The national/model source domain and pressure/time axes are not truncated.

The harness compares every result field with the owning Python implementation,
using strict shared tolerances except a 2e-9 m absolute allowance for the two
spherical distance diagnostics. Before/after native outputs were separately
compared for **exact parsed-JSON equality** on the same source:
165,416 canonical bytes, SHA-256
`9665d95d8a769d6c495fc3efd88b7daeaa9aba2b8cdef849f5747632a5b858dc`.
Both runs' canonical Python-reference result SHA-256 is
`d91391f10de470e3e3c0d1772434ae06d97dda0309ee1a15b2a1532a216b8b78`.
The final metadata-only float32 pressure-axis normalization fix was separately
checked against both measured binaries for the same exact native output.
Its final release SHA-256 is
`98ce58b39aa70a8ba32956a07a37dffb4ffc4ea005b79981349b94e6a82a376d`;
the timing table refers to the retained measured binaries, not a new timing run
of that final build.

Local artifacts:

- Whole-frame binary `/tmp/firesky-stage22-engine-bulk`, SHA-256
  `7160c5d2881016e9117943231c8775e463a4fba2cb4ccf56a5d02b2e47fd984b`.
- Measured sparse release binary `/tmp/firesky-stage22-engine-sparse-measured`, SHA-256
  `c50f4c52a6042de9ecbce628bf30947fb6781d8ff3d2a30cd48dbc9132e5d58f`.
- Inputs/results `/tmp/firesky-stage22-cross-before`,
  `/tmp/firesky-stage22-cross-after`,
  `/tmp/firesky-stage22-bench-before.json`,
  `/tmp/firesky-stage22-bench-after.json`.

Reproduce with a new directory on macOS:

```sh
cargo build --release --manifest-path engine/Cargo.toml
.venv/bin/python scripts/bench_cross_section_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --cache-dir /tmp/firesky-stage22-cross-new --trials 7 --side 128
```

This does not measure GRIB decoding, EC/CAMS downloads, full national grids,
production cross-section registration/cache reuse, concurrency, complete app
latency, meteorological accuracy or Python retirement. Existing edge clamping
is preserved, not proof of model coverage beyond stored coordinates. ecCodes
remains a C dependency of the overall weather pipeline.

## Stage 23: EC Service Output

Measured 2026-11-09 on the same macOS/aarch64 host, with five trials per backend
and cold/warm cache state. The source fixture has six cells, nine pressure
levels, twenty dawn samples and a forty-one-distance theoretical cross-section.
EC indexes/ranges are served by the loopback fixture; CAMS input generation is
outside timed code. Backend order reverses on alternate trials.

`scripts/bench_ecmwf_service_migration.py` measures:

- `python-service`: actual `_run_ecmwf_point_payload(..., force_refresh=True)`,
  including independent EC forecast, HTML, complete Web response,
  cross-section and durable response-cache writes. Warm runs reuse Python's
  derived caches, but do not return an existing response-cache hit.
- `rust-point`: existing point-only cached-raw workflow, a fresh native process
  with empty `PATH`/`PYTHONPATH` and nonexistent `FIRESKY_PYTHON`.
- `rust-service`: fresh native process plus full provenance, HTML,
  profile-aware response, verified cross-section and all durable tensor/profile/
  artifact writes, checksums and parent manifest publication.

The Python process, imports, fixture setup and OS caches are warm. “Cold” here
means empty per-trial raw/derived caches, not cold disk or cold Python. Native
client-side parsing of exported artifacts after manifest acknowledgement is
outside the timer. Native
service export has no production response-cache or HTTP integration, so these
are observed workloads, **not an equal production-workflow speed ratio**.

### Results

Final release SHA-256:
`5a91552da06843ba8b90b26bebca70d7a4b6abd881390538a8a1883b51167c91`.
Median total wall time in milliseconds:

| Workload | Cold Cache | Warm Cache |
| --- | ---: | ---: |
| Existing Python service, forced recompute | 379.345 | 66.949 |
| Rust point only | 544.534 | 475.404 |
| Rust service output | 557.721 | 492.685 |

The native service assembly/serialization/output descriptor reports median
**15.755 ms cold / 15.693 ms warm**, including HTML flush/sync/hash and verified
cross-section computation. It excludes the already-produced numerical tensors
and the later parent manifest commit. Native process peak RSS medians were
40,927,232 bytes cold and 40,517,632 bytes warm. Python RSS is not measured;
do not infer a memory advantage over Python.

Median native-service phase observations:

| Phase | Cold Cache, ms | Warm Cache, ms |
| --- | ---: | ---: |
| Raw header probe/admission (includes first ecCodes definition work) | 227.210 | 227.278 |
| Model decode/source identity | 68.810 | 68.886 |
| Native numerical assessment | 3.077 | 3.047 |
| Added service assembly/serialization/output | 15.755 | 15.693 |

These phase medians are not additive and their timing boundaries differ from
Python's. New-process initialization and strict raw admission remain much
larger than HTML/service assembly. The warm native workflow still decodes and
rewrites profiles instead of using Python's durable derived cache. This
measurement does **not** demonstrate a total Rust speedup.

### Optimization Decision

The existing Stage 22 sparse-column reader is reused with full checksums;
service export does not revert to whole-profile decoding. No additional
numerical approximation, source-domain truncation or checksum bypass was
introduced to improve this small-fixture latency.

The next justified work is a validated durable derived-cache path and a
separate long-lived-process/ecCodes-residency measurement. A faster HTML
formatter would address only a small part of the observed total; pinning
global C state or skipping admission without lifecycle/identity tests would
not establish a valid optimization. These cache/runtime changes remain
unfinished rather than being credited as implemented savings.

An earlier five-trial baseline used release
`af4d9cb8cef86e01b7111fd075bcf6cbddbb465ff1df072d7bdaf2be8f150f1b`.
It measured native service 562.870/491.500 ms cold/warm and about 17.6/18.0 ms
assembly. A second release,
`84ebc748507700ffe1d3db4cdc0d17a1bf59751f14887170ba56bbcc03ccc385`,
measured 579.547/499.752 ms and about 17.6/17.6 ms assembly after adding
nullable-duration/DST compatibility. The final release above also fixes an ADS
deadline error classification, outside this cached-CAMS benchmark path.
The sample does not exercise a DST transition. These are independent repeats,
not a before/after optimization claim; no export speedup is attributed to them.

Benchmark common-report parity SHA-256 is
`9fc491134ab2a35defab91d69fd02f14cb438f4b3965392b4425c8d7ac2dcb4c`
in all three runs. That guard covers only the listed common fields, not the entire
response; complete HTML/provenance/cross-section compatibility has separate
differential tests. Raw logs and trial samples are in
`/tmp/firesky-stage23-service-{baseline,final,verified}.json`; generated native HTML is
in the corresponding trial directories.

Reproduce with a fresh destination:

```sh
.venv/bin/python scripts/bench_ecmwf_service_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --cache-dir /tmp/firesky-stage23-service-new --trials 5
```

This is not a full-domain/national, P95, live-download, concurrent-service,
desktop, clean-machine, cross-platform or Python-retirement acceptance test.
The executable remains dependent on the ecCodes C library.

## Stage 24: Verified Derived Cache, 2026-11-09

Release SHA-256:
`a600dd715f10945eeedb02aa93089162f9feedaca071b8911b6085ea2696b7da`.
These measurements are pinned to that binary. A subsequent cache-directory
resolution correction was rebuilt as
`ea40abe1320407fff93c28458e1ffd7672bc9a39efd08dc780ffbf80bf004f83`;
it makes relative-request resolution idempotent and is not credited with any
additional performance improvement here.
The new native cache reuses only model decode/derivation. Solar/CAMS,
assessment, quality/profile diagnostics, frame/report generation, complete
independently copied pressure children, checksums and durable output remain
inside each timed native request. No raw-domain reduction, dtype change,
hard-linked output, response-cache shortcut or disabled checksum is used.
The engine runs with empty PATH/PYTHONPATH and nonexistent `FIRESKY_PYTHON`.
It still loads the ecCodes C library.

### Stored Local Point Workflows

Five alternating fresh-process samples per arm, using the same pinned local
raw GRIB/CAMS inputs as `bench_assessment_migration.py`. Every main float32
array and every complete per-hour profile array is checked bitwise after the
timed manifest acknowledgement, including NaN payload bits. This comparison
is Rust uncached versus Rust cached, not a Rust/Python performance ratio.

| Fixed Complete Point Workload | Uncached Median | Warm Derived Median | Uncached / Warm |
| --- | ---: | ---: | ---: |
| GFS: 3 frames, 147 x 169 = 24,843 cells, 11 pressure levels | 1310.138 ms | 1541.442 ms | 0.85x |
| EC basic: 3 frames, 29 x 29 = 841 selected cells, 9 pressure levels | 4122.077 ms | 962.487 ms | 4.28x |

GFS native peak RSS medians were 187,170,816 / 163,020,800 bytes
(178.500 / 155.469 MiB uncached/warm). EC medians were 88,227,840 /
45,645,824 bytes (84.141 / 43.531 MiB). These are process RSS observations,
not Python memory comparisons or whole-system peak memory.

The GFS bbox retains the complete original 0.25-degree model domain.
The EC bbox remains `[118,125,35,28]`; all selected cells and pressure fields
are retained while the original three raw files total about 84.6 MB.
This avoids re-decoding those larger raw files, not processing fewer cells
than the uncached arm. Neither test is a nationwide multi-observer forecast
acceptance run, even though GFS has a broad source domain.

Single initial cache-miss observations were 2134.915 ms GFS and 4752.084 ms EC.
They include building/publishing the durable cache as well as the complete
point output. One miss is not a cold-latency distribution or OS-cold measure.

Selected phase medians, in milliseconds:

| Phase | GFS Uncached | GFS Warm | EC Uncached | EC Warm |
| --- | ---: | ---: | ---: | ---: |
| Decode and initial raw-source identity | 427.740 | 7.835 | 3703.286 | 46.688 |
| Canonical packing, validation and derivation | 115.893 | 0 | 8.305 | 0 |
| Decoder/library/resource fingerprint | 0 | 450.701 | 0 | 445.377 |
| Sum of per-frame verified cache lookups | 0 | 308.598 | 0 | 57.440 |
| Fresh profile-child output/verification | 225.797 | 224.353 | 116.117 | 116.791 |
| CAMS assembly | 122.349 | 123.992 | 115.519 | 116.290 |
| Native assessment | 9.657 | 9.817 | 7.512 | 7.403 |

Phase medians are not additive. Cache lookup includes locking, array/header
decoding and repeated whole-file checksums; raw-source verification also
remains in publication/output paths. Keeping those checks explains part of
the GFS slowdown rather than being excluded from the reported total.

### Complete EC Service Fixture

The Stage 23 six-cell loopback fixture was rerun with a fourth arm that
explicitly enables the derived cache. Five cold/warm trials per arm retain
all 16 main arrays, pressure children, provenance, HTML and verified
cross-section service output:

| Workload | Empty Raw/Derived Cache | Warm Raw/Derived Cache |
| --- | ---: | ---: |
| Existing Python Web service, forced recompute | 387.452 ms | 68.132 ms |
| Rust point, derived cache disabled | 554.572 ms | 480.881 ms |
| Rust service, derived cache disabled | 568.342 ms | 496.570 ms |
| Rust service, verified derived cache enabled | 1107.104 ms | 887.811 ms |

Python remains warm in-process; native arms start fresh processes and produce
durable native artifacts. The production Rust HTTP/response-cache route is
still unfinished. These are observed workloads, not an equal production
speed ratio. The benchmark's selected report guard is supplementary; complete
cached/uncached arrays, report/quality/dominant diagnostics, HTML and
cross-section compatibility have separate tests.

### Measured Optimization And Default Decision

The initial implementation fingerprinted every effective ecCodes resource
serially. Release
`ffd952e5b6d955f5a42b76d5fccb7d1e0c645822a3f4fa23c6482cd0665304ec`
measured six-cell warm service 1222.882 ms, including 785.530 ms for its
resource fingerprint. It measured GFS uncached/warm 1285.962/1812.145 ms
and EC uncached/warm 4203.806/1276.023 ms.

The final implementation resolves/deduplicates resource targets and uses
at most four native I/O hash workers, folding results in deterministic path
order. Every resource content checksum is retained. The repeated six-cell
warm fingerprint median fell to 457.721 ms and warm service to 887.811 ms.
These are independent five-trial local runs, not an isolated CPU
microbenchmark or a general speedup guarantee.

EC's larger raw-file decode workload benefits substantially from the cache.
GFS and the six-cell service do not: integrity/readback overhead outweighs
the saved model work here. Therefore the cache remains **explicit opt-in**
(`derived_cache: {}` or an explicit directory); missing/null stays disabled.
Do not enable it globally based only on the EC improvement.

Remaining justified optimization work includes representative long-lived
runtime/resource-residency measurements, validated pressure-array readback,
packaged immutable decoder-resource identity, and the distinct production
response-cache/HTTP lifecycle. Do not drop checksums, shrink the source domain,
or omit diagnostic arrays to turn a negative result into a positive one.

Raw samples and retained artifacts:
`/tmp/firesky-stage24-{gfs,ec,service}-{verified,final}.json` and the
corresponding directories. Reproduce with fresh destinations:

```sh
.venv/bin/python scripts/bench_derived_cache_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --cache-dir /tmp/firesky-stage24-gfs-new --model gfs --trials 5
.venv/bin/python scripts/bench_derived_cache_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --cache-dir /tmp/firesky-stage24-ec-new --model ecmwf-basic --trials 5
.venv/bin/python scripts/bench_ecmwf_service_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --cache-dir /tmp/firesky-stage24-service-new --trials 5 --derived-cache
```

These checks establish neither throughput/CPU/P95 nor live-provider,
cancellation, clean-machine, desktop, cross-platform or Python-retirement
acceptance. All overall migration completion gates remain in effect.

## Stage 25: Complete EC Response Snapshot Reuse

Stage 24's derived cache cannot avoid solar/CAMS/scoring/report/service work
and was a net cost on the small EC fixture. Stage 25 adds a distinct
optimization already used by the owning Python service: reuse of a **complete
immutable response generation**. It retains every main/profile tensor,
report, diagnostic, HTML artifact and cross-section source, rather than
returning only a score or shrinking the domain.

`scripts/bench_response_cache_migration.py` measures the same native cached
service RPC on a response-cache miss, forced refresh and verified hit.
EC/CAMS raw inputs are seeded first; this experiment does not time live
downloads. Refresh performs full current input admission, computation,
durable output, checksummed receipt and atomic index publication. Hits
verify all stored array/JSON/HTML identities and inventory, but deliberately
do not inspect historical raw files or load ecCodes.

Both modes return the complete inline native service view, so the timed
fresh-child interval includes process startup, full result serialization
and client JSON parsing. Five alternating refresh/hit trials reduce ordering
bias. Every main array and all per-hour 24-field pressure-profile arrays are
checked bitwise outside the timed interval; full report fields excluding the
generation-specific report path, native quality and cross-section response
are also checked. Separate profile timings use the original registered token
after subsequent refreshes. A final five-request resident session separates
existing process reuse from fresh-child latency; it starts after `hello` and
has both Python and ecCodes unavailable.

### Measured Release

Final macOS/aarch64 release SHA-256:
`4212140a79e5bccf98328465563306978daa057866d99dfdcb57cefc7996d2fe`.
Two independent five-trial runs, performed without a concurrent test suite:

| Operation | First Run | Repeat |
| --- | ---: | ---: |
| Initial response miss, raw caches seeded, single sample | 624.580 ms | 630.608 ms |
| Forced complete service refresh, fresh child, median | 637.687 ms | 647.273 ms |
| Verified immutable response hit, fresh child, median | 24.703 ms | 24.792 ms |
| Registered original-generation profile, fresh child, median | 26.089 ms | 27.392 ms |
| Verified response hit, resident session, median | 16.083 ms | 16.210 ms |

The repeat's fresh-child hit is about 26.1 times lower latency than its
forced-refresh operation under the stated snapshot semantics. Median process
peak RSS is 39.609 MiB for refresh, 9.656 MiB for a hit and 11.469 MiB for a
registered profile read. The complete resident five-hit session peaks at
10.406 MiB; that is one process peak, not per-request memory or production
concurrency evidence.

Resident-session measurements identify avoidable fresh-child overhead for
future production consumers. They use the existing sequential debug process;
they do not implement production concurrency, cancellation or desktop IPC,
and are not a direct numerical-compute optimization.

Retained traces and complete generations:
`/tmp/firesky-stage25-response-{final,repeat}.json` and the corresponding
directories. Reproduce with a new, nonexistent destination:

```sh
.venv/bin/python scripts/bench_response_cache_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --cache-dir /tmp/firesky-stage25-response-new --trials 5
```

These modes have different freshness semantics. A response hit is a
verified historical forecast, not a newly calculated forecast or proof that
today's raw EC/CAMS inputs are valid. Each result exposes
`immutable-snapshot-not-current-raw-validation`; TTL/force refresh are explicit.
Therefore do not describe a refresh/hit ratio as a Rust numerical-kernel
speedup, or compare it with Python forced recomputation as equivalent work.

The benchmark remains a six-cell, nine-pressure-level, twenty-event-step
synthetic local EC basic case, with 41 cross-section distances. It does not
establish national/full-domain bandwidth costs, P95, CPU/throughput,
production HTTP/desktop integration, live-provider or clean-machine
performance. Whole-generation checksum work scales with retained file bytes;
large-domain and long-lived consumer acceptance remains necessary before
selecting further optimizations or changing production defaults.

In particular, the registered Stage 25 reader currently verifies the entire
generation before calling the Stage 22 sparse two-frame reader. Its integrity
policy is conservative but can add substantial unrelated-array I/O on large
domains. Measure representative complete files before considering a
receipt-anchored, requested-member verifier; every consumed source/manifest/
profile checksum and change-during-read check must remain enforced. The small
profile timings above do not resolve this large-domain optimization question.

Old generations intentionally remain available to active readers. Production
quota/eviction, reader leases and task cancellation must be added without
breaking those immutable tokens. Disabling checksums or dropping profile
fields is not an acceptable cache-read optimization.

## Stage 26: Geographic Timezone RPC Batching

The new independent location service uses complete pinned geographic data,
not a fixed timezone or longitude approximation. Its prepared resource is
about 65 MiB, with 444 zones, 1356 polygons and 792 holes from timezonefinder
9.2.0 / data package 4.2026.6 / dataset 2026f. The input manifest SHA-256 is
`29e415aea527811932c8aa19cbf0cb2b02316a4eef3f9354afa0e19c92110d49`.
Resource preparation is a build step; the measured native child has Python
and ecCodes unavailable.

### Measured Bottleneck And Optimization

An exploratory 20,000-point scalar run took 3287.706 ms through native
JSON-lines RPC, versus 59.227 ms for the reference already in-process.
This exposed a poor bulk-call boundary, not a demonstrated Rust kernel
advantage. Each scalar acquisition reread/deserialized the geographic
manifest, and every point incurred request/response transport. The original
dispatch also built an unused location-LRU key and rehashed the manifest
for operations that were never memoized.

Two changes were implemented:

- Only fast search and metadata operations build the location-LRU key.
- `location/timezones` validates 1..4096 ordered points together, acquires
  the verified resident dataset once per batch and emits one shared
  provenance object with the ordered zone results.

Every original coordinate is validated before rounding; duplicates, exact
ordering, four-decimal rounding, geographic candidate priority, holes,
boundary ownership and failure behavior remain unchanged. No checksum was
disabled to obtain a faster result. Each batch reads the bounded manifest;
the admitted immutable arrays remain resident under their verified identity.

### Final Release Measurement

macOS/aarch64 release SHA-256:
`84eeb1df306430656358dc99c2b36cc57d696d466fc2aeafc87073e60817effd`.
Three trials, performed after the full test suite ended, use the same binary
and prepared resource. All 20,000 scalar and batch zone outputs match the
pinned reference on every trial.

| Measurement | Median |
| --- | ---: |
| First native timezone query after `hello`, full resource validation/load | 195.985 ms |
| 20,000 native scalar RPC queries, resident process | 2489.632 ms |
| 20,000 native batched RPC queries, five batches | 140.457 ms |
| Reference queries, already initialized Python in-process | 58.514 ms |
| Amortized native batched query rate, serial local client | 142,392 points/s |
| Native session peak RSS | 216.484 MiB |
| Native session user CPU time | 1.42 s |
| Native session system CPU time | 1.05 s |

Batching reduces elapsed time about 17.7 times relative to scalar RPC on the
same final release and identical points. It remains about 2.4 times slower
than this reference's in-process calls. Different transport boundaries mean
neither ratio establishes a Rust-vs-Python numerical-kernel speedup.

CPU/RSS cover the entire native session: initial data load, all scalar calls,
then all batch calls. They are not per-operation memory/CPU or concurrent
service measurements. Batch measurements reuse geometry already exercised
by the preceding scalar pass. First-query samples include full checksum and
data loading after `hello`, but exclude process startup and do not establish
cold OS-buffer behavior. Reference process RSS was not measured, so there
is no measured Python/Rust memory comparison.

The earlier three-trial trace ran alongside the focused location suite and
is exploratory only. Final uncontended evidence is
`/tmp/firesky-stage26-location-verified.json`; initial/exploratory traces
are `/tmp/firesky-stage26-location-initial.json` and
`/tmp/firesky-stage26-location-final.json`.

Reproduce against a newly prepared resource:

```sh
uv run --no-project --isolated --python 3.14 \
  --with timezonefinder==9.2.0 --with timezonefinder-data==4.2026.6 \
  python scripts/bench_location_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --timezone-manifest /tmp/firesky-timezones-new/manifest.json --trials 3
```

Further optimization should separate in-process geometry cost from JSON/
manifest/IPC overhead before choosing a different kernel or data layout.
The resident integer coordinates and lazily constructed floating-point
polygons have a measurable memory cost; copy reduction or bounded geometry
retention are candidates, not implemented improvements. Dropping holes,
simplifying boundaries or changing overlap priority is not acceptable.

These results establish only local geographic lookup behavior and costs.
They do not establish full-forecast speed, live geocoding/elevation provider
latency, production concurrency/P95, packaged resource delivery or native
desktop/HTTP acceptance. Overall Python retirement remains incomplete.

## Stage 27: Real Desktop Location IPC

`scripts/bench_desktop_location_migration.py` measures the new direct
native-client location transport against the owning Python `DesktopService`.
Both use persistent child processes and the same original search/metadata
JSON-lines payloads. Each trial verifies complete results before accepting
timings. The Python reference's hello deliberately excludes credential access
and region advertisement; it is a benchmark-only adapter, not a Rust runtime
fallback. The native child has Python and ecCodes unavailable.

### Measured Concurrency Boundary

The owning Python `location.get_json` holds a per-host session lock across
the request, status check and JSON parsing. Its four desktop workers therefore
do not perform four simultaneous requests to the same metadata host.
The bounded Rust location workers can use the shared native HTTP client
concurrently without that application-level serialization.

The benchmark issues eight distinct, uncached metadata calls with a simulated
50 ms response delay. Both servers use four workers. This isolates the new
concurrent I/O path; it is not real Open-Meteo latency, permission to ignore
provider rate limits, or a numerical-compute speedup.

### Final Measurement

Final macOS/aarch64 release:
`30e56fe712f0d789932d917c057669ff66256ec17690dc8f13515e87c56b81de`.
Five trials after regression tests ended, with 1000 search memo hits and 1000
metadata memo hits per child:

| Measurement | Rust Child Median | Python Reference Median |
| --- | ---: | ---: |
| Process startup through location-only hello | 8.875 ms | 273.629 ms |
| First search plus required metadata | 1.705 ms | 3.845 ms |
| Repeated process-local search hit, including IPC | 0.034 ms | 0.038 ms |
| First metadata query | 0.689 ms | 1.308 ms |
| Repeated process-local metadata hit, including IPC | 0.028 ms | 0.039 ms |
| Eight uncached metadata calls, 50 ms simulated provider delay | 110.958 ms | 451.137 ms |
| Process-session peak RSS | 5.406 MiB | 61.156 MiB |
| Process-session user CPU time | 0.03 s | 0.31 s |
| Process-session system CPU time | 0.02 s | 0.06 s |

The eight-call latency ratio is about 4.07, consistent with removing
same-host serialization while retaining bounded four-worker admission.
Single-hit differences are only tens of microseconds; they do not justify
changing cache freshness rules or adding a more complicated cache.

An independent five-trial run with 100 repetitions measured 109.709 ms
versus 455.498 ms for the same eight-call test, with similar memo latency.
Its native CPU samples rounded to zero at the timing tool's precision,
which does not mean zero CPU work. The 1000-repetition run above provides
more useful process-session CPU samples.

### Scope And Remaining Optimization

The location child is only a subset of the eventual Rust desktop service.
The Python reference imports the owning desktop service's modules; the new
Rust subset is not a full forecast implementation. Its smaller footprint
and startup time must not be represented as full-application performance.
The currently opt-in hybrid client still starts the main Python child, so
these measurements do not establish lower application startup or combined
application RSS. Both native and legacy GUI transports still need final
production lifecycle acceptance.

No offline geographic resource or SRTM download is used in this IPC benchmark.
Both get valid local simulated online responses. In particular, its 5.406 MiB
Rust RSS is not the roughly 216 MiB full-world resident-geometry case from
Stage 26. The final release separately rechecked that complete resource over
20,000 points and three trials with zero scalar/batch differences:
`/tmp/firesky-stage27-geography-verified.json`.

CPU/RSS include process startup, all memo requests and the eight concurrent
queries. They do not identify per-request or per-kernel CPU cost. Results
do not establish live-provider throughput, rate-limit handling, P95,
full forecast/nowcast performance, GUI interaction, clean-machine packaging
or Windows deployment.

The demonstrated optimization is bounded concurrent native I/O plus reuse of
one persistent location process/client/cache, not dropping metadata or
loosening source validation. Further useful work is the remaining desktop
preflight/frozen-plan/confirmation/task lifecycle and eventual removal of the
main Python child. Bulk geographic memory/layout and full-profile I/O remain
separate measured boundaries; this local online fixture does not resolve them.

Final trace: `/tmp/firesky-stage27-location-wire-1000.json`.
Independent repeat: `/tmp/firesky-stage27-location-wire-verified.json`.
The one-trial initial trace ran alongside focused tests and is exploratory.
Reproduce without a concurrent test suite:

```sh
.venv/bin/python scripts/bench_desktop_location_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --trials 5 --repeats 1000
```

## Stage 28: Complete Desktop Inventory

`scripts/bench_desktop_inventory_migration.py` compares the original
`desktop_data.inventory` with the native desktop child over persistent
JSON-lines transports. Each backend starts three independent processes,
performs five complete scans per process, and alternates backend order between
trials. The full regression suite finished before measurement; the two fixture
modes ran separately.

Final release SHA-256:
`337d38e2b123ab15a6836177283ae26f7f01847f5e37ac1b08bcde72feced865`.
All 30 complete responses per fixture matched, including every GFS coverage
entry, EC duplicate-hour path, CAMS area and cycle field. Only unordered
area-key sets are normalized before comparison. Timings include wire transfer
and client JSON parsing, and exclude full-response comparison.

### Synthetic Metadata Fixture

Each source has 4000 raw files (12,000 total), including zero-length files and
unindexed GFS metadata. EC duplicate paths exercise the reference traversal
order. CAMS has 4000 distinct area keys and matching size/mtime area sidecars.
The raw payloads are intentionally invalid GRIB: this measures complete
inventory metadata handling, not field decoding. Python and ecCodes are both
unavailable in the native child.

| Measurement | Rust Child Median | Python Reference Median |
| --- | ---: | ---: |
| Startup through hello | 7.757 ms | 190.337 ms |
| First complete scan per process | 787.296 ms | 988.717 ms |
| Resident scan | 298.531 ms | 934.393 ms |
| Process-session peak RSS | 15.281 MiB | 73.141 MiB |
| Process-session user CPU | 0.67 s | 1.45 s |
| Process-session system CPU | 1.31 s | 3.50 s |

Resident scan time is about 3.13 times as fast; first-scan time is about
1.26 times as fast. Both cases remain relevant: the native area's first
population has a visible cost, so the resident result must not be presented
as first-use performance.

### Existing Workspace Cache

The read-only run scanned 52 GFS files across six cycles, eight EC files in one
cycle, and 81 CAMS files across four cycles (141 total). Six CAMS area keys
were resolved, with no null area results. Native Python execution remained
unavailable; the native ecCodes library was allowed if a first-message area
lookup needed it.

| Measurement | Rust Child Median | Python Reference Median |
| --- | ---: | ---: |
| Startup through hello | 8.606 ms | 189.687 ms |
| First complete scan per process | 4.417 ms | 98.263 ms |
| Resident scan | 3.795 ms | 15.954 ms |
| Process-session peak RSS | 5.672 MiB | 61.125 MiB |
| Process-session user CPU | 0.00 s | 0.27 s |
| Process-session system CPU | 0.01 s | 0.10 s |

Resident scans are about 4.20 times as fast. The much larger first-scan
difference also includes lazy imports and per-process initialization in the
owning Python reference; it is not a pure filesystem traversal comparison.
The native user-CPU sample rounds to zero at the timing tool's precision.

The reference's pending CAMS area-index exit publication is disabled for this
benchmark. Names, sizes and nanosecond mtimes of existing regular cache files
match before and after all scans. This check does not hash every raw byte,
and neither inventory proves file integrity or forecast readiness.

### Implemented Optimization And Limits

The native scanner enumerates each root once, reuses raw-file metadata for
counts and cycle aggregation, and retains a bounded metadata-keyed area LRU.
It reads bounded GFS sidecars and only the first GRIB message when an area
requires native decoding. Complete inventory results are never memoized:
every request observes a new filesystem scan.

The legacy implementation separately traverses source indexes and raw totals
and repeats metadata work. Reducing this work preserves the original payload
and file-selection behavior. No extra scanner workers were added; both
measurements retain the existing bounded desktop admission rules.

These are warm-filesystem, single-request subset measurements. The synthetic
fixture is populated just before timing; the existing cache is also examined
for the before/after check. Neither is cold-storage performance, P95,
concurrent throughput, large-production-cache acceptance or GUI latency.
Process CPU/RSS include startup and all scans, rather than an isolated scan.
Real area decoding still depends on ecCodes C, so the result does not prove
a C-free application. The opt-in client still starts its main Python process;
the RSS/startup figures are for each measured child, not the whole application.

The remaining desktop preflight, frozen confirmation/session plans and task
lifecycle are the next migration boundary. Further changes to the area-cache
lookup structure or path resolution should be profiled and measured separately;
these traces do not isolate their costs. Removing freshness checks or caching
the whole inventory would change the contract.

Final traces:
`/tmp/firesky-stage28-inventory-synthetic-verified.json` and
`/tmp/firesky-stage28-inventory-real-verified.json`.
Reproduce each mode separately, without a concurrent test/build:

```sh
.venv/bin/python scripts/bench_desktop_inventory_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --files-per-source 4000 --trials 3 --repeats 5

.venv/bin/python scripts/bench_desktop_inventory_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --existing-workspace "$PWD" --trials 3 --repeats 5
```

## Stage 29: Resolved Desktop Plan Checks

`scripts/bench_desktop_preflight_migration.py` compares complete original
dual-model cache-check reports for identical already resolved plans. The
native engine runs with Python unavailable. The owning Python reference uses
its original checkers, while the production opt-in bridge delegates cache
checks to the native engine. This benchmark excludes raw-request event/domain/
terrain planning, publication I/O, confirmation sessions and forecast execution.

Four paths run in rotating order over three trials and five checks per
process: persistent native, persistent Python reference, optimized production
bridge, and the original transient bridge. The last path is reconstructed by
a benchmark-only override to the old per-call subprocess transport; it is
not a product fallback or another backend selector.
Resident measurements exclude the first check: take each trial's median of
its remaining four checks, then the median across three trials.

Final native release SHA-256:
`1bccf58e61d989c70be7cac23bb5231845ca828d84466519d0eb914243e818c5`.
The tests/build are not running during either measured fixture. Every one of
60 complete reports per fixture matches, including per-model requirements,
missing keys/counts, local/publication labels and model/event windows.
The common GFS metadata index is built before all timers, so index creation
or rebuild cost is excluded uniformly.

### Large Fast Checks

The fixture has 12,000 background raw files plus six required files (12,006
total): 4002 per source. Background CAMS timestamps are unrelated to the event.
Two target files per source satisfy the original metadata admission contract.
These raw payloads are not valid meteorological fields. Native ecCodes and
Python execution are both unavailable; fast CAMS exact-area selection must
not accidentally become full content decoding.

| Measurement | Native | Python Reference | Optimized Bridge | Transient Bridge |
| --- | ---: | ---: | ---: | ---: |
| Startup through hello | 7.913 ms | 270.543 ms | 267.941 ms | 270.859 ms |
| First check per process | 265.310 ms | 473.195 ms | 261.365 ms | 258.645 ms |
| Resident check | 245.452 ms | 471.045 ms | 247.945 ms | 270.446 ms |

The native resident check is about 1.92 times as fast as Python; the actual
optimized bridge is about 1.90 times as fast. Bridge reuse improves on the
transient bridge by about 1.09 times in this filesystem-heavy fixture.
First checks have ordinary trial variation; these three trials do not
establish a first-check speedup from connection reuse.

For the standalone child processes, peak RSS is 10.938 MiB native versus
67.453 MiB Python. Session user CPU is 0.16 s versus 1.03 s, and system CPU
is 1.08 s versus 1.64 s. Native system CPU dominates its user CPU here.
That supports profiling remaining filesystem work before adding more
computation parallelism; it does not identify a particular syscall hotspot.

### Strict Checks And Implemented Optimization

The 306-file fixture has 100 unrelated background files per source plus
the six required files. Its two target CAMS files contain real complete
compact GRIB grids. Strict checks validate their optical contents through
native ecCodes; GFS/EC remain the original metadata-based checking boundary.
An accepted check still does not prove those model fields can forecast.

| Measurement | Native | Python Reference | Optimized Bridge | Transient Bridge |
| --- | ---: | ---: | ---: | ---: |
| Startup through hello | 9.102 ms | 269.158 ms | 269.207 ms | 265.447 ms |
| First check per process | 186.417 ms | 203.821 ms | 193.919 ms | 195.646 ms |
| Resident check | 7.809 ms | 17.867 ms | 8.774 ms | 194.884 ms |

The observed problem was repeated strict-check initialization. A fresh
engine for every bridge call reloads ecCodes and discards the native
file-version validation cache. Reusing one bounded connection preserves those
validated file versions while still rescanning/rechecking every request.
Resident bridge latency falls from 194.884 ms to 8.774 ms, about 22.21 times
as fast as the old transient bridge. The optimized bridge is about 2.04 times
as fast as the Python reference for this repeated unchanged-plan fixture.
The standalone native result is about 2.29 times as fast as Python.

The first strict check still costs roughly 194 ms through the optimized
bridge. The 22.21 ratio is a resident-file result, not a first-use improvement
or a guarantee for new files/areas/event windows. File changes can require
fresh optical validation. No complete ready report is memoized.

Standalone peak RSS is 24.750 MiB native versus 79.172 MiB Python. Session
user CPU is 0.17 s versus 0.46 s, and system CPU 0.04 s versus 0.11 s.
These values include startup, the initial decode and all repeated checks;
they do not isolate per-check optical CPU.

### Fidelity, Lifecycle And Limits

Source snapshots share the existing bounded inventory traversal without
inventory-only area lookups. CAMS filenames are parsed once per check and
reused for the union of required times across both independent model plans.
The bridge transfers that exact bracket union rather than every regional
point's intermediate solar timeline.

The persistent adapter serializes wire requests with unique IDs and bounds
active/queued calls to eight. It drains stderr into a bounded tail, rejects
oversized/malformed/mismatched responses, reconnects for changed executable/
environment identities, and fails a transport-broken call without retrying it.
Only a new independent check can reconnect. Tests exercise concurrent
admission, external exits, valid operation errors, EOF/framing failures,
large diagnostics, Python exit cleanup and independent post-fork connections.
Real-file tests verify changed CAMS bytes invalidate readiness within the
same reused child.

The benchmark retains fresh filesystem scans and existing GFS index checks.
Metadata-keyed optical reuse is the original file-version contract, not a
whole-raw SHA guarantee. Filesystem caches are warm after fixture preparation,
and only two target CAMS grids are decoded. This does not establish cold
storage, production cache sizes, concurrent throughput, P95 or live-provider
acceptance. Identical complete reports do not prove a raw user request was
converted to the correct plan; native raw-request planning is still unfinished.

The bridge's wrapper-reported RSS is not the sum of its Python and Rust
processes. Do not use its roughly 61 MiB parent-sized samples as combined
application memory. The application still launches Python, and real compact
GRIB decode still depends on the ecCodes C library. Neither C-free execution
nor full-application startup/memory/forecast performance is demonstrated.
Native-client session ownership and asynchronous operation cancellation remain
separate migration work.

Final traces:
`/tmp/firesky-stage29-preflight-fast-verified.json` and
`/tmp/firesky-stage29-preflight-strict-verified.json`.
Both stderr logs are empty. Early single-trial smoke runs overlapped tests
and are exploratory, not the values above.

### Stage 30 Raw Context Planning

Stage 30 keeps raw planning in the same persistent Rust child and transfers
large regional results through a temporary hashed JSON file. This avoids
serializing a 36,685-point plan through the 1 MiB RPC line while retaining
source and output identity checks. No Stage 30 end-to-end performance claim
has been published yet: the measured Stage 29 cache-check tables remain the
current benchmark baseline, and raw planning still needs representative
China, multi-region, timezone-resource and terrain-cache measurements.

Stage 31 reuses that persistent child for nowcast context and GFS/CAMS cache
admission. HSD frame lookup remains in the Python provider, so no combined
nowcast latency or memory claim is published yet. A separate benchmark must
include compact-frame discovery, GFS cache admission and the native/Python
boundary before reporting an end-to-end result.

Stage 32 keeps the same boundary for preparation planning. Rust now resolves
the GFS prior bbox/hour set and CAMS bracket set, but Python still performs
provider lookup, downloads, GRIB decode and strict cache validation. No
nowcast preparation speedup or memory reduction is inferred from moving this
small deterministic plan across the persistent child.

Stage 33 moves only compact HSD cache discovery and strict NPZ validation into
the resident Rust child. No nowcast end-to-end benchmark, provider-download
benchmark or memory claim is published yet: remote HSD listing/range I/O,
calibration, cache writes, frame construction and scoring remain Python.
On a warm local fixture containing two valid 300 x 300 float32 frames, the
first Rust bridge call (process startup plus strict NPZ validation) took
30.799 ms. After that, the resident file-version cache reduced the Rust
bridge median to 0.233 ms across twelve reads, versus 2.901 ms for the Python
compact provider (about 12.4x faster for this repeated cache-read subset).
The cache stores only validated metadata keyed by path/size/mtime; replacing a
same-named frame invalidates it and re-runs strict validation. These figures
exclude remote HSD listing/download, cache writes, frame construction and
scoring, and do not establish complete nowcast latency or memory reduction.
The next measurement should include stale/invalid candidates and the complete
preparation boundary before claiming a user-visible nowcast speedup.

```sh
.venv/bin/python scripts/bench_desktop_preflight_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --files-per-source 4000 --trials 3 --repeats 5

.venv/bin/python scripts/bench_desktop_preflight_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --files-per-source 100 --trials 3 --repeats 5 --strict
```

## Stage 34: HSD Frame Export

This microbenchmark uses two valid 300 x 300 float32 compact HSD frames
(latitudes, longitudes and B07/B13/B15), a persistent native JSON-lines child
and the same Python tensor-bundle reader used by the opt-in provider. It does
not include remote S3 listing/range downloads, Satpy calibration, GFS prior
interpolation, cloud-column retrieval or complete forecast assembly.

| Measurement | Rust/Python export path | Python NPZ provider |
| --- | ---: | ---: |
| First call, including child startup and bundle creation | 99.6 ms | 3.7 ms |
| Resident repeated call median | 0.65 ms | 2.89 ms |

The first native call is slower because it validates the NPZ, decodes it,
writes five NPY arrays plus a manifest and performs the initial Python bundle
hash/load. After the optimization, Rust retains decoded arrays within a
128 MiB process budget, source-keyed bundles are reused, and Python retains
validated frame objects using the bundle file version. The measured resident
path is about 4.4 times faster than the Python provider for this repeated
read subset. Replacing a same-named compact frame changes the source key and
forces a fresh decode; a frame larger than the resident budget is intentionally
not retained.

This is not an end-to-end nowcast speedup claim. It measures already-written
local frames and excludes all remaining Python-owned preparation, network,
calibration, motion/advection and scoring work. The native opt-in selector is
`FIRESKY_HSD_BACKEND=rust`; default production behavior remains Python until
the full nowcast workflow has differential and lifecycle acceptance.

## Stage 35: HSD Motion

This microbenchmark uses two source-bound 300 x 300 float32 HSD bundles with a
known B13 translation. It compares the Python NumPy phase-correlation helper
with the RustFFT RPC through the native provider. It excludes frame export,
remote HSD I/O, calibration, advection and cloud-column scoring.

| Measurement | Rust motion path | Python NumPy |
| --- | ---: | ---: |
| First native motion call, including bundle verification | 13.9 ms | 7.6 ms |
| Resident repeated motion median | 0.29 ms | 3.59 ms |

Rust is slower for a single uncached 300 x 300 calculation because NumPy's
FFT is backed by highly optimized native kernels and the first Rust call
verifies the complete bundle files. The bounded native result cache reduces
repeated identical diagnostics to sub-millisecond latency, about 12 times
faster than the Python helper in this repetition pattern. This is not a
single-request end-to-end nowcast speedup claim; it is the cost of moving the
deterministic motion contract into the Python-free engine. Keep the native
selector opt-in until advection and the full nowcast lifecycle are migrated.

## Stage 36: HSD Advection

This microbenchmark uses two valid 300 x 300 float32 HSD bundles and a
one-hour strong-motion plan. The first native provider call includes source
bundle verification, bilinear projection, five-array tensor-bundle
publication and Python validation of the derived bundle. Repeated calls use
the bounded provider cache after checking all input/output manifest and array
file versions.

| Measurement | Rust provider | Python bilinear helper |
| --- | ---: | ---: |
| First projected frame | 43.9 ms | 11.0 ms |
| Repeated identical projection median | 0.07 ms | 9.4 ms |

The first native call is slower because it creates a durable derived bundle
and validates it. The resident path is about 130 times faster than recalculating
the Python projection because the provider returns the already-validated
frame without another IPC or array pass. Replacing any source/derived bundle
file invalidates the cache. These figures exclude remote HSD listing/range
downloads, calibration, vertical-prior retrieval, cloud-column inference and
complete nowcast scoring; they are not an end-to-end application speedup
claim.

## Stage 37: HSD Cloud-Column Retrieval

This benchmark uses one synthetic local 3 x 3 HSD frame, one fixed four-level
vertical prior and the persistent JSON-lines engine. It measures the
deterministic posterior plus Python wrapper reconstruction; it excludes remote
HSD I/O, Satpy calibration, GFS interpolation and complete nowcast assembly.
The release binary was
`f809474bbc08aebcbfa4fccd5416f2ee2eddb2177fd92ba82979a52236081528`.

| Samples in one batch | First native call | Resident native median | Python median | Native/Python |
| ---: | ---: | ---: | ---: | ---: |
| 25 | 8.12 ms | 1.23 ms | 0.31 ms | 3.99x |
| 256 | 16.56 ms | 9.71 ms | 2.91 ms | 3.33x |

The first native call includes starting the Rust child. The resident path
uses the compact v2 response and the bounded validated-bundle contract cache;
the Python path performs the same posterior for each sample in-process. Rust
therefore has a clean migration/failure boundary but does not yet win this
end-to-end microbenchmark. The measured gap is transport, JSON/object
construction and the current Python-side sample/prior assembly, not evidence
that the Rust algorithm is intrinsically slower. A binary or shared-memory
sample/prior transport, or moving sampling and prior packing into the native
operation, is the next optimization class. Do not claim a whole-nowcast
speedup from this stage.

Reproduce with:

```sh
.venv/bin/python scripts/bench_hsd_retrieval_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --samples 25 --trials 20

.venv/bin/python scripts/bench_hsd_retrieval_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --samples 256 --trials 10
```

## Stage 38: Production Desktop Inventory Bridge

This benchmark extends `scripts/bench_desktop_inventory_migration.py` with a
third equal-wire path: the production `DesktopService.inventory` route using
`FIRESKY_DESKTOP_DATA_BACKEND=rust`. The direct native child and Python
reference remain in the comparison. It creates 4000 raw files per source
(12,000 total), performs three trials and five scans per process, checks the
complete response on every scan and alternates backend order. Only unordered
CAMS `area_keys` are normalized.

Release SHA-256:
`851c0e65f4baf7eaf9fd23430e05e38c998a03812f6f42c023083c18f11948b9`.

| Measurement | Direct Rust child | Python reference | Production Rust bridge |
| --- | ---: | ---: | ---: |
| Startup through hello | 7.09 ms | 188.59 ms | 265.97 ms |
| First complete scan | 749.46 ms | 991.24 ms | 797.45 ms |
| Resident scan | 290.29 ms | 896.25 ms | 313.39 ms |
| Peak RSS | 14.75 MiB | 73.23 MiB | 66.55 MiB |
| User CPU | 0.67 s | 1.43 s | 1.02 s |
| System CPU | 1.22 s | 3.33 s | 1.28 s |

All 45 complete responses matched. The production bridge retains most of the
Rust scan improvement: resident scanning is about 2.86 times faster than the
Python reference and adds about 23 ms over the direct native child. The
bridge's startup and RSS still include the Python `DesktopService`; these
numbers are not a Python-free application result. The measured bridge
overhead is a service/JSON boundary, so further inventory micro-optimization
would have lower value than moving the desktop session lifecycle itself into
Rust.

Reproduce:

```sh
.venv/bin/python scripts/bench_desktop_inventory_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --files-per-source 4000 --trials 3 --repeats 5
```

## Stage 39: Production Desktop Location Bridge

This benchmark extends `scripts/bench_desktop_location_migration.py` with
the production `DesktopService` using the Rust `desktop_location` operation.
All three paths use the original search/metadata wire payloads, persistent
children and a local simulated provider. Each trial checks complete results,
including eight uncached metadata calls with a controlled 50 ms response
delay. The release binary was
`299fbe88542d6c6f806db952d4018ca32a0829a5c87e861bf27425eef16efab2`.

| Measurement | Direct Rust child | Python reference | Production Rust bridge |
| --- | ---: | ---: | ---: |
| Startup through hello | 8.29 ms | 276.19 ms | 273.54 ms |
| First search | 1.58 ms | 4.21 ms | 9.60 ms |
| Memoized search | 0.041 ms | 0.045 ms | 0.266 ms |
| First metadata | 0.508 ms | 0.821 ms | 0.877 ms |
| Memoized metadata | 0.034 ms | 0.042 ms | 0.241 ms |
| Eight uncached metadata calls | 114.50 ms | 484.17 ms | 465.05 ms |
| Peak RSS | 5.81 MiB | 61.38 MiB | 61.31 MiB |

The production bridge retains the Rust concurrent-provider benefit for the
uncached eight-call path, but memo hits are slower because every hit crosses
the Python service and JSON boundary. Its RSS is still the Python service's
RSS, so the direct child numbers are not application startup or memory
claims. The local provider and fixed delay do not establish live service
throughput, rate-limit behavior, P95 or GUI acceptance. Adding another
Python-side cache would duplicate Rust's bounded location memo and alter
freshness semantics; the higher-value optimization is moving the desktop
session lifecycle into Rust.

Reproduce:

```sh
.venv/bin/python scripts/bench_desktop_location_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --trials 3 --repeats 20
```

## Stage 40: Native Desktop Session Authority

The session boundary is primarily a correctness and ownership migration, so
the useful optimization is the request shape rather than another local cache.
`desktop_session.validate` returns only the contract, token and model kind;
it does not copy the potentially large frozen payload/report snapshot across
the Python/Rust bridge. Complete snapshots remain on create/update/consume,
where they are required to establish or mutate authoritative state.

The native store is process-local and owner-scoped. A Rust child restart
therefore invalidates existing tokens, which is conservative for confirmation
security. The Python compatibility mirror remains in the production bridge,
so this stage is not a Python-free RSS or end-to-end forecast benchmark.
The release binary used for the implementation has SHA-256
`a375c972739318c39655e5008839921ffcfe1fddaee01f674ddea30a29eabcb6`.

The synthetic 100-cycle benchmark uses an expanded local report (three
datasets, missing-key lists and model requirements), five trials and no
provider I/O. It measured:

| Measurement | Direct Rust child | Python reference | Production Rust bridge |
| --- | ---: | ---: | ---: |
| Complete create/validate/update/consume cycle | 0.280 ms | 0.068 ms | 1.003 ms |
| Native `validate` only | 0.019 ms | n/a | n/a |
| Native full `get` snapshot only | 0.066 ms | n/a | n/a |

The production bridge is slower for this tiny synthetic lifecycle because
each complete snapshot mutation still crosses Python and JSON. The important
optimization is that normal entry validation transfers only token/kind:
the direct Rust `validate` response is about 3.5 times cheaper than the full
snapshot read in this fixture. These are component medians, not forecast
throughput or P95 claims.

Reproduce:

```sh
.venv/bin/python scripts/bench_desktop_session_migration.py \
  --engine-bin engine/target/release/firesky-engine \
  --cycles 100 --trials 5
```

## Stage 41: Desktop Publication Probing

Publication probing is dominated by provider response time and availability,
not by cycle arithmetic or JSON serialization. The native operation therefore
uses a five-minute resident cache per source/hour and probes only concrete
artifacts: one GFS `f006` object per candidate, one ECMWF index per mirror and
one CAMS constraints selection when ADS credentials are available. A failed
probe follows the existing heuristic path and is explicitly unverified.
Requested sources are probed in bounded scoped workers, so network timeout
cost is approximately the slowest source rather than the sum of all source
timeouts.

No stable speedup claim is made for this stage. The old Python implementation
also issued concurrent provider probes, and the native operation preserves
that fan-out while keeping one bounded request contract. The meaningful
optimization is ownership and cache reuse in the persistent engine;
end-to-end latency remains controlled by upstream network behavior.
Forecast throughput, P95, provider rate limits and clean-machine deployment
are outside this local contract test and still require the full desktop
workflow benchmark.

## Stage 42: Desktop Model Preparation

Native preparation now owns the model/CAMS provider boundary, but it is not
yet a benchmarked end-to-end forecast. The operation performs a terrain
prepass, reruns the event-hour plan, downloads GFS or ECMWF inputs, and
resolves CAMS brackets in one Rust process. Model-file resolution is currently
sequential by design: the dominant costs are remote transfer and provider
queueing, while deterministic cache-lock ordering makes a baseline easier to
audit. The `model_source="both"` path runs two independent native requests
from Python and retains the existing response shape.

No speedup claim is made from the bridge alone. The Python service still
owns request/session locks and forecast scoring, and the native operation
currently reports only coarse start/end progress. A useful optimization pass
needs a representative cold/warm fixture with identical raw inputs, provider
rate limits, total wall time, peak RSS and CPU before introducing bounded
parallel model downloads or finer-grained cancellation.

## Stage 43: Desktop Nowcast Preparation

The native nowcast preparation path keeps remote HSD discovery in Python but
moves the post-cache GFS/CAMS preparation and compact-HSD gate into Rust. The
operation includes the HSD frame timestamps when deriving required GFS hours,
so it preserves the old warm-up coverage rather than only preparing the
five-minute event samples. GFS file resolution and CAMS target handling remain
sequential for deterministic cache-lock and failure ordering.

The local ecCodes-backed fixture confirms correctness but is not a performance
benchmark: it contains no remote HSD transfer, ADS queue wait or GFS transfer.
No speedup claim is made. Before parallel downloads or cancellation-aware
transfer loops are introduced, measure cold/warm nowcast preparation with the
same compact frames and raw files, recording wall time, peak RSS, CPU and
provider request counts for the Python reference and native bridge.

## Stage 44: Desktop Point Forecast Execution

The stored-input point benchmark exercises the complete native validation,
model decode, CAMS assembly, profile output, frame/core assembly and point
materialization path. It is intentionally not the full desktop service or
public HTTP forecast: it performs no provider selection/download and Rust
writes durable checked bundles.

On the pinned local inputs used for this stage (three trials, one point), the
medians were:

| Measurement | Python reference | Rust native |
| --- | ---: | ---: |
| GFS stored point path | 792.39 ms | 1325.10 ms |
| ECMWF basic stored point path | 5598.30 ms | 4091.65 ms |

The EC path is about 1.37 times faster in this fixture. The GFS path is about
1.67 times slower overall; its native assessment and point materialization are
only about 10 ms each, while model profile/solar work and profile bundle
verification dominate the durable output path. The comparison is not equal-I/O
because the Rust path writes and verifies manifests, arrays and profile
children, so it is not a claim about pure algorithm throughput.

Two measured, low-risk optimizations are now in the production bridge:

1. Native desktop point requests enable the content-addressed derived-frame
   cache under the configured model cache. Warm requests reuse validated
   decoded/derived frames while preserving source, cycle, bbox and decoder
   identity checks.
2. The Python bridge calls `load_tensor_bundle(..., mmap=True)`. It still
   hashes and validates every tensor file, but does not copy unused arrays into
   the Python heap.

The reproducible component benchmark is:

```sh
.venv/bin/python scripts/bench_assessment_migration.py \
  --pinned-model gfs --trials 3 --point-forecast
.venv/bin/python scripts/bench_assessment_migration.py \
  --pinned-model ecmwf-basic --trials 3 --point-forecast
```

The next high-value GFS optimization is to reduce duplicate profile
serialization/verification or move cross-section assembly into the same
native request. That requires a new artifact/transport contract and another
full-output differential run; no speculative parallelism was enabled in this
stage.

## Stage 45: Regional Frames and Desktop Nowcast HSD

The regional desktop path reuses the Rust frame artifact rather than copying
assessment tensors through Python. The bridge still serializes the final JSON
result and writes the existing gzip cache, so this stage has no end-to-end
latency claim yet. A local ecCodes-backed two-point GFS fixture exercises the
actual Rust frame writer and the Python identity/cache adapter; it contains no
provider transfer or production regional grid.

Desktop Rust selection now makes the calibrated HSD provider and posterior
native by default when no explicit HSD backend override is set. This removes
an accidental Python retrieval path from native desktop nowcast requests, but
GFS prior decoding and event scoring remain Python-owned. No whole-nowcast
speedup claim is made until a complete native prior/scoring request is
available and measured with identical cached HSD/GFS/CAMS inputs.

## Stage 46: Public Regional Rust Route

The stored-frame benchmark on the pinned one-point GFS fixture (`--frames`,
three measured trials) reports a Python median of **781.31 ms** and a Rust
median of **1294.21 ms**. Rust is about **1.66x slower overall** in this
comparison because it also writes and verifies durable profile/frame bundles;
the native assessment itself is about **9.3 ms**.

The dominant Rust phases are model profile/solar preparation at about
**760 ms**, model decode/source hashing at about **411 ms**, and profile
write/verification at about **218 ms**. The next measured optimization target
is reducing duplicate profile serialization/verification and increasing warm
derived-cache reuse. Provider transfer and the persistent desktop/public
bridge are outside this fixture, so it is not an end-to-end HTTP latency claim.

The current regional bridge therefore disables the point-oriented derived
cache. In the dedicated cache benchmark, uncached stored frames had a median
of about **1272 ms**, while derived-warm frames were about **1479 ms**; the
warm cache reduced peak RSS but increased latency. Point forecasts retain the
cache because their access pattern and output contract differ.

## Stage 47: Native HSD Metric Aggregation

This stage moves the deterministic metric aggregation that follows HSD cloud
column retrieval into Rust. It has no standalone performance claim yet: the
same native HSD batch already carries the columns, so the useful result is
one fewer Python numerical pass and a versioned v3 response contract rather
than a separately measured end-to-end nowcast speedup.

The parity fixture covers both cloudy and clear inputs and checks the full
metric dictionary. Unknown columns remain excluded from usable probability
samples, while a non-empty batch with no usable probabilities keeps the
Python reference's neutral composition value. GFS prior decoding, final
event scoring and public transport remain outside this measurement.

## Stage 48: GFS Prior RPC

The native GFS prior constructor matches Python on the same three-level,
two-time fixture. A warm persistent-process measurement over 20 calls reports
a Rust median of **0.488 ms** and a Python median of **0.031 ms**. The native
path is therefore about **16x slower** at this granularity; the difference is
JSON serialization and the per-sample RPC boundary, not the interpolation
formula.

Stage 48 now also exposes `desktop_nowcast_prior_batch`, with the contract
`rust-desktop-nowcast-gfs-prior-batch-v1`. The satellite retrieval path sends
all sample points for one frame through this batch request, while the Python
provider preserves per-sample unknown semantics for missing columns. A warm
24-request measurement reports **0.278 ms per prior** in the batch path versus
**0.419 ms per prior** for separate RPCs, a **1.51x** transport reduction.
The batch path is still **8.91x** slower than the local Python construction
at **0.031 ms per prior**, so this is process-call amortization rather than a
Rust numerical-kernel speedup. No end-to-end nowcast latency or memory gain is
claimed yet.

`FIRESKY_GFS_PRIOR_BACKEND=rust` remains explicit. The next optimization target
is a compact decoded-grid/array transport that avoids repeating feature JSON
for every point; only after that should the full cached nowcast workflow be
remeasured for a default decision.

The release engine used for the final protocol smoke test has SHA-256
`21b553ca9619bde1eef2e4b6df162eba48d28d46fba68b58bbaf309ab32819e1`.

## Stage 49: Shared GFS Prior Batch

The prior batch transport now separates frame-level inputs from sample-level
metadata. `desktop_nowcast_prior_shared_batch` interpolates the left/right
feature arrays once, then selects a vertical column for each target. The
provider uses this path when the two decoded snapshots expose the same
coordinate/pressure domain and retains the previous batch contract as a
fallback for irregular domains.

On the same warm persistent engine and 24-request fixture, 20 measured trials
reported:

| Measurement | Previous batch | Shared batch | Local Python |
| --- | ---: | ---: | ---: |
| Complete call, ms | 6.697 | 3.185 | 0.752 |
| Per prior, ms | 0.279 | 0.133 | 0.031 |
| Request JSON bytes | 29,810 | 4,372 | n/a |

The shared contract is about **2.10x faster** than the previous batch and
reduces request bytes by **6.82x**, but remains about **4.24x slower** than
the in-process Python construction. This is a transport/interpolation
optimization; it does not establish an end-to-end nowcast win or justify
changing the explicit `FIRESKY_GFS_PRIOR_BACKEND=rust` default. The next
measurement must include cached GFS snapshot extraction, HSD retrieval and
final event scoring together.

## Stage 50: Cached 150-Point Nowcast Batch

The production satellite sampler can produce 150 points for a 900 km
analysis radius, while one desktop IPC request accepts at most 128 targets
and the line protocol has a 1 MiB limit. The provider now partitions shared
requests at both limits: no more than 128 targets and no more than 2,400
unique coordinate/pressure features per shared call. Irregular domains still
fall back to the previous batch contract, also partitioned to keep its
payload below the line limit.

On the cached fixture (`2026-11-07`, HSD `08:55Z`, GFS cycle `00Z`, Hangzhou,
900 km, 150 samples), the isolated GFS prior batch measured **747 ms** for
Rust and **796 ms** for Python. The complete cached calibrated retrieval
measured **1771 ms** for Rust and **1849 ms** for Python, with 512 retrieved
features and the same successful result. This is an approximately **4%
end-to-end improvement** on this local macOS cache; it is a warm local
fixture, not a remote or cross-platform deployment claim.

## Stage 51: Compact Single-Call Nowcast Quality

The previous nowcast adapter issued one event-scoring call and then one
multi-event call containing a single row per step. Both carried the full
75-field event component dictionary for every step. The new
`rust-desktop-nowcast-quality-v2` contract returns that dictionary once, plus
a 19-value instant matrix and a projected 10-value step ledger. The Python
bridge verifies the projection field order from Rust metadata and reconstructs
the existing response without changing any published step or event component.

The release binary used the same 18-field synthetic rows and a warm persistent
engine. Thirty measured trials reported:

| Steps | Previous two calls | Compact one call | Speedup |
| ---: | ---: | ---: | ---: |
| 1 | 0.693 ms | 0.343 ms | 2.02x |
| 5 | 1.412 ms | 0.526 ms | 2.68x |
| 20 | 3.739 ms | 1.011 ms | 3.70x |
| 60 | 9.515 ms | 2.120 ms | 4.49x |
| 128 | 18.437 ms | 3.738 ms | 4.93x |
| 150 | 21.003 ms | 4.238 ms | 4.96x |

The full/compact output comparison is exact for event components, instant
events and the complete 19-value instant matrix. The serialized response at
128 steps fell from **862,511** to **65,673** bytes, a **13.1x** reduction.
Rust also exposes bounded parallel instant composition for batch sizes of at
least 64; a dedicated test proves exact equality with the serial path.

This result is a local score-bridge measurement. It excludes HSD frame
decode, GFS prior sampling, observation constraints, report assembly, HTTP/CLI
transport and remote downloads. It therefore does not convert Stage 50's
measured 4% cached-retrieval improvement into a complete nowcast claim. The
Stage 52 measurement below covers the retrieval phases on the same 150-point
cache; complete nowcast orchestration remains unmeasured.

Release SHA-256:
`8806ac3e216d7beee0ce69489d42f305414d0c863b6bebe2dccf560d17acaeb0`.

## Stage 52: Unified Calibrated Retrieval

The native operation now owns one complete calibrated retrieval for 150 HSD
samples: footprint geometry, HSD pixel selection, decoded GFS priors, posterior
columns and horizontal metrics. A versioned, file-versioned cache validates
the HSD frame and both decoded GFS sidecars. The engine writes the large JSON
result to a temporary file and sends only a receipt over the 1 MiB line
protocol; the Python wrapper verifies receipt contract, byte count and SHA-256
before parsing.

The measurement used the real cached HSD frame
`frame-d8688b4a39853e83c6ef52e489108a7262c08bc906d475c66a8fafad06b547d9`,
GFS f008/f009 decoded sidecars, Hangzhou, a 900 km radius, 25-degree azimuth
and 150 samples. The release engine was warmed in a persistent process. Nine
trials produced:

| Measurement | Median | p90 |
| --- | ---: | ---: |
| Rust decoded prior | 19.98 ms | 21.38 ms |
| Python decoded prior | 809.31 ms | 830.55 ms |
| Native file RPC | 30.20 ms | 31.11 ms |
| Wrapper with Python object reconstruction | 50.54 ms | 52.91 ms |
| Python posterior fallback | 1862.57 ms | 1907.31 ms |
| Cold wrapper | 184.14 ms | n/a |

The file RPC is **61.67x** faster than the Python posterior. The complete
wrapper, including validation and reconstruction of Python objects, is
**36.85x** faster and reduces that measured interval by **97.29%**. Decoded
GFS prior construction alone is **40.50x** faster, and Python reconstruction
accounts for about **20.58 ms**. The result contained 616 features, 150 column
sets, 19 unknown column sets and **1,237,006 bytes** of JSON.

Rust's warm internal phase medians were **0.04 ms** HSD frame load, **1.09 ms**
HSD sampling, **0.03 ms** decoded-GFS grid load, **10.69 ms** retrieval and
**4.16 ms** assembly. These phases overlap the RPC's JSON serialization and
file I/O outside the internal timer and must not be added to infer a separate
end-to-end latency.

The exact scope is one warm, cache-local calibrated HSD/GFS retrieval. It
excludes HSD/CAMS/GFS downloads and cache warming, HSD motion/advection and
multi-frame orchestration, final `NowcastAdjustment` construction and event
scoring, HTTP/CLI transport and application UI. In particular, **50.54 ms is
not a promise of complete nowcast request latency** and does not establish a
pure-Rust production workflow. Reproduce with:

```sh
.venv/bin/python scripts/bench_nowcast_calibrated_retrieval.py --trials 9
```

Release SHA-256:
`055e39690070cb4ce671813ed1ac8d08582354133c698caf4139cfffa692a3f0`.

## Stage 53: Multi-Scan Nowcast Adjustment

The native adjustment operation now accepts one to four calibrated HSD scans,
deduplicates UTC instants, selects the five-minute scoring window, retrieves
each scan, applies the primary-scan coverage gate and assembles the complete
adjustment payload. Ordered retrieval uses at most four workers. The display
frame remains separate from scoring so advected image identity cannot alter
the primary metrics.

The fixture used the real cached HSD scans at **2026-11-07 08:55Z** and
**09:05Z**, the decoded GFS f008/f009 sidecars, Hangzhou
(`30.2741, 120.1551`), a **900 km** radius, a **25-degree** azimuth and target
time **09:00Z**. The release engine was warm in a persistent process. Seven
trials produced:

| Measurement | 1 scan | 2 scans |
| --- | ---: | ---: |
| HSD observed features | 641 | 1,257 |
| Cloud-column sets | 150 | 300 |
| JSON output bytes | 1,708,064 | 2,767,008 |
| Cold wrapper, ms | 171.76 | 216.15 |
| Native file RPC median, ms | 55.24 | 76.14 |
| Native file RPC p90, ms | 55.76 | 79.19 |
| Complete Python wrapper median, ms | 55.23 | 75.13 |
| Python wrapper p90, ms | 55.81 | 76.92 |
| Python object reconstruction median, ms | 15.67 | 32.18 |
| Python reference fallback median, ms | 1,274.59 | 3,063.54 |
| Python reference fallback p90, ms | 1,299.94 | 3,217.62 |

The two-scan wrapper is **40.78x** faster than the measured Python reference.
Before ordered two-worker retrieval, the same warm fixture measured **82.17
ms** for the complete two-scan wrapper. The parallel path reduced that median
by about **8.57%** and reduced the two-scan cold wrapper from **308.66 ms** to
**216.15 ms**. One-scan latency did not regress beyond normal trial variance.

Python object reconstruction becomes the dominant measured wrapper component
for two scans at about **32.18 ms**. The file RPC includes Rust retrieval,
JSON serialization and file I/O; reconstruction is a separately sampled
phase, so these values are not additive without accounting for filesystem
timing variance.

This scope starts with warm, cache-local HSD and decoded GFS inputs. It
excludes downloads and cache warming, HSD motion estimation, advected-frame
generation, final event scoring, HTTP/CLI/UI transport and packaging. It does
not establish complete production-nowcast latency or a pure-Rust runtime.
Reproduce with:

```sh
.venv/bin/python scripts/bench_nowcast_adjustment.py --trials 7
```

Release SHA-256:
`39726d44dcdc57c488170410d5eb8ede4e24ee2c9ecbaf40acd07453443ff412`.

## Stage 54: Nowcast Assessment Baseline

The Stage 53 adjustment result is followed by one Python
`satellite_nowcast_assessment` per observed scan, Python radar/path
constraints, then the already-native event/instant quality ledger. This
benchmark isolates that remaining post-retrieval boundary using the same
two-scan HSD/GFS cache fixture: **2026-11-07 08:55Z** and **09:05Z**,
Hangzhou, 900 km, target **09:00Z**. Seven warm trials produced:

| Measurement | p50 | p90 |
| --- | ---: | ---: |
| Both per-scan physical assessments | 66.54 ms | 67.00 ms |
| Both observation/radar constraints | 17.66 ms | 17.81 ms |
| Rust event-quality wrapper | 0.624 ms | 0.761 ms |
| Assessment + constraints + event quality | 84.56 ms | 85.31 ms |

The two assessments consumed **616** and **641** feature rows, emitted **169**
and **153** candidate signals, and each returned 21 reasons. On this fixture,
the physical assessment is the largest remaining measured compute boundary;
moving only the final event ledger again would not be useful.

The HSD feature objects carry `horizontal_cloud_probability` and
`cloud_probability_given_cloud`, while the legacy Rust scoring buffer has only
20 model fields and explicitly rejects those satellite values. The next Rust
assessment implementation must extend the feature contract and preserve those
probabilities through candidate, opacity and blocker calculations. Reusing
the old buffer while dropping them would change cloud-presence semantics.

### 2026-11-10 Optimization Follow-Up

`ForecastService` now constructs the shared radar/nowcast diagnostic
`NowcastAdjustment` only for the first observed scan. Every scan still
receives its own observation-constrained `TimeStepAssessment`; the focused
regression test confirms that skipping repeated adjustment construction
produces exactly the same constrained assessment.

The same pinned fixture and seven warm trials produced:

| Measurement | p50 | p90 |
| --- | ---: | ---: |
| Both per-scan physical assessments | 66.03 ms | 67.03 ms |
| Both observation/radar constraints | 8.31 ms | 8.88 ms |
| Rust event-quality wrapper | 0.797 ms | 0.854 ms |
| Assessment + constraints + event quality | 75.53 ms | 76.55 ms |

Compared with the baseline above, the combined p50 decreased by about **10.7%**;
the observation-constraint phase decreased by about **52.9%**. The small
increase in the event-quality wrapper is within sub-millisecond measurement
variation and unrelated to this change. The Python physical assessment remains
the dominant phase. These results do not change the missing satellite
probability fields in the legacy 20-field Rust scorer and do not establish
complete nowcast or production-request latency.

This is a warm local cache baseline, not a forecast-latency claim. It excludes
provider discovery, downloads, calibration, cache warming, HTTP/report
serialization and cross-platform execution. Reproduce with:

```sh
.venv/bin/python scripts/bench_nowcast_assessment.py --trials 7
```

Release SHA-256:
`39726d44dcdc57c488170410d5eb8ede4e24ee2c9ecbaf40acd07453443ff412`.

### Stage 54: Native Batch Nowcast Assessment

The opt-in `desktop_nowcast_assessment` engine operation (Stage 54 of the
migration) scores both fixture scans in one call and applies the radar
constraints inside the engine, using the same 27-column
`hsd-assessment-feature-row-v2` rows and the satellite probability semantics
that the legacy 20-field buffer cannot carry. The release identity is recorded
below; do not assume it is identical to the earlier baseline release.
The same pinned fixture and seven warm trials produced:

| Measurement | p50 | p90 |
| --- | ---: | ---: |
| Python per-scan assessments + constraints (baseline) | 75.76 ms | 76.18 ms |
| Python assessment + constraints + event quality | 75.53 ms | 76.55 ms |
| Native batch assessment (one call, constraints included) | 47.03 ms | 47.61 ms |
| Native batch assessment + event quality | 47.99 ms | 48.73 ms |

The native batch path scores both scans in about **62%** of the Python
assessment-plus-constraint time on this fixture, and the full
assessment-to-ledger segment runs in about **64%** of the Python path. The
native numbers include the same work the Python segments cover: per-scan
columns, spatial indexing, candidate signals with the dual-segment aerosol
path, the three lightpath families, the cross section, radar constraints,
regime composition and every reason line, plus the 18-value float32-quantized
ledger rows and the event/instant scoring. Candidate counts (**169** and
**153**) and feature rows (**616** and **641**) match the Python baseline
exactly, and the benchmark asserts structural equality between the two paths
before timing.

This remains a warm local cache measurement, not a forecast-latency claim. It
excludes provider discovery, downloads, calibration, cache warming, HTTP and
report serialization, packaging, and cross-platform execution. Reproduce with:

```sh
.venv/bin/python scripts/bench_nowcast_assessment.py --trials 7
```

Release SHA-256:
`aebb2fd7f6968d60788ac41b9cf579b5dd74b61f665789516040c76a6ef0c50b`.

### 2026-11-11 Python Hot-Path Optimization And Contract v2 Follow-Up

Two bit-identical redundancy eliminations landed in the Python assessment
path after the tables above. `assess_timestep` now reuses
`forward_burnable_cloud_field_amount` as `raw_extent` instead of calling
`candidate_angular_cloud_coverage` a second time per scan (scoring.py:3877),
and `path_clearance_weight` hoists the previously triple-computed
`estimated_cloud_base_m(candidate)` into one local (scoring.py:1924). cProfile
on `scripts/_profile_nowcast_assessment.py` ranked the per-scan repeated
angular-coverage grid first; the second hoist measured below trial noise.
Focused pytest run: 60 passed, 57 deselected.

The engine contract also moved to `rust-desktop-nowcast-assessment-v2` with a
request `detail` level: `"full"` keeps every diagnostic list for acceptance
differencing, `"model"` drops the lists the service-side
`TimeStepAssessment` reconstruction never reads, and unknown levels are
rejected. The service hot path uses `detail="model"` (service.py:1405); the
18-value ledger rows and the event/instant quality wrapper are identical on
both levels. The engine binary was rebuilt for this contract; the same pinned
fixture and seven warm trials then measured:

| Measurement | p50 | p90 |
| --- | ---: | ---: |
| Python per-scan assessments | 66.88 ms | 70.41 ms |
| Python observation/radar constraints | 9.99 ms | — |
| Rust event-quality wrapper | 0.62 ms | — |
| Python post-retrieval total | 81.25 ms | — |
| Native batch assessment (one call, constraints included) | 34.02 ms | 36.54 ms |
| Native batch assessment + event quality | 34.63 ms | — |

Earlier runs on the same binary and fixture measured the native batch at
**33.34 ms** p50 (**33.77 ms** p50 including event quality) with the Python
post-retrieval total at **78.75 ms** p50; the multi-run native/Python ratio
stays between roughly **43%** and **46%** of the Python assessment-plus-
constraint segment. Feature rows (**616**/**641**), candidate counts
(**169**/**153**) and reason counts (**21**/**21**) match the Python baseline
in every run, and the benchmark asserts structural equality between the two
paths before timing. The two optimization rounds are bit-identical by
construction (pure-function reuse; no numeric or failure-semantics change),
so the Python and native columns remain directly comparable across the
measurement dates.

These are warm local cache component measurements, not a forecast-latency
claim. They exclude provider discovery, downloads, calibration, cache
warming, HTTP/report serialization, packaging and cross-platform execution.
At the time of these measurements the production backend remained Python and
the native path was opt-in. The subsequent default-runtime cutover on
2026-11-11 is documented in `RUST_RUNTIME_CUTOVER.zh-CN.md`; it does not
retroactively turn these component timings into end-to-end measurements.
Reproduce with the same command above.

Release SHA-256:
`983894e36756de6aa6193fa6ea2c2964339941d131d4ff91f6100b31bbe9bdb7`.

### 2026-11-11 Final Native Runtime Build

After compilation, test runs and the macOS bundle build finished, seven warm
trials on the same pinned two-scan fixture produced:

| Measurement | p50 | p90 |
| --- | ---: | ---: |
| Python per-scan assessments | 63.28 ms | 63.58 ms |
| Python observation/radar constraints | 8.43 ms | 8.78 ms |
| Rust event-quality wrapper | 0.596 ms | 0.669 ms |
| Python post-retrieval total | 73.10 ms | 73.64 ms |
| Native batch assessment | 31.98 ms | 32.17 ms |
| Native post-retrieval total | 33.31 ms | 33.40 ms |

The measured segment is **2.19 times faster**, or **54.4% less elapsed time**.
The benchmark asserts equality of the Python and native assessment steps
before timing. Feature rows remain 616/641, candidates 169/153 and diagnostic
reason counts 21/21. The target is 2026-11-07 09:00 UTC at Hangzhou, using
observations five minutes before and after that time.

This is a frozen component fixture, not an ordinary live service request:
it uses explicit AOD550 0.15 and the historical fixture's pinned GFS prior
selection (including its latest-frame f009/zero interpolation fraction).
It does not validate a live CAMS pipeline. Discovery, downloads, calibration,
retrieval, cache warming, HTTP/report serialization, packaging and
cross-platform execution are excluded. The benchmark calls the native batch
operation through the Python differential harness; this is not a measurement
of the entire native HTTP request.

The default production runtime is now Rust. Internal `Value` conversions in
the quality/assessment interface remain an optimization opportunity; this
result does not establish zero serialization overhead or whole-site speedup.

Reproduce:

```sh
.venv/bin/python scripts/bench_nowcast_assessment.py --trials 7
```

Final release SHA-256:
`df88af4cc525210d12d48f4f09c98ae5568f1b47a606e437c75fcbfe2ad28aa7`.

Source identity:
`6b62145a51032e44b7ba07f02a3b1a358aafca04a551903f99d4071074e5fbe6`.
