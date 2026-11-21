#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT="$ROOT/apps/windows/FireSky/EngineClient.cs"
PROJECT="$ROOT/apps/windows/FireSky/FireSky.csproj"
PREFLIGHT="$ROOT/apps/windows/FireSky/PreflightDialog.cs"
# UI 已拆分为 partial 文件：契约 grep 覆盖全部 MainWindow 分片。
WINDOW_PARTIALS="$ROOT/apps/windows/FireSky/MainWindow.xaml.cs
$ROOT/apps/windows/FireSky/MainWindow.Data.cs
$ROOT/apps/windows/FireSky/MainWindow.History.cs
$ROOT/apps/windows/FireSky/MainWindow.Nowcast.cs
$ROOT/apps/windows/FireSky/MainWindow.Regional.cs
$ROOT/apps/windows/FireSky/MainWindow.Results.cs
$ROOT/apps/windows/FireSky/MainWindow.Settings.cs
$ROOT/apps/windows/FireSky/MainWindow.Updater.cs"
HEADER="$ROOT/engine/include/firesky_ffi.h"

test -f "$CLIENT"
test -f "$PROJECT"
test -f "$PREFLIGHT"
test -f "$HEADER"

for symbol in \
  "NativeLibrary.Load" \
  "firesky_ffi_abi_version" \
  "firesky_engine_open" \
  "firesky_engine_call" \
  "firesky_engine_start" \
  "firesky_task_cancel" \
  "firesky_task_join" \
  "firesky_task_free" \
  "firesky_ffi_string_free" \
  "ProgressCallback" \
  "RuntimeInformation.ProcessArchitecture" \
  "aarch64-pc-windows-msvc" \
  "x86_64-pc-windows-msvc" \
  "TrySetCanceled" \
  "Cleanup.Task"
do
  rg -q "$symbol" "$CLIENT"
done
rg -F -q "Path.GetExtension(engineBinary)" "$CLIENT"

for target in \
  "win-x64" \
  "win-arm64" \
  "x86_64-pc-windows-msvc" \
  "aarch64-pc-windows-msvc" \
  "firesky_engine.dll" \
  "--lib"
do
  rg -q -- "$target" "$PROJECT"
done

for method in check prepare; do
  rg -q "\"$method\"" "$PREFLIGHT"
done
for method in \
  retention_settings \
  save_retention_settings \
  profile_query \
  task_history \
  task_events \
  data_delete_preview \
  data_delete_execute \
  terrain_inventory \
  terrain_download \
  cams_credentials \
  save_cams_key \
  network_probe \
  data_sync_settings \
  save_data_sync_settings \
  data_sync_due \
  data_sync_run
do
  if ! rg -q "\"$method\"" $WINDOW_PARTIALS; then
    echo "Windows client does not expose the required Rust method: $method" >&2
    exit 1
  fi
done
for capability in profile_query task_history task_events result_restore; do
  rg -F -q "\"$capability\"" $WINDOW_PARTIALS
done
for method in task_forget result_restore data_sync_run; do
  rg -F -q "\"$method\"" $WINDOW_PARTIALS
done
rg -F -q '"city_ranking"' $WINDOW_PARTIALS
rg -q '"forecast"' $WINDOW_PARTIALS
rg -q '\["confirmed"\][[:space:]]*=[[:space:]]*true' $WINDOW_PARTIALS
rg -F -q 'if (page == "nowcast")' $WINDOW_PARTIALS
rg -F -q 'payload["forecast_mode"] = "nowcast"' $WINDOW_PARTIALS
rg -F -q 'payload["model_source"] = "satellite"' $WINDOW_PARTIALS
rg -F -q 'payload["satellite_source"] = "auto"' $WINDOW_PARTIALS
# v1.5.0 契约：周期龄确认与 elevation/ecmwf 来源透传。
rg -F -q 'cycle_age_confirmed' $WINDOW_PARTIALS
rg -F -q '"elevation_source"' $WINDOW_PARTIALS
rg -F -q '"ecmwf_source"' $WINDOW_PARTIALS
for resource_contract in \
  "RESOURCE-MANIFEST.json" \
  "FIRESKY_REQUIRE_APP_RESOURCES" \
  "packagedInstall" \
  "requireAppResources" \
  "appDataRoot" \
  "manifestPath"
do
  rg -F -q "$resource_contract" "$CLIENT"
done
rg -F -q 'if (backend == "jsonl")' "$CLIENT"
rg -F -q 'if (backend == "ffi" || packagedInstall)' "$CLIENT"
rg -F -q 'else if (backend == "ffi" || packagedInstall)' "$CLIENT"
if rg -F -q 'Environment.SetEnvironmentVariable("FIRESKY_RESOURCE_DIR", null)' "$CLIENT" ||
   rg -F -q 'Environment.SetEnvironmentVariable("FIRESKY_REQUIRE_APP_RESOURCES", null)' "$CLIENT"; then
  echo "Windows FFI setup must not clear resource environment variables on a missing package" >&2
  exit 1
fi
if rg -q 'SynchronousMethods|CallSync' "$CLIENT"; then
  echo "Windows EngineClient must keep all desktop calls on the async FFI task path" >&2
  exit 1
fi

if rg -qi "python" "$CLIENT"; then
  echo "Windows EngineClient must not mention or launch Python" >&2
  exit 1
fi

if command -v xmllint >/dev/null 2>&1; then
  xmllint --noout "$PROJECT"
fi

echo "Windows FFI contract: ok (static; dotnet/Windows runtime not exercised)"
