//! Native HTTP transport. Computation is bounded and never runs on async workers.
mod desktop;
mod security;

use crate::desktop_runtime::Runtime;
use axum::{
    extract::{DefaultBodyLimit, Path, Query, State},
    http::{HeaderValue, StatusCode},
    response::{
        sse::{Event, KeepAlive, Sse},
        Html, IntoResponse, Response,
    },
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    convert::Infallible,
    path::{Path as FsPath, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::Semaphore;
use tower_http::{
    compression::CompressionLayer,
    services::{ServeDir, ServeFile},
    set_header::SetResponseHeaderLayer,
};

const PREFIX: &str = "/api/v1/firesky";
const MAX_JOBS: usize = 32;
const JOB_TTL: Duration = Duration::from_secs(1800);

#[derive(Clone)]
struct App {
    runtime: Arc<Runtime>,
    workspace: Arc<PathBuf>,
    slots: Arc<Semaphore>,
    jobs: Arc<Mutex<BTreeMap<String, Job>>>,
    desktop_jobs: Arc<desktop::Registry>,
}

struct Job {
    id: String,
    request_id: String,
    defer_result: bool,
    payload: Value,
    status: &'static str,
    result: Option<Value>,
    error: Option<String>,
    created: Instant,
    created_at: String,
    completed_at: Option<String>,
    cancel: Arc<AtomicBool>,
}

impl Job {
    fn snapshot(&self, id: &str) -> Value {
        json!({
            "id": id, "job_id": id, "status": self.status,
            "progress_percent": if self.completed_at.is_some() {100} else {0},
            "progress_stage": self.status,
            "progress_message": self.error.as_deref().unwrap_or(
                if self.completed_at.is_none() && self.cancel.load(Ordering::Acquire) {
                    "cancellation requested; waiting for worker exit"
                } else { self.status }),
            "error_message": self.error, "request_payload": self.payload,
            "cancel_requested":self.cancel.load(Ordering::Acquire),
            "created_at": self.created_at, "started_at": self.created_at,
            "completed_at": self.completed_at, "algorithm_version": "firesky-rust",
        })
    }
    fn finish(&mut self, outcome: Result<Value, String>) {
        if self.cancel.load(Ordering::Acquire) {
            self.status = "cancelled";
            self.result = None;
            self.error = None;
        } else {
            match outcome {
                Ok(result) => {
                    self.status = if result["status"] == "failed" {
                        "failed"
                    } else {
                        "done"
                    };
                    self.result = Some(result);
                }
                Err(error) => {
                    self.status = "failed";
                    self.error = Some(error);
                }
            }
        }
        self.completed_at = Some(now());
    }
    fn terminal(&self) -> Option<Value> {
        match self.status {
            "done" if self.defer_result => {
                Some(json!({"event":"done","stage":"done","percent":100,
                "message":"done","result_ref":self.id}))
            }
            "done" => Some(json!({"event":"done","stage":"done","percent":100,
                "message":"done","result":self.result})),
            "failed" => Some(json!({"event":"failed","stage":"failed","percent":100,
                "message":self.error,"status":"failed"})),
            "cancelled" => Some(
                json!({"event":"cancelled","stage":"cancelled","percent":100,
                "message":"cancelled"}),
            ),
            _ => None,
        }
    }
}

fn history_job_snapshot(record: &Value) -> Option<Value> {
    if record["method"] != "forecast" {
        return None;
    }
    let id = record["task_id"].as_str()?;
    let status = record["status"].as_str()?;
    let progress = record["progress"].as_object();
    let terminal = matches!(status, "done" | "failed" | "cancelled" | "interrupted");
    let percent = progress
        .and_then(|value| value.get("percent"))
        .and_then(Value::as_f64)
        .unwrap_or(if terminal { 100.0 } else { 0.0 });
    let stage = progress
        .and_then(|value| value.get("stage"))
        .and_then(Value::as_str)
        .unwrap_or(status);
    let message = progress
        .and_then(|value| value.get("message"))
        .and_then(Value::as_str)
        .unwrap_or(status);
    let payload = record["retry_request"]["payload"].clone();
    let completed_at = if terminal {
        record["updated_at_utc"].clone()
    } else {
        Value::Null
    };
    let error_message = match status {
        "failed" => "forecast failed",
        "cancelled" => "forecast cancelled",
        "interrupted" => "runtime exited before forecast completed",
        _ => "",
    };
    let mut snapshot = json!({
        "id":id,
        "job_id":id,
        "task_id":id,
        "status":status,
        "progress_percent":percent,
        "progress_stage":stage,
        "progress_message":message,
        "error_message":if error_message.is_empty() {Value::Null} else {json!(error_message)},
        "request_payload":payload,
        "retry_request":record["retry_request"],
        "created_at":record["created_at_utc"],
        "started_at":record["created_at_utc"],
        "completed_at":completed_at,
        "algorithm_version":"firesky-rust",
        "cancel_requested":record["cancel_requested"],
        "event_seq":record["event_seq"],
    });
    if record["result_ref"] != Value::Null {
        snapshot["result_ref"] = record["result_ref"].clone();
    }
    Some(snapshot)
}

fn valid_cache_key(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

type ApiResult = Result<Json<Value>, ApiError>;
#[derive(Debug)]
struct ApiError(StatusCode, String);
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":self.1}))).into_response()
    }
}
fn failure(error: String) -> ApiError {
    let status = if error.contains("busy") || error.contains("another prepare/forecast is active") {
        StatusCode::TOO_MANY_REQUESTS
    } else if error.contains("unsupported") || error.contains("not implemented") {
        StatusCode::NOT_IMPLEMENTED
    } else if error.contains("not ready")
        || error.contains("unavailable")
        || error.contains("missing")
    {
        StatusCode::SERVICE_UNAVAILABLE
    } else {
        StatusCode::BAD_REQUEST
    };
    ApiError(status, error)
}
fn now() -> String {
    let stamp: chrono::DateTime<chrono::Utc> = SystemTime::now().into();
    crate::time_contract::iso(stamp)
}

async fn dispatch(app: &App, method: &str, params: Value) -> ApiResult {
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    let runtime = app.runtime.clone();
    let method = method.to_owned();
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        runtime.dispatch(&method, params).map(Json).map_err(failure)
    })
    .await
    .map_err(|e| ApiError(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
}

async fn hello(State(app): State<App>) -> ApiResult {
    dispatch(&app, "hello", json!({})).await
}
async fn health() -> Json<Value> {
    Json(json!({"status":"ok","engine":"firesky-rust","python_required":false}))
}
fn artifact_directory(app: &App, variable: &str, legacy: &str, visible: &str) -> PathBuf {
    crate::paths::resolve(&app.workspace, variable, legacy, visible)
}
fn artifact_failure(error: String) -> ApiError {
    let status = if error.contains("not found")
        || error.contains("No such file")
        || error.contains("os error 2)")
        || error.contains("os error 3)")
    {
        StatusCode::NOT_FOUND
    } else {
        StatusCode::BAD_REQUEST
    };
    ApiError(status, error)
}
async fn map_frame(
    State(app): State<App>,
    Query(params): Query<BTreeMap<String, String>>,
) -> ApiResult {
    let token = params
        .get("token")
        .cloned()
        .ok_or_else(|| ApiError(StatusCode::BAD_REQUEST, "missing token".into()))?;
    let time = params
        .get("time")
        .cloned()
        .ok_or_else(|| ApiError(StatusCode::BAD_REQUEST, "missing time".into()))?;
    let directory = artifact_directory(
        &app,
        "FIRESKY_MAP_CACHE_DIR",
        ".cache/map-frames",
        "map-frames",
    );
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        crate::map_frames::read(&directory, &token, &time)
            .map(Json)
            .map_err(artifact_failure)
    })
    .await
    .map_err(|e| ApiError(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
}
async fn report_html(
    State(app): State<App>,
    Path(id): Path<String>,
) -> Result<Html<String>, ApiError> {
    let directory = artifact_directory(&app, "FIRESKY_REPORT_DIR", "reports/web", "reports");
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        crate::report_publication::read(&directory, &id)
            .map(Html)
            .map_err(artifact_failure)
    })
    .await
    .map_err(|e| ApiError(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
}
async fn search(
    State(app): State<App>,
    Query(params): Query<BTreeMap<String, String>>,
) -> ApiResult {
    dispatch(
        &app,
        "search",
        json!({"query":params.get("query").cloned().unwrap_or_default()}),
    )
    .await
}
async fn metadata(
    State(app): State<App>,
    Query(params): Query<BTreeMap<String, String>>,
) -> ApiResult {
    let coordinate = |key: &str| -> Result<f64, ApiError> {
        params
            .get(key)
            .ok_or_else(|| ApiError(StatusCode::BAD_REQUEST, format!("missing {key}")))?
            .parse()
            .map_err(|_| failure(format!("invalid {key}")))
    };
    dispatch(
        &app,
        "metadata",
        json!({"lat":coordinate("lat")?,"lon":coordinate("lon")?}),
    )
    .await
}
async fn inventory(State(app): State<App>) -> ApiResult {
    data_operation(app, json!({}), "status").await
}
async fn regional_contract() -> Json<Value> {
    Json(
        json!({"scope":"regional","contract":"rust-desktop-regional-forecast-v1",
        "point_format":"native-object-v1","runtime_python_used":false}),
    )
}
async fn data_operation(app: App, payload: Value, operation: &'static str) -> ApiResult {
    validate_payload(&payload)?;
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        match operation {
            "status" => crate::native_data::status(&app.workspace),
            "requirements" => crate::native_data::requirements(&app.workspace, &payload),
            "prune" => crate::native_data::prune(&app.workspace, &payload),
            _ => Err("unsupported data operation".into()),
        }
        .map(Json)
        .map_err(failure)
    })
    .await
    .map_err(|e| ApiError(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
}
async fn data_requirements(State(app): State<App>, Json(payload): Json<Value>) -> ApiResult {
    data_operation(app, payload, "requirements").await
}
async fn data_prune(State(app): State<App>, Json(payload): Json<Value>) -> ApiResult {
    data_operation(app, payload, "prune").await
}
async fn data_fetch_status(
    State(app): State<App>,
    Query(params): Query<BTreeMap<String, String>>,
) -> ApiResult {
    let id = params.get("job_id").ok_or_else(missing_job)?;
    let jobs = app.jobs.lock().unwrap();
    let job = jobs.get(id).ok_or_else(missing_job)?;
    let mut value = job.result.clone().unwrap_or_else(|| job.snapshot(id));
    value["job_id"] = json!(id);
    if let Some(error) = &job.error {
        value["errors"] = json!([error]);
    }
    Ok(Json(value))
}
async fn data_fetch(State(app): State<App>, Json(payload): Json<Value>) -> ApiResult {
    validate_payload(&payload)?;
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    let mut jobs = app.jobs.lock().unwrap();
    jobs.retain(|_, j| j.completed_at.is_none() || j.created.elapsed() <= JOB_TTL);
    if jobs.len() >= MAX_JOBS {
        return Err(ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native job registry is full".into(),
        ));
    }
    let id = format!(
        "data-{:x}-{:x}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos(),
        JOB_SEQUENCE.fetch_add(1, Ordering::Relaxed)
    );
    let cancel = Arc::new(AtomicBool::new(false));
    jobs.insert(
        id.clone(),
        Job {
            id: id.clone(),
            request_id: id.clone(),
            defer_result: false,
            payload: payload.clone(),
            status: "running",
            result: None,
            error: None,
            created: Instant::now(),
            created_at: now(),
            completed_at: None,
            cancel: cancel.clone(),
        },
    );
    drop(jobs);
    let task = id.clone();
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            crate::native_data::fetch(&app.workspace, &payload, &cancel)
        }))
        .unwrap_or_else(|_| Err("native data worker panicked".into()));
        if let Some(job) = app.jobs.lock().unwrap().get_mut(&task) {
            job.finish(outcome);
        };
    });
    Ok(Json(json!({"job_id":id,"status":"running"})))
}
async fn read_cache(
    State(app): State<App>,
    Query(params): Query<BTreeMap<String, String>>,
) -> ApiResult {
    let key = params.get("key").cloned().unwrap_or_default();
    let regional = params
        .get("regional")
        .is_some_and(|v| v == "true" || v == "1");
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        app.runtime
            .read_cache(json!({"key":key,"regional":regional}))
            .map(Json)
            .map_err(failure)
    })
    .await
    .map_err(|e| ApiError(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
}
async fn workflow(
    State(app): State<App>,
    Path(method): Path<String>,
    Json(params): Json<Value>,
) -> ApiResult {
    if !matches!(
        method.as_str(),
        "check" | "prepare" | "forecast" | "publications" | "dismiss" | "network_probe"
    ) {
        return Err(ApiError(
            StatusCode::NOT_FOUND,
            "workflow method not found".into(),
        ));
    }
    dispatch(&app, &method, params).await
}

fn validate_payload(payload: &Value) -> Result<(), ApiError> {
    if !payload.is_object() {
        return Err(failure("forecast requires an object".into()));
    }
    Ok(())
}

// The HTTP API already represents confirmation. Desktop retains explicit sessions.
fn compute(
    runtime: &Runtime,
    mut payload: Value,
    regional: bool,
    cancel: Arc<AtomicBool>,
) -> Result<Value, String> {
    if payload.get("model_source").is_none() {
        payload["model_source"] = json!("gfs");
    }
    let selected = payload
        .get("model_source")
        .and_then(Value::as_str)
        .unwrap_or("gfs")
        .to_owned();
    let result = runtime.compute_with_cancel(payload, regional, cancel)?;
    select_model(result, &selected)
}

fn select_model(result: Value, selected: &str) -> Result<Value, String> {
    if let Some(value) = result.get("model_results").and_then(|v| v.get(selected)) {
        return Ok(value.clone());
    }
    if let Some(error) = result.get("model_errors").and_then(|v| v.get(selected)) {
        return Err(error
            .as_str()
            .unwrap_or("native model forecast failed")
            .to_owned());
    }
    Ok(result)
}

fn regional_query(params: &BTreeMap<String, String>) -> Result<Value, ApiError> {
    let mut payload = serde_json::Map::new();
    for key in [
        "target_date",
        "phenomenon",
        "model_source",
        "forecast_mode",
        "satellite_source",
        "compute_backend",
        "gfs_resolution",
        "custom_label",
        "custom_timezone_name",
    ] {
        if let Some(value) = params.get(key) {
            payload.insert(key.into(), json!(value));
        }
    }
    for key in ["spacing_deg", "radius_km"] {
        if let Some(value) = params.get(key) {
            let number = value
                .parse::<f64>()
                .ok()
                .filter(|v| v.is_finite())
                .ok_or_else(|| failure(format!("invalid {key}")))?;
            payload.insert(key.into(), json!(number));
        }
    }
    if let Some(value) = params.get("regions") {
        payload.insert(
            "regions".into(),
            json!(value.split(',').collect::<Vec<_>>()),
        );
    }
    if params.contains_key("custom_west") {
        let mut bounds = serde_json::Map::new();
        for key in ["west", "east", "north", "south"] {
            let number = params
                .get(&format!("custom_{key}"))
                .and_then(|v| v.parse::<f64>().ok())
                .filter(|v| v.is_finite())
                .ok_or_else(|| failure("invalid custom bounds".into()))?;
            bounds.insert(key.into(), json!(number));
        }
        payload.insert("custom_bounds".into(), Value::Object(bounds));
    }
    for model in ["gfs", "ec"] {
        if let Some(value) = params.get(&format!("{model}_cycle_override")) {
            payload.insert(format!("_{model}_cycle_override"), json!(value));
        }
    }
    Ok(Value::Object(payload))
}

async fn regional_cached(
    State(app): State<App>,
    Query(params): Query<BTreeMap<String, String>>,
) -> ApiResult {
    let payload = regional_query(&params)?;
    let selected = params
        .get("model_source")
        .cloned()
        .unwrap_or_else(|| "gfs".into());
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let result = if let Some(key) = params.get("key") {
            app.runtime.read_cache(json!({"key":key,"regional":true}))
        } else {
            app.runtime.read_cache_for(payload, true)
        };
        result
            .and_then(|value| select_model(value, &selected))
            .map(Json)
            .map_err(|error| ApiError(StatusCode::NOT_FOUND, format!("native cache miss: {error}")))
    })
    .await
    .map_err(|e| ApiError(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
}

async fn regional_cross_section(
    State(app): State<App>,
    Query(params): Query<BTreeMap<String, String>>,
) -> ApiResult {
    let mut payload = regional_query(&params)?;
    for (key, low, high) in [
        ("lat", -90., 90.),
        ("lon", -180., 180.),
        ("elevation_m", -500., 9000.),
        ("offset_minutes", -240., 240.),
    ] {
        let number = params
            .get(key)
            .and_then(|v| v.parse::<f64>().ok())
            .filter(|v| v.is_finite() && (low..=high).contains(v))
            .ok_or_else(|| {
                ApiError(StatusCode::BAD_REQUEST, format!("invalid or missing {key}"))
            })?;
        payload[key] = json!(number);
    }
    if let Some(zone) = params
        .get("custom_timezone_name")
        .or_else(|| params.get("timezone_name"))
    {
        payload["timezone_name"] = json!(zone);
    }
    let result = compute_http(app, payload, false).await?.0;
    result
        .get("cross_section")
        .filter(|v| v.is_object())
        .cloned()
        .map(Json)
        .ok_or_else(|| {
            ApiError(
                StatusCode::SERVICE_UNAVAILABLE,
                "native cross-section source is unavailable".into(),
            )
        })
}

async fn compute_http(app: App, payload: Value, regional: bool) -> ApiResult {
    validate_payload(&payload)?;
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        compute(
            &app.runtime,
            payload,
            regional,
            Arc::new(AtomicBool::new(false)),
        )
        .map(Json)
        .map_err(failure)
    })
    .await
    .map_err(|e| ApiError(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
}
async fn point_compute(State(app): State<App>, Json(payload): Json<Value>) -> ApiResult {
    compute_http(app, payload, false).await
}
async fn regional_compute(State(app): State<App>, Json(payload): Json<Value>) -> ApiResult {
    compute_http(app, payload, true).await
}
fn nowcast(mut payload: Value) -> Result<Value, ApiError> {
    validate_payload(&payload)?;
    payload["forecast_mode"] = json!("nowcast");
    payload["satellite_source"] = json!("hsd");
    Ok(payload)
}
async fn nowcast_compute(State(app): State<App>, Json(payload): Json<Value>) -> ApiResult {
    compute_http(app, nowcast(payload)?, false).await
}

async fn start(app: App, payload: Value, regional: bool) -> ApiResult {
    validate_payload(&payload)?;
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    let mut jobs = app.jobs.lock().unwrap();
    jobs.retain(|_, j| j.completed_at.is_none() || j.created.elapsed() <= JOB_TTL);
    if jobs.len() >= MAX_JOBS {
        return Err(ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native job registry is full".into(),
        ));
    }
    let request_id = format!(
        "http-{:x}-{:x}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos(),
        JOB_SEQUENCE.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
    );
    let kind = if payload["forecast_mode"].as_str() == Some("nowcast") {
        "nowcast"
    } else if regional {
        "regional"
    } else {
        "point"
    };
    let retry = json!({"kind":kind,"payload":payload});
    let request_payload = crate::task_history::retry_request_snapshot(&retry)["payload"].clone();
    let task = app
        .runtime
        .begin_task(&request_id, "forecast", &retry)
        .map_err(|message| ApiError(StatusCode::INTERNAL_SERVER_ERROR, message))?
        .ok_or_else(|| {
            ApiError(
                StatusCode::INTERNAL_SERVER_ERROR,
                "native forecast task history was not created".into(),
            )
        })?;
    let id = task.id().to_owned();
    let cancel = Arc::new(AtomicBool::new(false));
    jobs.insert(
        id.clone(),
        Job {
            id: id.clone(),
            request_id: request_id.clone(),
            defer_result: regional,
            payload: request_payload,
            status: "running",
            result: None,
            error: None,
            created: Instant::now(),
            created_at: now(),
            completed_at: None,
            cancel: cancel.clone(),
        },
    );
    drop(jobs);
    let task_id = id.clone();
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let mut outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            compute(&app.runtime, payload, regional, cancel)
        }))
        .unwrap_or_else(|_| Err("native forecast worker panicked".into()));
        let mut jobs = app.jobs.lock().unwrap();
        if let Some(job) = jobs.get_mut(&task_id) {
            // Serialize cancellation against both durable and in-memory completion.
            if let Err(error) = task.finish(&outcome, job.cancel.load(Ordering::Acquire)) {
                outcome = Err(format!("task history commit failed: {error}"));
            }
            job.finish(outcome);
        }
    });
    Ok(Json(json!({"job_id":id})))
}
static JOB_SEQUENCE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
async fn point_start(State(app): State<App>, Json(payload): Json<Value>) -> ApiResult {
    start(app, payload, false).await
}
async fn regional_start(State(app): State<App>, Json(payload): Json<Value>) -> ApiResult {
    start(app, payload, true).await
}
async fn nowcast_start(State(app): State<App>, Json(payload): Json<Value>) -> ApiResult {
    start(app, nowcast(payload)?, false).await
}
async fn list_jobs(
    State(app): State<App>,
    Query(params): Query<BTreeMap<String, String>>,
) -> ApiResult {
    let limit = params
        .get("limit")
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(50)
        .clamp(1, 200);
    let offset = params
        .get("offset")
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(0);
    let history = app
        .runtime
        .task_history_list(100, params.get("before").map(|s| s.as_str()))
        .map_err(|error| ApiError(StatusCode::INTERNAL_SERVER_ERROR, error))?;
    let mut merged = BTreeMap::<String, Value>::new();
    if let Some(tasks) = history["tasks"].as_array() {
        for task in tasks {
            if let Some(snapshot) = history_job_snapshot(task) {
                let id = snapshot["job_id"].as_str().unwrap_or_default().to_owned();
                merged.insert(id, snapshot);
            }
        }
    }
    {
        let jobs = app.jobs.lock().unwrap();
        for (id, job) in jobs.iter() {
            merged.insert(id.clone(), job.snapshot(id));
        }
    }
    let mut filtered: Vec<_> = merged
        .into_values()
        .filter(|job| {
            params
                .get("status")
                .is_none_or(|status| job["status"].as_str() == Some(status.as_str()))
        })
        .collect();
    filtered.sort_by(|left, right| {
        right["created_at"]
            .as_str()
            .cmp(&left["created_at"].as_str())
            .then_with(|| right["job_id"].as_str().cmp(&left["job_id"].as_str()))
    });
    let total = filtered.len();
    let rows: Vec<_> = filtered.into_iter().skip(offset).take(limit).collect();
    Ok(Json(
        json!({"items":rows,"jobs":rows,"total":total,"limit":limit,"offset":offset}),
    ))
}
fn missing_job() -> ApiError {
    ApiError(
        StatusCode::NOT_FOUND,
        "forecast job not found or expired".into(),
    )
}
async fn job_detail(State(app): State<App>, Path(id): Path<String>) -> ApiResult {
    if let Some(snapshot) = {
        let jobs = app.jobs.lock().unwrap();
        jobs.get(&id).map(|job| job.snapshot(&id))
    } {
        return Ok(Json(snapshot));
    }
    let record = app
        .runtime
        .task_history_detail(&id)
        .map_err(|_| missing_job())?;
    history_job_snapshot(&record)
        .map(Json)
        .ok_or_else(missing_job)
}
async fn job_result(State(app): State<App>, Path(id): Path<String>) -> ApiResult {
    if let Some(result) = {
        let jobs = app.jobs.lock().unwrap();
        let job = jobs.get(&id).ok_or_else(missing_job)?;
        if let Some(error) = &job.error {
            return Err(failure(error.clone()));
        }
        job.result.clone()
    } {
        return Ok(Json(result));
    }
    let record = app
        .runtime
        .task_history_detail(&id)
        .map_err(|_| missing_job())?;
    let snapshot = history_job_snapshot(&record).ok_or_else(missing_job)?;
    match snapshot["status"].as_str().unwrap_or("unknown") {
        "done" => {
            let key = record["result_ref"]["cache"]["key"]
                .as_str()
                .filter(|value| valid_cache_key(value))
                .map(str::to_owned);
            let regional = record["result_ref"]["cache"]["regional"].as_bool();
            if let (Some(key), Some(regional)) = (key, regional) {
                let selected = record["retry_request"]["payload"]["model_source"]
                    .as_str()
                    .unwrap_or("gfs")
                    .to_owned();
                if let Ok(permit) = app.slots.clone().try_acquire_owned() {
                    let runtime = app.runtime.clone();
                    if let Ok(Ok(result)) = tokio::task::spawn_blocking(move || {
                        let _permit = permit;
                        runtime
                            .read_cache(json!({"key":key,"regional":regional}))
                            .and_then(|value| select_model(value, &selected))
                    })
                    .await
                    {
                        return Ok(Json(result));
                    }
                }
            }
            Err(ApiError(
                StatusCode::CONFLICT,
                "forecast result is no longer in memory; use result_ref from the durable job record"
                    .into(),
            ))
        }
        "failed" => Err(failure(
            "forecast failed; error details are not persisted".into(),
        )),
        status => Err(ApiError(
            StatusCode::CONFLICT,
            format!("forecast is {status}"),
        )),
    }
}
async fn cancel_job(State(app): State<App>, Path(id): Path<String>) -> ApiResult {
    let mut jobs = app.jobs.lock().unwrap();
    let job = jobs.get_mut(&id).ok_or_else(missing_job)?;
    if job.completed_at.is_some() {
        return Err(ApiError(
            StatusCode::CONFLICT,
            "forecast job already finished".into(),
        ));
    }
    app.runtime
        .cancel_history(&job.request_id)
        .map_err(|error| ApiError(StatusCode::INTERNAL_SERVER_ERROR, error))?;
    job.cancel.store(true, Ordering::Release);
    Ok(Json(
        json!({"job_id":id,"status":job.status,"cancel_requested":true}),
    ))
}

fn history_terminal_event(record: &Value) -> Option<Value> {
    let status = record["status"].as_str()?;
    if !matches!(status, "done" | "failed" | "cancelled" | "interrupted") {
        return None;
    }
    let mut event = json!({
        "event":status,
        "stage":status,
        "percent":100,
        "message":status,
    });
    if status == "done" && !record["result_ref"].is_null() {
        event["result_ref"] = record["result_ref"].clone();
    }
    Some(event)
}

fn history_event_value(record: &Value, event: &Value) -> Value {
    let name = event["event"].as_str().unwrap_or("progress");
    let mut value = json!({
        "event":name,
        "stage":name,
        "percent":if matches!(name, "done" | "failed" | "cancelled" | "interrupted") {100} else {0},
        "message":name,
    });
    if let Some(data) = event["data"].as_object() {
        for (key, item) in data {
            value[key] = item.clone();
        }
    }
    if name == "done" && !record["result_ref"].is_null() {
        value["result_ref"] = record["result_ref"].clone();
    }
    value
}

async fn history_events(app: App, id: String, after: u64) -> Result<Response, ApiError> {
    let stream = futures_util::stream::unfold(
        (app, id, after, false),
        |(app, id, cursor, done)| async move {
            if done {
                return None;
            }
            loop {
                let page = match app.runtime.task_history_events(&id, cursor) {
                    Ok(value) => value,
                    Err(error) => {
                        let event = Event::default().event("failed").data(
                            json!({"event":"failed","stage":"failed","percent":100,
                            "message":error})
                            .to_string(),
                        );
                        return Some((Ok::<_, Infallible>(event), (app, id, cursor, true)));
                    }
                };
                if let Some(event) = page["events"].as_array().and_then(|events| events.first()) {
                    let next = event["seq"].as_u64().unwrap_or(cursor);
                    let value = history_event_value(&page, event);
                    let terminal = matches!(
                        value["event"].as_str(),
                        Some("done" | "failed" | "cancelled" | "interrupted")
                    );
                    let event = Event::default()
                        .event(value["event"].as_str().unwrap_or("progress"))
                        .data(value.to_string());
                    return Some((Ok::<_, Infallible>(event), (app, id, next, terminal)));
                }
                if let Some(value) = history_terminal_event(&page) {
                    let event = Event::default()
                        .event(value["event"].as_str().unwrap_or("failed"))
                        .data(value.to_string());
                    return Some((Ok::<_, Infallible>(event), (app, id, cursor, true)));
                }
                tokio::time::sleep(Duration::from_millis(200)).await;
            }
        },
    );
    Ok(Sse::new(stream)
        .keep_alive(KeepAlive::default().interval(Duration::from_secs(15)))
        .into_response())
}

async fn events(
    State(app): State<App>,
    Query(params): Query<BTreeMap<String, String>>,
) -> Result<Response, ApiError> {
    let id = params.get("job_id").ok_or_else(missing_job)?.clone();
    let after = params
        .get("after_seq")
        .map(|value| {
            value
                .parse::<u64>()
                .map_err(|_| ApiError(StatusCode::BAD_REQUEST, "invalid after_seq".into()))
        })
        .transpose()?
        .unwrap_or(0);
    if app.runtime.task_history_detail(&id).is_ok() {
        app.runtime
            .task_history_events(&id, after)
            .map_err(|error| ApiError(StatusCode::BAD_REQUEST, error))?;
        return history_events(app, id, after).await;
    }
    if !app.jobs.lock().unwrap().contains_key(&id) {
        return Err(missing_job());
    }
    let stream = futures_util::stream::unfold((app, id, false), |(app, id, done)| async move {
        if done {
            return None;
        }
        loop {
            let terminal = {
                let jobs = app.jobs.lock().unwrap();
                jobs.get(&id).map(Job::terminal).unwrap_or_else(|| {
                    Some(json!({
                        "event":"failed","message":"forecast job expired","percent":100
                    }))
                })
            };
            if let Some(value) = terminal {
                let event = Event::default()
                    .event(value["event"].as_str().unwrap_or("failed"))
                    .data(value.to_string());
                return Some((Ok::<_, Infallible>(event), (app, id, true)));
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    });
    Ok(Sse::new(stream)
        .keep_alive(KeepAlive::default().interval(Duration::from_secs(15)))
        .into_response())
}
async fn unknown_api() -> ApiError {
    ApiError(
        StatusCode::NOT_IMPLEMENTED,
        "this API has not been ported to the native runtime".into(),
    )
}

fn web_root(workspace: &FsPath, configured: Option<PathBuf>) -> PathBuf {
    let path = configured.unwrap_or_else(|| PathBuf::from("apps/shared-web/dist"));
    if path.is_absolute() {
        path
    } else {
        workspace.join(path)
    }
}

pub fn router(workspace: &FsPath, runtime: Arc<Runtime>) -> Router {
    secured_router(workspace, runtime, security::Policy::local())
}

fn secured_router(workspace: &FsPath, runtime: Arc<Runtime>, policy: security::Policy) -> Router {
    let state = App {
        runtime,
        workspace: Arc::new(workspace.to_owned()),
        slots: Arc::new(Semaphore::new(4)),
        jobs: Arc::new(Mutex::new(BTreeMap::new())),
        desktop_jobs: Arc::new(desktop::Registry::new()),
    };
    let build = web_root(
        workspace,
        std::env::var_os("FIRESKY_WEB_ROOT").map(PathBuf::from),
    );
    let index = if build.join("200.html").is_file() {
        build.join("200.html")
    } else {
        build.join("index.html")
    };
    let mut api = Router::new()
        .merge(desktop::routes())
        .route("/health", get(health))
        .route("/runtime", get(hello))
        .route("/runtime/cache", get(read_cache))
        .route("/locations/search", get(search))
        .route("/points/metadata", get(metadata))
        .route("/map-frames", get(map_frame))
        .route("/reports/{id}", get(report_html))
        .route("/data/status", get(inventory))
        .route("/data/requirements", post(data_requirements))
        .route("/data/fetch", post(data_fetch))
        .route("/data/fetch/status", get(data_fetch_status))
        .route("/data/prune", post(data_prune))
        .route("/desktop/{method}", post(workflow))
        .route("/forecasts", post(point_start))
        .route("/forecasts/compute", post(point_compute))
        .route("/forecasts/nowcast", post(nowcast_start))
        .route("/forecasts/nowcast/compute", post(nowcast_compute))
        .route("/forecasts/national", post(regional_start))
        .route("/forecasts/national/contract", get(regional_contract))
        .route("/forecasts/national/cache", get(regional_cached))
        .route("/forecasts/national/summary", get(regional_cached))
        .route(
            "/forecasts/national/cross-section",
            get(regional_cross_section),
        )
        .route("/forecasts/national/compute", post(regional_compute))
        .route("/forecasts/events", get(events))
        .route("/forecasts/jobs", get(list_jobs))
        .route("/forecasts/jobs/{id}", get(job_detail))
        .route("/forecasts/results/{id}", get(job_result))
        .route("/forecasts/{id}/cancel", post(cancel_job));
    api = api.fallback(unknown_api);
    Router::new()
        .nest(PREFIX, api)
        .route("/api/health", get(health))
        .route("/api/location", get(search))
        .route("/api/point", get(metadata))
        .route("/api/forecast", post(point_compute))
        .route("/api/forecast/start", post(point_start))
        .route("/api/forecast/events", get(events))
        .route("/api/forecast/{id}/cancel", post(cancel_job))
        .fallback_service(ServeDir::new(&build).not_found_service(ServeFile::new(index)))
        .layer(DefaultBodyLimit::max(1024 * 1024))
        .layer(CompressionLayer::new())
        .layer(SetResponseHeaderLayer::overriding(
            axum::http::header::CACHE_CONTROL,
            HeaderValue::from_static("no-store"),
        ))
        .layer(axum::middleware::from_fn_with_state(
            policy,
            security::enforce,
        ))
        .with_state(state)
}

pub async fn serve(workspace: &FsPath, address: &str) -> Result<(), String> {
    let policy = security::Policy::from_env()?;
    let listener = tokio::net::TcpListener::bind(address)
        .await
        .map_err(|e| e.to_string())?;
    policy.validate_bind(listener.local_addr().map_err(|e| e.to_string())?)?;
    let runtime = Arc::new(Runtime::new(workspace)?);
    eprintln!(
        "firesky-rust listening on http://{}",
        listener.local_addr().map_err(|e| e.to_string())?
    );
    axum::serve(listener, secured_router(workspace, runtime, policy))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        body::{to_bytes, Body},
        http::Request,
    };
    use tower::ServiceExt;

    fn test_app() -> (tempfile::TempDir, Router) {
        let dir = tempfile::tempdir().unwrap();
        let runtime = Arc::new(Runtime::new(dir.path()).unwrap());
        let app = router(dir.path(), runtime);
        (dir, app)
    }
    #[test]
    fn static_root_resolves_default_relative_and_absolute_paths() {
        let directory = tempfile::tempdir().unwrap();
        let workspace = directory.path();
        assert_eq!(
            web_root(workspace, None),
            workspace.join("apps/shared-web/dist")
        );
        assert_eq!(
            web_root(workspace, Some(PathBuf::from("shared-web"))),
            workspace.join("shared-web")
        );
        let absolute = workspace.join("absolute-web");
        assert_eq!(web_root(workspace, Some(absolute.clone())), absolute);
    }
    #[tokio::test]
    async fn native_health_and_unknown_api() {
        let (_dir, app) = test_app();
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("{PREFIX}/health"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let value: Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 1024).await.unwrap()).unwrap();
        assert_eq!(value["python_required"], false);
        let response = app
            .oneshot(
                Request::builder()
                    .uri(format!("{PREFIX}/not-ported"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_IMPLEMENTED);
    }
    #[tokio::test]
    async fn malformed_and_oversized_requests_are_rejected() {
        let (_dir, app) = test_app();
        for (body, status) in [
            ("[]".to_owned(), StatusCode::BAD_REQUEST),
            ("{".to_owned(), StatusCode::BAD_REQUEST),
            (" ".repeat(1024 * 1024 + 1), StatusCode::PAYLOAD_TOO_LARGE),
        ] {
            let response = app
                .clone()
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri(format!("{PREFIX}/forecasts/compute"))
                        .header("content-type", "application/json")
                        .body(Body::from(body))
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(response.status(), status);
        }
    }
    #[tokio::test]
    async fn native_content_addressed_artifact_routes() {
        use sha2::{Digest, Sha256};
        let (dir, app) = test_app();
        let html = "<html><body>native report</body></html>";
        let id = Sha256::digest(html.as_bytes())
            .iter()
            .map(|v| format!("{v:02x}"))
            .collect::<String>()
            + ".html";
        // Fresh workspace uses Visible layout (reports/native-html).
        let folder = dir.path().join("reports/native-html");
        std::fs::create_dir_all(&folder).unwrap();
        std::fs::write(folder.join(&id), html).unwrap();
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("{PREFIX}/reports/{id}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert!(response.headers()["content-type"]
            .to_str()
            .unwrap()
            .starts_with("text/html"));
        assert_eq!(to_bytes(response.into_body(), 4096).await.unwrap(), html);
        // Legacy workspace (.cache present) resolves to reports/web/native-html.
        let legacy_dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(legacy_dir.path().join(".cache")).unwrap();
        let legacy_folder = legacy_dir.path().join("reports/web/native-html");
        std::fs::create_dir_all(&legacy_folder).unwrap();
        std::fs::write(legacy_folder.join(&id), html).unwrap();
        let legacy_runtime = Arc::new(Runtime::new(legacy_dir.path()).unwrap());
        let legacy_app = router(legacy_dir.path(), legacy_runtime);
        let legacy_response = legacy_app
            .oneshot(
                Request::builder()
                    .uri(format!("{PREFIX}/reports/{id}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(legacy_response.status(), StatusCode::OK);
        assert_eq!(
            to_bytes(legacy_response.into_body(), 4096).await.unwrap(),
            html
        );
        for uri in [
            format!("{PREFIX}/map-frames"),
            format!("{PREFIX}/map-frames?token=../../bad&time=2026-11-11T09:00:00Z"),
            format!("{PREFIX}/reports/not-an-id.html"),
        ] {
            let response = app
                .clone()
                .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        }
        let response = app
            .oneshot(
                Request::builder()
                    .uri(format!(
                        "{PREFIX}/map-frames?token={}&time=2026-11-11T09:00:00Z",
                        "a".repeat(24)
                    ))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            failure("another prepare/forecast is active".into()).0,
            StatusCode::TOO_MANY_REQUESTS
        );
    }
    #[tokio::test]
    async fn native_regional_and_data_routes_have_explicit_contracts() {
        let (_dir, app) = test_app();
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("{PREFIX}/forecasts/national/contract"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let value: Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 4096).await.unwrap()).unwrap();
        assert_eq!(value["contract"], "rust-desktop-regional-forecast-v1");
        assert_eq!(value["point_format"], "native-object-v1");
        assert!(value.get("summary_version").is_none());
        for uri in [
            format!("{PREFIX}/forecasts/national/summary?key=invalid"),
            format!("{PREFIX}/forecasts/national/cross-section"),
        ] {
            let response = app
                .clone()
                .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert!(matches!(
                response.status(),
                StatusCode::BAD_REQUEST | StatusCode::NOT_FOUND
            ));
        }
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!("{PREFIX}/data/requirements"))
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"sources":["unknown"]}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }
    #[tokio::test]
    async fn cancelled_job_does_not_publish_a_result() {
        let dir = tempfile::tempdir().unwrap();
        let app = App {
            runtime: Arc::new(Runtime::new(dir.path()).unwrap()),
            workspace: Arc::new(dir.path().to_owned()),
            slots: Arc::new(Semaphore::new(1)),
            jobs: Arc::new(Mutex::new(BTreeMap::new())),
            desktop_jobs: Arc::new(desktop::Registry::new()),
        };
        let task = app
            .runtime
            .begin_task(
                "test-request",
                "forecast",
                &json!({"kind":"point","payload":{"lat":30.0,"lon":120.0}}),
            )
            .unwrap()
            .unwrap();
        let history_id = task.id().to_owned();
        let cancel = Arc::new(AtomicBool::new(false));
        app.jobs.lock().unwrap().insert(
            "test".into(),
            Job {
                id: "test".into(),
                request_id: "test-request".into(),
                defer_result: false,
                payload: json!({}),
                status: "running",
                result: None,
                error: None,
                created: Instant::now(),
                created_at: now(),
                completed_at: None,
                cancel: cancel.clone(),
            },
        );
        let permit = app.slots.clone().try_acquire_owned().unwrap();
        let worker_app = app.clone();
        let (exit, wait) = tokio::sync::oneshot::channel::<()>();
        let worker = tokio::spawn(async move {
            let _permit = permit;
            wait.await.unwrap();
            let mut jobs = worker_app.jobs.lock().unwrap();
            let job = jobs.get_mut("test").unwrap();
            let outcome = Ok(json!({"should_not_publish":true}));
            task.finish(&outcome, job.cancel.load(Ordering::Acquire))
                .unwrap();
            job.finish(outcome);
        });
        let response = cancel_job(State(app.clone()), Path("test".into()))
            .await
            .unwrap()
            .0;
        assert!(cancel.load(Ordering::Acquire));
        assert_eq!(response["status"], "running");
        assert_eq!(response["cancel_requested"], true);
        assert!(app.slots.clone().try_acquire_owned().is_err());
        assert!(app.jobs.lock().unwrap()["test"].terminal().is_none());
        let running = app.runtime.task_history_detail(&history_id).unwrap();
        assert_eq!(running["status"], "running");
        assert_eq!(running["cancel_requested"], true);
        assert_eq!(
            app.jobs.lock().unwrap()["test"].snapshot("test")["cancel_requested"],
            true
        );
        assert!(cancel_job(State(app.clone()), Path("test".into()))
            .await
            .is_ok());
        assert!(job_result(State(app.clone()), Path("test".into()))
            .await
            .is_err());
        exit.send(()).unwrap();
        worker.await.unwrap();
        assert!(app.slots.clone().try_acquire_owned().is_ok());
        assert!(app.jobs.lock().unwrap()["test"].result.is_none());
        assert_eq!(
            app.jobs.lock().unwrap()["test"].terminal().unwrap()["event"],
            "cancelled"
        );
        let finished = app.runtime.task_history_detail(&history_id).unwrap();
        assert_eq!(finished["status"], "cancelled");
        assert!(finished["result_ref"].is_null());
        assert!(cancel_job(State(app), Path("test".into())).await.is_err());
    }

    #[tokio::test]
    async fn generic_forecast_history_survives_runtime_restart_and_replays_events() {
        let dir = tempfile::tempdir().unwrap();
        let runtime = Arc::new(Runtime::new(dir.path()).unwrap());
        let app = App {
            runtime,
            workspace: Arc::new(dir.path().to_owned()),
            slots: Arc::new(Semaphore::new(1)),
            jobs: Arc::new(Mutex::new(BTreeMap::new())),
            desktop_jobs: Arc::new(desktop::Registry::new()),
        };
        let ticket = start(app.clone(), json!({}), false).await.unwrap().0;
        let id = ticket["job_id"].as_str().unwrap().to_owned();
        let current = tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                let value = job_detail(State(app.clone()), Path(id.clone()))
                    .await
                    .unwrap()
                    .0;
                if value["status"] != "running" {
                    break value;
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
        assert_eq!(current["status"], "failed");
        let history = app.runtime.task_history_list(10, None).unwrap();
        assert_eq!(history["total"], 1);
        assert_eq!(history["tasks"][0]["task_id"], id);
        assert_eq!(history["tasks"][0]["method"], "forecast");
        assert_eq!(
            app.runtime.task_history_events(&id, 0).unwrap()["events"]
                .as_array()
                .unwrap()
                .last()
                .unwrap()["event"],
            "failed"
        );
        drop(app);

        let restarted_runtime = Arc::new(Runtime::new(dir.path()).unwrap());
        let restarted = App {
            runtime: restarted_runtime,
            workspace: Arc::new(dir.path().to_owned()),
            slots: Arc::new(Semaphore::new(1)),
            jobs: Arc::new(Mutex::new(BTreeMap::new())),
            desktop_jobs: Arc::new(desktop::Registry::new()),
        };
        let recovered = job_detail(State(restarted.clone()), Path(id.clone()))
            .await
            .unwrap()
            .0;
        assert_eq!(recovered["status"], "failed");
        assert_eq!(recovered["job_id"], id);
        assert!(job_result(State(restarted.clone()), Path(id.clone()))
            .await
            .is_err());
        let listed = list_jobs(
            State(restarted.clone()),
            Query(BTreeMap::from([("limit".into(), "10".into())])),
        )
        .await
        .unwrap()
        .0;
        assert_eq!(listed["total"], 1);
        assert_eq!(listed["items"][0]["job_id"], id);
        assert_eq!(listed["jobs"][0]["job_id"], id);
        let page = restarted.runtime.task_history_events(&id, 0).unwrap();
        assert_eq!(page["status"], "failed");
        assert_eq!(page["truncated"], false);
        let future = events(
            State(restarted.clone()),
            Query(BTreeMap::from([
                ("job_id".into(), id.clone()),
                ("after_seq".into(), "99".into()),
            ])),
        )
        .await
        .unwrap_err();
        assert_eq!(future.0, StatusCode::BAD_REQUEST);
        let response = events(
            State(restarted),
            Query(BTreeMap::from([
                ("job_id".into(), id),
                ("after_seq".into(), "0".into()),
            ])),
        )
        .await
        .unwrap();
        let body = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
        let text = String::from_utf8(body.to_vec()).unwrap();
        assert!(text.contains("event: failed"), "{text}");
    }
}
