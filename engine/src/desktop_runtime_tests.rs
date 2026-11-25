use super::*;

#[test]
fn hello_is_python_free_and_advertises_native_nowcast() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let value = runtime.dispatch("hello", json!({})).unwrap();
    assert_eq!(value["runtime_python_used"], false);
    assert_eq!(value["production_desktop_complete"], false);
    assert!(value["capabilities"]
        .as_array()
        .unwrap()
        .contains(&json!("nowcast")));
    assert!(value["capabilities"]
        .as_array()
        .unwrap()
        .contains(&json!("result_restore")));
    assert!(value["capabilities"]
        .as_array()
        .unwrap()
        .contains(&json!("data_sync")));
    assert!(runtime
        .dispatch("check", json!({"kind":"nowcast","payload":{}}))
        .is_err());
}

#[test]
fn nowcast_session_never_claims_ready_without_satellite_inputs() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let checked = runtime
        .dispatch(
            "check",
            json!({
                "kind":"nowcast","payload":{
                    "lat":30.2741,"lon":120.1551,"elevation_m":30,
                    "timezone_name":"Asia/Shanghai","target_date":"2026-11-07",
                    "phenomenon":"dusk","radius_km":900,
                    "reference_time_utc":"2026-11-07T12:00:00Z",
                    "_gfs_cycle_override":"2026-11-07T00:00:00Z",
                },
            }),
        )
        .unwrap();
    assert_eq!(checked["report"]["ready"], false);
    assert_eq!(checked["report"]["satellite_frames"], 0);
    let token = checked["session"].as_str().unwrap();
    let snapshot = runtime.session("get", token, json!({})).unwrap();
    assert_eq!(snapshot["kind"], "nowcast");
    assert_eq!(
        snapshot["payload"]["reference_time_utc"],
        checked["report"]["reference_time_utc"]
    );
    let denied = runtime
        .dispatch("forecast", json!({"session":token,"confirmed":true}))
        .unwrap_err();
    assert!(denied.contains("prepare data"));
    runtime
        .dispatch("dismiss", json!({"session":token}))
        .unwrap();
    assert!(runtime.session("get", token, json!({})).is_err());
}

#[test]
fn sessions_are_owner_scoped_and_single_use() {
    let dir = tempfile::tempdir().unwrap();
    let a = Runtime::new(dir.path()).unwrap();
    let b = Runtime::new(dir.path()).unwrap();
    let created = a
        .session(
            "create",
            "",
            json!({
                "kind":"point","payload":{},"report":{"ready":true},
            }),
        )
        .unwrap();
    let token = created["session"].as_str().unwrap();
    assert!(b.session("get", token, json!({})).is_err());
    a.session("consume", token, json!({})).unwrap();
    assert!(a.session("consume", token, json!({})).is_err());
}

#[test]
fn forecast_rejects_unconfirmed_or_not_ready_before_filesystem_work() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let created = runtime
        .session(
            "create",
            "",
            json!({
                "kind":"point","payload":{},"report":{"ready":false},
            }),
        )
        .unwrap();
    let params = json!({"session":created["session"]});
    assert!(runtime
        .dispatch("forecast", params.clone())
        .unwrap_err()
        .contains("confirmation"));
    let mut params = params;
    params["confirmed"] = json!(true);
    assert!(runtime
        .dispatch("forecast", params)
        .unwrap_err()
        .contains("missing"));
    assert!(runtime
        .session("get", created["session"].as_str().unwrap(), json!({}))
        .is_ok());
}

#[test]
fn result_restore_recovers_snapshot_without_raw_cache_and_fails_closed_on_missing_report() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let html = b"<html>historical</html>";
    let sha: String = Sha256::digest(html)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    let html_dir = runtime.report_dir.join("native-html");
    fs::create_dir_all(&html_dir).unwrap();
    fs::write(html_dir.join(format!("{sha}.html")), html).unwrap();
    let url = format!("/api/v1/firesky/reports/{sha}.html");
    let result = json!({
        "model_results":{"gfs":{"html_report_url":url,"score":0.7}},"model_errors":{},
    });
    let task = runtime
        .begin_task(
            "restore-req",
            "forecast",
            &json!({"kind":"point","payload":{"name":"Hangzhou"}}),
        )
        .unwrap()
        .unwrap();
    let id = task.id().to_owned();
    task.finish(&Ok(result.clone()), false).unwrap();
    // No runtime cache record was ever published, yet the snapshot restores.
    assert_eq!(
        runtime
            .dispatch("result_restore", json!({"task_id":id}))
            .unwrap(),
        result
    );
    assert!(runtime.dispatch("result_restore", json!({})).is_err());
    assert!(runtime
        .dispatch("result_restore", json!({"task_id":"a".repeat(64)}))
        .is_err());
    // Removing the external report artifact makes restore fail closed.
    fs::remove_file(html_dir.join(format!("{sha}.html"))).unwrap();
    assert!(runtime
        .dispatch("result_restore", json!({"task_id":id}))
        .is_err());
}

#[test]
fn ready_unverified_or_older_cycle_requires_separate_confirmation() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let created = runtime
        .session(
            "create",
            "",
            json!({
                "kind":"regional","payload":{},
                "report":{"ready":true,"datasets":[{
                    "source":"gfs","ready":true,"verified":true,"is_latest":false,
                }]},
            }),
        )
        .unwrap();
    let token = created["session"].as_str().unwrap();
    let error = runtime
        .dispatch("forecast", json!({"session":token,"confirmed":true}))
        .unwrap_err();
    assert!(error.contains("cycle-age confirmation"));
    assert!(runtime.session("get", token, json!({})).is_ok());

    let report = json!({"ready":true,"datasets":[{
        "ready":true,"verified":true,"is_latest":true,
    }]});
    assert!(!needs_cycle_age_confirmation(&report));
    let unverified = json!({"ready":true,"datasets":[{
        "ready":true,"verified":false,"is_latest":false,
    }]});
    assert!(needs_cycle_age_confirmation(&unverified));
}

#[test]
fn cancelled_compute_does_not_start_a_context_or_provider() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let result = runtime.compute_with_cancel(json!({}), false, Arc::new(AtomicBool::new(true)));
    assert!(result.unwrap_err().contains("cancelled"));
}

/// Regional requests run exactly one model per call; "both" must be
/// rejected before any planning work, and an explicit choice is kept.
#[test]
fn regional_check_requires_a_single_model() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let payload = json!({
        "regions": ["east-asia"], "target_date": "2026-11-13",
        "phenomenon": "dusk", "model_source": "both",
    });
    let error = runtime
        .dispatch("check", json!({"kind":"regional","payload":payload}))
        .unwrap_err();
    assert!(error.contains("one model per request"), "{error}");
    let mut payload = payload.clone();
    payload["model_source"] = json!("ec");
    let checked = runtime
        .dispatch("check", json!({"kind":"regional","payload":payload}))
        .unwrap();
    let token = checked["session"].as_str().unwrap();
    let snapshot = runtime.session("get", token, json!({})).unwrap();
    assert_eq!(snapshot["payload"]["model_source"], json!("ec"));
    assert_eq!(snapshot["payload"]["spacing_deg"], json!(0.25));
    runtime
        .dispatch("dismiss", json!({"session":token}))
        .unwrap();
}

#[test]
fn native_cache_keys_are_scope_specific_and_force_refresh_independent() {
    assert_eq!(
        cache_key(&json!({"model_source":"gfs"}), false).unwrap(),
        cache_key(&json!({"force_refresh":true,"model_source":"gfs"}), false).unwrap()
    );
    assert_ne!(
        cache_key(&json!({"model_source":"gfs"}), false).unwrap(),
        cache_key(&json!({"model_source":"gfs"}), true).unwrap()
    );
}

#[test]
fn native_registry_verifies_report_checksum_and_scope() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = Runtime::new(dir.path()).unwrap();
    runtime.report_dir = dir.path().join("reports");
    runtime.point_cache_dir = dir.path().join("point-cache");
    runtime.regional_cache_dir = dir.path().join("regional-cache");
    runtime.map_cache_dir = dir.path().join("map-cache");
    let cycle = "2026-11-11T00:00:00Z";
    let payload =
        json!({"model_source":"gfs","target_date":"2026-11-11","gfs_cycle_override":cycle});
    let source = dir.path().join("input.grib");
    fs::write(&source, b"native-fixture").unwrap();
    let identity = tensor_bundle::regular_identity(&source).unwrap();
    let html = b"<html>native-cache-fixture</html>";
    let html_id = format!(
        "{}.html",
        Sha256::digest(html)
            .iter()
            .map(|v| format!("{v:02x}"))
            .collect::<String>()
    );
    fs::create_dir_all(runtime.report_dir.join("native-html")).unwrap();
    fs::write(runtime.report_dir.join("native-html").join(&html_id), html).unwrap();
    let key = cache_key(&payload, false).unwrap();
    let value = runtime
        .publish_cache(
            &payload,
            false,
            &key,
            json!({"model_results":{"gfs":{"forecast_mode":"model","cycle_time_utc":cycle,
                "html_report_url":format!("/api/v1/firesky/reports/{html_id}"),
                "input_manifest":{"sources":[{"path":source,"identity":identity}]}}},"model_errors":{}}),
            &AtomicBool::new(false),
        )
        .unwrap();
    let cached = runtime.read_cache(json!({"key":key})).unwrap();
    assert_eq!(cached["model_results"]["gfs"]["cache"]["status"], "hit");
    assert!(runtime
        .read_cache(json!({"key":key,"regional":true}))
        .is_err());
    assert!(runtime.read_cache(json!({"key":"../../escape"})).is_err());
    fs::write(&source, b"changed-fixture").unwrap();
    assert!(runtime
        .read_cache(json!({"key":key}))
        .unwrap_err()
        .contains("source identity"));
    fs::write(&source, b"native-fixture").unwrap();
    let report = runtime
        .report_dir
        .join(value["cache"]["report_file"].as_str().unwrap());
    fs::write(report, b"{}").unwrap();
    assert!(runtime
        .read_cache(json!({"key":key}))
        .unwrap_err()
        .contains("integrity"));
}

#[test]
fn cancelled_result_is_not_published() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let payload = json!({"model_source":"gfs"});
    let key = cache_key(&payload, false).unwrap();
    assert!(runtime
        .publish_cache(
            &payload,
            false,
            &key,
            json!({"model_results":{},"model_errors":{}}),
            &AtomicBool::new(true)
        )
        .unwrap_err()
        .contains("cancelled"));
}

fn save_cams_only_sync_settings(workspace: &Path) {
    crate::desktop_sync::save(
        workspace,
        json!({"settings":{"enabled":true,"sources":{"cams":true},
            "interval_minutes":60,"anchor_region":"china"}}),
    )
    .unwrap();
}

#[test]
fn data_sync_yields_to_manual_work_and_serializes_rounds() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    save_cams_only_sync_settings(dir.path());
    // Manual prepare/forecast holds the compute lock; the round yields
    // with the "active" keyword that maps to the FFI busy code.
    let operation = runtime.operation.try_lock().unwrap();
    let error = runtime
        .dispatch("data_sync_run", json!({"sources":["cams"]}))
        .unwrap_err();
    assert!(error.contains("another prepare/forecast is active"));
    assert!(error.contains("active"));
    drop(operation);
    // A second concurrent round is rejected by the dedicated sync mutex.
    let sync = runtime.sync.try_lock().unwrap();
    let error = runtime
        .dispatch("data_sync_run", json!({"sources":["cams"]}))
        .unwrap_err();
    assert!(error.contains("another data sync is active"));
    // due() reports busy (and skips the probe) while a round is running;
    // sources that are not enabled in settings stay "disabled".
    let value = runtime.dispatch("data_sync_due", json!({})).unwrap();
    assert_eq!(value["sources"]["cams"]["reason"], "busy");
    assert_eq!(value["sources"]["gfs"]["reason"], "disabled");
    assert_eq!(value["due_for_any"], false);
    drop(sync);
}

#[test]
fn data_sync_settings_persist_and_report_merged_status() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let saved = runtime
        .dispatch(
            "save_data_sync_settings",
            json!({"settings":{"enabled":true,"sources":{"cams":true,"gfs":true},
                "interval_minutes":90,"anchor_region":"china","lead_horizon_hours":120,
                "ecmwf_source":"aws"}}),
        )
        .unwrap();
    assert_eq!(saved["settings"]["enabled"], true);
    assert_eq!(saved["retention_configured"], false);
    let status = runtime.dispatch("data_sync_settings", json!({})).unwrap();
    assert_eq!(status["configured"], true);
    assert_eq!(status["settings"]["interval_minutes"], 90);
    assert_eq!(status["state"]["contract"], "rust-data-sync-state-v1");
    // Invalid payloads are rejected and leave the saved settings intact.
    assert!(runtime
        .dispatch(
            "save_data_sync_settings",
            json!({"settings":{"enabled":true}})
        )
        .is_err());
    assert!(runtime
        .dispatch(
            "save_data_sync_settings",
            json!({"settings":{"enabled":true,"sources":{"cams":true,"gfs":true},
                "interval_minutes":90,"anchor_region":"china"}})
        )
        .is_err());
    let status = runtime.dispatch("data_sync_settings", json!({})).unwrap();
    assert_eq!(status["settings"]["interval_minutes"], 90);
    assert_eq!(status["settings"]["lead_horizon_hours"], 120);
}

#[test]
fn data_sync_run_rejects_disabled_state_and_unlisted_sources_without_probing() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    assert!(runtime
        .dispatch("data_sync_run", json!({"sources":["cams"]}))
        .unwrap_err()
        .contains("not enabled"));
    // Empty and unknown source lists are rejected before any state write.
    assert!(runtime.dispatch("data_sync_run", json!({})).is_err());
    assert!(runtime
        .dispatch("data_sync_run", json!({"sources":[]}))
        .is_err());
    assert!(runtime
        .dispatch("data_sync_run", json!({"sources":["satellite"]}))
        .is_err());
    save_cams_only_sync_settings(dir.path());
    let error = runtime
        .dispatch("data_sync_run", json!({"sources":["gfs"]}))
        .unwrap_err();
    assert!(error.contains("gfs is not enabled in settings"));
}

#[test]
fn data_sync_rounds_are_tracked_as_cancellable_history_tasks() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let task = runtime
        .begin_task("sync-req-1", "data_sync", &json!({"sources":["cams"]}))
        .unwrap()
        .unwrap();
    let id = task.id().to_owned();
    task.finish(&Ok(json!({"sources":{}})), false).unwrap();
    let list = runtime
        .dispatch("task_history", json!({"limit":10}))
        .unwrap();
    assert_eq!(list["tasks"][0]["method"], "data_sync");
    assert_eq!(list["tasks"][0]["status"], "done");
    assert_eq!(list["tasks"][0]["task_id"], id);
    // A cancelled round is a terminal cancelled state with no published result.
    let task = runtime
        .begin_task("sync-req-2", "data_sync", &json!({"sources":[]}))
        .unwrap()
        .unwrap();
    task.finish(&Ok(json!({"sources":{}})), true).unwrap();
    let list = runtime
        .dispatch("task_history", json!({"limit":10}))
        .unwrap();
    let cancelled = list["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|task| task["method"] == "data_sync" && task["status"] == "cancelled")
        .cloned()
        .unwrap();
    let detail = runtime
        .task_history_detail(cancelled["task_id"].as_str().unwrap())
        .unwrap();
    assert_eq!(detail["result_ref"], Value::Null);
}

#[test]
fn terrain_download_records_workflow_history() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    // v1.9.5 回归：runtime 与 TaskHistory 两份白名单必须同步——此前
    // runtime 侧放行 terrain_download、TaskHistory::begin 侧拒绝，
    // GUI 地形下载报 "method does not create workflow history"。
    let task = runtime
        .begin_task("terrain-req-1", "terrain_download", &json!({"region":"中国"}))
        .unwrap()
        .unwrap();
    task.finish(&Ok(json!({"tile_count": 1})), false).unwrap();
    let list = runtime
        .dispatch("task_history", json!({"limit":10}))
        .unwrap();
    assert_eq!(list["tasks"][0]["method"], "terrain_download");
    assert_eq!(list["tasks"][0]["status"], "done");
    // 读取类地形方法不产生历史。
    assert!(runtime
        .begin_task("terrain-req-2", "terrain_inventory", &json!({}))
        .unwrap()
        .is_none());
}

#[test]
fn data_sync_wire_method_records_history_under_design_label() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    // The wire method is `data_sync_run`; the history label stays `data_sync`
    // (design 7.2), so a round that fails contract validation is still a
    // queryable failed task instead of vanishing from the workflow record.
    let task = runtime
        .begin_task("sync-wire-1", "data_sync_run", &json!({"sources":[]}))
        .unwrap()
        .unwrap();
    task.finish(
        &Err("data sync requires an explicit nonempty sources array".into()),
        false,
    )
    .unwrap();
    let list = runtime
        .dispatch("task_history", json!({"limit":10}))
        .unwrap();
    assert_eq!(list["tasks"][0]["method"], "data_sync");
    assert_eq!(list["tasks"][0]["status"], "failed");
    // Read-only setup methods never create workflow history.
    assert!(runtime
        .begin_task("sync-wire-2", "data_sync_due", &json!({}))
        .unwrap()
        .is_none());
    assert!(runtime
        .begin_task("sync-wire-3", "save_data_sync_settings", &json!({}))
        .unwrap()
        .is_none());
}

#[test]
fn regional_temp_derived_cache_is_reused_by_cloned_city_request_and_cleaned_on_drop() {
    let dir = tempfile::tempdir().unwrap();
    let expected_cache_dir = dir.path().join("derived-cache");
    let request = json!({
        "model": "gfs",
        "cycle_utc": "2026-11-11T00:00:00Z",
        "derived_cache": regional_temp_derived_cache(dir.path()),
    });
    let mut city_request = request.clone();
    city_request["points"] = json!([]);
    assert_eq!(city_request["derived_cache"], request["derived_cache"]);
    let config: crate::derived_cache::Config =
        serde_json::from_value(city_request["derived_cache"].clone()).unwrap();
    assert_eq!(config.directory.as_deref(), Some(expected_cache_dir.as_path()));
    assert_eq!(config.lock_timeout_seconds, 30.0);
    if let Ok(codes) = crate::grib::EcCodes::load() {
        let source = dir.path().join("frame.grib2");
        fs::write(&source, b"synthetic-grib-payload").unwrap();
        let identity = tensor_bundle::source_identity(&source).unwrap();
        let sample_data = || crate::derived_cache::Data {
            latitudes: vec![30.0, 30.0, 31.0, 31.0],
            longitudes: vec![110.0, 111.0, 110.0, 111.0],
            surface: vec![100.0; 4],
            derived: crate::native::derivation::DerivedGrid {
                levels: vec![300],
                columns: vec![[0.0; 36]; 4],
                profiles: vec![[0.0; 24]; 4],
            },
            boundary_semantics: "instantaneous".into(),
            ec_proxy_coverage: None,
            ec_native_grid_shape: None,
        };
        let mut primary =
            crate::derived_cache::Session::new(Some(config.clone()), dir.path(), &codes)
                .unwrap();
        primary
            .frame(
                &source,
                &identity,
                assessment::Model::Gfs,
                "2026-11-11T00:00:00Z",
                6,
                None,
                || Ok(sample_data()),
            )
            .unwrap();
        assert_eq!(primary.frames[0]["status"], "miss");
        assert!(expected_cache_dir.is_dir());

        let mut city_ranking =
            crate::derived_cache::Session::new(Some(config), dir.path(), &codes).unwrap();
        city_ranking
            .frame(
                &source,
                &identity,
                assessment::Model::Gfs,
                "2026-11-11T00:00:00Z",
                6,
                None,
                || panic!("city ranking must hit temp derived cache without re-decoding"),
            )
            .unwrap();
        assert_eq!(city_ranking.frames[0]["status"], "hit");
    }
    drop(dir);
    assert!(!expected_cache_dir.exists());
}

#[test]
fn watchdog_exits_immediately_without_thirty_second_stall() {
    let dir = tempfile::tempdir().unwrap();
    let runtime = Runtime::new(dir.path()).unwrap();
    let started = Instant::now();
    let cancel = Arc::new(AtomicBool::new(false));
    let err = runtime
        .dispatch_cancellable(
            "check",
            json!({"kind": "nowcast", "payload": {}}),
            Arc::clone(&cancel),
            &|_| Ok(()),
            None,
        )
        .unwrap_err();
    assert!(!err.is_empty());
    let ok = runtime
        .dispatch_cancellable(
            "check",
            json!({
                "kind": "nowcast",
                "payload": {
                    "lat": 30.2741,
                    "lon": 120.1551,
                    "elevation_m": 30,
                    "timezone_name": "Asia/Shanghai",
                    "target_date": "2026-11-07",
                    "phenomenon": "dusk",
                    "radius_km": 900,
                    "reference_time_utc": "2026-11-07T12:00:00Z",
                    "_gfs_cycle_override": "2026-11-07T00:00:00Z",
                },
            }),
            cancel,
            &|_| Ok(()),
            None,
        )
        .unwrap();
    assert!(ok.get("session").and_then(Value::as_str).is_some());
    assert!(
        started.elapsed() < std::time::Duration::from_secs(5),
        "dispatch_cancellable stalled for {:?}",
        started.elapsed()
    );
}
