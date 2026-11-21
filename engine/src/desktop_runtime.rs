//! Rust-owned desktop workflow orchestration and bounded private IPC.
use crate::{
    assessment, cross_section, desktop_context, desktop_credentials, desktop_hsd,
    desktop_inventory, desktop_network, desktop_nowcast, desktop_nowcast_prepare,
    desktop_preflight, desktop_prepare, desktop_publications, desktop_session, ecmwf_plan,
    input_plan, location, request_file,
    rpc::{self, Line},
    tensor_bundle, time_contract,
};
use chrono::{Duration, Timelike, Utc};
use serde::{de::DeserializeOwned, Deserialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{self, BufRead, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
    time::Instant,
};

static OWNERS: AtomicU64 = AtomicU64::new(0);

pub struct Runtime {
    workspace: PathBuf,
    inventory: desktop_inventory::Config,
    location: location::Config,
    timezone_manifest: Option<PathBuf>,
    city_catalog_path: PathBuf,
    report_dir: PathBuf,
    point_cache_dir: PathBuf,
    regional_cache_dir: PathBuf,
    map_cache_dir: PathBuf,
    owner: String,
    history: Arc<crate::task_history::TaskHistory>,
    operation: Mutex<()>,
    network_probe: Mutex<()>,
    sync: Mutex<()>,
    terrain: Mutex<()>,
}

fn decode<T: DeserializeOwned>(value: Value) -> Result<T, String> {
    serde_json::from_value(value).map_err(|e| e.to_string())
}

fn check(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("desktop operation cancelled".into())
    } else {
        Ok(())
    }
}

fn json_file(path: &Path, value: &Value) -> Result<(), String> {
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    serde_json::to_writer(&mut file, value).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())
}

fn env_path(workspace: &Path, key: &str, default: &str) -> PathBuf {
    let path = std::env::var_os(key)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(default));
    if path.is_absolute() {
        path
    } else {
        workspace.join(path)
    }
}

fn hsd_cache_dir(workspace: &Path) -> PathBuf {
    if std::env::var_os("FIRESKY_HSD_CACHE_DIR").is_some() {
        crate::paths::resolve(workspace, "FIRESKY_HSD_CACHE_DIR", ".cache/himawari-hsd", "himawari-hsd")
    } else {
        crate::paths::resolve(workspace, "FIRESKY_SATELLITE_CACHE_DIR", ".cache/himawari-hsd", "himawari-hsd")
    }
}

impl Runtime {
    pub fn new(workspace: &Path) -> Result<Self, String> {
        let workspace = workspace.canonicalize().map_err(|e| e.to_string())?;
        if !workspace.is_dir() {
            return Err("desktop workspace must be a directory".into());
        }
        let resources = crate::app_resources::resolve(&workspace)?;
        let inventory: desktop_inventory::Config = decode(json!({
            "gfs_cache_dir":crate::paths::resolve(&workspace,"FIRESKY_GFS_CACHE_DIR",".cache/gfs","gfs"),
            "ecmwf_cache_dir":crate::paths::resolve(&workspace,"FIRESKY_ECMWF_CACHE_DIR",".cache/ecmwf","ecmwf"),
            "cams_cache_dir":crate::paths::resolve(&workspace,"FIRESKY_CAMS_CACHE_DIR",".cache/cams","cams"),
        }))?;
        inventory.validate()?;
        let timezone_manifest = resources.timezone_manifest.clone();
        let location = decode(json!({
            "location_cache_path":crate::paths::resolve(&workspace,"FIRESKY_LOCATIONS_PATH",".cache/locations.json","locations.json"),
            "srtm_cache_dir":crate::paths::resolve(&workspace,"FIRESKY_SRTM_CACHE_DIR",".cache/srtm","srtm"),
            "timezone_manifest":timezone_manifest,
        }))?;
        let history = crate::task_history::TaskHistory::open(&crate::paths::resolve(
            &workspace,
            "FIRESKY_TASK_HISTORY_DIR",
            ".firesky/state/tasks",
            "state/tasks",
        ))?;
        Ok(Self {
            report_dir: crate::paths::resolve(&workspace, "FIRESKY_REPORT_DIR", "reports/web", "reports"),
            point_cache_dir: crate::paths::resolve(
                &workspace,
                "FIRESKY_POINT_CACHE_DIR",
                ".cache/point-forecasts",
                "point-forecasts",
            ),
            regional_cache_dir: crate::paths::resolve(
                &workspace,
                "FIRESKY_REGIONAL_CACHE_DIR",
                ".cache/regional-forecasts",
                "regional-forecasts",
            ),
            map_cache_dir: crate::paths::resolve(&workspace, "FIRESKY_MAP_CACHE_DIR", ".cache/map-frames", "map-frames"),
            workspace,
            inventory,
            location,
            timezone_manifest,
            city_catalog_path: resources.city_catalog,
            history,
            owner: format!(
                "runtime-{}-{}",
                std::process::id(),
                OWNERS.fetch_add(1, Ordering::Relaxed)
            ),
            operation: Mutex::new(()),
            network_probe: Mutex::new(()),
            sync: Mutex::new(()),
            terrain: Mutex::new(()),
        })
    }

    fn context_input(&self, payload: &Value, regional: bool, fast: bool) -> Value {
        json!({
            "payload":payload,"regional":regional,"fast":fast,
            "now_utc":std::env::var("FIRESKY_CONTEXT_NOW_UTC").ok(),
            "config":{
                "gfs_cache_dir":self.inventory.gfs_cache_dir,
                "ecmwf_cache_dir":self.inventory.ecmwf_cache_dir,
                "cams_cache_dir":self.inventory.cams_cache_dir,
                "timezone_manifest":self.timezone_manifest,
                "city_catalog_path":self.city_catalog_path,
            },
        })
    }

    fn context(&self, payload: &Value, regional: bool, fast: bool) -> Result<Value, String> {
        let dir = tempfile::tempdir().map_err(|e| e.to_string())?;
        let path = dir.path().join("request.json");
        json_file(&path, &self.context_input(payload, regional, fast))?;
        desktop_context::resolve_path(&path)
    }

    fn session(&self, action: &str, token: &str, fields: Value) -> Result<Value, String> {
        let mut params = fields.as_object().cloned().unwrap_or_default();
        params.insert("action".into(), json!(action));
        params.insert("owner".into(), json!(self.owner));
        if !token.is_empty() {
            params.insert("session".into(), json!(token));
        }
        desktop_session::execute(decode(Value::Object(params))?)
    }

    fn models(payload: &Value) -> Result<Vec<&'static str>, String> {
        match payload["model_source"].as_str().unwrap_or("both") {
            "both" => Ok(vec!["gfs", "ec"]),
            "gfs" => Ok(vec!["gfs"]),
            "ec" => Ok(vec!["ec"]),
            _ => Err("model_source must be gfs, ec or both".into()),
        }
    }

    fn normalized(payload: Value) -> Result<Value, String> {
        let mut payload = payload
            .as_object()
            .cloned()
            .ok_or("payload must be an object")?;
        if payload.get("forecast_mode").and_then(Value::as_str) == Some("nowcast") {
            return Err("Rust desktop nowcast provider lifecycle is not implemented".into());
        }
        payload.insert("forecast_mode".into(), json!("model"));
        payload.insert("satellite_source".into(), json!("off"));
        payload.insert("workers".into(), json!(1));
        payload.entry("phenomenon").or_insert(json!("dusk"));
        payload.entry("model_source").or_insert(json!("gfs"));
        Ok(Value::Object(payload))
    }

    fn plan(context: &Value) -> Result<Value, String> {
        let times = context["times"].as_array().ok_or("context has no times")?;
        let mut brackets = BTreeSet::new();
        for target in times {
            let time = time_contract::parse_utc(target.as_str().ok_or("invalid context time")?)?;
            let left = time
                .with_hour(time.hour() / 3 * 3)
                .and_then(|v| v.with_minute(0))
                .and_then(|v| v.with_second(0))
                .and_then(|v| v.with_nanosecond(0))
                .ok_or("CAMS bracket arithmetic failed")?;
            brackets.insert(time_contract::iso(left));
            if time != left {
                brackets.insert(time_contract::iso(left + Duration::hours(3)));
            }
        }
        Ok(json!({
            "model_source":context["common"]["model_source"],"cycle_utc":context["cycle"],
            "event_start":times.first().ok_or("context time list is empty")?,
            "bbox":context["bbox"],"area":context["area"],"hours":context["hours"],
            "cams_valid_times":brackets,"point_count":context["points"].as_array().ok_or("context points missing")?.len(),
            "terrain_pending":context["terrain_pending"],
            "compute_region":context["common"]["_compute_region"],"notes":[],
        }))
    }

    fn preflight(
        &self,
        payload: &Value,
        regional: bool,
        fast: bool,
        publications: &Value,
        cancel: &AtomicBool,
    ) -> Result<Value, String> {
        // 日期策略（v1.8.0 用户裁定）：BeyondCams 在预检入口即明确拒绝；
        // Past 标注 cache-only（旧日期仅本地缓存，不重新下载）。
        let now_utc = std::env::var("FIRESKY_CONTEXT_NOW_UTC").ok();
        let policy = crate::desktop_prepare::date_policy(
            payload["target_date"].as_str().unwrap_or(""),
            payload["timezone_name"].as_str().unwrap_or("UTC"),
            now_utc.as_deref(),
        )?;
        if policy == crate::desktop_prepare::DatePolicy::BeyondCams {
            let date = payload["target_date"].as_str().unwrap_or("");
            return Err(format!(
                "CAMS 全球大气数据仅覆盖未来 {} 天内的日期；所选 {date} 超出时效窗口，无法计算",
                crate::desktop_prepare::CAMS_LEAD_DAYS
            ));
        }
        let policy_label = match policy {
            crate::desktop_prepare::DatePolicy::Past => "past",
            crate::desktop_prepare::DatePolicy::InWindow => "in-window",
            crate::desktop_prepare::DatePolicy::BeyondCams => unreachable!(),
        };
        let mut plans = Vec::new();
        for model in Self::models(payload)? {
            check(cancel)?;
            let mut request = payload.clone();
            request["model_source"] = json!(model);
            plans.push(Self::plan(&self.context(&request, regional, fast)?)?);
        }
        let result = desktop_preflight::execute_cancellable(
            decode(json!({
                "plans":plans,"fast":fast,"publications":publications,
                "date_policy":policy_label,
                "config":{
                    "gfs_cache_dir":self.inventory.gfs_cache_dir,
                    "ecmwf_cache_dir":self.inventory.ecmwf_cache_dir,
                    "cams_cache_dir":self.inventory.cams_cache_dir,
                },
            }))?,
            cancel,
        )?;
        check(cancel)?;
        Ok(result["report"].clone())
    }

    fn pin(payload: &mut Value, report: &Value) {
        if let Some(cycles) = report["model_cycles"].as_object() {
            for (model, cycle) in cycles {
                payload[format!("_{model}_cycle_override")] = cycle.clone();
            }
        } else if let Some(model) = report["model_source"].as_str() {
            payload[format!("_{model}_cycle_override")] = report["model_cycle"].clone();
        }
    }

    fn prepare_one(
        &self,
        payload: &Value,
        regional: bool,
        cancel: &AtomicBool,
        progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
    ) -> Result<Value, String> {
        let dir = tempfile::tempdir().map_err(|e| e.to_string())?;
        let path = dir.path().join("prepare.json");
        json_file(&path, &self.context_input(payload, regional, false))?;
        let mut result =
            desktop_prepare::execute_cancellable(desktop_prepare::Params { path }, cancel, progress)?;
        if let Some(retention) = crate::retention_settings::apply_configured(&self.workspace)? {
            result["retention"] = retention;
        }
        Ok(result)
    }

    fn nowcast_preflight(
        &self,
        payload: &Value,
        fast: bool,
        cancel: &AtomicBool,
    ) -> Result<Value, String> {
        check(cancel)?;
        let context = desktop_nowcast::execute(decode(json!({"payload":payload,"fast":fast}))?)?;
        check(cancel)?;
        let hsd = desktop_hsd::execute(decode(json!({
            "cache_dir":hsd_cache_dir(&self.workspace),
            "reference_time_utc":context["lookup"],"lat":context["request"]["lat"],
            "lon":context["request"]["lon"],"radius_km":context["request"]["radius_km"],
            "count":2,"max_age_minutes":30.0,"output_dir":null,
        }))?)?;
        let frames = hsd["frames"]
            .as_array()
            .ok_or("HSD preflight frames missing")?;
        let frame_times = frames
            .iter()
            .map(|frame| {
                time_contract::parse_utc(
                    frame["valid_time_utc"]
                        .as_str()
                        .ok_or("HSD preflight frame time missing")?,
                )
            })
            .collect::<Result<Vec<_>, _>>()?;
        let hours = desktop_nowcast_prepare::model_hours(&context, &frame_times)?;
        let cycle = time_contract::parse_utc(
            context["cycle"]
                .as_str()
                .ok_or("nowcast cycle is missing")?,
        )?;
        let result = desktop_preflight::execute_cancellable(
            decode(json!({
                "plans":[{
                    "model_source":"gfs","cycle_utc":context["cycle"],
                    "event_start":context["times"].as_array().and_then(|v|v.first()),
                    "bbox":context["bbox"],"area":context["area"],"hours":hours,
                    "cams_valid_times":context["cams_valid_times"],"point_count":1,
                    "terrain_pending":false,"compute_region":context["region"],"notes":[],
                }],
                "fast":fast,"publications":{},
                "config":{
                    "gfs_cache_dir":self.inventory.gfs_cache_dir,
                    "ecmwf_cache_dir":self.inventory.ecmwf_cache_dir,
                    "cams_cache_dir":self.inventory.cams_cache_dir,
                },
            }))?,
            cancel,
        )?;
        check(cancel)?;
        let mut report = result["report"].clone();
        let satellite_ready = frames.len() >= 2 && frame_times.iter().all(|time| *time >= cycle);
        report["ready"] = json!(report["ready"] == true && satellite_ready);
        // Surface the satellite requirement as a first-class dataset row so the
        // preflight UI can name what is missing (instead of an empty "缺少"
        // list) and the download button has a visible target.
        if let Some(datasets) = report["datasets"].as_array_mut() {
            datasets.push(json!({
                "source": "hsd",
                "ready": satellite_ready,
                "verified": satellite_ready,
                "is_latest": satellite_ready,
                "missing_count": if satellite_ready { 0 } else { 2 - frames.len() as i64 },
                "missing_hours": [],
                "missing_keys": [],
                "missing_valid_times": [],
                "local_files": frames.len(),
                "request_cycle": context["lookup"],
                "latest_local_cycle": context["lookup"],
                "latest_available_cycle": "未核验",
                "latest_available_cycle_utc": Value::Null,
            }));
        }
        report["satellite_frames"] = json!(frames.len());
        report["satellite_reference"] = context["lookup"].clone();
        report["satellite_valid_times"] = json!(frames
            .iter()
            .map(|v| v["valid_time_utc"].clone())
            .collect::<Vec<_>>());
        report["reference_time_utc"] = context["reference"].clone();
        report["cams_update_cycle"] = Value::Null;
        Ok(report)
    }

    fn prepare_nowcast(
        &self,
        payload: &Value,
        cancel: &AtomicBool,
        progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
    ) -> Result<Value, String> {
        let dir = tempfile::tempdir().map_err(|e| e.to_string())?;
        let path = dir.path().join("nowcast-prepare.json");
        json_file(
            &path,
            &json!({
                "payload":payload,"fast":false,
                "config":{
                    "gfs_cache_dir":self.inventory.gfs_cache_dir,
                    "cams_cache_dir":self.inventory.cams_cache_dir,
                    "satellite_cache_dir":hsd_cache_dir(&self.workspace),
                },
            }),
        )?;
        let mut result = desktop_nowcast_prepare::execute_cancellable(
            desktop_nowcast_prepare::Params { path },
            cancel,
            progress,
        )?;
        if let Some(retention) = crate::retention_settings::apply_configured(&self.workspace)? {
            result["retention"] = retention;
        }
        Ok(result)
    }

    fn compute_nowcast(&self, mut payload: Value, cancel: &AtomicBool) -> Result<Value, String> {
        payload["_runtime_config"] = json!({
            "workspace":self.workspace,"gfs_cache_dir":self.inventory.gfs_cache_dir,
            "cams_cache_dir":self.inventory.cams_cache_dir,"timezone_manifest":self.timezone_manifest,
            "map_cache_dir":self.map_cache_dir,
            "satellite_cache_dir":hsd_cache_dir(&self.workspace),
        });
        let mut result = crate::nowcast_runtime::compute_with_cancel(&payload, cancel)?;
        result["profile_source"] =
            crate::desktop_profiles::retain_satellite(&self.workspace, &payload, &result, cancel)?;
        crate::report_publication::publish(&self.report_dir, &mut result)?;
        check(cancel)?;
        Ok(result)
    }

    pub fn dispatch(&self, method: &str, params: Value) -> Result<Value, String> {
        self.dispatch_cancellable(
            method,
            params,
            Arc::new(AtomicBool::new(false)),
            &|_| Ok(()),
            None,
        )
    }

    pub(crate) fn begin_task(
        &self,
        request_id: &str,
        method: &str,
        params: &Value,
    ) -> Result<Option<crate::task_history::Task>, String> {
        // The wire method is `data_sync_run`; history records it under the
        // design's `data_sync` label so users can tell background sync rounds
        // apart from manual prepare/forecast tasks (design 3.2/7.2).
        let label = if method == "data_sync_run" || method == "data_sync_tick" {
            "data_sync"
        } else {
            method
        };
        if !matches!(
            method,
            "check"
                | "prepare"
                | "forecast"
                | "publications"
                | "network_probe"
                | "data_sync"
                | "data_sync_run"
                | "data_sync_tick"
                | "terrain_download"
        ) {
            return Ok(None);
        }
        let retry = if method == "check" {
            params.clone()
        } else if matches!(
            params["kind"].as_str(),
            Some("point" | "regional" | "nowcast")
        ) && params["payload"].is_object()
        {
            params.clone()
        } else if let Some(token) = params["session"].as_str() {
            self.session("get", token, json!({}))
                .map(|snapshot| json!({"kind":snapshot["kind"],"payload":snapshot["payload"]}))
                .unwrap_or(Value::Null)
        } else {
            Value::Null
        };
        self.history.begin(request_id, label, retry).map(Some)
    }

    pub(crate) fn cancel_history(&self, request_id: &str) -> Result<(), String> {
        self.history.request_cancel(request_id)
    }

    pub(crate) fn task_history_list(&self, limit: usize, before: Option<&str>) -> Result<Value, String> {
        self.history.list(limit, before)
    }

    pub(crate) fn task_history_detail(&self, task_id: &str) -> Result<Value, String> {
        self.history.detail(task_id)
    }

    pub(crate) fn task_history_events(
        &self,
        task_id: &str,
        after_seq: u64,
    ) -> Result<Value, String> {
        self.history.events(task_id, after_seq)
    }

    /// v1.9.8 卡死治理：这些长方法在空闲看门狗监管下执行——超过
    /// STALL_SECONDS 没有任何进度事件（含 ADS 排队/读取心跳）即取消并
    /// 转明确错误。引擎/网络真卡死时 UI 不再永久 busy（退出守卫误报
    /// 与假死的根因），任务以可重试失败收场。
    const STALL_METHODS: [&str; 7] = [
        "check",
        "prepare",
        "forecast",
        "terrain_download",
        "data_sync_run",
        "data_sync_tick",
        "network_probe",
    ];
    const STALL_SECONDS: u64 = 900;

    pub(crate) fn dispatch_cancellable(
        &self,
        method: &str,
        params: Value,
        cancel: Arc<AtomicBool>,
        progress: &(dyn Fn(Value) -> Result<(), String> + Sync),
        task_id: Option<&str>,
    ) -> Result<Value, String> {
        check(&cancel)?;
        if !params.is_object() {
            return Err("desktop params must be an object".into());
        }
        // 看门狗：progress 包装更新最近事件时间；独立线程每 30s 检查。
        let last_event = Arc::new(Mutex::new(Instant::now()));
        let stalled = Arc::new(AtomicBool::new(false));
        let watchdog_stop = Arc::new(AtomicBool::new(false));
        let watchdog = if Self::STALL_METHODS.contains(&method) {
            let last = Arc::clone(&last_event);
            let stalled = Arc::clone(&stalled);
            let cancel = Arc::clone(&cancel);
            let stop = Arc::clone(&watchdog_stop);
            Some(std::thread::spawn(move || loop {
                std::thread::sleep(std::time::Duration::from_secs(30));
                if stop.load(Ordering::Acquire) {
                    return;
                }
                let idle = last.lock().map(|t| t.elapsed()).unwrap_or_default();
                if idle >= std::time::Duration::from_secs(Self::STALL_SECONDS) {
                    stalled.store(true, Ordering::Release);
                    cancel.store(true, Ordering::Release);
                    return;
                }
            }))
        } else {
            None
        };
        let last_for_progress = Arc::clone(&last_event);
        let progress = move |event: Value| -> Result<(), String> {
            if let Ok(mut stamp) = last_for_progress.lock() {
                *stamp = Instant::now();
            }
            progress(event)
        };
        let outcome = match method {
            "hello" => Ok(json!({
                "protocol":1,"engine":"firesky-rust","workspace":self.workspace,
                "source_id":env!("FIRESKY_ENGINE_SOURCE_ID"),
                "runtime_python_used":false,"production_desktop_complete":false,
                "capabilities":["point","regional","nowcast","inventory","search","metadata","network_probe","data_sync","cancel","profile_query","task_history","task_events","result_restore","data_delete","terrain_inventory","terrain_download","data_layout"],
                "profile_capabilities":{
                    "dynamic_model_cross_section":true,"regional_result_points":true,
                    "satellite_step_solar_path":true,"dynamic_satellite_vertical":false,
                    "downloads_during_query":false,"source_lifetime_seconds":1800,
                },
                "unsupported":[],
                "regions":regions(),"cams_credentials":desktop_credentials::status(),
            })),
            "cams_credentials" => Ok(desktop_credentials::status()),
            "retention_settings" => crate::retention_settings::status(&self.workspace),
            "save_retention_settings" => crate::retention_settings::save(&self.workspace, params),
            "task_history" => {
                let limit = match params.get("limit") {
                    None => 50,
                    Some(value) => value
                        .as_u64()
                        .ok_or("task history limit must be an integer")?
                        as usize,
                };
                let before = params["before"].as_str();
                self.history.list(limit, before)
            }
            "task_events" => {
                let id = params["task_id"].as_str().ok_or("task_id is required")?;
                let after = match params.get("after_seq") {
                    None => 0,
                    Some(value) => value
                        .as_u64()
                        .ok_or("after_seq must be a nonnegative integer")?,
                };
                self.history.events(id, after)
            }
            "read_cache" => {
                let key = params["key"].as_str().ok_or("cache key is required")?;
                let regional = params["regional"].as_bool().unwrap_or(false);
                self.read_cache(json!({"key": key, "regional": regional}))
            }
            "result_restore" => {
                let id = params["task_id"].as_str().ok_or("task_id is required")?;
                let result = self.history.restore(id)?;
                self.verify_restore_dependencies(&result)?;
                Ok(result)
            }
            "task_forget" => self
                .history
                .forget(params["task_id"].as_str().ok_or("task_id is required")?),
            "save_cams_key" => desktop_credentials::save(params),
            "data_sync_settings" => crate::desktop_sync::status(&self.workspace),
            "save_data_sync_settings" => crate::desktop_sync::save(&self.workspace, params),
            "data_sync_due" => {
                // A busy engine (manual prepare/forecast or a running round)
                // skips the publication probe entirely.
                let operation_busy = self.operation.try_lock().is_err();
                let sync_busy = self.sync.try_lock().is_err();
                crate::desktop_sync::due(
                    &self.workspace,
                    operation_busy || sync_busy,
                    params["now_utc"].as_str(),
                )
            }
            "data_sync_run" => {
                // Lock matrix (design 7.1): the compute lock is only probed —
                // an in-flight manual prepare/forecast makes the round yield
                // (the "active" keyword maps to the FFI busy code) — and the
                // dedicated sync mutex keeps at most one round at a time.
                let operation_free = self.operation.try_lock().is_ok();
                if !operation_free {
                    return Err(
                        "data sync skipped because another prepare/forecast is active".into(),
                    );
                }
                let _sync_guard = self
                    .sync
                    .try_lock()
                    .map_err(|_| "another data sync is active")?;
                crate::desktop_sync::run(
                    &crate::desktop_sync::RunContext {
                        workspace: &self.workspace,
                        cams_cache_dir: self.inventory.cams_cache_dir.clone(),
                        gfs_cache_dir: self.inventory.gfs_cache_dir.clone(),
                        ecmwf_cache_dir: self.inventory.ecmwf_cache_dir.clone(),
                    },
                    params,
                    task_id,
                    &cancel,
                    &progress,
                )
            }
            "data_sync_tick" => {
                // One-shot entry for scheduled helpers (launchd / Task
                // Scheduler): judge due, then run only the due sources. Keeps
                // the due+run decision in the cross-platform engine so every
                // platform's helper is a single `desktop-call data_sync_tick`.
                let operation_busy = self.operation.try_lock().is_err();
                let sync_busy = self.sync.try_lock().is_err();
                let due = crate::desktop_sync::due(
                    &self.workspace,
                    operation_busy || sync_busy,
                    params["now_utc"].as_str(),
                )?;
                let sources: Vec<String> = due["sources"]
                    .as_object()
                    .map(|rows| {
                        rows.iter()
                            .filter(|(_, row)| row["due"].as_bool() == Some(true))
                            .map(|(name, _)| name.clone())
                            .collect()
                    })
                    .unwrap_or_default();
                if sources.is_empty() {
                    return Ok(json!({
                        "contract": "rust-data-sync-tick-v1",
                        "skipped": true,
                        "due": due,
                    }));
                }
                let operation_free = self.operation.try_lock().is_ok();
                if !operation_free {
                    return Err(
                        "data sync skipped because another prepare/forecast is active".into(),
                    );
                }
                let _sync_guard = self
                    .sync
                    .try_lock()
                    .map_err(|_| "another data sync is active")?;
                let round = crate::desktop_sync::run(
                    &crate::desktop_sync::RunContext {
                        workspace: &self.workspace,
                        cams_cache_dir: self.inventory.cams_cache_dir.clone(),
                        gfs_cache_dir: self.inventory.gfs_cache_dir.clone(),
                        ecmwf_cache_dir: self.inventory.ecmwf_cache_dir.clone(),
                    },
                    json!({ "sources": sources }),
                    task_id,
                    &cancel,
                    &progress,
                )?;
                Ok(json!({
                    "contract": "rust-data-sync-tick-v1",
                    "skipped": false,
                    "due": due,
                    "round": round,
                }))
            }
            "network_probe" => {
                let _guard = self
                    .network_probe
                    .try_lock()
                    .map_err(|_| "network probe is already active")?;
                desktop_network::execute_with_progress(params, progress)
            }
            "search" | "metadata" => {
                location::desktop_dispatch(method, &params, &self.location, true, cancel)
            }
            "inventory" => {
                let mut value = desktop_inventory::execute(&self.inventory, cancel.clone())?;
                // v1.9.2 数据管理类目（方案 §5.1 完整表格）：模型源之外的
                // 卫星/地形/报告/地图帧/结果缓存统计。
                value["categories"] = crate::paths::inventory_categories(&self.workspace, &cancel)?;
                Ok(value)
            }
            "data_layout_status" => {
                let mut value = crate::paths::status(&self.workspace);
                // 迁移需要独占：忙时 UI 显示原因而非失败。
                value["operation_busy"] = json!(self.operation.try_lock().is_err());
                value["sync_busy"] = json!(self.sync.try_lock().is_err());
                Ok(value)
            }
            "data_layout_migrate" => {
                // 迁移独占计算与同步锁（方案 8.2-3：暂停任务并协调活跃读写）。
                let _operation = self
                    .operation
                    .try_lock()
                    .map_err(|_| "another prepare/forecast is active")?;
                let _sync = self
                    .sync
                    .try_lock()
                    .map_err(|_| "another data sync is active")?;
                // 迁移改变工作区路径布局：本进程内的已解析路径随之失效，
                // 响应标记 requires_restart，UI 成功后重连引擎通道。
                let result = crate::paths::migrate(&self.workspace)?;
                Ok(json!({"requires_restart": true, "result": result}))
            }
            "data_delete_preview" | "data_delete_execute" => {
                // Deletion shares the compute lock with prepare/forecast and
                // refuses to run while a sync round is active, matching the
                // cross-process occupancy matrix (design 7.1 / phase C).
                let _operation = self
                    .operation
                    .try_lock()
                    .map_err(|_| "another prepare/forecast is active")?;
                let _sync = self
                    .sync
                    .try_lock()
                    .map_err(|_| "another data sync is active")?;
                if method == "data_delete_preview" {
                    crate::desktop_delete::preview(&self.workspace, &params, &cancel)
                } else {
                    crate::desktop_delete::execute(&self.workspace, &params, &cancel)
                }
            }
            "terrain_inventory" => {
                let mut config = self.location.clone();
                config.cancellation = Some(cancel);
                config.terrain_inventory(&params)
            }
            "terrain_download" => {
                let _terrain = self
                    .terrain
                    .try_lock()
                    .map_err(|_| "another terrain download is active")?;
                if params["mode"].as_str() == Some("global") {
                    // Bulk runs last hours and must not hold the compute lock;
                    // per-tile download locks already protect concurrent
                    // readers, and inventory/delete detect the live locks.
                    let mut config = self.location.clone();
                    config.cancellation = Some(cancel);
                    config.terrain_download(&params, &progress)
                } else {
                    let _operation = self
                        .operation
                        .try_lock()
                        .map_err(|_| "another prepare/forecast is active")?;
                    let mut config = self.location.clone();
                    config.cancellation = Some(cancel);
                    config.terrain_download(&params, &progress)
                }
            }
            "profile_query" => {
                crate::desktop_profiles::query(&self.workspace, decode(params)?, &cancel)
            }
            "check" => {
                let kind = params["kind"].as_str().unwrap_or("point");
                if !matches!(kind, "point" | "regional" | "nowcast") {
                    return Err("desktop kind must be point, regional or nowcast".into());
                }
                if kind == "nowcast" {
                    let mut payload = params["payload"]
                        .as_object()
                        .cloned()
                        .map(Value::Object)
                        .ok_or("payload must be an object")?;
                    payload["forecast_mode"] = json!("nowcast");
                    payload["model_source"] = json!("satellite");
                    payload["satellite_source"] = json!("auto");
                    let report = self.nowcast_preflight(&payload, true, &cancel)?;
                    Self::pin(&mut payload, &report);
                    payload["reference_time_utc"] = report["reference_time_utc"].clone();
                    check(&cancel)?;
                    let created = self.session(
                        "create",
                        "",
                        json!({"kind":kind,"payload":payload,"report":report}),
                    )?;
                    return Ok(json!({"session":created["session"],"report":report}));
                }
                let mut payload = Self::normalized(params["payload"].clone())?;
                if kind == "regional" {
                    // 区域预测一次只跑一个模式；默认 GFS，调用方可显式选 EC。
                    let model = payload["model_source"].as_str().unwrap_or("gfs");
                    if !matches!(model, "gfs" | "ec") {
                        return Err("regional forecast runs one model per request: model_source must be gfs or ec".into());
                    }
                    payload["model_source"] = json!(model);
                    // 区域网格固定 0.25°（产品契约，不做粗化）。
                    payload["spacing_deg"] = json!(0.25);
                } else {
                    // Desktop point checks always check both sources, even when one model is unavailable.
                    payload["model_source"] = json!("both");
                }
                let report =
                    self.preflight(&payload, kind == "regional", true, &json!({}), &cancel)?;
                Self::pin(&mut payload, &report);
                check(&cancel)?;
                let created = self.session(
                    "create",
                    "",
                    json!({
                        "kind":kind,"payload":payload,"report":report,
                    }),
                )?;
                Ok(json!({"session":created["session"],"report":report}))
            }
            "dismiss" => self
                .session(
                    "dismiss",
                    params["session"].as_str().unwrap_or(""),
                    json!({}),
                )
                .map(|_| json!({"dismissed":true})),
            "publications" => {
                let token = params["session"].as_str().ok_or("session is required")?;
                let snapshot = self.session("get", token, json!({}))?;
                let value = desktop_publications::execute(decode(json!({
                    "sources":["gfs","ecmwf","cams"],"now_utc":null,"timeout_seconds":2.0,
                }))?)?;
                check(&cancel)?;
                let mut report = snapshot["report"].clone();
                apply_publications(&mut report, &value["publications"]);
                // No long operation lock is held during publication network I/O.
                // If preparation/forecast has started, a stale probe must not overwrite it.
                if let Ok(_guard) = self.operation.try_lock() {
                    let current = self.session("get", token, json!({}))?;
                    if current["report"] == snapshot["report"] {
                        self.session("update", token, json!({"report":report}))?;
                    } else {
                        report = current["report"].clone();
                    }
                } else {
                    report = self.session("get", token, json!({}))?["report"].clone();
                }
                Ok(json!({"session":token,"report":report}))
            }
            "prepare" | "forecast" => {
                let _guard = self
                    .operation
                    .try_lock()
                    .map_err(|_| "another prepare/forecast is active")?;
                // v1.9.7 手动优先：放置跨进程让路哨兵，后台/助手的同步轮在
                // 源边界与文件粒度上让路（cancelled 不计熔断）；Drop 清理。
                let _yield_guard = crate::desktop_sync::manual_yield_guard(&self.workspace);
                let token = params["session"].as_str().ok_or("session is required")?;
                let snapshot = self.session("get", token, json!({}))?;
                let regional = snapshot["kind"] == "regional";
                let nowcast = snapshot["kind"] == "nowcast";
                let mut payload = snapshot["payload"].clone();
                if method == "prepare" {
                    self.session(
                        "update",
                        token,
                        json!({"report":{
                            "ready":false,"datasets":snapshot["report"]["datasets"],
                        }}),
                    )?;
                    if nowcast {
                        progress(json!({"stage":"prepare","percent":10,"source":"satellite",
                            "message":"正在准备卫星、GFS 与 CAMS 数据"}))?;
                        self.prepare_nowcast(&payload, &cancel, Some(&progress))?;
                    } else {
                        for model in Self::models(&payload)? {
                            check(&cancel)?;
                            progress(json!({"stage":"prepare","percent":5,"source":model,
                            "message":format!("正在准备 {} 模式与 CAMS 数据", model.to_uppercase())}))?;
                            let mut request = payload.clone();
                            request["model_source"] = json!(model);
                            self.prepare_one(&request, regional, &cancel, Some(&progress))?;
                        }
                    }
                    let report = if nowcast {
                        self.nowcast_preflight(&payload, false, &cancel)?
                    } else {
                        self.preflight(&payload, regional, false, &json!({}), &cancel)?
                    };
                    Self::pin(&mut payload, &report);
                    self.session("update", token, json!({"payload":payload,"report":report}))?;
                    return Ok(json!({"session":token,"report":report}));
                }
                if params["confirmed"] != true {
                    return Err("explicit confirmation is required before forecast".into());
                }
                if snapshot["report"]["ready"] != true {
                    return Err("inputs are missing; prepare data before forecasting".into());
                }
                if needs_cycle_age_confirmation(&snapshot["report"])
                    && params["cycle_age_confirmed"] != true
                {
                    return Err(
                        "data cycle is unverified or older than the latest release; explicit cycle-age confirmation is required"
                            .into(),
                    );
                }
                let report = if nowcast {
                    self.nowcast_preflight(&payload, false, &cancel)?
                } else {
                    self.preflight(&payload, regional, false, &json!({}), &cancel)?
                };
                if report["ready"] != true {
                    return Err("strict input integrity check failed".into());
                }
                check(&cancel)?;
                self.session("update", token, json!({"report":report}))?;
                self.session("consume", token, json!({}))?;
                progress(json!({"stage":"forecast","percent":20,"message":"预测计算已开始"}))?;
                if nowcast {
                    payload["allow_download"] = json!(false);
                    self.compute_nowcast(payload, &cancel)
                } else {
                    self.forecast_models(&payload, regional, &cancel, &progress)
                }
            }
            _ => Err(format!("unsupported Rust desktop method: {method}")),
        };
        // 停看门狗并检查空转触发：Err 结果若由看门狗取消所致，替换为
        // 明确的可重试错误（成功结果不受影响）。
        watchdog_stop.store(true, Ordering::Release);
        if let Some(handle) = watchdog {
            let _ = handle.join();
        }
        if stalled.load(Ordering::Acquire) {
            return Err(format!(
                "任务超过 {} 分钟没有任何进展（网络或数据源无响应），已自动中断；可重试或稍后再试",
                Self::STALL_SECONDS / 60
            ));
        }
        outcome
    }

    /// Direct non-interactive API/CLI entry; unlike desktop IPC no confirmation token is required.
    pub fn compute(&self, payload: Value, regional: bool) -> Result<Value, String> {
        self.compute_with_cancel(payload, regional, Arc::new(AtomicBool::new(false)))
    }

    pub fn compute_with_cancel(
        &self,
        payload: Value,
        regional: bool,
        cancel: Arc<AtomicBool>,
    ) -> Result<Value, String> {
        let _guard = self
            .operation
            .try_lock()
            .map_err(|_| "another prepare/forecast is active")?;
        check(&cancel)?;
        if payload["forecast_mode"] == "nowcast" || payload.get("cached_nowcast").is_some() {
            if regional {
                return Err("regional nowcast is not supported".into());
            }
            return self.compute_nowcast(payload, &cancel);
        }
        let payload = Self::normalized(payload)?;
        self.forecast_models(&payload, regional, &cancel, &|_| Ok(()))
    }

    fn forecast_models(
        &self,
        payload: &Value,
        regional: bool,
        cancel: &AtomicBool,
        progress: &(dyn Fn(Value) -> Result<(), String> + Sync),
    ) -> Result<Value, String> {
        let key = cache_key(payload, regional)?;
        if payload["force_refresh"] != true {
            if let Ok(value) = self.read_cache(json!({"key":key,"regional":regional})) {
                check(cancel)?;
                if value["model_errors"].as_object().is_some_and(Map::is_empty) {
                    return Ok(value);
                }
            }
        }
        let mut results = Map::new();
        let mut errors = Map::new();
        for model in Self::models(payload)? {
            check(cancel)?;
            let mut request = payload.clone();
            request["model_source"] = json!(model);
            let result = self
                .preflight(&request, regional, false, &json!({}), cancel)
                .and_then(|report| {
                    if report["ready"] != true {
                        return Err(format!("{model} forecast inputs are incomplete: {report}"));
                    }
                    Self::pin(&mut request, &report);
                    if regional {
                        self.regional(&request, model, cancel, progress)
                    } else {
                        self.point(&request, model, cancel, progress)
                    }
                });
            match result {
                Ok(result) => {
                    results.insert(model.into(), result);
                }
                Err(error) => {
                    errors.insert(model.into(), json!(error));
                }
            }
        }
        check(cancel)?;
        if results.is_empty() {
            return Err(format!(
                "all model forecasts failed: {}",
                Value::Object(errors)
            ));
        }
        let result = json!({"model_results":results,"model_errors":errors});
        self.publish_cache(payload, regional, &key, result, cancel)
    }

    fn cache_dir(&self, regional: bool) -> &Path {
        if regional {
            &self.regional_cache_dir
        } else {
            &self.point_cache_dir
        }
    }

    /// Read only this runtime's separate, checksummed cache namespace.
    /// Legacy Python cache keys and projections are deliberately not accepted.
    pub fn read_cache(&self, params: Value) -> Result<Value, String> {
        let key = params["key"]
            .as_str()
            .filter(|v| {
                v.len() == 64
                    && v.bytes()
                        .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            })
            .ok_or("native cache requires an exact lowercase SHA-256 key")?;
        let regional = params["regional"].as_bool().unwrap_or(false);
        let record_path = self
            .cache_dir(regional)
            .join("rust-runtime-v1")
            .join(format!("{key}.json"));
        tensor_bundle::regular_identity(&record_path)?;
        let (record, _): (Value, _) = request_file::read(&record_path, 256 * 1024)?;
        let generated = time_contract::parse_utc(
            record["generated_at_utc"]
                .as_str()
                .ok_or("native cache timestamp missing")?,
        )?;
        let age =
            (chrono::DateTime::<Utc>::from(std::time::SystemTime::now()) - generated).num_seconds();
        if record["contract"] != "rust-native-runtime-cache-v1"
            || record["key"] != key
            || record["source_id"] != env!("FIRESKY_ENGINE_SOURCE_ID")
            || record["regional"] != regional
            || !(0..=300).contains(&age)
            || cache_key(&record["request"], regional)? != key
        {
            return Err("native cache identity is invalid or expired".into());
        }
        let expected_name = format!(
            "native-{}-{key}.json",
            if regional { "regional" } else { "point" }
        );
        let value = artifact(&self.report_dir, &record["report"], &expected_name)?;
        if value["cache"]["contract"] != "rust-native-runtime-cache-v1"
            || value["cache"]["key"] != key
        {
            return Err("native report cache identity does not match registry".into());
        }
        for (model, result) in value["model_results"]
            .as_object()
            .ok_or("cached model results missing")?
        {
            if !regional {
                let id = result["html_report_url"]
                    .as_str()
                    .and_then(|url| url.strip_prefix("/api/v1/firesky/reports/"))
                    .ok_or("cached point HTML report identity missing")?;
                crate::report_publication::read(&self.report_dir, id)?;
            }
            let sources = result["input_manifest"]["sources"]
                .as_array()
                .filter(|v| !v.is_empty())
                .ok_or("cached result has no frozen source identities")?;
            for source in sources {
                let path = Path::new(source["path"].as_str().ok_or("cached input path missing")?);
                let expected: tensor_bundle::SourceIdentity = decode(source["identity"].clone())?;
                if tensor_bundle::regular_identity(path)? != expected {
                    return Err("native cache source identity changed".into());
                }
            }
            let mut request = record["request"].clone();
            request["model_source"] = json!(model);
            let cycle = result["cycle_time_utc"]
                .as_str()
                .ok_or("cached cycle missing")?;
            let public = format!("{model}_cycle_override");
            let private = format!("_{model}_cycle_override");
            let current = request
                .get(&public)
                .or_else(|| request.get(&private))
                .and_then(Value::as_str)
                .map(str::to_owned)
                .map(Ok)
                .unwrap_or_else(|| {
                    self.context(&request, regional, true)?
                        .get("cycle")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                        .ok_or_else(|| "current model cycle missing".to_string())
                })?;
            if time_contract::parse_utc(cycle)? != time_contract::parse_utc(&current)? {
                return Err("native cache model cycle no longer matches request".into());
            }
        }
        let mut result = value;
        result["cache"]["status"] = json!("hit");
        let cache = result["cache"].clone();
        if let Some(models) = result["model_results"].as_object_mut() {
            for model in models.values_mut() {
                model["cache"] = cache.clone();
            }
        }
        Ok(result)
    }

    pub fn read_cache_for(&self, payload: Value, regional: bool) -> Result<Value, String> {
        let payload = Self::normalized(payload)?;
        let key = cache_key(&payload, regional)?;
        self.read_cache(json!({"key":key,"regional":regional}))
    }

    /// Verify that a restored snapshot's external HTML report artifacts still
    /// resolve. Raw meteorological inputs are deliberately not revalidated, and
    /// regional map frames travel inside the snapshot itself, so a historical
    /// result remains restorable after the original inputs have been evicted.
    fn verify_restore_dependencies(&self, result: &Value) -> Result<(), String> {
        let mut urls = Vec::new();
        if let Some(url) = result["html_report_url"].as_str() {
            urls.push(url);
        }
        if let Some(models) = result["model_results"].as_object() {
            for model in models.values() {
                if let Some(url) = model["html_report_url"].as_str() {
                    urls.push(url);
                }
            }
        }
        for url in urls {
            let id = url
                .strip_prefix("/api/v1/firesky/reports/")
                .ok_or("restored result has an invalid report identity")?;
            crate::report_publication::read(&self.report_dir, id)
                .map_err(|_| "restored historical report artifact is no longer available")?;
        }
        Ok(())
    }

    fn publish_cache(
        &self,
        payload: &Value,
        regional: bool,
        key: &str,
        mut result: Value,
        cancel: &AtomicBool,
    ) -> Result<Value, String> {
        let generated = time_contract::iso(std::time::SystemTime::now().into());
        let report_name = format!(
            "native-{}-{key}.json",
            if regional { "regional" } else { "point" }
        );
        result["cache"] = json!({
            "contract":"rust-native-runtime-cache-v1","status":"refresh","key":key,
            "generated_at_utc":generated,"max_age_seconds":300,"source_id":env!("FIRESKY_ENGINE_SOURCE_ID"),
            "report_file":report_name,"regional":regional,"legacy_cache_compatible":false,
        });
        let cache = result["cache"].clone();
        if let Some(models) = result["model_results"].as_object_mut() {
            for model in models.values_mut() {
                model["cache"] = cache.clone();
            }
        }
        let mut map_artifacts = Map::new();
        if regional {
            if let Some(models) = result["model_results"].as_object() {
                for (model, value) in models {
                    check(cancel)?;
                    if value["frame_data"].is_object() {
                        let name = format!("native-{key}-{model}.json");
                        atomic_json(
                            &self.map_cache_dir.join("rust-runtime-v1").join(&name),
                            &value["frame_data"],
                        )?;
                        map_artifacts.insert(model.clone(),json!({
                            "file":name,"identity":tensor_bundle::regular_identity(&self.map_cache_dir.join("rust-runtime-v1").join(&name))?,
                        }));
                    }
                }
            }
        }
        check(cancel)?;
        let report_path = self.report_dir.join(&report_name);
        atomic_json(&report_path, &result)?;
        let mut request = payload.clone();
        request
            .as_object_mut()
            .ok_or("cache request must be an object")?
            .remove("force_refresh");
        let record = json!({
            "contract":"rust-native-runtime-cache-v1","source_id":env!("FIRESKY_ENGINE_SOURCE_ID"),
            "key":key,"regional":regional,"generated_at_utc":generated,"request":request,
            "report":{"file":report_name,"identity":tensor_bundle::regular_identity(&report_path)?},
            "map_artifacts":map_artifacts,
        });
        check(cancel)?;
        atomic_json(
            &self
                .cache_dir(regional)
                .join("rust-runtime-v1")
                .join(format!("{key}.json")),
            &record,
        )?;
        Ok(result)
    }

    fn point(
        &self,
        payload: &Value,
        model: &str,
        cancel: &AtomicBool,
        progress: &(dyn Fn(Value) -> Result<(), String> + Sync),
    ) -> Result<Value, String> {
        let context = self.context(payload, false, false)?;
        let point = context["points"]
            .as_array()
            .filter(|v| v.len() == 1)
            .and_then(|v| v.first())
            .ok_or("point context must have exactly one observer")?;
        let mut observer = json!({
            "name":point["name"],"lat":point["lat"],"lon":point["lon"],
            "timezone_name":point["timezone_name"],"elevation_source":point["_elevation_source"],
        });
        if point["_elevation_source"] == "observer" {
            observer["elevation_m"] = point["elevation_m"].clone();
        }
        let mut request = json!({
            "date":payload["target_date"],"phenomenon":payload["phenomenon"],
            "radius_km":context["common"]["radius_km"],"cycle_utc":context["cycle"],
            "cams_cache_dir":self.inventory.cams_cache_dir,
            "derived_cache":{
                "directory":if model == "gfs" {self.inventory.gfs_cache_dir.join("derived/rust-v1")} else {self.inventory.ecmwf_cache_dir.join("derived/rust-v1")},
                "lock_timeout_seconds":30.0,
            },
        });
        if model == "gfs" {
            request["points"] = json!([observer]);
            request["gfs"] = json!({
                "cache_dir":self.inventory.gfs_cache_dir,"resolution":"0p25",
                "region":context["common"]["_compute_region"],
            });
            request["bbox"] = context["bbox"].clone();
            request["batch_min_points"] = json!(64);
            request["allow_gfs_download"] = json!(false);
        } else {
            request["point"] = observer;
            request["ec"] = json!({
                "cache_dir":self.inventory.ecmwf_cache_dir,"resolution":"0p25",
                "source":payload["ecmwf_source"].as_str().unwrap_or("ecmwf"),
            });
            request["allow_ec_download"] = json!(false);
            request["compute_backend"] =
                json!(payload["compute_backend"].as_str().unwrap_or("auto"));
        }
        let dir = tempfile::tempdir().map_err(|e| e.to_string())?;
        let path = dir.path().join("request.json");
        let output = dir.path().join("output");
        json_file(&path, &request)?;
        let manifest = if model == "gfs" {
            input_plan::forecast_with_progress(&path, &output, &|permille| {
                let _ = progress(json!({
                    "stage":"forecast",
                    "percent":20 + permille * 70 / 2000,
                    "message":"单点预测计算",
                }));
            })?
        } else {
            ecmwf_plan::forecast_with_progress(&path, &output, &|permille| {
                let _ = progress(json!({
                    "stage":"forecast",
                    "percent":20 + permille * 70 / 2000,
                    "message":"单点预测计算",
                }));
            })?
        };
        let files = tensor_bundle::verify_files(&manifest, "point-forecast-v1")?;
        if files.source != tensor_bundle::regular_identity(&path)? {
            return Err("point bundle source changed".into());
        }
        let key = if model == "gfs" {
            "point_forecast"
        } else {
            "ecmwf_point_report"
        };
        let name = if model == "gfs" {
            "point_forecast.json"
        } else {
            "ecmwf_point.json"
        };
        let expected_contract = if model == "gfs" {
            "rust-stored-point-forecast-v1"
        } else {
            "rust-ecmwf-point-report-v1"
        };
        if files.metadata[key]["contract"] != expected_contract
            || files.metadata[key]["public_forecast_complete"] != false
            || (model == "gfs"
                && (files.metadata[key]["point_core_validated"] != true
                    || files.metadata[key]["stored_point_payload_complete"] != true))
            || (model == "ec" && files.metadata[key]["report_complete"] != true)
        {
            return Err("point artifact contract is incomplete".into());
        }
        let document = artifact(&output, &files.metadata[key], name)?;
        let mut result = if model == "gfs" {
            document
        } else {
            let mut result = document["web_report_payload"].clone();
            if !result.is_object() {
                return Err("EC point report is incomplete".into());
            }
            result["confidence_score_semantics"] = document["confidence_semantics"].clone();
            result["native_ecmwf_report"] = json!({
                "contract":document["contract"],"native_quality":document["native_quality"],
                "dominant_burnable":document["dominant_burnable"],"public_forecast_complete":false,
            });
            if let Some(dominant) = document["dominant_burnable"].as_object() {
                for suffix in [
                    "pressure_family",
                    "cloud_phase",
                    "cloud_base_asl_mean_m",
                    "cloud_top_asl_mean_m",
                    "target_terrain_asl_mean_m",
                    "cloud_base_agl_mean_m",
                    "cloud_top_agl_mean_m",
                    "cloud_base_above_observer_mean_m",
                    "cloud_top_above_observer_mean_m",
                ] {
                    result[format!("dominant_burnable_{suffix}")] =
                        dominant.get(suffix).cloned().unwrap_or(Value::Null);
                }
                result["dominant_burnable_cloud_genus_diagnosis"] =
                    document["dominant_burnable"]["genus_diagnosis"].clone();
                result["cloud_genus_proxy"] = document["dominant_burnable"]["genus_proxy"].clone();
                result["cloud_phase_proxy"] = document["dominant_burnable"]["phase_proxy"].clone();
            }
            result["point_refinement"] = json!("rust-ecmwf-point-report-v1");
            result["diagnostic_source"] = json!("ec-native-regional-cloud-and-solar-profile");
            result["data_provenance"] = json!({
                "model_source":"ec","cycle_utc":result["cycle_time_utc"],
                "gfs_cache_used":false,"cams_cache_used":true,"observation_used":false,
                "confidence_semantics":document["confidence_semantics"],
            });
            result
        };
        if result["model_source"] != model
            || result["target_date"] != request["date"]
            || result["phenomenon"] != request["phenomenon"]
            || result["forecast_mode"] != "model"
        {
            return Err("point result does not match requested model/event".into());
        }
        let cross = &files.metadata["cross_section_source"];
        if cross.is_object() {
            artifact(&output, cross, "cross_section_source.json")?;
            let anchor = chrono::DateTime::parse_from_rfc3339(
                result["anchor_local"]
                    .as_str()
                    .ok_or("point anchor is missing")?,
            )
            .map_err(|e| e.to_string())?;
            let best = result["best_time_local"]
                .as_str()
                .map(chrono::DateTime::parse_from_rfc3339)
                .transpose()
                .map_err(|e| e.to_string())?;
            let best_offset = best
                .map(|best| (best - anchor).num_seconds() as f64 / 60.0)
                .unwrap_or(0.0);
            let offset = match payload.get("offset_minutes") {
                Some(value) => value
                    .as_f64()
                    .filter(|v| v.is_finite() && (-240.0..=240.0).contains(v))
                    .ok_or("offset_minutes must be finite and within -240..240")?,
                None => best_offset,
            };
            // Report anchors (sunset/sunrise) can sit sub-minute away from the
            // frozen profile timeline anchor; snap before the strict check.
            let offset = cross_section::snap_offset_to_event_grid(
                &output.join("cross_section_source.json"),
                &crate::frames::Point {
                    name: String::new(),
                    lat: point["lat"].as_f64().ok_or("point lat missing")?,
                    lon: point["lon"].as_f64().ok_or("point lon missing")?,
                    timezone_name: point["timezone_name"]
                        .as_str()
                        .ok_or("point timezone missing")?
                        .to_owned(),
                    elevation_m: point["elevation_m"]
                        .as_f64()
                        .ok_or("point elevation missing")?,
                },
                offset,
            )?;
            result["cross_section"] = cross_section::execute(decode(json!({
                "source_path":output.join("cross_section_source.json"),"source_identity":cross["identity"],
                "point":{"lat":point["lat"],"lon":point["lon"],"elevation_m":point["elevation_m"],
                    "timezone_name":point["timezone_name"]},
                "offset_minutes":offset,
            }))?)?;
        } else {
            result["cross_section"] = Value::Null;
        }
        result["profile_source"] = if cross.is_object() {
            crate::desktop_profiles::retain_model(
                &self.workspace,
                &output,
                cross,
                vec![crate::desktop_profiles::observer(point, &result)?],
                cancel,
            )?
        } else {
            json!({"available":false,"reason":"This result has no frozen model profile source"})
        };
        result["cross_section_request"] = Value::Null;
        result["native_forecast"] = json!({
            "contract":"rust-desktop-point-forecast-v1","runtime_python_used":false,
            "model_source":model,"point_payload_complete":true,
            "native_artifact_contract":files.metadata[key]["contract"],
            "provenance_complete":files.metadata[key]["provenance_complete"] == true,
            "public_web_contract_complete":false,
        });
        result["input_manifest"] = json!({
            "contract":"rust-runtime-input-provenance-v1","sources":files.metadata["sources"],
        });
        crate::map_frames::publish(
            &self.map_cache_dir,
            &mut result,
            point["lat"].as_f64().ok_or("point latitude is missing")?,
            point["lon"].as_f64().ok_or("point longitude is missing")?,
        )?;
        crate::report_publication::publish(&self.report_dir, &mut result)?;
        Ok(result)
    }

    fn regional(
        &self,
        payload: &Value,
        model: &str,
        cancel: &AtomicBool,
        progress: &(dyn Fn(Value) -> Result<(), String> + Sync),
    ) -> Result<Value, String> {
        let context = self.context(payload, true, false)?;
        check(cancel)?;
        let prepared = self.prepare_one(payload, true, cancel, Some(progress))?;
        check(cancel)?;
        if prepared["model_source"] != model
            || time_contract::parse_utc(
                prepared["cycle"]
                    .as_str()
                    .ok_or("prepared cycle is missing")?,
            )? != time_contract::parse_utc(
                context["cycle"]
                    .as_str()
                    .ok_or("context cycle is missing")?,
            )?
        {
            return Err("regional preparation model/cycle differs from frozen context".into());
        }
        let rows = if model == "gfs" {
            &prepared["model"]["files"]
        } else {
            &prepared["model"]["resolution"]["files"]
        };
        let mut frames = BTreeMap::new();
        for row in rows
            .as_array()
            .ok_or("regional preparation has no model files")?
        {
            let hour = row["forecast_hour"]
                .as_u64()
                .ok_or("invalid model forecast hour")?;
            let path = row["path"].as_str().ok_or("invalid model frame path")?;
            let mut paths = vec![path.to_owned()];
            if let Some(extra) = row["parts"].as_array() {
                for part in extra {
                    paths.push(
                        part["path"]
                            .as_str()
                            .ok_or("invalid wrapped model frame path")?
                            .to_owned(),
                    );
                }
            }
            if frames.insert(hour, paths).is_some() {
                return Err("duplicate regional model forecast hour".into());
            }
        }
        let expected_hours = prepared["hours"]
            .as_array()
            .ok_or("prepared hours missing")?
            .iter()
            .map(|v| v.as_u64().ok_or("invalid prepared hour"))
            .collect::<Result<BTreeSet<_>, _>>()?;
        if frames.is_empty() || expected_hours != frames.keys().copied().collect() {
            return Err("regional preparation hours do not match source files".into());
        }
        let mut cams = BTreeMap::new();
        for row in prepared["cams"]["files"]
            .as_array()
            .ok_or("regional CAMS files missing")?
        {
            if row["exact"] != true || row["valid_time"] != row["requested_valid_time"] {
                return Err("regional CAMS source is not an exact requested time".into());
            }
            let time = row["valid_time"]
                .as_str()
                .ok_or("invalid CAMS valid time")?;
            time_contract::parse_utc(time)?;
            let path = row["path"].as_str().ok_or("invalid CAMS file path")?;
            let mut paths = vec![path.to_owned()];
            if let Some(extra) = row["paths"].as_array() {
                for value in extra {
                    paths.push(
                        value
                            .as_str()
                            .ok_or("invalid wrapped CAMS file path")?
                            .to_owned(),
                    );
                }
            }
            paths.sort();
            paths.dedup();
            let catalog_row = json!({
                "valid_time": time,
                "path": paths.first().ok_or("CAMS file group is empty")?,
                "paths": paths.iter().skip(1).collect::<Vec<_>>(),
            });
            if let Some(previous) = cams.insert(time.to_owned(), catalog_row.clone()) {
                if previous != catalog_row {
                    return Err("conflicting regional CAMS valid time".into());
                }
            }
        }
        let points = context["points"]
            .as_array()
            .filter(|v| !v.is_empty())
            .ok_or("regional context has no observers")?;
        let observers = points.iter().map(|point| {
            let mut observer = json!({
                "name":point["name"],"lat":point["lat"],"lon":point["lon"],
                "timezone_name":point["timezone_name"],"elevation_source":point["_elevation_source"],
            });
            if point["_elevation_source"] == "observer" {
                observer["elevation_m"] = point["elevation_m"].clone();
            }
            observer
        }).collect::<Vec<_>>();
        // The regional context bbox already carries the full source buffer
        // (>=1000 km around the point union). Re-expanding it for EC would
        // double the buffered envelope, multiplying the decoded grid cells
        // past the bounded output size. Use the same buffered bbox for both
        // models; it covers every observer and the derived cloud fields.
        let model_bbox = decode(context["bbox"].clone())?;
        let request = json!({
            "model":if model == "gfs" {"gfs"} else {"ecmwf-basic"},
            "cycle_utc":context["cycle"],"date":payload["target_date"],
            "phenomenon":payload["phenomenon"],"points":observers,
            "frames":frames.iter().map(|(hour,paths)|json!({
                "hour":hour,"path":paths[0],
                "paths":paths.iter().skip(1).collect::<Vec<_>>(),
            })).collect::<Vec<_>>(),
            "cams":cams.values().cloned().collect::<Vec<_>>(),
            "radius_km":context["common"]["radius_km"],"bbox":model_bbox,
            "include_point_core":false,"point_spacing_deg":payload["spacing_deg"].as_f64().unwrap_or(0.25),
            "compute_backend_requested":payload["compute_backend"].as_str().unwrap_or("auto"),
            "derived_cache":null,
            "compact_results":true,
        });
        let dir = tempfile::tempdir().map_err(|e| e.to_string())?;
        let path = dir.path().join("request.json");
        let output = dir.path().join("output");
        json_file(&path, &request)?;
        check(cancel)?;
        let manifest =
            assessment::frames_to_files_cancellable(&path, &output, cancel, &|permille| {
                let percent = 20 + permille * 65 / 2000;
                let message = match permille {
                    p if p < 1000 => "解码模型帧",
                    p if p < 1400 => "装配 CAMS 气溶胶",
                    p if p < 1600 => "对齐观测点锚点",
                    _ => "光线追踪评估",
                };
                let _ = progress(json!({"stage":"forecast","percent":percent,"message":message}));
            })?;
        check(cancel)?;
        let files = tensor_bundle::verify_files(&manifest, "regional-frames-v1")?;
        if files.source != tensor_bundle::regular_identity(&path)?
            || files.metadata["frame_payload"]["contract"] != "rust-regional-frames-v1"
            || files.metadata["frame_payload"]["public_forecast_complete"] != false
        {
            return Err("regional frame artifact source/contract is invalid".into());
        }
        let document = artifact(&output, &files.metadata["frame_payload"], "forecast.json")?;
        if document["point_core"] != Value::Null
            || document["results"].as_array().map(Vec::len) != Some(points.len())
            || !document["frames"]["precision"].is_object()
            || files.metadata["frame_payload"]["compact_results"] != true
        {
            return Err("regional frame artifact payload identity is invalid".into());
        }
        let coverage = coverage(payload)?;
        let city_ranking = match self.city_ranking(
            payload,
            model,
            &request,
            model_bbox,
            &coverage,
            dir.path(),
            cancel,
            progress,
        ) {
            Ok(value) => value,
            Err(error) if cancel.load(Ordering::Acquire) => return Err(error),
            Err(error) => json!({
                "contract":"rust-regional-city-ranking-v1",
                "status":"unavailable",
                "source":"china-city-catalog",
                "model_source":model,
                "reason":error,
                "items":[],
            }),
        };
        check(cancel)?;
        let profile_source = if files.metadata["cross_section_source"].is_object() {
            let rows = document["results"]
                .as_array()
                .ok_or("regional result points missing")?;
            let observers = points
                .iter()
                .zip(rows)
                .map(|(point, report)| crate::desktop_profiles::observer(point, report))
                .collect::<Result<Vec<_>, _>>()?;
            if observers.len() > crate::desktop_profiles::MAX_OBSERVERS {
                json!({"available":false,"reason":format!(
                    "Dynamic regional profiles support up to {} grid points; this result has {}.",
                    crate::desktop_profiles::MAX_OBSERVERS,observers.len()
                )})
            } else {
                match crate::desktop_profiles::retain_model(
                    &self.workspace,
                    &output,
                    &files.metadata["cross_section_source"],
                    observers,
                    cancel,
                ) {
                    Ok(source) => source,
                    Err(error) if cancel.load(Ordering::Acquire) => return Err(error),
                    Err(_) => json!({"available":false,
                        "reason":"Regional forecast completed, but its frozen profile source could not be retained."}),
                }
            }
        } else {
            json!({"available":false,"reason":"This regional result has no frozen model profile source"})
        };
        let mut point_results = document["results"]
            .as_array()
            .cloned()
            .ok_or("regional result points missing")?;
        for (point, report) in points.iter().zip(&mut point_results) {
            if !report.is_object() {
                return Err("regional point result is not an object".into());
            }
            report["timezone_name"] = point["timezone_name"].clone();
        }
        Ok(json!({
            "scope":"regional",
            "point_mode":if payload["points"].as_array().is_some_and(|v| !v.is_empty())
                {"explicit-single-points"} else {"generated-regional-grid"},
            "regions":coverage.iter().map(|v|v["code"].clone()).collect::<Vec<_>>(),
            "coverage":coverage,"target_date":payload["target_date"],"phenomenon":payload["phenomenon"],
            "model_source":model,"forecast_mode":"model","compute_backend":"cpu",
            "compute_backend_requested":request["compute_backend_requested"],
            "precision":document["frames"]["precision"],"cycle_time_utc":context["cycle"],
            "point_count":points.len(),"success_count":points.len(),"error_count":0,
            "points":point_results,"frame_data":document["frames"],"errors":[],
            "city_ranking":city_ranking,
            "profile_source":profile_source,
            "timing":{"prefetch_seconds":prepared["elapsed_ms"].as_f64().unwrap_or(0.0)/1000.0,
                "native_timings_ms":files.metadata["timings_ms"]},
            "input_manifest":{"contract":"rust-runtime-input-provenance-v1","sources":files.metadata["sources"]},
            "native_forecast":{"contract":"rust-desktop-regional-forecast-v1",
                "runtime_python_used":false,"model_source":model,"point_payload_complete":true,
                "frame_payload_complete":true,"public_forecast_complete":false},
        }))
    }

    fn city_ranking(
        &self,
        payload: &Value,
        model: &str,
        request: &Value,
        model_bbox: [f64; 4],
        coverage: &[Value],
        dir: &Path,
        cancel: &AtomicBool,
        progress: &(dyn Fn(Value) -> Result<(), String> + Sync),
    ) -> Result<Value, String> {
        check(cancel)?;
        let bounds = coverage
            .iter()
            .filter_map(|row| {
                let values = row["bounds"].as_array()?;
                if values.len() != 4 {
                    return None;
                }
                Some([
                    values[0].as_f64()?,
                    values[2].as_f64()?,
                    values[3].as_f64()?,
                    values[1].as_f64()?,
                ])
            })
            .collect::<Vec<_>>();
        let mut cities = desktop_context::city_catalog_points(
            &self.city_catalog_path,
            self.timezone_manifest.as_deref(),
            &bounds,
        )?;
        cities.retain(|point| {
            let lat = point["lat"].as_f64().unwrap_or(f64::NAN);
            let lon = point["lon"].as_f64().unwrap_or(f64::NAN);
            desktop_context::longitude_contains(model_bbox[0], model_bbox[1], lon)
                && lat >= model_bbox[3] - 1e-6
                && lat <= model_bbox[2] + 1e-6
        });
        if cities.is_empty() {
            return Ok(json!({
                "contract":"rust-regional-city-ranking-v1",
                "status":"not_applicable",
                "source":"china-city-catalog",
                "model_source":model,
                "count":0,
                "items":[],
            }));
        }
        let mut city_request = request.clone();
        city_request["points"] = Value::Array(cities.clone());
        city_request["include_point_core"] = json!(false);
        city_request["point_spacing_deg"] = request["point_spacing_deg"].clone();
        city_request["compact_results"] = json!(true);
        let request_path = dir.join(format!("city-ranking-{model}.json"));
        json_file(&request_path, &city_request)?;
        let output = dir.join(format!("city-ranking-{model}"));
        let manifest =
            assessment::frames_to_files_cancellable(&request_path, &output, cancel, &|permille| {
                let _ = progress(json!({
                    "stage":"forecast",
                    "percent":85 + permille * 10 / 2000,
                    "message":"城市排行评估",
                }));
            })?;
        let files = tensor_bundle::verify_files(&manifest, "regional-frames-v1")?;
        let payload_identity = files.metadata["frame_payload"].clone();
        if payload_identity["contract"] != "rust-regional-frames-v1"
            || payload_identity["public_forecast_complete"] != false
        {
            return Err("city ranking frame artifact contract is invalid".into());
        }
        let document = artifact(&output, &payload_identity, "forecast.json")?;
        let mut rows = document["results"]
            .as_array()
            .cloned()
            .ok_or("city ranking result points missing")?;
        if rows.len() != cities.len() {
            return Err("city ranking result count does not match the catalog selection".into());
        }
        for (city, row) in cities.iter().zip(&mut rows) {
            if !row.is_object() {
                return Err("city ranking row is not an object".into());
            }
            row["timezone_name"] = city["timezone_name"].clone();
        }
        rows.sort_by(|left, right| {
            let score = |row: &Value, key: &str| {
                row[key]
                    .as_f64()
                    .filter(|value| value.is_finite())
                    .unwrap_or(-1.0)
            };
            score(right, "peak_quality_index")
                .total_cmp(&score(left, "peak_quality_index"))
                .then_with(|| {
                    score(right, "quality_index").total_cmp(&score(left, "quality_index"))
                })
                .then_with(|| {
                    score(right, "confidence_score").total_cmp(&score(left, "confidence_score"))
                })
                .then_with(|| {
                    left["name"]
                        .as_str()
                        .unwrap_or_default()
                        .cmp(right["name"].as_str().unwrap_or_default())
                })
        });
        for (index, row) in rows.iter_mut().enumerate() {
            row["rank"] = json!(index + 1);
            row["ranking_contract"] = json!("peak-quality-desc-v1");
        }
        Ok(json!({
            "contract":"rust-regional-city-ranking-v1",
            "status":"ready",
            "source":"china-city-catalog",
            "model_source":model,
            "target_date":payload["target_date"],
            "phenomenon":payload["phenomenon"],
            "cycle_time_utc":request["cycle_utc"],
            "count":rows.len(),
            "items":rows,
        }))
    }
}

fn cache_key(payload: &Value, regional: bool) -> Result<String, String> {
    let mut request = payload
        .as_object()
        .cloned()
        .ok_or("cache payload must be an object")?;
    request.remove("force_refresh");
    let bytes = serde_json::to_vec(&json!({
        "contract":"rust-native-runtime-cache-v1","source_id":env!("FIRESKY_ENGINE_SOURCE_ID"),
        "regional":regional,"request":request,
    }))
    .map_err(|e| e.to_string())?;
    Ok(Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect())
}

fn atomic_json(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path.parent().ok_or("native artifact parent missing")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut file = tempfile::Builder::new()
        .prefix(".native-")
        .tempfile_in(parent)
        .map_err(|e| e.to_string())?;
    serde_json::to_writer(&mut file, value).map_err(|e| e.to_string())?;
    file.as_file().sync_all().map_err(|e| e.to_string())?;
    file.persist(path).map_err(|e| e.to_string())?;
    Ok(())
}

fn regions() -> Vec<Value> {
    desktop_context::REGIONS.iter().map(|(code,prefix,b)| {
        let label = match *code {
            "china" => "中国", "east-asia" => "东亚", "southeast-asia" => "东南亚",
            "south-asia" => "南亚", "europe" => "欧洲", "north-america" => "北美",
            "south-america" => "南美", "oceania" => "大洋洲",
            "africa-middle-east" => "非洲及中东", "north-asia" => "北亚", _ => code,
        };
        json!({"code":code,"label":label,"bounds":[b[0],b[3],b[1],b[2]],
            "point_prefix":prefix,"timezone_name":if *code == "china" {Some("Asia/Shanghai")} else {None}})
    }).collect()
}

fn coverage(payload: &Value) -> Result<Vec<Value>, String> {
    let defaults = vec![json!("east-asia")];
    let requested = payload.get("regions").map_or(Ok(&defaults), |v| {
        v.as_array().ok_or("regions must be an array")
    })?;
    let known = regions();
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    for value in requested {
        let code = value
            .as_str()
            .ok_or("regional code must be text")?
            .trim()
            .to_lowercase();
        if !seen.insert(code.clone()) {
            continue;
        }
        if code == "custom" {
            let b = &payload["custom_bounds"];
            let bounds = ["west", "south", "east", "north"]
                .iter()
                .map(|key| {
                    b[*key]
                        .as_f64()
                        .filter(|v| v.is_finite())
                        .ok_or("invalid custom bounds")
                })
                .collect::<Result<Vec<_>, _>>()?;
            if bounds.iter().any(|value| !value.is_finite())
                || bounds[0] < -180.0
                || bounds[0] > 180.0
                || bounds[2] < -180.0
                || bounds[2] > 180.0
                || bounds[1] < -90.0
                || bounds[1] > 90.0
                || bounds[3] < -90.0
                || bounds[3] > 90.0
                || bounds[0] == bounds[2]
                || (bounds[0] == 180.0 && bounds[2] == -180.0)
                || bounds[1] >= bounds[3]
            {
                return Err("invalid custom bounds".into());
            }
            result.push(json!({"code":"custom","label":payload["custom_label"].as_str().unwrap_or("自定义区域"),
                "bounds":bounds,"timezone_name":payload["custom_timezone_name"],"point_prefix":"CUSTOM"}));
        } else {
            result.push(
                known
                    .iter()
                    .find(|v| v["code"] == code)
                    .cloned()
                    .ok_or("unknown regional domain")?,
            );
        }
    }
    if result.is_empty() {
        return Err("regions must be nonempty".into());
    }
    Ok(result)
}

fn artifact(root: &Path, entry: &Value, expected: &str) -> Result<Value, String> {
    if entry["file"] != expected {
        return Err("native artifact filename does not match contract".into());
    }
    let path = root.join(expected);
    let identity = tensor_bundle::regular_identity(&path)?;
    let expected_identity: tensor_bundle::SourceIdentity = decode(entry["identity"].clone())?;
    let limit = if expected == "forecast.json" || expected.starts_with("native-regional-") {
        512 * 1024 * 1024
    } else {
        8 * 1024 * 1024
    };
    if identity != expected_identity || identity.bytes > limit {
        return Err("native artifact integrity check failed".into());
    }
    let (document, verified): (Value, _) = request_file::read(&path, limit)?;
    if verified != identity {
        return Err("native artifact changed while reading".into());
    }
    Ok(document)
}

fn apply_publications(report: &mut Value, publications: &Value) {
    report["publication_pending"] = json!(false);
    if let Some(datasets) = report["datasets"].as_array_mut() {
        for row in datasets {
            let source = row["source"].as_str().unwrap_or_default();
            let Some(publication) = publications.get(source) else {
                continue;
            };
            let verified = publication["verified"] == true;
            let label = publication["label"].as_str().unwrap_or("unverified");
            let latest = verified
                && row["ready"] == true
                && row["request_cycle"]
                    .as_str()
                    .is_some_and(|v| v.split(" / ").all(|v| v == label));
            row["latest_available_cycle"] = json!(label);
            row["latest_available_cycle_utc"] = publication["cycle"].clone();
            row["verified"] = json!(verified);
            row["is_latest"] = json!(latest);
        }
    }
    if publications["cams"]["verified"] == true {
        report["cams_update_cycle"] = publications["cams"]["cycle"].clone();
    }
}

fn needs_cycle_age_confirmation(report: &Value) -> bool {
    report["ready"] == true
        && report["datasets"].as_array().is_some_and(|datasets| {
            datasets.iter().any(|row| {
                row["ready"] == true && (row["verified"] != true || row["is_latest"] != true)
            })
        })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    id: String,
    method: String,
    #[serde(default = "empty_params")]
    params: Value,
}
fn empty_params() -> Value {
    json!({})
}
fn error(id: Value, code: &str, message: &str) -> Value {
    json!({"id":id,"type":"error","error":{"code":code,"message":message}})
}
fn send(output: &Mutex<impl Write>, value: &Value) -> io::Result<()> {
    let mut output = output
        .lock()
        .map_err(|_| io::Error::other("desktop output poisoned"))?;
    serde_json::to_writer(&mut *output, value)?;
    output.write_all(b"\n")?;
    output.flush()
}

/// Bounded workers leave the reader available for cancellation while a forecast runs.
pub fn serve(input: impl BufRead, output: impl Write + Send, workspace: &Path) -> io::Result<()> {
    serve_runtime(
        input,
        output,
        Runtime::new(workspace).map_err(io::Error::other)?,
    )
}

/// Serve an already-constructed runtime; in-process hosts (engine-mobile)
/// validate `Runtime::new` before handing the service to a background thread.
pub fn serve_runtime(
    mut input: impl BufRead,
    output: impl Write + Send,
    runtime: Runtime,
) -> io::Result<()> {
    let active = Mutex::new(BTreeMap::<String, Arc<AtomicBool>>::new());
    let output = Mutex::new(output);
    let (sender, receiver) =
        mpsc::sync_channel::<(Request, Arc<AtomicBool>, Option<crate::task_history::Task>)>(16);
    let receiver = Mutex::new(receiver);
    thread::scope(|scope| {
        let mut workers = Vec::new();
        for _ in 0..4 {
            let (receiver, runtime, output, active) = (&receiver, &runtime, &output, &active);
            workers.push(scope.spawn(move || -> io::Result<()> {
                loop {
                    let job = receiver
                        .lock()
                        .map_err(|_| io::Error::other("desktop queue poisoned"))?
                        .recv();
                    let Ok((request, cancel, task)) = job else {
                        return Ok(());
                    };
                    let progress = |event| {
                        check(&cancel)?;
                        if let Some(task) = &task {
                            task.progress(&event)?;
                        }
                        send(
                            output,
                            &json!({"id":request.id,"type":"progress","event":event}),
                        )
                        .map_err(|e| e.to_string())
                    };
                    let mut result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        runtime.dispatch_cancellable(
                            &request.method,
                            request.params,
                            cancel.clone(),
                            &progress,
                            task.as_ref().map(|task| task.id()),
                        )
                    }))
                    .unwrap_or_else(|_| Err("native desktop worker panicked".into()));
                    let mut active = active
                        .lock()
                        .map_err(|_| io::Error::other("desktop state poisoned"))?;
                    let cancelled = cancel.load(Ordering::Acquire);
                    if let Some(task) = task {
                        if let Err(message) = task.finish(&result, cancelled) {
                            result = Err(format!("task history commit failed: {message}"));
                        }
                    }
                    let message = if cancelled {
                        error(
                            json!(request.id),
                            "cancelled",
                            "desktop operation cancelled",
                        )
                    } else {
                        match result {
                            Ok(value) => json!({"id":request.id,"type":"result","result":value}),
                            Err(message) => error(json!(request.id), "operation_failed", &message),
                        }
                    };
                    active.remove(&request.id);
                    send(output, &message)?;
                }
            }));
        }
        let reading = (|| -> io::Result<()> {
            let mut line = Vec::new();
            loop {
                match rpc::read_line(&mut input, &mut line)? {
                    Line::End => break,
                    Line::Oversized => {
                        send(
                            &output,
                            &error(Value::Null, "invalid_request", "request exceeds 1 MiB"),
                        )?;
                        continue;
                    }
                    Line::Data => {}
                }
                let request = serde_json::from_slice::<Request>(&line);
                let Ok(request) = request else {
                    send(
                        &output,
                        &error(Value::Null, "invalid_request", "invalid desktop request"),
                    )?;
                    continue;
                };
                if request.id.is_empty()
                    || request.id.len() > 128
                    || request.method.len() > 64
                    || request.method.is_empty()
                    || !request.params.is_object()
                {
                    send(
                        &output,
                        &error(
                            json!(request.id),
                            "invalid_request",
                            "invalid desktop request",
                        ),
                    )?;
                    continue;
                }
                let mut active = active
                    .lock()
                    .map_err(|_| io::Error::other("desktop state poisoned"))?;
                if active.contains_key(&request.id) {
                    send(
                        &output,
                        &error(
                            json!(request.id),
                            "invalid_request",
                            "duplicate active request id",
                        ),
                    )?;
                } else if request.method == "hello" {
                    let message = match runtime.dispatch("hello", request.params) {
                        Ok(value) => json!({"id":request.id,"type":"result","result":value}),
                        Err(message) => error(json!(request.id), "operation_failed", &message),
                    };
                    send(&output, &message)?;
                } else if request.method == "cancel" {
                    let target = request.params["request_id"]
                        .as_str()
                        .and_then(|id| active.get(id));
                    if let Some(cancel) = target {
                        cancel.store(true, Ordering::Release);
                        if let Err(message) = runtime
                            .cancel_history(request.params["request_id"].as_str().unwrap_or(""))
                        {
                            send(
                                &output,
                                &error(json!(request.id), "operation_failed", &message),
                            )?;
                            continue;
                        }
                    }
                    send(
                        &output,
                        &json!({"id":request.id,"type":"result","result":{"requested":target.is_some()}}),
                    )?;
                } else if active.len() >= 16 {
                    send(
                        &output,
                        &error(json!(request.id), "busy", "too many active requests"),
                    )?;
                } else {
                    let task =
                        match runtime.begin_task(&request.id, &request.method, &request.params) {
                            Ok(task) => task,
                            Err(message) => {
                                send(
                                    &output,
                                    &error(json!(request.id), "operation_failed", &message),
                                )?;
                                continue;
                            }
                        };
                    let cancel = Arc::new(AtomicBool::new(false));
                    active.insert(request.id.clone(), cancel.clone());
                    if let Err(failure) = sender.try_send((request, cancel, task)) {
                        let (mpsc::TrySendError::Full((request, _, task))
                        | mpsc::TrySendError::Disconnected((request, _, task))) = failure;
                        if let Some(task) = task {
                            task.finish(&Err("desktop queue unavailable".into()), false)
                                .map_err(io::Error::other)?;
                        }
                        active.remove(&request.id);
                        send(
                            &output,
                            &error(json!(request.id), "busy", "desktop queue unavailable"),
                        )?;
                    }
                }
            }
            Ok(())
        })();
        for (id, cancel) in active
            .lock()
            .map_err(|_| io::Error::other("desktop state poisoned"))?
            .iter()
        {
            cancel.store(true, Ordering::Release);
            let _ = runtime.cancel_history(id);
        }
        drop(sender);
        for worker in workers {
            worker
                .join()
                .map_err(|_| io::Error::other("desktop worker panicked"))??;
        }
        reading
    })
}

#[cfg(test)]
mod tests {
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
}
