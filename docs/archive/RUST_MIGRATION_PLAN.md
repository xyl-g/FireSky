# Python Runtime Retirement

2026-11-11 update: default build/HTTP/CLI/native desktop launch and container
commands now target the Rust runtime. See
[RUST_RUNTIME_CUTOVER.zh-CN.md](RUST_RUNTIME_CUTOVER.zh-CN.md) for the implemented
routes, actual validation and remaining compatibility gaps. The inventory below
is the earlier routing baseline, not a statement that current defaults launch Python.

## Objective

Complete the Python-to-Rust migration without reducing existing forecast,
data-management or diagnostic behavior. Production engine execution must not
require a Python interpreter, Python packages, subprocess Python adapters or
silent fallbacks. Existing native desktop frontends remain native; Python
source may remain as test/reference material, not a production requirement.
Analyze the Python-free engine's performance, identify measured bottlenecks
and implement verified optimizations without changing numerical/data contracts.

The experimental Rust switches and migrated numerical helpers are intermediate
work, not the completion criterion. No production-default switch or removal
of the reference implementation is justified by helper tests alone.

## Current Evidence

See [RUST_MIGRATION.md](RUST_MIGRATION.md) for commands, contracts and observed
stage results. Explicit legacy entrypoints still depend on Python; default native
entrypoints no longer launch it, but full behavior retirement is not yet accepted.
See [RUST_PERFORMANCE.md](RUST_PERFORMANCE.md) for the stored-input benchmark
and implemented packing/coordinate optimizations. Full-forecast performance
acceptance remains outstanding.
For the current Chinese closeout and the distinction between unfinished,
replaceable and external/platform-owned work, see
[RUST_MIGRATION_STATUS.zh-CN.md](RUST_MIGRATION_STATUS.zh-CN.md).

| Area | Owning Python sources | Current Rust coverage | Remaining work |
| --- | --- | --- | --- |
| Shared numerical core | `native_core.py`, `forward_field_scoring.py`, `national_batch.py` | Existing ray tracer; complete empirical scalar/regional event composition, all 19 instantaneous fields, full 103-key event-quality diagnostics (105 with scalar ceilings); GFS feature assembly; full canonical cloud-column/profile derivation; bitwise-verified parallel cell/event/diagnostic execution; complete compact frame/core builder and validated point materializer | Remaining tied-window selection semantics and production workflow acceptance |
| GFS input | `gfs.py`, `cycle_availability.py` | ecCodes decode, canonical grid, point features, regional tensors; NOMADS selectors, shared raw-cache metadata/index, bounded locked retry downloads; automatic scalar/regional event hours, source domains, heuristic cycles and terrain-corrected replanning | Publication probes, full production scheduling, eviction/refresh/task lifecycle and production acceptance |
| EC input | `ecmwf.py`, `ecmwf_ifs.py`, `ecmwf_point.py`, `ecmwf_tensor_cache.py` | Strict public/basic and separate offline full/hybrid tensors; explicit basic TCC/RH proxy grids, independent basic regional derivation, isolated transport/cache identities; opt-in NDJSON/Range basic raw downloads, shared schema/locking/header admission and 404-only event-cycle publication selection; independent point event/source-domain/CAMS planning and batched raw resolution; complete legacy point provenance with explicit native profile layout; verified per-hour cross-section source and complete theoretical profile reader; opt-in verified model-derived frames and complete immutable EC service snapshots with registered generation-bound full-profile reads | Regional orchestration, production cross-section route/cache registration, bounded derived/response-cache retention and task lifecycle, legacy/other-product downloads, remaining capability/freshness probes, live-source acceptance; no full-tensor scorer is implied |
| CAMS | `aerosol.py`, `cams_optics.py` | Compact AOD550/11-layer float64 decode, spatial sampling, float32 time blend, scale-height fit, model-grid sidecars; expanded 31-field spectral/SSA/asymmetry decode and physical diagnostics; automatic GFS-event cache selection; opt-in ADS v1 authenticated availability/job/transfer, batched strict atomic raw caching and neighboring-lead fallback | Live-account/production acceptance, corridor-reading and other aerosol-provider orchestration, production sidecar identities/checksums/cache lifecycle and full forecast assembly |
| Point/regional engine | `engine.py`, `service.py`, `national_point_core.py`, `national_batch.py`, `scoring.py` | Regional solar/model-time samples; independent stored GFS/EC-basic + strict frozen CAMS catalog -> all 82 native assessment fields, complete per-frame pressure profiles, full empirical event/instant quality, complete event/selected-column diagnostics; twenty compact frames, complete regional dictionaries, strict request/core validation and complete stored-input point materialization with evolution/window; cache-resolved GFS and separate EC point workflows with automatic input planning and EC-specific report assembly | Complete online weather/file scheduling, production integration of the separate EC workflow, point/regional batching, persistent service/cache and remaining shared result schemas |
| Astronomy/geometry/time | `geometry.py`, `time_utils.py`, `time_contract.py`, `analysis_constants.py`, `national_batch.py` | Shared point solar position, full-sun event anchors, UTC crossings/glow windows; date/time/cycle contracts and pinned IANA zones; observer/ray geometry; regional local-date anchors, twenty solar samples and strict model-hour brackets, standalone tensor output | Complete weather/event scheduling and forecast assembly; older Python parser and broader timezone parity acceptance |
| Satellite/radar/nowcast | `satellite.py`, `satellite_cloud_column.py`, `radar.py`, `scoring.py`, `service.py` | Strict native HSD compact-cache NPZ validation and tensor-bundle export; native B13 phase-correlation motion and advection; one-call calibrated retrieval over decoded GFS priors, HSD sampling and posterior/metrics with receipt-bound output; shared GFS prior batches; default Rust nowcast event/instant quality ledger; multi-scan Rust retrieval, timeline selection, coverage gate, provenance and adjustment assembly | Remote HSD listing/range download, raw calibration and compact-cache writes; complete provider/motion/advection lifecycle; per-observation physical assessment and radar constraints; Python report/application assembly; end-to-end production acceptance |
| Diagnostics/export | `forecast_characteristics.py`, `regional_diagnostics.py`, `cross_section.py`, `map_frames.py`, `html_report.py` | Material classifier; complete event-quality ground/layer/optical components; finite-fallback selected-profile sampling and material/geometry confidence; scene/phase/genus/proxy/color in frame/core assembly; evolution/score-curve-window rules integrated into stored-input point results; complete theoretical pressure-profile cross-section matrices, targets, illumination and spherical terrain/horizon visibility; byte-compatible report HTML and durable EC bundled export with integrity-bound profile lookup | Production cross-section registration/cache/route, report filename/URL mounting, non-Unix host timestamps and remaining chart/map/export contracts; remaining production diagnostics |
| Storage/cache/services | `storage.py`, `assessment_cache.py`, `feature_cache.py`, `redis_cache.py`, `bounded_lock.py`, `cache_tasks.py`, `national_frame_contract.py` | Bounded NPY tensor transport with manifest/source/file integrity, experimental namespaces and EC backend identities; GFS raw cache/index compatibility, advisory locks, atomic publication and bounded in-process index reuse; source/decoder/code-bound opt-in derived frames; complete EC response generations with whole-inventory verification, atomic indexes, TTL/forced refresh and stable old profile tokens | Remaining production identities, eviction/leases/quotas, cancellation, refresh task lifecycle and HTTP/client registration |
| Location/config | `location.py`, `models.py`, `config/`, `version.py`, `logging_config.py` | Independent fast/full geocoding and metadata fallback, legacy location cache, SRTM decode/download/cache, complete pinned geographic timezone resources and scalar/batched lookup | Production consumer integration, packaged/offline resource delivery, live-provider acceptance, remaining typed application config/logging |
| Desktop IPC | `desktop_data.py`, `desktop_rpc.py` | Separate debug protocol; opt-in native-client location and complete read-only GFS/EC/CAMS inventory with original wire payloads, bounded workers/admission, cooperative cancellation and handshake validation; production `DesktopService.search`, `metadata` and `inventory` can now use the same Rust location/inventory operations through the persistent bridge; opt-in original desktop cache reports against resolved point/regional plans, including strict CAMS and paired-model requirements; native owner-scoped session tokens with expiry, dismissal and single-use confirmation authority | Raw-payload event/terrain planning, publication/nowcast checks, frozen plan construction, preparation/forecast task ownership, progress and full cancellation lifecycle; Windows runtime acceptance and removal of the main Python process |
| HTTP/CLI | `api.py`, `web_server.py`, `__main__.py` | Debug executable | Route/CLI compatibility, authentication, SSE, jobs, failures and startup |
| Packaging | `scripts/`, `apps/`, desktop build configuration | Local Rust artifacts | Native clients launching Rust, ecCodes/resources, OS builds/installers without Python |

This inventory is a routing baseline, not evidence that unlisted code is
unnecessary. Before retiring an entrypoint, follow its actual imports,
subprocesses, runtime resources and externally consumed schemas.

## Execution Order

1. Integrate the completed stored-input GFS, EC-basic/offline-full and
   compact/expanded CAMS input contracts with production cache lifecycle
   without reducing the supported national domain. Full catalogs remain an
   input-only contract; do not replace their model-level evidence with the
   separate basic TCC/RH proxy.
2. Accept the completed frozen-input validated single-point path against
   production point/regional services, retaining existing EC point behavior,
   solar scheduling, interpolation, aerosol sidecars and all diagnostics.
3. Port data planning/download/integrity/cache contracts and location services.
4. Port satellite/radar/nowcast and remaining exports.
5. Implement production desktop IPC and HTTP/CLI on the Rust engine; retain
   token safety, cancellation, request concurrency and error semantics.
6. Switch native clients and production defaults only after end-to-end
   acceptance, then package and verify Python-free releases.

These steps are dependencies, not permission to omit later behavior.

## Next EC Workflow Boundary

Stage 21 follows `run_ecmwf_basic_point_forecast` for the candidate-plus-trace
source domain, canonical event/hour selection, strict raw/CAMS resolution,
terrain/grid consistency and EC-specific step/report/native-quality,
dominant-burnable and Web-report normalization. It resolves source hours in
one native batch, addressing the Stage 20 per-hour bridge boundary.
Stage 22 supplies the explicit verified per-hour profile geometry/file source,
complete theoretical cross-section output and sparse selected-column reading
with whole-array checksums. Stage 23 adds full legacy point provenance, compatible
HTML, EC Web response assembly and integrity-bound native profile lookup.
The service artifacts explicitly distinguish native profiles/keys from Python's
mmap/response-cache identity, and do not fabricate report URLs or cache hits.
Stage 24 adds explicit, model-only native derived-cache reuse in both stored
and independently planned GFS/EC workflows. Its format remains separate from
Python's derived/profile caches. It is opt-in pending representative
lifecycle/performance acceptance. Stage 25 adds complete immutable EC response
snapshots, atomic per-key generation publication, optional age expiry/forced
refresh and receipt-bound registered full-profile reads, including explicitly
resolved new map points. Hits verify the full historical generation but do not
claim current raw-input validation; automatic cycle selection still runs its
normal policy. Old generations remain readable and are not yet evicted.
Stage 26 adds independent location/elevation/geographic-timezone resolution.
Stage 27 connects the native clients' location methods to an opt-in Rust
child with bounded concurrent IPC and cancellation, without Python fallback.
Stage 28 adds the full original desktop data-page inventory to that child,
retaining counts, cycle/hour grouping, GFS coverage, EC paths and CAMS areas.
Stage 29 ports the cache checks and complete paired-model reports against
resolved event plans, connected through an opt-in Python bridge. Native checks
share one parsed CAMS catalog across required targets/models; original request
planning, publication/refresh and session ownership remain Python.
Next, port the remaining native IPC session/data workflows and production
consumer registration/HTTP integration and resource
delivery together with bounded retention/cancellation.
Full service/application lifecycle is still unfinished. Do not
substitute the generic stored-point payload or pretend the native per-hour
profile bundles are the Python combined profile mmap.

The owning reference reader is `national_batch.build_national_cross_section`, reached
through `web_server._ecmwf_cross_section_from_provenance`. It currently reads
a four-dimensional `[hour, cell, pressure, 24]` NPY/NPZ profile, checks the
pressure/time axes and exact canonical five-minute offset, bilinearly/time
samples the solar-azimuth path, and returns all thermodynamic/cloud/wind
matrices plus terrain, illumination, adaptive-400-hPa families, spherical
horizon/terrain-clearance diagnostics and theoretical targets. Preserve that
whole response and its failure semantics; the existing seven-field selected
column diagnostic is not a substitute. Stage 22's independent reader preserves
that response over explicit per-hour manifests; the current Python production
consumer still expects its original mmap and is not switched. Keep cache
identity/geometry and consumer contracts explicit. Also exercise the combined EC-plus-ADS workflow,
which Stage 21's complete cached-CAMS report tests do not establish.

Stage 23 measurements show about 16-18 ms for added service assembly/export in the
six-cell fixture; raw header initialization/admission and repeated decode remain
larger costs. Next cache work must measure cold and warm complete outputs and
retain source/model/time/grid identities, not disable validation to obtain a
faster cache hit. Stage 25 now measures whole-generation refresh versus verified
immutable hits separately; those operations have different freshness semantics.
Long-lived runtime/ecCodes initialization should be measured
separately from fresh-child latency before selecting another optimization.

## Location Boundary

The current owning source is `location.py`, not just the coordinate fields
in forecast recipes. Preserve both fast search and complete resolution:
Nominatim/Open-Meteo fallback order, candidate-versus-resolved result fields,
cache schema and failures. Interactive metadata first uses rounded
four-decimal coordinates with Open-Meteo; its offline/provider-failure
fallback is the local timezone dataset plus SRTM elevation.

Local timezone resolution must retain actual geographic IANA-zone lookup;
a longitude-to-offset approximation or a fixed observer timezone is not a
replacement. SRTM requires north/south tile naming, the existing coverage
boundary, 1201/3601-square big-endian signed samples, bilinear interpolation
and the exact partial/all-void fallback behavior. Preserve bounded locking,
safe download/decompression, atomic cache publication and explicit failure.
Stage 26 implements these contracts independently and verifies pinned local
data and simulated providers. Batched timezone lookup shares one verified
resident dataset acquisition across up to 4096 points. Production metadata/
selected-point registration is not yet connected to the Stage 25 reader.
Dataset provenance, licensing, resource packaging and offline availability
also remain release gates.

For the next desktop adapter, follow `desktop_rpc.py`'s owning contract:
desktop `search` always combines fast search (6 s) with point metadata (8 s),
then returns the compact complete observer fields. Desktop `metadata` retains
the caller's original coordinates while only its provider lookup is rounded.
The new debug `location/search` candidate and `location/metadata` provenance
envelope are not drop-in replacements for those wire payloads.
`apps/macos/.../Store.swift` still calls the original desktop method names.
Stage 27's optional client routing preserves those payloads over a direct
Rust child; the default main process and remaining methods still use Python.
Keep OS positioning and permission handling separate; no IP-derived location
substitution is introduced by this migration.

## Next Desktop Data Boundary

Stage 28's inventory is a read-only filesystem report, not the production
preflight state machine. Follow `desktop_data.model_context`,
`check_model_data` and `check_nowcast_data` for point/regional source domains,
terrain-dependent hour replanning, independent GFS/EC cycles, paired model
results, CAMS requirements and satellite frame availability. Do not turn raw
filename counts into ready/verified flags.

Stage 29 implements model-plan cache checks, retaining independent cycles,
GFS covering metadata/schema, fast/strict CAMS bracket selection and complete
grouped report fields. Its input plans still come from Python `model_context`,
and its refresh gate is still the owning Python publication/artifact policy.
Move those raw-payload event/domain/terrain operations next; the resolved-plan
debug method is not a replacement for the native client's session-based
`check`. Keep the explicit `FIRESKY_DESKTOP_DATA_BACKEND` bridge until the
full lifecycle can be owned by Rust.

Stage 30 adds the raw `desktop_context` operation and keeps the resolved-plan
preflight operation below it. The raw operation returns a temporary, hashed
context document through the persistent engine transport. Its remaining release
gate is resource and behavior coverage for non-China geographic timezone
lookup, publication refresh, nowcast/satellite checks and the desktop service
session lifecycle.

Stage 31 adds `desktop_nowcast_context` and routes nowcast GFS/CAMS cache
admission through the existing Rust preflight contract while retaining the HSD
frame provider. The next boundary is publication probing and HSD compact-cache
validation in Rust, followed by nowcast preparation/scoring.

Stage 32 extends the nowcast boundary with a native preparation plan: Rust
owns the GFS prior footprint/hour set and CAMS three-hour target set, while
the existing Python providers continue to perform HSD, GRIB and ADS I/O.
This is a planning migration only; it does not authorize replacing provider
validation, forecast scoring, session ownership or the desktop service.

Stage 33 moves HSD compact-cache discovery and strict NPZ validation into the
Rust engine for the opt-in nowcast desktop backend. It preserves newest-first
selection and skips stale frames, while leaving remote listing/download,
calibration, cache writes, frame construction, motion/scoring and lifecycle
ownership in Python. The remaining boundary is provider/network execution and
the complete nowcast preparation/scoring/session workflow.

Stage 34 adds `desktop_nowcast_hsd_export`. Rust reuses the strict compact
cache admission path, decodes the calibrated coordinate/B07/B13/B15 arrays and
commits each source file to a `hsd-compact-frame-v1` tensor bundle with a
source identity. `FIRESKY_HSD_BACKEND=rust` lets the nowcast analyzer consume
those bundles through `RustHsdCompactCacheProvider`; Python no longer parses
the compact NPZ on that path. The Python backend remains the default, and
remote HSD discovery/range I/O, calibration, compact-cache writes, motion,
advection, cloud-column retrieval, GFS-prior orchestration, scoring and
desktop session ownership remain open.

Stage 35 adds `desktop_nowcast_hsd_motion`. The native operation reads the
source-bound B13 bundle, reproduces the Python overlap gate, common-mean
fill, Hanning-windowed 2-D phase correlation, wrapped peak, correlation
quality and velocity contract, and is selected by the Rust HSD provider for
paired frames. A bounded manifest-version cache avoids repeating resident
diagnostics. Advection/interpolation, cloud-column retrieval and the rest of
the nowcast lifecycle remain open.

Stage 36 adds `desktop_nowcast_hsd_advection`. For the Rust HSD provider,
validated B07/B13/B15 bundles are projected with the same non-wrapping
bilinear interpolation, target-time/source provenance and four-hour plan gate
as Python. Derived bundles remain source-bound to the input manifest and are
reused only after complete file validation. A bounded Python-side projection
cache is invalidated by all manifest/array file versions, so repeated identical
nowcast requests do not invoke IPC or reload arrays. HCAI categorical fields,
remote HSD I/O/calibration, cloud-column retrieval, GFS-prior orchestration,
scoring and desktop lifecycle remain open.

Stage 37 adds `desktop_nowcast_hsd_retrieval` for the deterministic HSD
B07/B13/B15 posterior. The Rust operation validates the source-bound compact
frame, reproduces the Python cloud-presence sigmoid, conservative clear-sky
intersection, GFS-supported layer selection/weights, base-height posterior,
height profile and profile-conservation step, then returns a compact v2
response. `calibrated_satellite_cloud_retrieval` batches all HSD sample points
into one native call when `FIRESKY_HSD_BACKEND=rust`; HCAI frames continue
through the Python categorical retrieval. Rust keeps a bounded process-local
validated-bundle contract cache, and the Python wrapper reconstructs the full
`SatelliteCloudColumn` from the compact response without changing the public
schema. Differential tests cover cloudy and conservative-clear cases plus the
full fan/feature adapter. The default backend remains Python.

Stage 37 is a correctness and boundary migration, not an unconditional
end-to-end speedup claim. On a synthetic local release benchmark, resident
native retrieval was about 1.23 ms for 25 samples and 9.71 ms for 256 samples,
versus 0.31 ms and 2.91 ms for the Python posterior. The measured native
overhead is the persistent JSON bridge and object reconstruction; pure Rust
execution is not isolated by this benchmark. Further speedup requires a
binary/shared-memory transport or moving more of the sample/prior assembly
across the boundary, not another translation of the same Python objects.

Stage 38 connects the production `DesktopService.inventory` route to the
existing Rust inventory operation when `FIRESKY_DESKTOP_DATA_BACKEND=rust`.
The Python implementation remains `_python_inventory` and is still the
default/reference path. The new `desktop_inventory` RPC returns an explicit
`rust-desktop-inventory-v1` identity, preserves the complete GFS/EC/CAMS
payload and does not silently fall back after a native failure. The bridge
passes the configured cache-root spelling unchanged so EC cycle paths retain
the original `Path.rglob` contract, while Rust still resolves the displayed
dataset root as before.

The three-way 4000-file benchmark compares the direct native child, the
Python reference service and `DesktopService -> Rust serve`. Complete
responses matched across 45 scans. Resident scans were 290.29 ms, 896.25 ms
and 313.39 ms respectively; the production bridge therefore retains most of
the Rust scan gain but adds about 23 ms of service/JSON overhead. Its process
RSS remains 66.55 MiB because the Python desktop service is still present,
versus 14.75 MiB for the direct Rust child. This stage therefore improves
production routing and removes Python inventory traversal, but is not a
Python-free desktop service or a whole-application memory result.

The next desktop boundary remains the native session lifecycle: raw request
planning, publication/nowcast checks, frozen confirmation tokens,
preparation/forecast task ownership, progress and cancellation. Moving that
lifecycle is the meaningful path to remove the bridge overhead and the
Python RSS component.

Stage 39 extends the same opt-in bridge to production `DesktopService.search`
and `metadata`. Rust reuses the already differential-tested
`location::desktop_dispatch` contract, while the Python adapter transfers
only provider/cache configuration and validates an explicit
`rust-desktop-location-v1` response. Search and metadata failures surface
through the existing RPC error path; the Python geocoding/metadata functions
are not a fallback in Rust mode. The selector still defaults to Python.

The local provider benchmark shows the direct native child remains the
lowest-overhead path, while the production bridge retains the Python service
startup/RSS and adds JSON/adapter cost on memo hits. The eight uncached
metadata calls still benefit from Rust's native concurrent provider workers,
but this is not a whole-application or live-provider throughput claim.

Stage 40 moves the opt-in desktop session authority into the persistent Rust
child. `desktop_session` owns per-service owner isolation, token generation,
30-minute expiry, the 32-session bound, dismissal and single-use consumption.
The Python service keeps a compatibility mirror of payload/report objects and
its existing per-session operation locks, but validates every native-backed
operation against Rust. A lightweight `validate` action avoids copying the
full report and payload back across the bridge on every prepare/publications/
forecast entry; full snapshots are transferred only on create/update/consume.
Rust child restart conservatively invalidates the old tokens instead of
falling back to Python state.

This stage reduces Python's security-critical session authority but does not
complete raw request planning, publication I/O, preparation/forecast task
ownership, progress or cancellation. Stage 41 below moves publication probing;
the native client's main Python child must remain until the remaining full
workflows have native implementations and tests.

Stage 41 moves desktop publication probing into the persistent Rust child.
`desktop_publications` owns the five-minute source-keyed cache and probes the
same concrete GFS product, ECMWF Open Data index candidates and CAMS ADS
constraints used by the Python implementation. The three requested sources
are probed in bounded scoped workers, matching the legacy service's
concurrent source fan-out. GFS/ECMWF/CAMS provider
failures retain the conservative heuristic cycle with `verified=false`;
verified provider results keep the existing ISO/label payload. Both the
`DesktopService.publications` route and Rust-backed model/nowcast preflight
use this operation, and native errors are not converted into a Python probe.
The Python `published_cycles` function remains the default/reference backend
and is retained for non-Rust callers and differential tests.

This stage removes Python publication-network ownership from the opt-in
desktop backend, but the report mutation adapter, raw request/event planning,
provider downloads, preparation/forecast execution, progress/cancellation and
the Python service process remain open. Publication probing is network-bound;
the migration does not claim a forecast latency improvement from the small
Rust adapter alone.

Stage 42 moves production model-input preparation into the native engine.
`desktop_prepare` reuses the Rust desktop context and terrain planner, downloads
the first model frame when a model-grid elevation prepass is pending, reruns
the context to obtain the terrain-aware event-hour set, then resolves all GFS
or ECMWF raw files and exact CAMS profile brackets in Rust. The Python bridge
only writes the bounded request file, maps the existing `both` model selector
to two native requests, reports coarse progress and validates the
`rust-desktop-prepare-v1` response. Native preparation fails closed when the
terrain pass or any CAMS target remains incomplete; it does not fall back to a
Python provider.

This stage is intentionally limited to model-data preparation. Nowcast HSD
retrieval/download, forecast scoring, task progress/cancellation and the Python
desktop process remain open. Model downloads are currently resolved
sequentially inside one native request so cache-lock and error ordering stay
deterministic; a clean benchmark is required before changing that scheduling.

Stage 43 extends preparation to the native nowcast data boundary. After the
existing Python HSD provider has discovered and written the two recent compact
frames, `desktop_nowcast_prepare` validates those frames in Rust, resolves the
nowcast GFS hour set including the frame timestamps, and downloads/resolves all
exact CAMS brackets natively. The Python route retains only the remote HSD
listing/range-download/cache-write boundary, then validates the native
`rust-desktop-nowcast-prepare-v1` response. Local compact HSD, GFS metadata and
ecCodes CAMS fixtures prove the path without a Python GFS/CAMS provider or
network access.

This stage does not claim remote HSD transport, HSD calibration, nowcast
scoring/retrieval, fine-grained progress/cancellation, or a Python-free desktop
process. Native nowcast preparation has the same sequential provider and
cache-lock semantics as Stage 42 until cold/warm workflow measurements justify
bounded parallelism.

Stage 44 moves the opt-in Rust desktop backend's point forecast execution into
the native engine for both GFS and EC. `DesktopService._forecast` now sends
point requests through the native context resolver, automatic frozen-input
planner, CAMS/model cache checks, native decoding, scoring and point
materialization. GFS results are read from the validated
`rust-stored-point-forecast-v1` artifact. EC results are read from the separate
`rust-ecmwf-point-report-v1` artifact and promote its `web_report_payload`
without confusing the report wrapper with the public result. Cross-section
source identity and output files are still verified by the Python adapter
before the native cross-section RPC is called.

The desktop request enables the content-addressed Rust derived-frame cache
under each configured model cache (`derived/rust-v1`). The cache is keyed by
raw source identity, model, cycle, hour and bbox and remains guarded by the
native lock/manifest validation contract. The Python bridge validates the full
tensor manifest and hashes but mmap-maps arrays instead of copying them into
the Python heap. Region forecasts, nowcast scoring/retrieval, HTML/map
publication and the public web contract remain outside this stage; native
point artifacts continue to declare `public_forecast_complete=false`.

Focused verification covers the GFS and EC bridge contracts, a real EC fixture,
desktop routing and the existing desktop group. The full Python suite passed
1871 tests after this stage; the default backend remains Python, and native
failures do not silently fall back for the Rust point path.

Stage 45 moves the opt-in Rust desktop backend's regional frame execution into
the native engine for GFS and ECMWF. The bridge reuses the existing
`rust-regional-frames-v1` artifact, maps the native desktop context points and
the `desktop_prepare` model/CAMS file lists into a frozen request, then
persists the internal regional result through the existing cache writer.
Python remains a result/cache adapter; the native frame artifact is still
explicitly `public_forecast_complete=false`.

Regional cache reads now accept only the native internal contract when the Rust
desktop backend is selected, so an older Python cache cannot silently bypass
Rust. The public summary path rejects the internal artifact and forces the
owning public computation instead of promoting incomplete data to array-v6.
Selecting `FIRESKY_DESKTOP_DATA_BACKEND=rust` also selects the Rust compact-HSD
provider/retrieval by default for desktop nowcast scoring when no explicit
`FIRESKY_HSD_BACKEND` override is present. This moves the calibrated HSD
posterior, motion and advection path consistently with the desktop selector;
GFS vertical-prior assembly, final nowcast event scoring, HTML/map publication
and remote HSD warming remain Python-owned.

Focused verification covers five regional bridge tests, including a real Rust
GFS frame artifact, cache-boundary tests, 15 nowcast/context tests and the
desktop RPC/process group. This stage does not claim a Python-free public
regional API or a complete Python-free nowcast workflow.
The complete Python suite then passed **1877 tests** in **661.93 seconds**,
with the existing FastAPI/Starlette and fork warnings.

Stage 46 adds an opt-in public regional route with
`FIRESKY_REGIONAL_DATA_BACKEND=rust`. GFS and EC model-region requests call the
same native frame executor with a separate
`rust-public-regional-forecast-v1` cache contract. The bridge first validates
the native point fields against the existing array-v6 projection, then writes
the established full cache and public summary. A Python-generated full cache
or summary is ignored while this selector is active, so it cannot hide an
unexercised Rust route.

The default remains Python, and nowcast requests still use the existing public
owner. This stage does not claim a Python-free HTTP process, provider
transport, or final nowcast workflow; it removes the Python numerical regional
scorer from the selected public model path.

The point-oriented native derived-frame cache is deliberately disabled for
regional frame requests after a warm benchmark showed extra revalidation and
profile reload cost. Point forecasts continue to use the cache; regional
frames retain their complete profile-child integrity checks.
The complete Python suite then passed **1879 tests** in **658.60 seconds**,
with the existing FastAPI/Starlette and fork warnings.

Stage 47 moves the calibrated HSD horizontal metric aggregation into the Rust
retrieval contract. The new opt-in
`rust-desktop-nowcast-hsd-retrieval-v3` response computes the same
deduplicated near/far/horizon ratios, texture, uniformity, blanket,
clearance and composition fields as `calibrated_cloud_metrics`; the Python
adapter validates and consumes those fields while retaining the v1/v2
retrieval contracts for callers that do not request metrics.

Cloudy and clear native fixtures now compare the complete metric dictionary
against the Python reference, including empty/unknown-column semantics.
GFS vertical-prior assembly, final event scoring, public nowcast transport,
HTTP/CLI ownership and remote HSD warming remain open completion gates. This
stage moves one numerical nowcast boundary, not the complete nowcast
workflow. The complete Python suite then passed **1879 tests** in
**677.79 seconds**, with the same two existing warnings.

Stage 48 adds an opt-in Rust GFS vertical-prior constructor:
`desktop_nowcast_prior` performs the left/right forecast interpolation,
nearest-column selection, terrain filtering and `VerticalPriorLevel` assembly.
The Python provider still owns cache lookup and decoded-grid extraction, then
validates the native `rust-desktop-nowcast-gfs-prior-v1` response.

The native prior fixture matches Python interpolation and prior construction.
The selector is explicit (`FIRESKY_GFS_PRIOR_BACKEND=rust`) because a measured
single-prior RPC costs about **0.488 ms** versus **0.031 ms** for the local
Python construction on the same three-level fixture. The stage now also
advertises `desktop_nowcast_prior_batch` and routes all sample points in one
frame through `rust-desktop-nowcast-gfs-prior-batch-v1`. The batch measurement
is **0.278 ms per prior**, versus **0.419 ms** for separate RPCs (1.51x
faster), but remains **8.91x** slower than local Python because each request
still repeats the feature arrays in JSON. This is transport amortization, not
an end-to-end nowcast speedup. The next stage moves those arrays to one shared
frame-level input while retaining the old batch contract for irregular domains;
the Rust selector therefore stays explicit.

Stage 49 adds `desktop_nowcast_prior_shared_batch`. It interpolates shared
left/right feature arrays once and returns one prior per target. The 24-sample
warm fixture measured **3.185 ms** versus **6.697 ms** for the previous batch
(2.10x), with JSON request bytes falling **29,810 -> 4,372** (6.82x). Local
Python remained **0.031 ms per prior**, so this is a measured transport
optimization rather than proof of a full nowcast speedup. The complete Python
suite then passed **1881 tests** in **673.03 seconds**, with the same two
existing warnings.

Stage 50 closes the first real batch-size failure found in the cached nowcast
workflow. A 900 km footprint may generate 150 samples, so shared native calls
are partitioned at 128 targets and 2,400 unique coordinate/pressure features;
the previous batch remains the irregular-domain fallback. On the cached
150-point fixture, the GFS prior batch measured **747 ms Rust / 796 ms
Python**, and complete calibrated retrieval measured **1771 ms Rust / 1849 ms
Python**. The measured end-to-end improvement is about **4%** on this warm
local fixture. The remaining completion gates still require Python-free
production entrypoints, final event scoring ownership, HTTP/CLI coverage,
packaging and reproducible representative baselines.

Stage 51 adds `score_nowcast_fields` with the
`rust-desktop-nowcast-quality-v2` contract. One call returns the complete event
component dictionary, a declared 19-field instant-value matrix and a projected
10-field per-step event ledger. It removes the repeated 75-field component
dictionary from every instant event. Parallel instant composition is bounded
to batches of at least 64 rows and has exact serial/parallel parity coverage.

The release score bridge measured **3.70x** at 20 steps, **4.93x** at 128 steps
and **4.96x** at 150 steps, while the 128-step serialized response fell from
**862,511** to **65,673** bytes. This closes final event/instant scoring as a
transport-heavy Python boundary for the existing nowcast caller, but does not
remove Python result assembly or establish an end-to-end cached-nowcast
speedup. HSD retrieval orchestration, GFS prior extraction, observation
constraints, HTTP/CLI ownership and packaged production startup remain open.

Stage 52 adds one unified calibrated-retrieval RPC for a single HSD frame. It
combines sample geometry, decoded GFS prior construction, frame-pixel
selection, calibrated posterior and horizontal metrics, then returns a
versioned file receipt instead of putting the approximately 1.2 MB result on
the persisted 1 MiB line protocol. File byte count and SHA-256 are checked
before JSON parsing.

The native frame and decoded-grid caches are bounded and keyed by complete
file versions. The HSD nearest-pixel index handles the date line and footprint
bounds, while the Python bridge reconstructs all existing objects and preserves
ordered `None` priors. Five native/Python retrieval scenarios plus receipt
tamper and oversized-file coverage protect that boundary. The final-adjustment
fixture separately proves that GFS prior provenance and levels reach the
published vertical profile.

The 9-trial real-cache fixture measured **30.20 ms** for the native file RPC,
**50.54 ms** including Python object reconstruction and **1862.57 ms** for the
Python posterior. Those are **61.67x** and **36.85x** warm retrieval-scope
speedups. They exclude HSD/CAMS/GFS download and warming, multi-frame and
motion/advection orchestration, final adjustment/event scoring, HTTP/UI and
packaging. Complete production-nowcast latency and a pure-Rust runtime remain
unverified.

Stage 53 adds a complete multi-scan nowcast adjustment boundary. Rust
now owns UTC deduplication, timeline offsets, per-scan calibrated retrieval,
ordered retrieval across up to four scans, primary selection, coverage gate,
provenance aggregation and the complete public adjustment payload. Rust also
owns the default event/instant quality ledger through `score_nowcast_fields`.
Python still owns HSD/CAMS/GFS warming, motion/advection orchestration,
per-observation physical assessment, radar constraints, report assembly,
HTTP/CLI/UI, packaging and cross-platform release validation.

The bridge fails closed on stale scoring windows, rejects contract or receipt
tampering after Rust execution and falls back only when no native retrieval
context can be prepared. On the pinned two-scan fixture, seven warm trials
measured **75.13 ms** through the complete Python wrapper versus **3063.54 ms**
for the Python reference path. This is a cache-local adjustment-assembly
measurement, not complete download-to-response latency.

Stage 54 adds the opt-in native batch per-observation assessment operation
`desktop_nowcast_assessment` (contract `rust-desktop-nowcast-assessment-v2`
inline, `rust-desktop-nowcast-assessment-file-v1` for the file receipt), covering
one to sixteen observed scans in a single engine call. Rust now owns, per scan:
the conditional-probability cloud-column construction, the `FeatureSpatialIndex`
query family, `candidate_signals` with the dual-segment aerosol path and dawn
branch, the local-deck-edge, remote-boundary-lightpath and cloud-hole signal
families, `diagnose_solar_inflow_blockage`, both weather penalties and the full
solar-path cross section with profile samples, then the `assess_timestep`
composition (extent, angular coverage, vividness, regime/scene/color/phase
diagnostics, boundary uncertainty, structure gate, duration, all reason lines,
map cells) and the radar constraints with the same field semantics as
`apply_nowcast_observation_constraints`. The operation also emits the 18-value
ledger rows with the same float32 quantization chain as
`fire_cloud_quality_from_assessments` and the canonical event/instant ledger.
Python keeps the service-side scan assembly, the `assess_satellite_snapshot`
wrapper semantics, the nowcast-side radar diagnosis, event `best_index`
and core-window selection, and the production default backend.

The feature contract extends the legacy 20-column buffer to the versioned
27-column `hsd-assessment-feature-row-v2` (adding surface temperature/dewpoint,
footprint geometry and `horizontal_cloud_probability` /
`cloud_probability_given_cloud`; absent cells travel as NaN and are never
backfilled to 0). The v2 buffer enables the native metric path that the legacy
buffer refused for satellite probabilities; kernels still consuming the 20-column
ABI (candidate/boundary prefilter, path/boundary clearance, aggregation,
interpolation, grid indexing) stay explicit Python or fail closed for v2 buffers.
The request carries a `detail` level: `"full"` keeps every diagnostic list for
acceptance differencing, `"model"` drops the lists only the service's
`TimeStepAssessment` reconstruction never reads, and unknown levels are rejected.

`FIRESKY_NOWCAST_ASSESSMENT_BACKEND` selects the path per process with
`python` as the default; an invalid value raises, and a native failure after
Rust execution propagates instead of falling back to Python. Differential
coverage on the pinned 2026-11-07 two-scan fixture and synthetic batteries
includes complete `TimeStepAssessment` field equality against the Python
reference (scores, counts, all signal tops, boundary uncertainty, cross section
with profile and summary strings, solar inflow blockage, color diagnosis, every
reason line), rows equality against the `national_batch` float32 oracle at
`rtol=3e-11`, missing-prior/no-usable-column/radar-absent/radar-low-coverage
semantics, stable-sort and boundary-merge ties, event-time selection rules,
empty scans, receipt and payload tampering rejected, and the slim `model`
projection rebuilding identical `TimeStepAssessment` objects. The complete
Python suite passed **1989 tests** after this stage.

This stage does not make the production nowcast Python-free. The backend
selector defaults to `python`, production routing is not switched, and
end-to-end forecast latency is not established. HSD/CAMS/GFS downloads and
cache warming, main-scan selection, event `best_index`/window rules,
HTTP/CLI/UI ownership, packaging and clean-machine validation remain outside
the migrated boundary. The per-scan timing in
[RUST_PERFORMANCE.md](RUST_PERFORMANCE.md#stage-54-nowcast-assessment-baseline)
is the Python baseline table; the "Stage 54: Native Batch Nowcast Assessment"
section there measures the new operation against that baseline with the same
pinned fixture — the two tables share the Stage 54 name but measure different
paths. Release SHA-256 of the measured binary is
`983894e36756de6aa6193fa6ea2c2964339941d131d4ff91f6100b31bbe9bdb7`; an earlier
intermediate measurement in that section used
`aebb2fd7f6968d60788ac41b9cf579b5dd74b61f665789516040c76a6ef0c50b` before the
`detail`-level contract bump.

## Next Nowcast Assessment Boundary

Stage 54 resolved the bounded batch assessment this section previously called
for: the engine
operation exists, is differential-tested against the Python reference on
pinned HSD/GFS fixtures (including missing priors, no usable columns, radar
absence/low coverage and tied event windows), and feeds its native rows
directly into the Rust event/instant scorer. What remains before production
routing changes is acceptance of the two backends on real service traffic and
a default-backend switch decision; the selector still defaults to `python`.
Remote HSD acquisition and raw calibration remain separate provider work, and
main-scan selection plus event `best_index`/window rules remain Python-owned
by design for this stage.

## Completion Gates

- Every production entrypoint launches and performs its complete workflows
  with Python unavailable. Static dependency and subprocess inspection must
  agree with runtime evidence, not merely an environment switch.
- GFS and EC point/regional forecasts run independently from identical pinned
  raw inputs and retain full result/diagnostic contracts. CAMS missing/invalid
  data must preserve explicit failure/absence semantics.
- Satellite/radar/nowcast, location, downloads, publication checks, cache
  refresh and export workflows retain their existing capabilities.
- Desktop IPC supports `hello`, `search`, `metadata`, `inventory`, `check`,
  `publications`, `prepare`, `forecast`, `dismiss` and `cancel` with verified
  frozen-plan, single-use confirmation, expiry, cancellation and concurrent
  request behavior. The debugging protocol alone does not pass this gate.
- Existing HTTP/CLI interfaces and security boundaries have compatibility
  tests, including progress/SSE, retries, malformed inputs and failures.
- Numerical checks cover whole forecast payloads, not just final scores.
  Resolve or explicitly reproduce the existing tied-window diagnostic
  selection contract before changing defaults.
- Cache identities and on-disk formats distinguish incompatible algorithms,
  models, timestamps, dimensions and provenance; stale cache hits cannot hide
  an unexercised Rust path.
- Release artifacts carry required native libraries/resources and complete
  clean-machine smoke tests for the supported target platforms. Local macOS
  tests do not establish Windows/Linux deployment.
- No required behavior or named gate remains unimplemented, unverified or
  dependent on a Python fallback. Only then is the overall goal complete.
- Representative complete Rust workflows have reproducible cold/warm latency,
  throughput, CPU and peak-memory baselines. Optimize measured hotspots and
  verify unchanged full outputs, failure behavior and numerical contracts.
  Component microbenchmarks alone do not satisfy this gate.
