# FireSky Linux Native FFI

This directory contains the Linux-native FFI foundation and a small GTK4
workflow shell. The shell is deliberately compact rather than a finished
feature-parity desktop: it proves the native UI boundary and the shared
preflight lifecycle before a full map/report surface is added. GTK, Qt, or
another native toolkit can reuse the same `firesky::Engine` and
`firesky::Task` classes without reimplementing the C ABI ownership rules.

The client loads `libfiresky_engine.so` with `dlopen`, verifies ABI version 1,
opens a Rust runtime, and exposes:

- synchronous versioned JSON calls;
- cancellable asynchronous tasks;
- progress callbacks that copy Rust's temporary event string immediately;
- RAII cleanup for task handles, engine handles, returned strings, and
  `dlclose`.

Progress callbacks run on Rust worker threads. A future GTK/Qt front end must
queue those events onto its main loop before changing widgets. `Task` owns the
runtime/library state while it is alive, so destroying an `Engine` does not
unload the shared object underneath an in-flight task.

The optional GTK4 shell follows the same Rust lifecycle as the macOS/Windows
clients and exposes `point`, `regional` and `nowcast` modes. It is still a
compact native shell: its completeness check, preparation, and forecast
actions are currently separate controls rather than the modal preflight sheet
used by the macOS baseline. The Rust session/confirmation contract is the
same, but this UI presentation remains a Linux acceptance task.

1. Connect and validate `hello` through the FFI.
2. Run `check` for the selected point, 0.25-degree custom region, or nowcast
   payload and display the completeness report.
3. Run `prepare` only after the user chooses to fill missing inputs.
4. Run `forecast` only after the report is ready and send
   `confirmed: true`.

The GTK shell also exposes the shared native contracts used by the macOS
baseline:

- cache retention settings for GFS, ECMWF, CAMS, satellite, report and map
  data, persisted by Rust through `retention_settings` and
  `save_retention_settings`;
- immutable frozen-result `profile_query` requests, including source identity,
  model, coordinates and valid UTC time;
- a separate regional city-ranking contract view after a regional forecast.

These controls call the Rust FFI asynchronously and do not start a Python
subprocess. The GTK result pane remains raw JSON for now, while the ranking
contract is shown in a dedicated expandable view.

Regional bounds accept both ordinary `west < east` intervals and wrapped
dateline intervals (`west > east`), while rejecting zero-width longitude and
latitude spans. This matches the macOS/Rust contract. Nowcast selects
`forecast_mode=nowcast`, `model_source=satellite` and `satellite_source=auto`.

The shell exposes a Cancel action for the active task and marshals progress
events back to the GTK main loop. It intentionally displays the raw versioned
JSON result while the detailed map/report presentation is still follow-up
work.

## Build and smoke

On Linux with Cargo, CMake, a C++20 compiler and GTK4 development headers:

```sh
./scripts/test_linux_ffi.sh
```

The script builds `engine` as a `cdylib`, compiles the client and performs
sync/async `hello`, retention, profile-query and preflight calls. It sets
`FIRESKY_REQUIRE_APP_RESOURCES=1` and includes a negative smoke that verifies
the FFI runtime fails closed when the immutable resource bundle is missing.
When GTK4 is installed, the same CMake configure also builds
`firesky_linux_native`. Set `FIRESKY_ENGINE_FFI_LIBRARY` to use a prebuilt
library. On non-Linux hosts the script exits with a clear skip message.

Debian/Ubuntu prerequisites:

```sh
sudo apt-get install build-essential cmake pkg-config libgtk-4-dev
```

If GTK4 is unavailable, configuration prints a skip message and still builds
the FFI library and headless smoke executable.

Manual build:

```sh
cmake -S apps/linux -B build/linux-ffi -DCMAKE_BUILD_TYPE=Release
cmake --build build/linux-ffi --parallel
build/linux-ffi/firesky_linux_ffi_smoke \
  engine/target/release/libfiresky_engine.so "$PWD"
```

When GTK4 is available, start the native shell with:

```sh
build/linux-ffi/firesky_linux_native
```

The current GTK shell has no Python subprocess path. Linux packaging,
Wayland/X11 location permissions, map integration, full report parity and a
real GTK4 target-host build/runtime smoke remain acceptance gates; this
macOS host cannot prove those Linux-specific gates.

