//! Desktop JSON-lines service internalized in-process (blueprint MN1).
//!
//! Orchestration semantics are a faithful port of `src/firesky/desktop_rpc.py`
//! (`DesktopService.dispatch` + `serve`): progress envelope redaction, wire
//! level cooperative cancellation, session tokens with 1800s expiry /
//! single-use / ≤32 window, and non-blocking single-flight compute lock.
//! Method arms are reused from `engine/src/rpc.rs`
//! (`firesky_engine::rpc::dispatch`) — the session authority is the engine's
//! `desktop_session` arm, exactly like the Python native-sessions path.

use crate::backend::DesktopBackend;
use crate::pipe::PipeReader;
use serde_json::{json, Map, Value};
use std::collections::{HashMap, VecDeque};
use std::io::BufRead;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

pub const PROTOCOL_VERSION: i64 = 1;
/// Wire frame limit, identical to desktop and engine (`MAX_LINE_BYTES`).
pub const MAX_LINE_BYTES: usize = 1024 * 1024;
/// desktop_rpc.py `SESSION_SECONDS`.
pub const SESSION_SECONDS: u64 = 1800;
const MAX_ID_BYTES: usize = 128;
const MAX_SESSIONS: usize = 32;
/// desktop_rpc.py serve: `ThreadPoolExecutor(max_workers=4)` with ≤16 active.
const MAX_ACTIVE_REQUESTS: usize = 16;
const WORKER_THREADS: usize = 4;
const EXPIRED: &str = "数据检查已过期，请重新检查。";

/// Cooperative cancellation marker carried through the progress chain.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cancelled;

/// Operation failure: cooperative cancel or an error message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OpError {
    Cancelled,
    Message(String),
}

impl From<Cancelled> for OpError {
    fn from(_: Cancelled) -> Self {
        OpError::Cancelled
    }
}

/// Raw progress sink; the wire layer applies cancel-check + redaction +
/// envelope. Backends call it with desktop-shaped events (`percent`, `stage`,
/// `message`, optional `model_source` / `probe`).
pub type RawProgress<'a> = &'a (dyn Fn(Value) -> Result<(), Cancelled> + 'a);

/// Frame sink: one serialized JSON frame per call (result/error/progress).
pub type FrameSink = Arc<dyn Fn(&str) + Send + Sync>;

/// Start-time configuration (desktop cwd / ecCodes definitions / cache roots).
#[derive(Clone, Debug, Default)]
pub struct MobileConfig {
    /// Desktop semantics of the process working directory.
    pub workspace_root: PathBuf,
    /// ecCodes definitions directory (singular ECCODES_DEFINITION_PATH, MM0
    /// pitfall 10); injected by the FFI layer before engine initialization.
    pub definitions_path: Option<PathBuf>,
    /// Sandbox cache root for gfs/ecmwf/cams datasets.
    pub cache_root: Option<PathBuf>,
}

impl MobileConfig {
    /// Engine `desktop_inventory::Config` JSON derived from the cache root.
    pub fn cache_config(&self) -> Option<Value> {
        let root = self.cache_root.as_ref()?;
        Some(json!({
            "gfs_cache_dir": root.join("gfs"),
            "ecmwf_cache_dir": root.join("ecmwf"),
            "cams_cache_dir": root.join("cams"),
        }))
    }
}

struct SessionCell {
    kind: String,
    payload: Mutex<Value>,
    report: Mutex<Arc<Value>>,
    created: Mutex<Instant>,
    consumed: AtomicBool,
    dismissed: AtomicBool,
    /// desktop_rpc.py `session.lock` — one operation at a time, try-acquire.
    op_lock: Mutex<()>,
}

type SessionRef = Arc<SessionCell>;

struct Queue {
    jobs: Mutex<VecDeque<(Value, Arc<AtomicBool>)>>,
    signal: Condvar,
    open: AtomicBool,
}

impl Queue {
    fn push(&self, job: (Value, Arc<AtomicBool>)) {
        self.jobs.lock().unwrap().push_back(job);
        self.signal.notify_one();
    }

    /// EOF (desktop executor shutdown) runs already-queued jobs, then exits.
    fn close(&self) {
        self.open.store(false, Ordering::Release);
        self.signal.notify_all();
    }

    fn pop(&self) -> Option<(Value, Arc<AtomicBool>)> {
        let mut jobs = self.jobs.lock().unwrap();
        loop {
            if let Some(job) = jobs.pop_front() {
                return Some(job);
            }
            if !self.open.load(Ordering::Acquire) {
                return None;
            }
            jobs = self.signal.wait(jobs).unwrap();
        }
    }
}

struct Inner {
    config: MobileConfig,
    backend: Arc<dyn DesktopBackend>,
    sink: FrameSink,
    sessions: Mutex<HashMap<String, SessionRef>>,
    /// desktop_rpc.py `compute_lock` — prepare/forecast single-flight.
    compute_lock: Mutex<()>,
    owner: String,
    active: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
}

static OWNER_SEQUENCE: AtomicU64 = AtomicU64::new(0);

fn new_owner() -> String {
    let sequence = OWNER_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let pid = std::process::id();
    format!("mobile-{nanos:x}-{sequence}-{pid}")
}

impl Inner {
    /// Method dispatch: orchestration methods implemented per desktop_rpc.py;
    /// everything else is delegated to the shared engine arms.
    fn dispatch(
        &self,
        method: &str,
        params: &Value,
        progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<Value, OpError> {
        match method {
            "hello" => Ok(self.hello()),
            "check" => self.check(params, progress, cancel),
            "dismiss" => self.dismiss(params),
            "publications" => self.publications(params),
            "prepare" => self.prepare(params, progress, cancel),
            "forecast" => self.forecast(params, progress, cancel),
            other => firesky_engine::rpc::dispatch(other, params.clone())
                .map_err(OpError::Message),
        }
    }

    /// desktop_rpc.py `hello` — protocol handshake, mobile v1 capabilities.
    fn hello(&self) -> Value {
        json!({
            "protocol": PROTOCOL_VERSION,
            "engine": "firesky",
            "capabilities": ["point", "inventory", "network_probe"],
            "cams_credentials": cams_status(),
            "workspace": self.config.workspace_root.to_string_lossy(),
            // v1 移动端无区域预测（docs/MOBILE_NATIVE_PLAN.md §3）。
            "regions": [],
        })
    }

    /// Engine arm call — the shared `desktop_session` authority.
    fn session_arm(&self, params: Value) -> Result<Value, String> {
        firesky_engine::rpc::dispatch("desktop_session", params)
    }

    /// desktop_rpc.py `_session` — token lookup + native validation.
    fn session(&self, token: &str) -> Result<SessionRef, OpError> {
        let session = {
            let sessions = self.sessions.lock().unwrap();
            let Some(session) = sessions.get(token).cloned() else {
                return Err(OpError::Message(EXPIRED.to_string()));
            };
            if session.dismissed.load(Ordering::Acquire)
                || session.consumed.load(Ordering::Acquire)
                // Mirror of desktop `SESSION_SECONDS`; the engine arm enforces
                // the same bound on its own clock.
                || session.created.lock().unwrap().elapsed()
                    > Duration::from_secs(SESSION_SECONDS)
            {
                return Err(OpError::Message(EXPIRED.to_string()));
            }
            session
        };
        let native = self
            .session_arm(json!({
                "action": "validate", "owner": &self.owner, "session": token,
            }))
            .map_err(|_| OpError::Message(EXPIRED.to_string()))?;
        if native.get("kind") != Some(&json!(session.kind)) {
            return Err(OpError::Message(EXPIRED.to_string()));
        }
        Ok(session)
    }

    /// desktop_rpc.py `check` — normalize, check, pin, create session token.
    fn check(
        &self,
        params: &Value,
        progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<Value, OpError> {
        let kind = params
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("point")
            .to_string();
        if !matches!(kind.as_str(), "point" | "regional" | "nowcast") {
            return Err(OpError::Message("未知预测类型。".into()));
        }
        let mut payload = match params.get("payload") {
            Some(payload) if payload.is_object() => payload.clone(),
            _ => return Err(OpError::Message("预测参数必须是对象。".into())),
        };
        payload["model_source"] = json!("both");
        payload["workers"] = json!(1);
        if kind == "regional" {
            payload["spacing_deg"] = json!(0.25);
        }
        let report = Arc::new(self.backend.check(&payload, &kind, true, false, progress, cancel)?);
        progress(json!({"percent": 100, "stage": "check"}))?;
        let mut pinned = payload.clone();
        pin(&mut pinned, &report);
        let token = {
            let mut sessions = self.sessions.lock().unwrap();
            sessions.retain(|_, session| {
                !session.dismissed.load(Ordering::Acquire)
                    && !session.consumed.load(Ordering::Acquire)
                    && session
                        .created
                        .lock()
                        .unwrap()
                        .elapsed()
                        <= Duration::from_secs(SESSION_SECONDS)
            });
            if sessions.len() >= MAX_SESSIONS {
                return Err(OpError::Message("检查窗口过多，请关闭后重试。".into()));
            }
            let native = self
                .session_arm(json!({
                    "action": "create", "owner": &self.owner, "kind": kind,
                    "payload": &pinned, "report": report.as_ref(),
                }))
                .map_err(OpError::Message)?;
            let token = native
                .get("session")
                .and_then(Value::as_str)
                .ok_or_else(|| OpError::Message("desktop session token is missing".into()))?
                .to_string();
            sessions.insert(
                token.clone(),
                Arc::new(SessionCell {
                    kind,
                    payload: Mutex::new(pinned),
                    report: Mutex::new(report.clone()),
                    created: Mutex::new(Instant::now()),
                    consumed: AtomicBool::new(false),
                    dismissed: AtomicBool::new(false),
                    op_lock: Mutex::new(()),
                }),
            );
            token
        };
        Ok(json!({"session": token, "report": client_report(&report)}))
    }

    /// desktop_rpc.py `dismiss`.
    fn dismiss(&self, params: &Value) -> Result<Value, OpError> {
        let token = params
            .get("session")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let known = self.sessions.lock().unwrap().contains_key(&token);
        if known {
            // Idempotent on the native authority, like the Python path.
            self.session_arm(json!({
                "action": "dismiss", "owner": &self.owner, "session": &token,
            }))
            .map_err(OpError::Message)?;
        }
        if let Some(session) = self.sessions.lock().unwrap().get(&token) {
            session.dismissed.store(true, Ordering::Release);
        }
        Ok(json!({"dismissed": true}))
    }

    /// desktop_rpc.py `publications` — never blocks the session operation lock.
    fn publications(&self, params: &Value) -> Result<Value, OpError> {
        let token = params
            .get("session")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let session = self.session(&token)?;
        let (snapshot, sources) = {
            let report = session.report.lock().unwrap();
            let sources = report
                .get("datasets")
                .and_then(Value::as_array)
                .map(|rows| {
                    rows.iter()
                        .filter_map(|row| {
                            row.get("source").and_then(Value::as_str).map(String::from)
                        })
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            (report.clone(), sources)
        };
        let publications = self.backend.publications(&sources)?;
        if let Ok(_operation) = session.op_lock.try_lock() {
            let current = session.report.lock().unwrap().clone();
            if Arc::ptr_eq(&current, &snapshot)
                && !session.dismissed.load(Ordering::Acquire)
                && !session.consumed.load(Ordering::Acquire)
            {
                let next = Arc::new(self.backend.apply_publications(&current, &publications)?);
                *session.report.lock().unwrap() = next.clone();
                self.session_arm(json!({
                    "action": "update", "owner": &self.owner, "session": &token,
                    "report": next.as_ref(),
                }))
                .map_err(OpError::Message)?;
            }
        }
        let report = session.report.lock().unwrap().clone();
        Ok(json!({"session": &token, "report": client_report(report.as_ref())}))
    }

    /// desktop_rpc.py `prepare` — refresh check, pin, prepare, strict recheck.
    fn prepare(
        &self,
        params: &Value,
        progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<Value, OpError> {
        let token = params
            .get("session")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let session = self.session(&token)?;
        let _operation = session
            .op_lock
            .try_lock()
            .map_err(|_| OpError::Message("当前数据检查仍在处理，请稍候。".into()))?;
        if session.dismissed.load(Ordering::Acquire) || session.consumed.load(Ordering::Acquire) {
            return Err(OpError::Message("数据检查已关闭，请重新检查。".into()));
        }
        let _compute = self
            .compute_lock
            .try_lock()
            .map_err(|_| OpError::Message("已有下载或预测任务正在运行。".into()))?;
        let kind = session.kind.clone();
        let snapshot = session.report.lock().unwrap().clone();
        if needs_refresh(&snapshot) {
            let mut payload = session.payload.lock().unwrap().clone();
            if let Some(object) = payload.as_object_mut() {
                object.remove("_gfs_cycle_override");
                object.remove("_ec_cycle_override");
            }
            let fresh = self
                .backend
                .check(&payload, &kind, true, true, progress, cancel)?;
            *session.payload.lock().unwrap() = payload;
            *session.report.lock().unwrap() = Arc::new(fresh);
        }
        let payload = {
            let mut payload = session.payload.lock().unwrap();
            pin(&mut payload, session.report.lock().unwrap().as_ref());
            let mut report = session.report.lock().unwrap().as_ref().clone();
            report["ready"] = json!(false);
            *session.report.lock().unwrap() = Arc::new(report.clone());
            payload.clone()
        };
        let report_for_update = session.report.lock().unwrap().as_ref().clone();
        self.session_arm(json!({
            "action": "update", "owner": &self.owner, "session": &token,
            "payload": &payload, "report": report_for_update,
        }))
        .map_err(OpError::Message)?;
        self.backend.prepare(&payload, &kind, progress, cancel)?;
        let final_report =
            Arc::new(self.backend.check(&payload, &kind, false, false, progress, cancel)?);
        {
            let mut payload = session.payload.lock().unwrap();
            pin(&mut payload, final_report.as_ref());
        }
        *session.report.lock().unwrap() = final_report.clone();
        let final_payload = session.payload.lock().unwrap().clone();
        self.session_arm(json!({
            "action": "update", "owner": &self.owner, "session": &token,
            "payload": final_payload, "report": final_report.as_ref(),
        }))
        .map_err(OpError::Message)?;
        Ok(json!({"session": &token, "report": client_report(final_report.as_ref())}))
    }

    /// desktop_rpc.py `forecast` — confirmed/ready gates, strict recheck,
    /// single-use consumption, then the forecast chains.
    fn forecast(
        &self,
        params: &Value,
        progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<Value, OpError> {
        let token = params
            .get("session")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let session = self.session(&token)?;
        let _operation = session
            .op_lock
            .try_lock()
            .map_err(|_| OpError::Message("当前数据检查仍在处理，请稍候。".into()))?;
        if session.dismissed.load(Ordering::Acquire) || session.consumed.load(Ordering::Acquire) {
            return Err(OpError::Message("数据检查已关闭，请重新检查。".into()));
        }
        if params.get("confirmed").and_then(Value::as_bool) != Some(true) {
            return Err(OpError::Message("请先确认数据，再开始预测。".into()));
        }
        if !session
            .report
            .lock()
            .unwrap()
            .get("ready")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            return Err(OpError::Message("数据有缺失，请先下载。".into()));
        }
        let _compute = self
            .compute_lock
            .try_lock()
            .map_err(|_| OpError::Message("已有下载或预测任务正在运行。".into()))?;
        let kind = session.kind.clone();
        let payload = session.payload.lock().unwrap().clone();
        // Metadata-only checks do not prove integrity: strict recheck.
        let strict =
            Arc::new(self.backend.check(&payload, &kind, false, false, progress, cancel)?);
        if !strict
            .get("ready")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            *session.report.lock().unwrap() = strict;
            return Err(OpError::Message(
                "数据校验未通过，请重新检查并下载。".into(),
            ));
        }
        *session.report.lock().unwrap() = strict.clone();
        self.session_arm(json!({
            "action": "update", "owner": &self.owner, "session": &token,
            "payload": &payload, "report": strict.as_ref(),
        }))
        .map_err(OpError::Message)?;
        progress(json!({"stage": "score", "percent": 0}))?;
        if session.dismissed.load(Ordering::Acquire) {
            return Err(OpError::Message("预测已取消。".into()));
        }
        self.session_arm(json!({
            "action": "consume", "owner": &self.owner, "session": &token,
        }))
        .map_err(OpError::Message)?;
        session.consumed.store(true, Ordering::Release);
        let mut forecast_payload = payload.clone();
        if let Some(object) = forecast_payload.as_object_mut() {
            object.remove("_cams_refresh_cycle");
        }
        self.forecast_run(&kind, forecast_payload, progress, cancel)
    }

    /// desktop_rpc.py `_forecast` — nowcast single chain or the gfs/ec model
    /// pair with percent remap and partial-failure collection.
    fn forecast_run(
        &self,
        kind: &str,
        payload: Value,
        progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<Value, OpError> {
        if kind == "nowcast" {
            return self.backend.forecast(&payload, kind, progress, cancel);
        }
        let mut results = Map::new();
        let mut errors: Vec<(String, String)> = Vec::new();
        for (model, index) in [("gfs", 0usize), ("ec", 1usize)] {
            let update = |event: Value| -> Result<(), Cancelled> {
                let mut event = event;
                if let Some(object) = event.as_object_mut() {
                    object.insert("model_source".into(), json!(model));
                    let percent = object
                        .get("percent")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0)
                        .trunc();
                    let percent = percent.clamp(0.0, 100.0) as i64;
                    object.insert("percent".into(), json!(index as i64 * 50 + percent / 2));
                }
                progress(event)
            };
            update(json!({"percent": 0}))?;
            let mut model_payload = payload.clone();
            model_payload["model_source"] = json!(model);
            match self.backend.forecast(&model_payload, kind, &update, cancel) {
                Ok(value) => {
                    results.insert(model.to_string(), value);
                }
                Err(OpError::Cancelled) => return Err(OpError::Cancelled),
                Err(OpError::Message(message)) => errors.push((model.to_string(), message)),
            }
        }
        if results.is_empty() {
            let joined = errors
                .iter()
                .map(|(model, message)| format!("{}: {}", model.to_uppercase(), message))
                .collect::<Vec<_>>()
                .join("；");
            return Err(OpError::Message(joined));
        }
        let model_errors: Map<String, Value> = errors
            .into_iter()
            .map(|(model, message)| (model, json!(message)))
            .collect();
        Ok(json!({
            "model_results": Value::Object(results),
            "model_errors": Value::Object(model_errors),
        }))
    }
}

/// desktop_rpc.py `_pin` — freeze the checked cycles into the payload.
fn pin(payload: &mut Value, report: &Value) {
    let cycle = report
        .get("model_cycle")
        .filter(|value| value.as_str().is_some_and(|cycle| !cycle.is_empty()));
    if let (Some(cycle), Some(source)) = (cycle, report.get("model_source").and_then(Value::as_str))
    {
        payload[format!("_{source}_cycle_override")] = cycle.clone();
    }
    if let Some(cycles) = report.get("model_cycles").and_then(Value::as_object) {
        for (model, cycle) in cycles {
            payload[format!("_{model}_cycle_override")] = cycle.clone();
        }
    }
    if let Some(reference) = report.get("reference_time_utc").filter(|v| !v.is_null()) {
        payload["reference_time_utc"] = reference.clone();
    }
    if let Some(cams) = report.get("cams_update_cycle").filter(|v| !v.is_null()) {
        payload["_cams_refresh_cycle"] = cams.clone();
    }
}

/// desktop_rpc.py `client_report` — hsd row mirroring for satellite reports.
fn client_report(report: &Value) -> Value {
    let mut result = report.clone();
    if let Some(count) = result.get("satellite_frames").and_then(Value::as_i64) {
        if let Some(datasets) = result.get_mut("datasets").and_then(Value::as_array_mut) {
            datasets.push(json!({
                "source": "hsd", "ready": count >= 2, "verified": true,
                "is_latest": true, "missing_count": (2 - count).max(0),
            }));
        }
    }
    result
}

/// desktop_rpc.py prepare refresh predicate.
fn needs_refresh(report: &Value) -> bool {
    report
        .get("datasets")
        .and_then(Value::as_array)
        .is_some_and(|rows| {
            rows.iter().any(|row| {
                row.get("source").and_then(Value::as_str) != Some("cams")
                    && row
                        .get("verified")
                        .and_then(Value::as_bool)
                        .unwrap_or(false)
                    && row
                        .get("latest_available_cycle_utc")
                        .is_some_and(|value| !value.is_null())
                    && {
                        let latest = row
                            .get("latest_available_cycle")
                            .and_then(Value::as_str)
                            .unwrap_or_default();
                        let requested = row
                            .get("request_cycle")
                            .and_then(Value::as_str)
                            .unwrap_or_default();
                        !requested.split(" / ").any(|part| part == latest)
                    }
            })
        })
}

/// Native mirror of firesky/desktop_credentials.py `cams_status` (path probe
/// only, matching engine ads.rs precedence; never returns the key).
fn cams_status() -> Value {
    let env_key = std::env::var_os("CDSAPI_KEY")
        .map(|value| !value.is_empty())
        .unwrap_or(false);
    let path = std::env::var_os("CDSAPI_RC")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME")
                .map(|home| PathBuf::from(home).join(".cdsapirc"))
        });
    let configured = env_key
        || path.as_ref().is_some_and(|path| {
            std::fs::metadata(path)
                .map(|metadata| metadata.is_file() && metadata.len() <= 64 * 1024)
                .unwrap_or(false)
                && std::fs::read_to_string(path)
                    .map(|text| {
                        text.lines().any(|line| {
                            line.trim()
                                .split_once(':')
                                .is_some_and(|(name, value)| {
                                    name.trim() == "key" && !value.trim().is_empty()
                                })
                        })
                    })
                    .unwrap_or(false)
        });
    let path = path.unwrap_or_else(|| PathBuf::from(".cdsapirc"));
    json!({
        "configured": configured,
        "state": if configured { "configured" } else { "missing" },
        "path": path.to_string_lossy(),
    })
}

/// desktop_rpc.py `execute` progress redaction — the safe event carries only
/// percent/stage/source (plus a filtered probe for network_probe).
fn redact(event: &Value, method: &str) -> Value {
    let source = event
        .get("model_source")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let message = event
        .get("message")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_uppercase();
    let dataset = ["CAMS", "GFS", "EC", "HSD"]
        .into_iter()
        .find(|name| message.contains(name))
        .map(str::to_string)
        .unwrap_or_else(|| source.to_uppercase());
    let mut safe = json!({
        "percent": event.get("percent").cloned().unwrap_or(json!(0)),
        "stage": event.get("stage").cloned().unwrap_or(json!("")),
        "source": dataset,
    });
    if method == "network_probe" {
        if let Some(probe) = event.get("probe").filter(|probe| probe.is_object()) {
            let mut filtered = Map::new();
            for key in [
                "source", "title", "host", "state", "detail", "bytes_received", "elapsed_ms",
            ] {
                if let Some(value) = probe.get(key) {
                    filtered.insert(key.to_string(), value.clone());
                }
            }
            safe["probe"] = Value::Object(filtered);
        }
    }
    safe
}

/// Bounded line framing, ported from engine rpc.rs `read_line`: oversized
/// lines are drained without buffering so the next request survives.
fn read_line(input: &mut impl BufRead, line: &mut Vec<u8>) -> std::io::Result<Line> {
    line.clear();
    let mut oversized = false;
    let mut seen = false;
    loop {
        let chunk = input.fill_buf()?;
        if chunk.is_empty() {
            break;
        }
        seen = true;
        let newline = chunk.iter().position(|&byte| byte == b'\n');
        let take = newline.map_or(chunk.len(), |pos| pos + 1);
        if !oversized {
            if line.len() + take > MAX_LINE_BYTES {
                oversized = true;
                line.clear();
            } else {
                line.extend_from_slice(&chunk[..take]);
            }
        }
        input.consume(take);
        if newline.is_some() {
            break;
        }
    }
    Ok(if !seen {
        Line::End
    } else if oversized {
        Line::Oversized
    } else {
        Line::Data
    })
}

enum Line {
    End,
    Oversized,
    Data,
}

/// Running service handles; `stop` mirrors desktop EOF semantics.
pub struct Started {
    pub writer: crate::pipe::PipeWriter,
    queue: Arc<Queue>,
    reader: Option<JoinHandle<()>>,
    workers: Vec<JoinHandle<()>>,
    active: Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>,
    /// Orchestration core; exposed for the acceptance-matrix tests.
    #[cfg_attr(not(test), allow(dead_code))]
    inner: Arc<Inner>,
}

impl Started {
    /// Close the input, cancel in-flight requests, wait for workers to finish.
    pub fn stop(&mut self) {
        self.writer.close();
        if let Some(reader) = self.reader.take() {
            if let Err(error) = reader.join() {
                eprintln!("engine-mobile: reader thread join failed: {error:?}");
            }
        }
        self.queue.close();
        for worker in self.workers.drain(..) {
            if let Err(error) = worker.join() {
                eprintln!("engine-mobile: worker join failed: {error:?}");
            }
        }
    }

    /// Cooperative cancel by request id: 0 found, 1 unknown.
    pub fn cancel(&self, request_id: &str) -> i32 {
        let active = self.active.lock().unwrap();
        match active.get(request_id) {
            Some(flag) => {
                flag.store(true, Ordering::Release);
                0
            }
            None => 1,
        }
    }
}

impl Drop for Started {
    fn drop(&mut self) {
        self.writer.close();
        self.queue.close();
    }
}

/// Start the in-process service (reader thread + 4 workers, ≤16 active).
pub fn start(config: MobileConfig, backend: Arc<dyn DesktopBackend>, sink: FrameSink) -> Started {
    let (writer, reader) = crate::pipe::channel();
    let queue = Arc::new(Queue {
        jobs: Mutex::new(VecDeque::new()),
        signal: Condvar::new(),
        open: AtomicBool::new(true),
    });
    let inner = Arc::new(Inner {
        config,
        backend,
        sink,
        sessions: Mutex::new(HashMap::new()),
        compute_lock: Mutex::new(()),
        owner: new_owner(),
        active: Arc::new(Mutex::new(HashMap::new())),
    });
    let reader_thread = {
        let inner = inner.clone();
        let queue = queue.clone();
        std::thread::Builder::new()
            .name("fsmobile-wire".into())
            .spawn(move || run_wire(reader, inner, queue))
            .expect("spawn engine-mobile wire thread")
    };
    let workers = (0..WORKER_THREADS)
        .map(|index| {
            let inner = inner.clone();
            let queue = queue.clone();
            std::thread::Builder::new()
                .name(format!("fsmobile-worker-{index}"))
                .spawn(move || loop {
                    let Some((request, cancel)) = queue.pop() else {
                        break;
                    };
                    execute(&inner, request, cancel);
                })
                .expect("spawn engine-mobile worker thread")
        })
        .collect();
    Started {
        writer,
        queue,
        reader: Some(reader_thread),
        workers,
        active: inner.active.clone(),
        inner,
    }
}

/// Wire loop (desktop_rpc.py `serve` read half): validate, dedup, cancel,
/// submit; EOF cancels everything in flight.
fn run_wire(reader: PipeReader, inner: Arc<Inner>, queue: Arc<Queue>) {
    let mut input = reader;
    let mut line = Vec::new();
    loop {
        match read_line(&mut input, &mut line) {
            Ok(Line::End) => break,
            Ok(Line::Oversized) => {
                (inner.sink)(
                    &json!({"id": Value::Null, "type": "error", "error": {
                        "code": "invalid_request", "message": "请求过大。",
                    }})
                    .to_string(),
                );
            }
            Ok(Line::Data) => admit(&inner, &line, &queue),
            Err(error) => {
                eprintln!("engine-mobile: input stream error: {error}");
                break;
            }
        }
    }
    for (_, flag) in inner.active.lock().unwrap().iter() {
        flag.store(true, Ordering::Release);
    }
}

/// desktop_rpc.py `serve` admission half (runs on the wire thread).
fn admit(inner: &Arc<Inner>, line: &[u8], queue: &Queue) {
    let respond = |id: Value, code: &str, message: &str| {
        (inner.sink)(
            &json!({"id": id, "type": "error", "error": {"code": code, "message": message}})
                .to_string(),
        )
    };
    let request = match serde_json::from_slice::<Value>(line) {
        Ok(value) => value,
        Err(error) => return respond(Value::Null, "invalid_request", &error.to_string()),
    };
    if !request.is_object() {
        return respond(Value::Null, "invalid_request", "无效请求。");
    }
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    let id_text = id.as_str().unwrap_or_default().to_string();
    if id_text.is_empty() || id_text.len() > MAX_ID_BYTES {
        return respond(Value::Null, "invalid_request", "无效请求编号。");
    }
    if !request.get("params").map(Value::is_object).unwrap_or(true) {
        return respond(id, "invalid_request", "无效参数。");
    }
    let mut active = inner.active.lock().unwrap();
    if active.contains_key(&id_text) {
        return respond(id, "invalid_request", "重复请求编号。");
    }
    if request.get("method").and_then(Value::as_str) == Some("cancel") {
        let target = request
            .get("params")
            .and_then(|params| params.get("request_id"))
            .and_then(Value::as_str)
            .and_then(|request_id| active.get(request_id));
        if let Some(flag) = target {
            flag.store(true, Ordering::Release);
        }
        (inner.sink)(
            &json!({"id": id, "type": "result", "result": {"requested": target.is_some()}})
                .to_string(),
        );
        return;
    }
    if active.len() >= MAX_ACTIVE_REQUESTS {
        return respond(id, "invalid_request", "任务过多，请稍候。");
    }
    let cancel = Arc::new(AtomicBool::new(false));
    active.insert(id_text, cancel.clone());
    queue.push((request, cancel));
}

/// desktop_rpc.py `execute` — one request on a worker thread.
fn execute(inner: &Arc<Inner>, request: Value, cancel: Arc<AtomicBool>) {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    let method = request
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let params = request.get("params").cloned().unwrap_or_else(|| json!({}));
    let inner_scope = inner.clone();
    let id_scope = id.clone();
    let cancel_scope = cancel.clone();
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(move || {
        let progress = |event: Value| -> Result<(), Cancelled> {
            if cancel_scope.load(Ordering::Acquire) {
                return Err(Cancelled);
            }
            let safe = redact(&event, &method);
            (inner_scope.sink)(
                &json!({"id": id_scope, "type": "progress", "event": safe}).to_string(),
            );
            Ok(())
        };
        let result = inner_scope.dispatch(&method, &params, &progress, &cancel_scope)?;
        if cancel_scope.load(Ordering::Acquire) {
            return Err(OpError::Cancelled);
        }
        Ok::<Value, OpError>(result)
    }));
    let outcome = match outcome {
        Ok(result) => result,
        Err(panic) => Err(OpError::Message(panic_message(&panic))),
    };
    match outcome {
        Ok(result) => {
            (inner.sink)(
                &json!({"id": id, "type": "result", "result": result}).to_string(),
            );
        }
        Err(OpError::Cancelled) => {
            (inner.sink)(
                &json!({"id": id, "type": "error", "error": {
                    "code": "cancelled", "message": "任务已取消",
                }})
                .to_string(),
            );
        }
        Err(OpError::Message(message)) => {
            let code = if cancel.load(Ordering::Acquire) {
                "cancelled"
            } else {
                "operation_failed"
            };
            (inner.sink)(
                &json!({"id": id, "type": "error", "error": {"code": code, "message": message}})
                    .to_string(),
            );
        }
    }
    if let Some(id) = id.as_str() {
        inner.active.lock().unwrap().remove(id);
    }
}

fn panic_message(panic: &Box<dyn std::any::Any + Send>) -> String {
    if let Some(text) = panic.downcast_ref::<&str>() {
        return format!("internal panic: {text}");
    }
    if let Some(text) = panic.downcast_ref::<String>() {
        return format!("internal panic: {text}");
    }
    "internal panic".to_string()
}

#[cfg(test)]
#[path = "service_tests.rs"]
mod service_tests;
