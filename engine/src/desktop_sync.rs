//! Background data synchronization helper for the native desktop workspace
//! (CAMS/GFS/EC latest cycles; design `docs/DATA_SYNC_HELPER_DESIGN.zh-CN.md`).
//!
//! The engine owns the deterministic parts: the versioned settings contract
//! (`rust-data-sync-settings-v1`), the per-source sync state contract
//! (`rust-data-sync-state-v1`), the pure due decision, and one synchronous
//! `data_sync_run` round that reuses the existing providers (CAMS continent
//! envelope + `all_leads`, per-lead GFS resolve, EC per-hour download). The
//! UI owns only the 60-second tick and calls `data_sync_due`/`data_sync_run`.
//!
//! Hard boundaries inherited from the design: no Python on any path, default
//! disabled settings with no invented values, per-source fail-closed rounds,
//! credentials never appear in state, results or errors, and the compute lock
//! is only probed (busy yield), never held during an ADS queue.

use crate::{
    cams_data, continents, desktop_context, desktop_publications, ecmwf_data, gfs_data,
    retention_settings, time_contract,
};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::{
    collections::BTreeSet,
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    time::{Instant, SystemTime},
};

const MAX_BYTES: u64 = 16 * 1024;
const SETTINGS_CONTRACT: &str = "rust-data-sync-settings-v1";
const STATE_CONTRACT: &str = "rust-data-sync-state-v1";
const SOURCES: [&str; 3] = ["cams", "gfs", "ec"];
const MIN_INTERVAL_MINUTES: u32 = 5;
const MAX_INTERVAL_MINUTES: u32 = 1440;
const MIN_LEAD_HORIZON_HOURS: u32 = 3;
const MAX_LEAD_HORIZON_HOURS: u32 = 240;
/// Circuit-breaker threshold adopted from design section 16 decision 1.
const SUSPENSION_THRESHOLD: u32 = 3;
/// Publication probes reuse the bounded `desktop_publications` timeout
/// contract (upper limit 60 s; results carry its 300 s cache).
const PROBE_TIMEOUT_SECONDS: f64 = 60.0;
/// Coverage-aware sync revision (F1). Bump when the synced footprint changes
/// (bbox/hours/schema); states recorded under an older revision no longer
/// count as `current` and the same cycle is re-evaluated against the cache.
const COVERAGE_VERSION: u32 = 2;

fn check(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("desktop operation cancelled".into())
    } else {
        Ok(())
    }
}

fn clip(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// User-facing source name for progress messages (F3 localization).
fn source_label(name: &str) -> &'static str {
    match name {
        "cams" => "CAMS",
        "gfs" => "GFS",
        _ => "EC",
    }
}

/// User-facing round status for progress messages (F3 localization).
fn sync_status_label(status: &str) -> &'static str {
    match status {
        "synced" => "已同步",
        "current" => "已是最新",
        "failed" => "失败",
        "cancelled" => "已取消",
        _ => "结束",
    }
}

// ---------------------------------------------------------------------------
// Settings contract (`rust-data-sync-settings-v1`)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct SourceToggles {
    pub cams: bool,
    pub gfs: bool,
    pub ec: bool,
}

impl SourceToggles {
    fn toggle(&self, name: &str) -> bool {
        match name {
            "cams" => self.cams,
            "gfs" => self.gfs,
            "ec" => self.ec,
            _ => false,
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub enabled: bool,
    pub sources: SourceToggles,
    pub interval_minutes: Option<u32>,
    pub anchor_region: Option<String>,
    pub lead_horizon_hours: Option<u32>,
    pub ecmwf_source: Option<String>,
}

fn settings_path(workspace: &Path) -> PathBuf {
    let configured = std::env::var_os("FIRESKY_DATA_SYNC_SETTINGS_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            crate::paths::resolve(workspace, "", ".firesky/settings/data_sync.json", "settings/data_sync.json")
        });
    if configured.is_absolute() {
        configured
    } else {
        workspace.join(configured)
    }
}

fn enabled_sources(settings: &Settings) -> Vec<&'static str> {
    SOURCES
        .iter()
        .copied()
        .filter(|name| settings.sources.toggle(name))
        .collect()
}

fn region_code(name: &str) -> bool {
    desktop_context::REGIONS
        .iter()
        .any(|(code, _, _)| *code == name)
}

fn validate(settings: &Settings) -> Result<(), String> {
    if let Some(value) = settings.interval_minutes {
        if !(MIN_INTERVAL_MINUTES..=MAX_INTERVAL_MINUTES).contains(&value) {
            return Err(format!(
                "interval_minutes must be within {MIN_INTERVAL_MINUTES}..{MAX_INTERVAL_MINUTES} minutes"
            ));
        }
    }
    if let Some(region) = &settings.anchor_region {
        if !region_code(region) {
            return Err("anchor_region must be one of the desktop regions".into());
        }
    }
    if let Some(hours) = settings.lead_horizon_hours {
        if hours < MIN_LEAD_HORIZON_HOURS || hours > MAX_LEAD_HORIZON_HOURS || hours % 3 != 0 {
            return Err(format!(
                "lead_horizon_hours must be a multiple of 3 within {MIN_LEAD_HORIZON_HOURS}..{MAX_LEAD_HORIZON_HOURS}"
            ));
        }
    }
    if let Some(source) = &settings.ecmwf_source {
        if !["aws", "google", "ecmwf"].contains(&source.as_str()) {
            return Err("ecmwf_source must be ecmwf, aws or google".into());
        }
    }
    if settings.enabled {
        if enabled_sources(settings).is_empty() {
            return Err("enabling data sync requires at least one source".into());
        }
        if settings.interval_minutes.is_none() {
            return Err("interval_minutes is required when data sync is enabled".into());
        }
        if settings.anchor_region.is_none() {
            return Err("anchor_region is required when data sync is enabled".into());
        }
        if (settings.sources.gfs || settings.sources.ec) && settings.lead_horizon_hours.is_none() {
            return Err("lead_horizon_hours is required when gfs or ec sync is enabled".into());
        }
        if settings.sources.ec && settings.ecmwf_source.is_none() {
            return Err("ecmwf_source is required when ec sync is enabled".into());
        }
    }
    Ok(())
}

fn regular_file(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() {
                return Err("data sync settings path cannot be a symlink".into());
            }
            Ok(metadata.is_file())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.to_string()),
    }
}

fn read_settings(path: &Path) -> Result<Settings, String> {
    if !regular_file(path)? {
        return Ok(Settings::default());
    }
    let metadata = fs::metadata(path).map_err(|e| e.to_string())?;
    if metadata.len() > MAX_BYTES {
        return Err("data sync settings file exceeds limit".into());
    }
    let value: Value = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
        .map_err(|_| "data sync settings file is invalid".to_string())?;
    if value["contract"] != SETTINGS_CONTRACT {
        return Err("data sync settings contract is invalid".into());
    }
    let settings: Settings = serde_json::from_value(value["settings"].clone())
        .map_err(|_| "data sync settings payload is invalid".to_string())?;
    validate(&settings)?;
    Ok(settings)
}

fn serialized(settings: &Settings) -> Result<Vec<u8>, String> {
    validate(settings)?;
    serde_json::to_vec_pretty(&json!({
        "contract": SETTINGS_CONTRACT,
        "settings": settings,
    }))
    .map_err(|e| e.to_string())
}

pub fn load(workspace: &Path) -> Result<Settings, String> {
    read_settings(&settings_path(workspace))
}

fn retention_configured(workspace: &Path) -> Result<bool, String> {
    Ok(retention_settings::load(workspace)? != retention_settings::RetentionSettings::default())
}

/// Merged settings + state + retention status (design 9/11.1: the macOS page
/// renders the per-source last_cycle/last_result rows and the unconfigured-
/// retention soft warning from this single response).
pub fn status(workspace: &Path) -> Result<Value, String> {
    let file = settings_path(workspace);
    let settings = read_settings(&file)?;
    let state = read_state(&state_path(workspace));
    Ok(json!({
        "contract": SETTINGS_CONTRACT,
        "settings": settings,
        "configured": settings != Settings::default(),
        "path": file,
        "state": {"contract": STATE_CONTRACT, "sources": state},
        "retention_configured": retention_configured(workspace)?,
        "runtime_python_used": false,
    }))
}

pub fn save(workspace: &Path, payload: Value) -> Result<Value, String> {
    let raw = payload.get("settings").cloned().unwrap_or(payload);
    let settings: Settings =
        serde_json::from_value(raw).map_err(|_| "invalid data sync settings".to_string())?;
    let bytes = serialized(&settings)?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err("data sync settings exceed limit".into());
    }
    let destination = settings_path(workspace);
    if fs::symlink_metadata(&destination).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("data sync settings path cannot be a symlink".into());
    }
    let parent = destination
        .parent()
        .ok_or("data sync settings path has no parent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".data-sync-settings-")
        .tempfile_in(parent)
        .map_err(|e| e.to_string())?;
    temporary
        .write_all(&bytes)
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|e| e.to_string())?;
    temporary
        .persist(&destination)
        .map_err(|e| e.error.to_string())?;
    // Re-saving settings is the documented manual unlock channel (design 8.3):
    // clear the failure breaker without touching last_cycle/last_result. The
    // read-modify-write runs under the same state lock the sync round uses for
    // its per-source commits, so it cannot be lost to a round-end merge.
    with_state_lock(|| {
        let state_file = state_path(workspace);
        let mut state = read_state(&state_file);
        for source in [&mut state.cams, &mut state.gfs, &mut state.ec] {
            source.consecutive_failures = 0;
            source.suspended = false;
        }
        persist_state(&state_file, &state)
    })?;
    status(workspace)
}

// ---------------------------------------------------------------------------
// State contract (`rust-data-sync-state-v1`)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct SourceState {
    pub last_cycle: Option<String>,
    pub last_attempt_utc: Option<String>,
    pub last_result: Option<Value>,
    pub consecutive_failures: u32,
    pub suspended: bool,
    /// Fingerprint of the synced footprint: `v{COVERAGE_VERSION}:{bbox}:{hours}`
    /// for GFS (anchor point-domain envelope + hourly horizon), the continent
    /// envelope for CAMS, and the published-step horizon for EC. `None` means
    /// the state predates coverage tracking and must be re-verified.
    pub coverage_fingerprint: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct State {
    pub cams: SourceState,
    pub gfs: SourceState,
    pub ec: SourceState,
}

impl State {
    fn source(&self, name: &str) -> Option<&SourceState> {
        match name {
            "cams" => Some(&self.cams),
            "gfs" => Some(&self.gfs),
            "ec" => Some(&self.ec),
            _ => None,
        }
    }

    fn source_mut(&mut self, name: &str) -> Option<&mut SourceState> {
        match name {
            "cams" => Some(&mut self.cams),
            "gfs" => Some(&mut self.gfs),
            "ec" => Some(&mut self.ec),
            _ => None,
        }
    }
}

fn state_path(workspace: &Path) -> PathBuf {
    crate::paths::resolve(workspace, "", ".firesky/state/data-sync.json", "state/data-sync.json")
}

/// The state file is advisory bookkeeping: a corrupted, oversized or
/// foreign-contract file is equivalent to "never synced" (design 4.2/13.1).
/// The next due comparison re-plans from the publication probe and the cache
/// locks deduplicate any repeated download, so no user data is at risk.
fn read_state(path: &Path) -> State {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return State::default();
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() || metadata.len() > MAX_BYTES {
        return State::default();
    }
    let Ok(bytes) = fs::read(path) else {
        return State::default();
    };
    let Ok(value) = serde_json::from_slice::<Value>(&bytes) else {
        return State::default();
    };
    if value["contract"] != STATE_CONTRACT {
        return State::default();
    }
    serde_json::from_value(value["sources"].clone()).unwrap_or_default()
}

fn persist_state(path: &Path, state: &State) -> Result<(), String> {
    let bytes = serde_json::to_vec_pretty(&json!({
        "contract": STATE_CONTRACT,
        "sources": state,
    }))
    .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err("data sync state exceeds limit".into());
    }
    let parent = path.parent().ok_or("data sync state path has no parent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".data-sync-state-")
        .tempfile_in(parent)
        .map_err(|e| e.to_string())?;
    temporary
        .write_all(&bytes)
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|e| e.to_string())?;
    temporary.persist(path).map_err(|e| e.error.to_string())?;
    Ok(())
}

/// In-process read-modify-write serialization for the state file. Round
/// outcomes are committed as short per-source merges (never as a whole-file
/// overwrite of a long-held snapshot), so a settings save that clears the
/// breaker mid-round (design 8.3 unlock channel) cannot be rolled back when
/// the round commits. Cross-process races remain bounded by the same
/// last-writer-wins semantics as before; the cache locks stay the dedup core.
static STATE_LOCK: Mutex<()> = Mutex::new(());

fn with_state_lock<T>(operation: impl FnOnce() -> T) -> T {
    let _guard = STATE_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    operation()
}

/// Per-source attempt bookkeeping, executed when that source actually starts:
/// a manual round on a suspended source starts a fresh failure budget, and a
/// source that is never reached (cancelled earlier in the round) consumes
/// neither an attempt nor the unlock.
fn commit_source_attempt(
    state_file: &Path,
    name: &str,
    now: DateTime<Utc>,
) -> Result<SourceState, String> {
    with_state_lock(|| {
        let mut latest = read_state(state_file);
        let snapshot = latest.source(name).cloned().unwrap_or_default();
        if let Some(source) = latest.source_mut(name) {
            if source.suspended {
                source.suspended = false;
                source.consecutive_failures = 0;
            }
            source.last_attempt_utc = Some(time_contract::iso(now));
        }
        persist_state(state_file, &latest).map(|_| snapshot)
    })
}

/// A failed publication probe means nothing was attempted downstream: record
/// an honest per-source failed row without consuming the interval gate (no
/// attempt) or the breaker budget (no failure counter), keeping the state
/// file consistent with what actually happened.
fn commit_probe_failure(
    state_file: &Path,
    requested: &[&str],
    error: &str,
    task_id: Option<&str>,
) -> Result<(), String> {
    with_state_lock(|| {
        let mut latest = read_state(state_file);
        let row = json!({
            "status": "failed",
            "error": clip(error, 800),
            "retention": Value::Null,
            "task_id": task_id.map(|value| json!(value)).unwrap_or(Value::Null),
        });
        for name in requested {
            if let Some(source) = latest.source_mut(name) {
                // v1.9.6：标记这是探测失败（未真正尝试下载、未消耗间隔），
                // 供 due_for_source 的欠账重试区分——探测失败不触发重试，
                // 避免上游不可达时 60s tick 空转放大。
                let mut summary = result_summary("failed", &row, task_id);
                summary["probe_only"] = json!(true);
                source.last_result = Some(summary);
            }
        }
        persist_state(state_file, &latest)
    })
}

/// v1.9.7 手动优先让路：手动 prepare/forecast 开始时放置哨兵文件，
/// 同步轮在源边界与每个文件完成后检查——发现即停止本轮（记录为
/// cancelled，不计熔断），把带宽让给手动操作。跨进程生效（helper 与
/// GUI 是独立进程，靠文件而不是内存互斥量通信）。
pub(crate) fn yield_flag_path(workspace: &Path) -> PathBuf {
    state_path(workspace).with_file_name("sync-yield")
}

pub(crate) fn request_manual_yield(workspace: &Path) {
    let path = yield_flag_path(workspace);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(&path, std::process::id().to_string());
}

/// 检查并消费让路哨兵：存在则删除并返回 true（由同步轮执行）。
fn take_manual_yield(workspace: &Path) -> bool {
    let path = yield_flag_path(workspace);
    match std::fs::remove_file(&path) {
        Ok(()) => true,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
        Err(_) => false,
    }
}

/// Drop 时停止重放并移除哨兵（手动操作结束/失败都要清理，避免下一轮
/// 同步被幽灵哨兵误停；崩溃场景由轮次的 take 自愈——哨兵被消费一次后
/// 即消失）。
pub(crate) struct ManualYieldGuard {
    path: PathBuf,
    stop: Arc<AtomicBool>,
    watcher: Option<std::thread::JoinHandle<()>>,
}

impl Drop for ManualYieldGuard {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(watcher) = self.watcher.take() {
            let _ = watcher.join();
        }
        let _ = std::fs::remove_file(&self.path);
    }
}

pub(crate) fn manual_yield_guard(workspace: &Path) -> ManualYieldGuard {
    request_manual_yield(workspace);
    let path = yield_flag_path(workspace);
    let stop = Arc::new(AtomicBool::new(false));
    // 重放线程：轮次消费哨兵后 1s 内重新放置，手动操作全程保持让路信号。
    let watcher_stop = stop.clone();
    let watcher_path = path.clone();
    let watcher = std::thread::spawn(move || {
        while !watcher_stop.load(Ordering::Acquire) {
            std::thread::sleep(std::time::Duration::from_millis(1000));
            if watcher_stop.load(Ordering::Acquire) {
                break;
            }
            let _ = std::fs::write(&watcher_path, std::process::id().to_string());
        }
    });
    ManualYieldGuard {
        path,
        stop,
        watcher: Some(watcher),
    }
}

struct RoundOutcome {
    name: String,
    status: &'static str,
    last_cycle: Option<String>,
    coverage_fingerprint: Option<String>,
    source: Value,
}

/// Merge the round's per-source outcomes into the latest on-disk state. A
/// failed counter increments from the current disk value, so a mid-round
/// settings-save unlock (0) followed by a failure lands on 1 instead of
/// resurrecting the pre-round count; `last_attempt_utc` rows written while
/// each source started are preserved untouched.
fn commit_round_outcomes(
    state_file: &Path,
    outcomes: &[RoundOutcome],
    task_id: Option<&str>,
) -> Result<(), String> {
    with_state_lock(|| {
        let mut latest = read_state(state_file);
        for outcome in outcomes {
            record_source_outcome(
                latest.source_mut(&outcome.name),
                outcome.status,
                outcome.last_cycle.clone(),
                outcome.coverage_fingerprint.clone(),
                &outcome.source,
                task_id,
            );
        }
        persist_state(state_file, &latest)
    })
}

/// Apply one round outcome to the per-source state (design 8.2/8.3): success
/// clears the breaker, a failure increments the counter and suspends the
/// source at the threshold, a cancelled round is user action — never counted
/// as an upstream failure — and `last_cycle` only ever advances on a real sync.
fn record_source_outcome(
    target: Option<&mut SourceState>,
    status: &str,
    last_cycle: Option<String>,
    coverage_fingerprint: Option<String>,
    source: &Value,
    task_id: Option<&str>,
) {
    let Some(target) = target else {
        return;
    };
    target.last_result = Some(result_summary(status, source, task_id));
    match status {
        "synced" => {
            target.last_cycle = last_cycle;
            target.coverage_fingerprint = coverage_fingerprint;
            target.consecutive_failures = 0;
            target.suspended = false;
        }
        "current" => {
            // A coverage-verified `current` re-stamps the footprint so an old
            // untracked state becomes trusted without re-downloading.
            if coverage_fingerprint.is_some() {
                target.coverage_fingerprint = coverage_fingerprint;
            }
            target.consecutive_failures = 0;
            target.suspended = false;
        }
        "failed" => {
            target.consecutive_failures = target.consecutive_failures.saturating_add(1);
            target.suspended = target.consecutive_failures >= SUSPENSION_THRESHOLD;
        }
        _ => {}
    }
}

fn result_summary(status: &str, source: &Value, task_id: Option<&str>) -> Value {
    let files = source["files"].as_array().map_or(0, |rows| rows.len());
    let mut errors = Vec::new();
    if let Some(text) = source["error"].as_str() {
        errors.push(clip(text, 400));
    }
    for key in ["group_errors", "missing_valid_times"] {
        if let Some(rows) = source[key].as_array() {
            for row in rows {
                if let Some(text) = row.as_str() {
                    errors.push(clip(text, 400));
                }
            }
        }
    }
    let error_count = errors.len();
    errors.truncate(4);
    // v1.9.7：缺失周期单独成字段（欠账可视化），最多展示 8 个。
    let missing_cycles: Vec<String> = source["missing_valid_times"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .filter_map(|row| row.as_str())
                .map(|text| clip(text, 40))
                .take(8)
                .collect()
        })
        .unwrap_or_default();
    json!({
        "status": status,
        "files": files,
        "errors": errors,
        "error_count": error_count,
        "missing_cycles": missing_cycles,
        "elapsed_ms": source["elapsed_ms"],
        "task_id": task_id.map(|value| json!(value)).unwrap_or(Value::Null),
        "finished_at_utc": time_contract::iso(DateTime::<Utc>::from(SystemTime::now())),
    })
}

/// Retention cleanup runs only after a round in which every requested source
/// succeeded (design 6.4/8.2/13.1: any failed or cancelled source defers the
/// cleanup to the next fully successful round).
fn cleanup_allowed(statuses: &[&str]) -> bool {
    !statuses.is_empty()
        && statuses
            .iter()
            .all(|status| *status == "synced" || *status == "current")
}

// ---------------------------------------------------------------------------
// Due determination (pure decision + probe-backed method)
// ---------------------------------------------------------------------------

struct Publication {
    cycle: Option<DateTime<Utc>>,
    verified: bool,
    label: String,
}

fn publication_from(value: &Value) -> Publication {
    Publication {
        cycle: value["cycle"]
            .as_str()
            .and_then(|value| time_contract::parse_utc(value).ok()),
        verified: value["verified"] == true,
        label: value["label"].as_str().unwrap_or("unverified").to_owned(),
    }
}

/// Ordered per-source decision (design 5.2). The publication result is
/// injected, keeping the function deterministic and unit-testable; heuristic
/// (`verified=false`) cycles never trigger a download.
fn due_for_source(
    state: &SourceState,
    interval_minutes: u32,
    publication: Option<&Publication>,
    now: DateTime<Utc>,
    coverage_matches: bool,
) -> (bool, String) {
    if state.suspended {
        return (false, "suspended_failures".into());
    }
    if let Some(attempt) = state
        .last_attempt_utc
        .as_deref()
        .and_then(|value| time_contract::parse_utc(value).ok())
    {
        if now - attempt < Duration::minutes(i64::from(interval_minutes)) {
            return (false, "interval_not_elapsed".into());
        }
    }
    let Some(publication) = publication.filter(|p| p.verified && p.cycle.is_some()) else {
        // v1.9.6 欠账重试：上一轮真实下载失败（非探测失败）且间隔已过时，
        // 即使发布探测暂未核验也再次尝试——run() 会重新探测，ADS 恢复可达
        // （如代理就绪）后自动补齐失败周期，无需手动干预。探测失败（网络
        // 层，未消耗间隔）不触发重试，避免上游不可达时空转放大。
        let retryable = state.last_result.as_ref().is_some_and(|result| {
            result["status"].as_str() == Some("failed") && result["probe_only"] != true
        });
        return if retryable {
            (true, "retry_after_failure".into())
        } else {
            (false, "publication_unverified".into())
        };
    };
    if let Some(last) = state
        .last_cycle
        .as_deref()
        .and_then(|value| time_contract::parse_utc(value).ok())
    {
        if publication.cycle.is_some_and(|cycle| cycle <= last) {
            // Coverage awareness (F1): a `current` cycle only counts when its
            // recorded footprint matches the active settings. An untracked or
            // stale footprint (old 3-hour anchor sync, changed horizon/anchor)
            // re-opens the same cycle so run() can verify or backfill coverage.
            if coverage_matches {
                return (false, "cycle_current".into());
            }
            return (true, format!("coverage_stale:{}", publication.label));
        }
    }
    (true, format!("new_cycle:{}", publication.label))
}

fn publication_name(source: &str) -> &'static str {
    match source {
        "cams" => "cams",
        "gfs" => "gfs",
        _ => "ecmwf",
    }
}

fn probe_publications(
    sources: &[&str],
    now_utc: Option<&str>,
    force_refresh: bool,
) -> Result<Map<String, Value>, String> {
    let params: desktop_publications::Params = serde_json::from_value(json!({
        "sources": sources.iter().map(|name| publication_name(name)).collect::<Vec<_>>(),
        "now_utc": now_utc,
        "timeout_seconds": PROBE_TIMEOUT_SECONDS,
        "force_refresh": force_refresh,
    }))
    .map_err(|e| e.to_string())?;
    let value = desktop_publications::execute(params)?;
    Ok(value["publications"]
        .as_object()
        .cloned()
        .unwrap_or_default())
}

/// `data_sync_due`: no side effects beyond the shared publication-probe cache.
/// A busy engine (manual prepare/forecast or a running sync round) reports
/// `busy` without probing; disabled sources report `disabled`.
pub fn due(workspace: &Path, busy: bool, now_utc: Option<&str>) -> Result<Value, String> {
    let settings = load(workspace)?;
    let state = read_state(&state_path(workspace));
    // The master switch gates the whole method (design 5.2: only sources that
    // are enabled AND toggled are judged), so a disabled contract never probes
    // the network nor reports due sources that run() would refuse to execute.
    let names = if settings.enabled {
        enabled_sources(&settings)
    } else {
        Vec::new()
    };
    let publications = if names.is_empty() || busy {
        Map::new()
    } else {
        // Enabled settings always carry an interval (validated on save/load).
        settings
            .interval_minutes
            .ok_or("data sync interval is not configured")?;
        probe_publications(&names, now_utc, false)?
    };
    let now = match now_utc {
        Some(value) => time_contract::parse_utc(value)?,
        None => DateTime::<Utc>::from(SystemTime::now()),
    };
    let mut sources = Map::new();
    let mut due_sources = Vec::new();
    for name in SOURCES {
        let state_fields = |source: &SourceState| {
            json!({
                "suspended": source.suspended,
                "consecutive_failures": source.consecutive_failures,
                "last_cycle": source.last_cycle,
                "last_attempt_utc": source.last_attempt_utc,
            })
        };
        let Some(source_state) = state.source(name) else {
            continue;
        };
        if !names.contains(&name) {
            let mut row = json!({"due": false, "reason": "disabled", "publication": Value::Null});
            if let Some(fields) = row.as_object_mut() {
                if let Some(state_json) = state_fields(source_state).as_object() {
                    for (key, value) in state_json {
                        fields.insert(key.clone(), value.clone());
                    }
                }
            }
            sources.insert(name.to_owned(), row);
            continue;
        }
        if busy {
            let mut row = json!({"due": false, "reason": "busy", "publication": Value::Null});
            if let Some(fields) = row.as_object_mut() {
                if let Some(state_json) = state_fields(source_state).as_object() {
                    for (key, value) in state_json {
                        fields.insert(key.clone(), value.clone());
                    }
                }
            }
            sources.insert(name.to_owned(), row);
            continue;
        }
        let publication = publications
            .get(publication_name(name))
            .map(publication_from);
        let interval = settings
            .interval_minutes
            .ok_or("data sync interval is not configured")?;
        let publication_json = publication.as_ref().map(|p| {
            json!({
                "cycle": p.cycle.map(|cycle| json!(time_contract::iso(cycle))).unwrap_or(Value::Null),
                "verified": p.verified,
                "label": p.label,
            })
        });
        let coverage_matches = expected_fingerprint(name, &settings)
            .ok()
            .is_some_and(|expected| {
                source_state.coverage_fingerprint.as_deref() == Some(expected.as_str())
            });
        let (is_due, reason) = due_for_source(
            source_state,
            interval,
            publication.as_ref(),
            now,
            coverage_matches,
        );
        if is_due {
            due_sources.push(name);
        }
        let mut row = json!({"due": is_due, "reason": reason, "publication": publication_json});
        if let Some(fields) = row.as_object_mut() {
            if let Some(state_json) = state_fields(source_state).as_object() {
                for (key, value) in state_json {
                    fields.insert(key.clone(), value.clone());
                }
            }
        }
        sources.insert(name.to_owned(), row);
    }
    Ok(json!({
        "contract": "rust-data-sync-due-v1",
        "runtime_python_used": false,
        "enabled": settings.enabled,
        "due_for_any": !due_sources.is_empty(),
        "due_sources": due_sources,
        "sources": sources,
    }))
}

// ---------------------------------------------------------------------------
// Single-round execution (`data_sync_run`)
// ---------------------------------------------------------------------------

pub struct RunContext<'a> {
    pub workspace: &'a Path,
    pub cams_cache_dir: PathBuf,
    pub gfs_cache_dir: PathBuf,
    pub ecmwf_cache_dir: PathBuf,
}

fn requested_sources(params: &Value) -> Result<Vec<&'static str>, String> {
    let rows = params["sources"]
        .as_array()
        .filter(|rows| !rows.is_empty())
        .ok_or("data sync requires an explicit nonempty sources array")?;
    let mut requested = BTreeSet::new();
    for row in rows {
        let name = row
            .as_str()
            .ok_or("data sync sources must be source names")?;
        if !SOURCES.contains(&name) {
            return Err(format!("unknown data sync source: {name}"));
        }
        requested.insert(name);
    }
    Ok(SOURCES
        .iter()
        .copied()
        .filter(|name| requested.contains(name))
        .collect())
}

fn region_bounds(code: &str) -> Result<[f64; 4], String> {
    desktop_context::REGIONS
        .iter()
        .find(|(name, _, _)| *name == code)
        .map(|(_, _, bounds)| *bounds)
        .ok_or_else(|| format!("unknown data sync anchor region: {code}"))
}

/// REGIONS store `[west, east, north, south]` while `continents::envelope_for`
/// and the CAMS area contract use `[north, west, south, east]`; the reorder is
/// explicit and unit-tested (design 6.1 step 1).
fn cams_area_from_region(bounds: [f64; 4]) -> Result<[f64; 4], String> {
    let [west, east, north, south] = bounds;
    continents::envelope_for([north, west, south, east])
}

/// Pin the continent-cycle group to the probed run with three exact targets
/// (run +12/24/36 h): they are 3-hour aligned, stay inside the bounded target
/// limit, and `Availability.choose` converges all three on the same newest run
/// (design 6.1 step 3; convergence itself is acceptance-tested against ADS).
fn cams_target_utcs(run: DateTime<Utc>) -> Result<Vec<String>, String> {
    [12, 24, 36]
        .iter()
        .map(|hours| {
            time_contract::checked(
                run.checked_add_signed(Duration::hours(*hours))
                    .ok_or("CAMS sync target overflow")?,
            )
        })
        .collect::<Result<Vec<_>, String>>()
        .map(|targets| {
            targets
                .iter()
                .map(|target| time_contract::iso(*target))
                .collect()
        })
}

/// The configured horizon applied on the EC open-data publication grid
/// (`ecmwf::published_step`): 3-hour steps through 144 h, 6-hour steps beyond.
/// Off-grid hours would fail the whole download, so the plan requests exactly
/// the published hours within the configured horizon; the configured value
/// stays visible in the round result.
fn ec_forecast_hours(horizon: u32) -> Vec<i64> {
    let mut hours = Vec::new();
    let mut hour = 0_u32;
    while hour <= horizon {
        if crate::ecmwf::published_step(i64::from(hour)) {
            hours.push(i64::from(hour));
        }
        hour += 3;
    }
    hours
}

/// Canonical GFS sync footprint for an anchor: the point-domain envelope at
/// the default compute radius, so any point forecast inside the anchor can
/// reuse the synced cache (design 7.4 coverage reuse).
fn gfs_sync_bbox(anchor: &str) -> Result<[f64; 4], String> {
    gfs_data::canonical_bbox(
        desktop_context::gfs_point_domain_envelope(
            region_bounds(anchor)?,
            desktop_context::DEFAULT_RADIUS_KM,
        ),
        "0p25",
    )
}

/// The footprint a successful round with these settings would produce. Used
/// both to stamp state after a sync and to detect stale `current` states.
fn expected_fingerprint(name: &str, settings: &Settings) -> Result<String, String> {
    let anchor = settings
        .anchor_region
        .as_deref()
        .ok_or("data sync anchor region is not configured")?;
    match name {
        "gfs" => {
            let horizon = settings
                .lead_horizon_hours
                .ok_or("lead_horizon_hours is not configured")?;
            Ok(format!(
                "v{COVERAGE_VERSION}:gfs:{:?}:0-{horizon}",
                gfs_sync_bbox(anchor)?
            ))
        }
        "cams" => Ok(format!(
            "v{COVERAGE_VERSION}:cams:{:?}",
            cams_area_from_region(region_bounds(anchor)?)?
        )),
        _ => {
            let horizon = settings
                .lead_horizon_hours
                .ok_or("lead_horizon_hours is not configured")?;
            let source = settings
                .ecmwf_source
                .as_deref()
                .ok_or("ecmwf_source is not configured")?;
            Ok(format!("v{COVERAGE_VERSION}:ec:{source}:0-{horizon}"))
        }
    }
}

/// Cheap on-disk coverage probe for GFS: does the cache already cover every
/// hourly lead of the sync footprint for this cycle? Uses the same
/// index/schema/bbox-covering lookup as preflight, with downloads disabled.
fn gfs_cache_complete(
    context: &RunContext<'_>,
    anchor: &str,
    cycle: DateTime<Utc>,
    horizon: u32,
) -> Result<bool, String> {
    let bbox = gfs_sync_bbox(anchor)?;
    let client = gfs_data::GfsClient::new(gfs_data::Config {
        cache_dir: context.gfs_cache_dir.clone(),
        resolution: "0p25".into(),
        region: Some(anchor.to_owned()),
        timeout_seconds: 90.0,
        lock_timeout_seconds: 300.0,
        download_attempts: 4,
    })?;
    for hour in 0..=horizon {
        let url = gfs_data::build_url(cycle, hour, Some(bbox), "0p25", false)?;
        if client.find(cycle, hour, Some(bbox), Some(&url))?.is_none() {
            return Ok(false);
        }
    }
    Ok(true)
}

fn sync_cams(
    context: &RunContext<'_>,
    progress: &(dyn Fn(Value) -> Result<(), String> + Sync),
    anchor: &str,
    cycle: DateTime<Utc>,
    cancel: &AtomicBool,
) -> Result<Value, String> {
    check(cancel)?;
    let envelope = cams_area_from_region(region_bounds(anchor)?)?;
    let (_, area_key, _) = cams_data::area_identity(json!(envelope))?;
    let targets = cams_target_utcs(cycle)?;
    let result = cams_data::execute_cancellable(
        cams_data::Params {
            operation: "batch".into(),
            cache_dir: context.cams_cache_dir.clone(),
            area: json!(envelope),
            target_utcs: targets,
            target_utc: None,
            config: crate::ads::Config::default(),
            allow_download: true,
            require_exact_valid_time: true,
            refresh_before_cycle: Some(time_contract::iso(cycle)),
            all_leads: true,
            model_bbox: None,
        },
        cancel,
        Some(progress),
    )?;
    // Fail-closed: a partial continent cycle must not advance last_cycle.
    let group_errors = &result["group_errors"];
    let missing = &result["missing_valid_times"];
    if group_errors.as_array().is_some_and(|rows| !rows.is_empty())
        || missing.as_array().is_some_and(|rows| !rows.is_empty())
    {
        return Err(format!(
            "CAMS sync is incomplete: group_errors={group_errors}, missing_valid_times={missing}"
        ));
    }
    let mut value = result;
    value["envelope"] = json!(envelope);
    value["area_key"] = json!(area_key);
    Ok(value)
}

/// 逐文件下载的工作线程池大小。NOMADS 允许的连接数远高于此；GFS 缓存
/// 索引写有全局 flock、每目标文件各有 lock，并发安全（v1.9.6：串行
/// 121 文件 ≈ 22 min → 4 并发 ≈ 6 min）。
const SYNC_DOWNLOAD_WORKERS: usize = 4;

fn sync_gfs<G>(
    context: &RunContext<'_>,
    anchor: &str,
    cycle: DateTime<Utc>,
    horizon: u32,
    cancel: &AtomicBool,
    file_progress: &G,
) -> Result<Value, String>
where
    G: Fn(u64, u64, u64) -> Result<(), String> + Sync,
{
    let started = Instant::now();
    // Point forecasts request hourly leads over a domain enlarged by
    // source_radius (up to 2000 km). Prefetch the same hourly grid over an
    // envelope covering any point domain inside the anchor, so a later point
    // forecast reuses this cache instead of re-downloading (coverage reuse).
    let bbox = gfs_sync_bbox(anchor)?;
    let hours: Vec<u32> = (0..=horizon).collect();
    let files = crate::cancel_scope::with_shared_cancel(cancel, |shared| {
        let client = gfs_data::GfsClient::with_cancel(
            gfs_data::Config {
                cache_dir: context.gfs_cache_dir.clone(),
                resolution: "0p25".into(),
                region: Some(anchor.to_owned()),
                timeout_seconds: 90.0,
                lock_timeout_seconds: 300.0,
                download_attempts: 4,
            },
            shared,
        )?;
        let rows = download_pool(&hours, cancel, file_progress, |hour| {
            let row = client
                .resolve(cycle, *hour, Some(bbox), false, true)?
                .ok_or_else(|| format!("GFS f{hour:03} did not resolve to a cache file"))?;
            // v1.9.7：仅统计真实下载字节（缓存命中不计入吞吐）。
            let bytes = if row["status"].as_str() == Some("downloaded") {
                std::fs::metadata(row["path"].as_str().unwrap_or(""))
                    .map(|meta| meta.len())
                    .unwrap_or(0)
            } else {
                0
            };
            Ok((
                json!({
                    "forecast_hour": hour,
                    "path": row["path"],
                    "status": row["status"],
                    "bytes": bytes,
                }),
                bytes,
            ))
        })?;
        Ok(rows)
    })?;
    let cache_hits = files
        .iter()
        .filter(|row| row["status"].as_str().is_some_and(|s| s != "downloaded"))
        .count();
    let downloaded = files.len() - cache_hits;
    Ok(json!({
        "region": anchor,
        "bbox": bbox,
        "hours": hours,
        "files": files,
        "cache_hits": cache_hits,
        "downloaded": downloaded,
        "elapsed_ms": started.elapsed().as_secs_f64() * 1000.0,
    }))
}

/// 共享的逐文件下载线程池：光标分发 work 项，4 个 worker 并发调用
/// `fetch`；首错即停（其余 worker 快速退出）；每完成一项回调 file_progress
/// （done / total / 累计下载字节），回调失败置取消位（轮次状态转
/// cancelled，与源级 progress 错误语义一致）。返回按 work 项原始顺序
/// 排列的结果。
pub(crate) fn download_pool<T, F, G>(
    items: &[T],
    cancel: &AtomicBool,
    file_progress: &G,
    fetch: F,
) -> Result<Vec<Value>, String>
where
    T: Sync,
    F: Fn(&T) -> Result<(Value, u64), String> + Sync,
    G: Fn(u64, u64, u64) -> Result<(), String> + Sync,
{
    let total = items.len() as u64;
    let cursor = AtomicUsize::new(0);
    let done = AtomicUsize::new(0);
    let bytes = AtomicU64::new(0);
    let first_error: Mutex<Option<String>> = Mutex::new(None);
    let rows: Mutex<Vec<Option<Value>>> = Mutex::new((0..items.len()).map(|_| None).collect());
    std::thread::scope(|scope| {
        for _ in 0..SYNC_DOWNLOAD_WORKERS.min(items.len()) {
            scope.spawn(|| loop {
                if cancel.load(Ordering::Acquire) || first_error.lock().unwrap().is_some() {
                    return;
                }
                let index = cursor.fetch_add(1, Ordering::AcqRel);
                if index >= items.len() {
                    return;
                }
                match fetch(&items[index]) {
                    Ok((row, item_bytes)) => {
                        rows.lock().unwrap()[index] = Some(row);
                        let total_bytes = bytes.fetch_add(item_bytes, Ordering::AcqRel)
                            + item_bytes;
                        let finished = done.fetch_add(1, Ordering::AcqRel) + 1;
                        if file_progress(finished as u64, total, total_bytes).is_err() {
                            cancel.store(true, Ordering::Release);
                        }
                    }
                    Err(error) => {
                        let _ = first_error.lock().unwrap().replace(error);
                    }
                }
            });
        }
    });
    if let Some(error) = first_error.lock().unwrap().take() {
        return Err(error);
    }
    check(cancel)?;
    let rows = rows.into_inner().map_err(|_| "sync worker rows poisoned")?;
    Ok(rows
        .into_iter()
        .map(|row| row.unwrap_or_else(|| json!({"status": "skipped"})))
        .collect())
}

fn sync_ec<G>(
    context: &RunContext<'_>,
    cycle: DateTime<Utc>,
    horizon: u32,
    source: &str,
    cancel: &AtomicBool,
    file_progress: &G,
) -> Result<Value, String>
where
    G: Fn(u64, u64, u64) -> Result<(), String> + Sync,
{
    check(cancel)?;
    let started = Instant::now();
    let hours = ec_forecast_hours(horizon);
    // Per-hour cancellation: the provider checks the flag before every
    // transfer, so a cancelled round stops within one file download.
    // v1.9.6：改为 4 并发逐小时下载——每目标文件有独立 lock（ecmwf_data
    // download 的 {file}.lock），并发安全；41 文件 ≈ 32 min → ≈ 8 min。
    let brackets: Mutex<Option<Value>> = Mutex::new(None);
    let index_requests = AtomicUsize::new(0);
    let range_requests = AtomicUsize::new(0);
    let downloaded_bytes = AtomicUsize::new(0);
    let rows = download_pool(&hours, cancel, file_progress, |hour| {
        let mut value = ecmwf_data::execute_cancellable(
            ecmwf_data::Params {
                operation: "download".into(),
                config: ecmwf_data::Config {
                    cache_dir: context.ecmwf_cache_dir.clone(),
                    source: source.to_owned(),
                    ..Default::default()
                },
                cycle_utc: Some(time_contract::iso(cycle)),
                forecast_hour: None,
                forecast_hours: Some(vec![*hour]),
                reference_utc: None,
                now_utc: None,
                evaluation_utcs: Vec::new(),
                max_cycles: 8,
                allow_download: true,
                use_cache_for_cycle: true,
            },
            cancel,
        )?;
        index_requests.fetch_add(
            value["index_requests"].as_u64().unwrap_or(0) as usize,
            Ordering::AcqRel,
        );
        range_requests.fetch_add(
            value["range_requests"].as_u64().unwrap_or(0) as usize,
            Ordering::AcqRel,
        );
        downloaded_bytes.fetch_add(
            value["downloaded_bytes"].as_u64().unwrap_or(0) as usize,
            Ordering::AcqRel,
        );
        let _ = brackets.lock().unwrap().get_or_insert(value["brackets"].clone());
        let hour_bytes = value["downloaded_bytes"].as_u64().unwrap_or(0);
        let files = value["files"].take();
        Ok((files, hour_bytes))
    })?;
    let files = rows
        .into_iter()
        .flat_map(|row| match row {
            value @ Value::Array(_) => value.as_array().unwrap().to_owned(),
            Value::Null => Vec::new(),
            row => vec![row],
        })
        .collect::<Vec<_>>();
    Ok(json!({
        "contract": "rust-ecmwf-data-v1",
        "cycle_utc": time_contract::iso(cycle),
        "lead_horizon_hours": horizon,
        "ecmwf_source": source,
        "forecast_hours": hours,
        "brackets": brackets.lock().unwrap().clone().unwrap_or(Value::Null),
        "files": files,
        "index_requests": index_requests.load(Ordering::Acquire),
        "range_requests": range_requests.load(Ordering::Acquire),
        "downloaded_bytes": downloaded_bytes.load(Ordering::Acquire),
        "elapsed_ms": started.elapsed().as_secs_f64() * 1000.0,
    }))
}

/// One synchronous sync round. Sources fail independently (fail-closed,
/// per-source state updates); the round-level error is reserved for contract
/// violations and state/retention write failures.
pub fn run(
    context: &RunContext<'_>,
    params: Value,
    task_id: Option<&str>,
    cancel: &AtomicBool,
    progress: &(dyn Fn(Value) -> Result<(), String> + Sync),
) -> Result<Value, String> {
    let started = Instant::now();
    let started_at = time_contract::iso(DateTime::<Utc>::from(SystemTime::now()));
    let settings = load(context.workspace)?;
    if !settings.enabled {
        return Err("data sync is not enabled".into());
    }
    let force = params.get("force").and_then(Value::as_bool).unwrap_or(false);
    let requested = requested_sources(&params)?;
    for name in &requested {
        if !settings.sources.toggle(name) {
            return Err(format!(
                "data sync source {name} is not enabled in settings"
            ));
        }
    }
    let anchor = settings
        .anchor_region
        .clone()
        .ok_or("data sync anchor region is not configured")?;
    let horizon = if requested.iter().any(|name| *name == "gfs" || *name == "ec") {
        settings
            .lead_horizon_hours
            .ok_or("lead_horizon_hours is not configured")?
    } else {
        0
    };
    let ecmwf_source = if requested.contains(&"ec") {
        settings
            .ecmwf_source
            .clone()
            .ok_or("ecmwf_source is not configured")?
    } else {
        String::new()
    };

    let state_file = state_path(context.workspace);
    let task_json = task_id.map(|value| json!(value)).unwrap_or(Value::Null);

    // Cross-process round lock: a scheduled helper (launchd / Task Scheduler)
    // and the app are separate processes, so the in-process `sync` mutex cannot
    // coordinate them. Try-lock (0s): contention means another process is
    // already running a round; fail visibly instead of double-downloading.
    let lock_path = state_file.with_file_name("sync.lock");
    if let Some(parent) = lock_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let _round_lock = crate::cache_lock::Lock::acquire(&lock_path, 0.0)
        .map_err(|_| {
            // Name the holder when known: the lock file carries the owning PID
            // so a yielding round (and the UI) can tell which process is syncing
            // instead of guessing "the background helper".
            let holder = std::fs::read_to_string(&lock_path)
                .ok()
                .and_then(|text| text.trim().parse::<u32>().ok())
                .filter(|pid| *pid > 0);
            match holder {
                Some(pid) => {
                    format!("another data sync round is active in another process (pid {pid})")
                }
                None => "another data sync round is active in another process".to_string(),
            }
        })?;
    // Record this round's PID for contenders; the flock stays on the open fd
    // held by `_round_lock`, so rewriting the content does not release it.
    let _ = std::fs::write(&lock_path, std::process::id().to_string());

    // The publication probe runs before ANY state write: a failed probe
    // consumes neither the interval gate nor the breaker budget, and the
    // per-source failed rows keep the state file consistent with reality.
    let publications = match probe_publications(&requested, None, force) {
        Ok(value) => value,
        Err(error) => {
            commit_probe_failure(&state_file, &requested, &error, task_id)?;
            let mut results = Map::new();
            for name in &requested {
                results.insert(
                    (*name).to_owned(),
                    json!({
                        "status": "failed",
                        "cycle": Value::Null,
                        "cycle_label": Value::Null,
                        "publication_verified": false,
                        "error": clip(&error, 800),
                        "retention": Value::Null,
                        "task_id": task_json,
                    }),
                );
            }
            return Ok(json!({
                "contract": "rust-data-sync-v1",
                "runtime_python_used": false,
                "started_at_utc": started_at,
                "elapsed_ms": started.elapsed().as_secs_f64() * 1000.0,
                "sources": Value::Object(results),
            }));
        }
    };

    let mut results = Map::new();
    let mut outcomes: Vec<RoundOutcome> = Vec::new();
    let mut stop = cancel.load(Ordering::Acquire);
    let total = requested.len();
    for (index, name) in requested.iter().enumerate() {
        // v1.9.7：源边界让路——手动 prepare/forecast 等待期间不再启动新源。
        if take_manual_yield(context.workspace) {
            stop = true;
        }
        let publication = publications
            .get(publication_name(name))
            .map(publication_from);
        let cycle_json = publication
            .as_ref()
            .and_then(|p| p.cycle)
            .map(|cycle| json!(time_contract::iso(cycle)))
            .unwrap_or(Value::Null);
        let label_json = publication
            .as_ref()
            .map(|p| json!(p.label))
            .unwrap_or(Value::Null);
        let verified = publication
            .as_ref()
            .is_some_and(|p| p.verified && p.cycle.is_some());

        let cancelled_row = |error: Value| {
            json!({
                "status": "cancelled",
                "cycle": cycle_json.clone(),
                "cycle_label": label_json.clone(),
                "publication_verified": verified,
                "error": error,
                "retention": Value::Null,
                "task_id": task_json,
            })
        };
        if stop {
            let source = cancelled_row(Value::Null);
            outcomes.push(RoundOutcome {
                name: (*name).to_owned(),
                status: "cancelled",
                last_cycle: None,
                coverage_fingerprint: None,
                source: source.clone(),
            });
            results.insert((*name).to_owned(), source);
            continue;
        }
        let message = format!("正在同步 {} 最新周期", source_label(name));
        if progress(
            json!({"stage":"sync","percent":index * 100 / total,"source":name,"message":message}),
        )
        .is_err()
        {
            stop = true;
            let source = cancelled_row(Value::Null);
            outcomes.push(RoundOutcome {
                name: (*name).to_owned(),
                status: "cancelled",
                last_cycle: None,
                coverage_fingerprint: None,
                source: source.clone(),
            });
            results.insert((*name).to_owned(), source);
            continue;
        }
        // This source actually starts now: the short critical section unlocks
        // a suspended source (manual channel, design 8.3) and records its
        // attempt. Sources cancelled before their turn consume neither.
        let source_state = match commit_source_attempt(
            &state_file,
            name,
            DateTime::<Utc>::from(SystemTime::now()),
        ) {
            Ok(snapshot) => snapshot,
            Err(error) => return Err(error),
        };

        let mut is_current = false;
        let footprint = expected_fingerprint(name, &settings).ok();
        let coverage_tracked =
            footprint.is_some() && source_state.coverage_fingerprint == footprint;
        let outcome: Result<Value, String> = if !verified {
            Err(
                "publication is unavailable or unverified; refusing to download (fail-closed)"
                    .to_string(),
            )
        } else {
            let cycle = publication
                .as_ref()
                .and_then(|p| p.cycle)
                .unwrap_or_default();
            let last = source_state
                .last_cycle
                .as_deref()
                .and_then(|value| time_contract::parse_utc(value).ok());
            if let Some(last) = last.filter(|last| !force && cycle <= *last && coverage_tracked) {
                is_current = true;
                Ok(json!({
                    "files": [],
                    "cache_hits": 0,
                    "elapsed_ms": 0.0,
                    "current_cycle": time_contract::iso(last),
                }))
            } else if *name == "gfs"
                && last.is_some_and(|last| cycle <= last)
                && gfs_cache_complete(context, &anchor, cycle, horizon)?
            {
                // Same cycle, untracked footprint, but the cache already
                // covers every hourly lead: trust it without re-downloading
                // and stamp the fingerprint (F1 fast path).
                is_current = true;
                Ok(json!({
                    "files": [],
                    "cache_hits": 0,
                    "elapsed_ms": 0.0,
                    "current_cycle": time_contract::iso(cycle),
                    "coverage_verified": true,
                }))
            } else {
                // New cycle, or the same cycle with a stale/untracked
                // footprint: re-run the provider; per-lead resolve reuses
                // covering cache files and only downloads what is missing.
                // v1.9.6：把本源在轮内的百分比区间细分为文件进度——长下载
                // （GFS 20+ min）期间 UI 不再停在源起点百分比不动。
                let base_percent = index * 100 / total;
                let span_percent = 100 / total.max(1);
                let source_key: &str = name;
                let round_started = started;
                let yield_workspace = context.workspace.to_path_buf();
                let file_progress = move |done: u64, total_files: u64, bytes: u64| {
                    // 手动优先：文件粒度让路（秒级生效），错误会置取消位。
                    if take_manual_yield(&yield_workspace) {
                        return Err("manual prepare/forecast takes priority".into());
                    }
                    let within = if total_files == 0 {
                        0
                    } else {
                        done.min(total_files) * span_percent as u64 / total_files
                    };
                    // v1.9.7：吞吐随事件计算（MB/s），网络问题一眼可辨；
                    // bytes/elapsed_ms 字段同时机读供 UI 自行渲染。
                    let elapsed_ms = round_started.elapsed().as_secs_f64() * 1000.0;
                    let mibs = if elapsed_ms > 0.0 {
                        bytes as f64 / 1024.0 / 1024.0 / (elapsed_ms / 1000.0)
                    } else {
                        0.0
                    };
                    progress(json!({
                        "stage": "sync",
                        "percent": (base_percent + within as usize).min(99),
                        "source": source_key,
                        "message": format!(
                            "{} 正在下载 {} / {} · {:.1} MB/s",
                            source_label(source_key),
                            done,
                            total_files,
                            mibs
                        ),
                        "bytes": bytes,
                        "elapsed_ms": elapsed_ms,
                    }))
                };
                match *name {
                    "cams" => sync_cams(context, progress, &anchor, cycle, cancel),
                    "gfs" => sync_gfs(context, &anchor, cycle, horizon, cancel, &file_progress),
                    _ => sync_ec(context, cycle, horizon, &ecmwf_source, cancel, &file_progress),
                }
            }
        };
        let was_cancelled = cancel.load(Ordering::Acquire);
        let status: &str = if was_cancelled || stop {
            "cancelled"
        } else if outcome.is_err() {
            "failed"
        } else if is_current {
            "current"
        } else {
            "synced"
        };
        let mut source = match outcome {
            Ok(mut value) => {
                value["cycle"] = cycle_json.clone();
                value["cycle_label"] = label_json.clone();
                value["publication_verified"] = json!(true);
                value["retention"] = Value::Null;
                value["task_id"] = task_json.clone();
                value
            }
            Err(error) => json!({
                "status": "failed",
                "cycle": cycle_json.clone(),
                "cycle_label": label_json.clone(),
                "publication_verified": verified,
                "error": clip(&error, 800),
                "retention": Value::Null,
                "task_id": task_json.clone(),
            }),
        };
        source["status"] = json!(status);
        outcomes.push(RoundOutcome {
            name: (*name).to_owned(),
            status,
            last_cycle: if status == "synced" {
                publication
                    .and_then(|p| p.cycle)
                    .map(|cycle| time_contract::iso(cycle))
            } else {
                None
            },
            coverage_fingerprint: if matches!(status, "synced" | "current") {
                footprint.clone()
            } else {
                None
            },
            source: source.clone(),
        });
        if !stop {
            let message = format!("{} 同步{}", source_label(name), sync_status_label(status));
            if progress(
                json!({"stage":"sync","percent":(index + 1) * 100 / total,"source":name,"message":message}),
            )
            .is_err()
            {
                stop = true;
            }
        }
        results.insert((*name).to_owned(), source);
    }
    let statuses: Vec<String> = results
        .values()
        .filter_map(|source| source["status"].as_str().map(str::to_owned))
        .collect();
    let status_refs: Vec<&str> = statuses.iter().map(String::as_str).collect();
    // Commit the per-source outcomes first, merged into the latest on-disk
    // state; retention cleanup only runs afterwards, and a cleanup failure is
    // surfaced on the round result instead of discarding the committed state.
    commit_round_outcomes(&state_file, &outcomes, task_id)?;
    let mut round = json!({
        "contract": "rust-data-sync-v1",
        "runtime_python_used": false,
        "started_at_utc": started_at,
        "elapsed_ms": started.elapsed().as_secs_f64() * 1000.0,
        "sources": Value::Object(results),
    });
    if !stop && cleanup_allowed(&status_refs) {
        match retention_settings::apply_configured(context.workspace) {
            Ok(Some(report)) => {
                if let Some(sources) = round["sources"].as_object_mut() {
                    for name in &requested {
                        if let Some(source) = sources.get_mut(*name) {
                            if matches!(source["status"].as_str(), Some("synced") | Some("current"))
                            {
                                source["retention"] = report.clone();
                            }
                        }
                    }
                }
            }
            Ok(None) => {}
            Err(error) => {
                round["retention_error"] = json!(clip(&error, 400));
            }
        }
    }
    Ok(round)
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Timelike};

    fn settings_enabled() -> Value {
        json!({
            "enabled": true,
            "sources": {"cams": true, "gfs": true, "ec": true},
            "interval_minutes": 60,
            "anchor_region": "china",
            "lead_horizon_hours": 120,
            "ecmwf_source": "aws",
        })
    }

    fn state_with_failures(failures: u32, suspended: bool) -> State {
        State {
            cams: SourceState {
                last_cycle: Some("2026-11-12T00:00:00+00:00".into()),
                last_result: None,
                consecutive_failures: failures,
                suspended,
                ..Default::default()
            },
            ..Default::default()
        }
    }

    fn publication(cycle: Option<DateTime<Utc>>, verified: bool) -> Publication {
        Publication {
            cycle,
            verified,
            label: "2026-11-13 12Z".into(),
        }
    }

    #[test]
    fn missing_settings_are_disabled() {
        let workspace = tempfile::tempdir().unwrap();
        let value = status(workspace.path()).unwrap();
        assert_eq!(value["configured"], false);
        assert_eq!(value["retention_configured"], false);
        assert_eq!(value["settings"]["enabled"], false);
        // 全新工作区 → 可见布局；旧布局存在时仍解析到 .firesky（Legacy）。
        let legacy = workspace.path().join(".firesky").exists();
        let settings_suffix = if legacy { ".firesky/settings/data_sync.json" } else { "settings/data_sync.json" };
        let state_suffix = if legacy { ".firesky/state/data-sync.json" } else { "state/data-sync.json" };
        assert!(settings_path(workspace.path()).ends_with(settings_suffix));
        assert!(state_path(workspace.path()).ends_with(state_suffix));
    }

    #[test]
    fn save_round_trips_and_reports_state() {
        let workspace = tempfile::tempdir().unwrap();
        let value = save(workspace.path(), json!({"settings": settings_enabled()})).unwrap();
        assert_eq!(value["configured"], true);
        assert_eq!(value["settings"]["interval_minutes"], 60);
        assert_eq!(value["settings"]["sources"]["ec"], true);
        assert_eq!(value["state"]["contract"], STATE_CONTRACT);
        assert_eq!(
            load(workspace.path()).unwrap(),
            Settings {
                enabled: true,
                sources: SourceToggles {
                    cams: true,
                    gfs: true,
                    ec: true
                },
                interval_minutes: Some(60),
                anchor_region: Some("china".into()),
                lead_horizon_hours: Some(120),
                ecmwf_source: Some("aws".into()),
            }
        );
        // Disabled settings may keep valid optional values.
        save(
            workspace.path(),
            json!({"settings": {"enabled": false, "interval_minutes": 30}}),
        )
        .unwrap();
    }

    #[test]
    fn enabled_settings_require_every_explicit_field() {
        let workspace = tempfile::tempdir().unwrap();
        let cases = vec![
            json!({"settings": {"enabled": true, "sources": {}, "interval_minutes": 60,
                "anchor_region": "china"}}),
            json!({"settings": {"enabled": true, "sources": {"cams": true},
                "anchor_region": "china"}}),
            json!({"settings": {"enabled": true, "sources": {"cams": true},
                "interval_minutes": 60}}),
            json!({"settings": {"enabled": true, "sources": {"gfs": true},
                "interval_minutes": 60, "anchor_region": "china"}}),
            json!({"settings": {"enabled": true, "sources": {"ec": true},
                "interval_minutes": 60, "anchor_region": "china"}}),
            json!({"settings": {"enabled": true, "sources": {"ec": true},
                "interval_minutes": 60, "anchor_region": "china",
                "lead_horizon_hours": 120}}),
        ];
        for case in cases {
            assert!(
                save(workspace.path(), case).is_err(),
                "enabled settings without a required field must be rejected"
            );
        }
    }

    #[test]
    fn invalid_values_and_unknown_fields_are_rejected() {
        let workspace = tempfile::tempdir().unwrap();
        for case in vec![
            json!({"settings": {"interval_minutes": 4}}),
            json!({"settings": {"interval_minutes": 1441}}),
            json!({"settings": {"anchor_region": "atlantis"}}),
            json!({"settings": {"lead_horizon_hours": 121}}),
            json!({"settings": {"lead_horizon_hours": 119}}),
            json!({"settings": {"lead_horizon_hours": 241}}),
            json!({"settings": {"ecmwf_source": "noaa"}}),
            json!({"settings": {"unknown": 1}}),
            json!({"settings": {"sources": {"cams": true, "bogus": true}}}),
        ] {
            assert!(
                save(workspace.path(), case).is_err(),
                "invalid settings payload must be rejected"
            );
        }
    }

    // unix::fs::symlink 仅 POSIX；Windows 测试环境无符号链接语义。
    #[cfg(unix)]
    #[test]
    fn settings_symlink_is_rejected() {
        let workspace = tempfile::tempdir().unwrap();
        let target = workspace.path().join("elsewhere.json");
        fs::write(&target, "{}").unwrap();
        fs::create_dir_all(workspace.path().join(".firesky/settings")).unwrap();
        std::os::unix::fs::symlink(
            &target,
            workspace.path().join(".firesky/settings/data_sync.json"),
        )
        .unwrap();
        assert!(save(workspace.path(), json!({"settings": settings_enabled()})).is_err());
    }

    #[test]
    fn resaving_settings_unlocks_suspended_sources_and_keeps_cycles() {
        let workspace = tempfile::tempdir().unwrap();
        let state_file = state_path(workspace.path());
        let mut state = state_with_failures(3, true);
        state.gfs.consecutive_failures = 1;
        persist_state(&state_file, &state).unwrap();
        save(workspace.path(), json!({"settings": settings_enabled()})).unwrap();
        let unlocked = read_state(&state_file);
        assert_eq!(unlocked.cams.suspended, false);
        assert_eq!(unlocked.cams.consecutive_failures, 0);
        assert_eq!(unlocked.gfs.consecutive_failures, 0);
        assert_eq!(
            unlocked.cams.last_cycle.as_deref(),
            Some("2026-11-12T00:00:00+00:00")
        );
    }

    #[test]
    fn run_fails_visibly_when_another_process_holds_the_round_lock() {
        let workspace = tempfile::tempdir().unwrap();
        save(workspace.path(), json!({"settings": settings_enabled()})).unwrap();
        let lock_path = state_path(workspace.path()).with_file_name("sync.lock");
        fs::create_dir_all(lock_path.parent().unwrap()).unwrap();
        // flock is per open-file-description, so a second fd in this process
        // contends exactly like a separate helper process would.
        let _held = crate::cache_lock::Lock::acquire(&lock_path, 0.0).unwrap();
        let context = RunContext {
            workspace: workspace.path(),
            cams_cache_dir: workspace.path().join("cams"),
            gfs_cache_dir: workspace.path().join("gfs"),
            ecmwf_cache_dir: workspace.path().join("ec"),
        };
        let cancel = std::sync::atomic::AtomicBool::new(false);
        let err = run(
            &context,
            json!({"sources": ["cams"]}),
            None,
            &cancel,
            &|_| Ok(()),
        )
        .unwrap_err();
        assert!(
            err.contains("another data sync round is active in another process"),
            "{err}"
        );
    }

    #[test]
    fn round_lock_busy_error_names_the_holder_pid() {
        let workspace = tempfile::tempdir().unwrap();
        save(workspace.path(), json!({"settings": settings_enabled()})).unwrap();
        let lock_path = state_path(workspace.path()).with_file_name("sync.lock");
        fs::create_dir_all(lock_path.parent().unwrap()).unwrap();
        let _held = crate::cache_lock::Lock::acquire(&lock_path, 0.0).unwrap();
        // A live holder writes its PID into the lock file; the yielding round
        // must surface it so the UI can attribute the busy state correctly.
        fs::write(&lock_path, "4242").unwrap();
        let context = RunContext {
            workspace: workspace.path(),
            cams_cache_dir: workspace.path().join("cams"),
            gfs_cache_dir: workspace.path().join("gfs"),
            ecmwf_cache_dir: workspace.path().join("ec"),
        };
        let cancel = std::sync::atomic::AtomicBool::new(false);
        let err = run(
            &context,
            json!({"sources": ["cams"]}),
            None,
            &cancel,
            &|_| Ok(()),
        )
        .unwrap_err();
        assert!(
            err.contains("another data sync round is active in another process (pid 4242)"),
            "{err}"
        );
    }

    #[test]
    fn state_read_fails_closed_to_never_synced() {
        let workspace = tempfile::tempdir().unwrap();
        let state_file = state_path(workspace.path());
        fs::create_dir_all(state_file.parent().unwrap()).unwrap();
        // Missing file
        assert_eq!(read_state(&state_file), State::default());
        // Corrupt payload
        fs::write(&state_file, "not-json").unwrap();
        assert_eq!(read_state(&state_file), State::default());
        // Foreign contract
        fs::write(
            &state_file,
            serde_json::to_vec(&json!({"contract": "other-v9", "sources": {}})).unwrap(),
        )
        .unwrap();
        assert_eq!(read_state(&state_file), State::default());
        // Oversized payload
        fs::write(&state_file, "x".repeat(MAX_BYTES as usize + 1)).unwrap();
        assert_eq!(read_state(&state_file), State::default());
        // Unknown source keys inside the contract are rejected the same way.
        fs::write(
            &state_file,
            serde_json::to_vec(&json!({"contract": STATE_CONTRACT,
                "sources": {"cams": {}, "himawari": {}}}))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(read_state(&state_file), State::default());
        // A valid round trip survives.
        persist_state(&state_file, &state_with_failures(2, false)).unwrap();
        assert_eq!(read_state(&state_file), state_with_failures(2, false));
    }

    #[test]
    fn due_decision_follows_the_documented_order() {
        let cycle = Utc.with_ymd_and_hms(2026, 11, 13, 12, 0, 0).unwrap();
        let now = Utc.with_ymd_and_hms(2026, 11, 13, 15, 0, 0).unwrap();
        let idle = SourceState::default();
        // Fresh source with a verified new cycle is due.
        let (due, reason) =
            due_for_source(&idle, 60, Some(&publication(Some(cycle), true)), now, false);
        assert!(due);
        assert_eq!(reason, "new_cycle:2026-11-13 12Z");
        // Suspended sources are never auto-scheduled.
        let suspended = SourceState {
            suspended: true,
            consecutive_failures: 3,
            ..Default::default()
        };
        assert_eq!(
            due_for_source(
                &suspended,
                60,
                Some(&publication(Some(cycle), true)),
                now,
                false
            ),
            (false, "suspended_failures".into())
        );
        // Interval not elapsed.
        let recent = SourceState {
            last_attempt_utc: Some(time_contract::iso(now - Duration::minutes(30))),
            ..Default::default()
        };
        assert_eq!(
            due_for_source(
                &recent,
                60,
                Some(&publication(Some(cycle), true)),
                now,
                false
            ),
            (false, "interval_not_elapsed".into())
        );
        // Elapsed interval proceeds to the publication gate.
        let old = SourceState {
            last_attempt_utc: Some(time_contract::iso(now - Duration::minutes(61))),
            ..Default::default()
        };
        let (due, reason) =
            due_for_source(&old, 60, Some(&publication(Some(cycle), true)), now, false);
        assert!(due);
        assert_eq!(reason, "new_cycle:2026-11-13 12Z");
        // Heuristic (unverified) publications never trigger downloads.
        assert_eq!(
            due_for_source(&old, 60, Some(&publication(Some(cycle), false)), now, false),
            (false, "publication_unverified".into())
        );
        assert_eq!(
            due_for_source(&old, 60, None, now, false),
            (false, "publication_unverified".into())
        );
        // Verified cycle already synced is current.
        let synced = SourceState {
            last_cycle: Some(time_contract::iso(cycle)),
            ..Default::default()
        };
        assert_eq!(
            due_for_source(
                &synced,
                60,
                Some(&publication(Some(cycle), true)),
                now,
                true
            ),
            (false, "cycle_current".into())
        );
        // An older verified publication is also current.
        assert_eq!(
            due_for_source(
                &synced,
                60,
                Some(&publication(Some(cycle - Duration::hours(12)), true)),
                now,
                true
            ),
            (false, "cycle_current".into())
        );
        // After a manual unlock the source is due again.
        let mut unlocked = suspended;
        unlocked.suspended = false;
        unlocked.consecutive_failures = 0;
        assert!(
            due_for_source(
                &unlocked,
                60,
                Some(&publication(Some(cycle), true)),
                now,
                false
            )
            .0
        );
    }

    #[test]
    fn due_reopens_a_current_cycle_when_coverage_is_stale() {
        // F1 regression: a cycle marked `current` under the old 3-hour anchor
        // sync (no coverage fingerprint) must become due again so run() can
        // verify or backfill the hourly envelope.
        let cycle = Utc.with_ymd_and_hms(2026, 11, 13, 12, 0, 0).unwrap();
        let now = Utc.with_ymd_and_hms(2026, 11, 13, 15, 0, 0).unwrap();
        let synced = SourceState {
            last_cycle: Some(time_contract::iso(cycle)),
            coverage_fingerprint: None,
            ..Default::default()
        };
        assert_eq!(
            due_for_source(
                &synced,
                60,
                Some(&publication(Some(cycle), true)),
                now,
                false
            ),
            (true, "coverage_stale:2026-11-13 12Z".into())
        );
        // A recorded-but-different footprint (changed horizon/anchor) is also
        // stale, while a matching fingerprint keeps the cycle current.
        let tracked = SourceState {
            coverage_fingerprint: Some("v1:gfs:old-bbox:0-120".into()),
            ..synced.clone()
        };
        assert_eq!(
            due_for_source(
                &tracked,
                60,
                Some(&publication(Some(cycle), true)),
                now,
                false
            )
            .0,
            true
        );
        assert_eq!(
            due_for_source(
                &synced,
                60,
                Some(&publication(Some(cycle), true)),
                now,
                true
            ),
            (false, "cycle_current".into())
        );
    }

    #[test]
    fn due_method_reports_disabled_sources_without_probing() {
        let workspace = tempfile::tempdir().unwrap();
        let value = due(workspace.path(), false, None).unwrap();
        assert_eq!(value["contract"], "rust-data-sync-due-v1");
        assert_eq!(value["enabled"], false);
        assert_eq!(value["due_for_any"], false);
        for name in SOURCES {
            assert_eq!(value["sources"][name]["reason"], "disabled");
        }
    }

    #[test]
    fn due_method_reports_busy_without_probing() {
        let workspace = tempfile::tempdir().unwrap();
        save(workspace.path(), json!({"settings": settings_enabled()})).unwrap();
        let value = due(workspace.path(), true, None).unwrap();
        assert_eq!(value["enabled"], true);
        assert_eq!(value["due_for_any"], false);
        assert_eq!(value["sources"]["cams"]["reason"], "busy");
        assert_eq!(value["sources"]["gfs"]["reason"], "busy");
    }

    #[test]
    fn due_method_uses_the_pure_decision_with_injected_now() {
        let workspace = tempfile::tempdir().unwrap();
        save(workspace.path(), json!({"settings": settings_enabled()})).unwrap();
        // `now_utc` injects the reference time; the probe itself is skipped by
        // marking the engine busy, so this stays hermetic while exercising the
        // merged response shape.
        let value = due(workspace.path(), true, Some("2026-11-13T15:00:00Z")).unwrap();
        assert_eq!(value["sources"]["cams"]["last_attempt_utc"], Value::Null);
    }

    #[test]
    fn cams_area_reorders_region_bounds_before_envelope_lookup() {
        assert_eq!(region_bounds("china").unwrap(), [73.0, 136.0, 54.0, 18.0]);
        // China anchor resolves to the Asia continent envelope.
        assert_eq!(
            cams_area_from_region(region_bounds("china").unwrap()).unwrap(),
            [81.0, 26.0, -11.0, 180.0]
        );
        // Regions that intersect several continents resolve to their union
        // (the union behavior itself is pinned by the continents.rs tests).
        assert_eq!(
            cams_area_from_region(region_bounds("europe").unwrap()).unwrap(),
            [81.0, -25.0, -35.0, 180.0]
        );
        // An ocean bbox outside every continent rectangle keeps its own area.
        assert_eq!(
            cams_area_from_region([-160.0, -150.0, -20.0, -30.0]).unwrap(),
            [-20.0, -160.0, -30.0, -150.0]
        );
        assert!(region_bounds("elsewhere").is_err());
    }

    #[test]
    fn cams_targets_are_three_hour_aligned_and_cross_days() {
        let run = Utc.with_ymd_and_hms(2026, 11, 13, 12, 0, 0).unwrap();
        let targets = cams_target_utcs(run).unwrap();
        assert_eq!(
            targets,
            vec![
                "2026-11-14T00:00:00+00:00",
                "2026-11-14T12:00:00+00:00",
                "2026-11-15T00:00:00+00:00",
            ]
        );
        for target in
            cams_target_utcs(Utc.with_ymd_and_hms(2026, 11, 13, 0, 0, 0).unwrap()).unwrap()
        {
            let parsed = time_contract::parse_utc(target.as_str()).unwrap();
            assert_eq!(parsed.hour() % 3, 0);
        }
    }

    #[test]
    fn ec_hours_follow_the_published_grid_within_the_horizon() {
        assert_eq!(ec_forecast_hours(3), vec![0, 3]);
        let hours = ec_forecast_hours(240);
        assert_eq!(hours.first().copied(), Some(0));
        assert_eq!(hours.last().copied(), Some(240));
        assert!(hours.iter().all(|hour| crate::ecmwf::published_step(*hour)));
        // Beyond 144 h the grid steps by 6 hours, not 3.
        assert!(hours.contains(&144));
        assert!(!hours.contains(&147));
        assert!(hours.contains(&150));
        assert_eq!(
            ec_forecast_hours(144),
            (0..=144).step_by(3).collect::<Vec<i64>>()
        );
    }

    #[test]
    fn requested_sources_are_explicit_and_canonical() {
        assert!(requested_sources(&json!({})).is_err());
        assert!(requested_sources(&json!({"sources": []})).is_err());
        assert!(requested_sources(&json!({"sources": ["satellite"]})).is_err());
        assert!(requested_sources(&json!({"sources": ["cams", 1]})).is_err());
        assert_eq!(
            requested_sources(&json!({"sources": ["ec", "cams", "ec"]})).unwrap(),
            vec!["cams", "ec"]
        );
    }

    #[test]
    fn source_outcomes_update_the_breaker_state() {
        let mut source = SourceState {
            last_cycle: Some("2026-11-12T00:00:00+00:00".into()),
            consecutive_failures: 2,
            ..Default::default()
        };
        record_source_outcome(
            Some(&mut source),
            "failed",
            None,
            None,
            &json!({"error": "boom"}),
            None,
        );
        assert_eq!(source.consecutive_failures, 3);
        assert_eq!(source.suspended, true);
        assert_eq!(
            source.last_cycle.as_deref(),
            Some("2026-11-12T00:00:00+00:00")
        );
        assert_eq!(source.last_result.as_ref().unwrap()["status"], "failed");
        assert_eq!(source.last_result.as_ref().unwrap()["errors"][0], "boom");
        // A success clears the breaker and advances the cycle.
        record_source_outcome(
            Some(&mut source),
            "synced",
            Some("2026-11-13T12:00:00+00:00".into()),
            Some("v2:cams:test".into()),
            &json!({"files": ["a"], "elapsed_ms": 5.0}),
            Some("task-1"),
        );
        assert_eq!(source.consecutive_failures, 0);
        assert_eq!(source.suspended, false);
        assert_eq!(
            source.last_cycle.as_deref(),
            Some("2026-11-13T12:00:00+00:00")
        );
        assert_eq!(source.last_result.as_ref().unwrap()["files"], 1);
        assert_eq!(source.last_result.as_ref().unwrap()["task_id"], "task-1");
        // "current" clears failures without touching last_cycle.
        record_source_outcome(Some(&mut source), "current", None, None, &json!({}), None);
        assert_eq!(
            source.last_cycle.as_deref(),
            Some("2026-11-13T12:00:00+00:00")
        );
        assert_eq!(source.coverage_fingerprint.as_deref(), Some("v2:cams:test"));
        assert_eq!(source.suspended, false);
        // Cancelled rounds are never counted as failures.
        source.consecutive_failures = 2;
        record_source_outcome(Some(&mut source), "cancelled", None, None, &json!({}), None);
        assert_eq!(source.consecutive_failures, 2);
        assert_eq!(source.last_result.as_ref().unwrap()["status"], "cancelled");
    }

    #[test]
    fn cleanup_only_follows_a_fully_successful_round() {
        assert!(cleanup_allowed(&["synced"]));
        assert!(cleanup_allowed(&["synced", "current"]));
        assert!(cleanup_allowed(&["current"]));
        assert!(!cleanup_allowed(&[]));
        assert!(!cleanup_allowed(&["synced", "failed"]));
        assert!(!cleanup_allowed(&["cancelled", "synced"]));
    }

    #[test]
    fn unknown_source_state_names_are_rejected() {
        let mut state = State::default();
        assert!(state.source("cams").is_some());
        assert!(state.source("satellite").is_none());
        assert!(state.source_mut("himawari").is_none());
    }

    #[test]
    fn due_method_never_judges_when_the_master_switch_is_off() {
        let workspace = tempfile::tempdir().unwrap();
        // Toggled sources with the master switch off are a valid save; due
        // must report disabled instead of probing or returning due sources.
        save(
            workspace.path(),
            json!({"settings": {"enabled": false, "sources": {"cams": true}}}),
        )
        .unwrap();
        let value = due(workspace.path(), false, None).unwrap();
        assert_eq!(value["enabled"], false);
        assert_eq!(value["due_for_any"], false);
        assert_eq!(value["sources"]["cams"]["reason"], "disabled");
        assert_eq!(value["sources"]["gfs"]["reason"], "disabled");
    }

    #[test]
    fn source_attempts_are_recorded_when_the_source_actually_starts() {
        let workspace = tempfile::tempdir().unwrap();
        let state_file = state_path(workspace.path());
        fs::create_dir_all(state_file.parent().unwrap()).unwrap();
        persist_state(&state_file, &state_with_failures(3, true)).unwrap();
        let now = Utc.with_ymd_and_hms(2026, 11, 13, 15, 0, 0).unwrap();
        // The snapshot returned before the unlock is what the round uses for
        // its current-cycle comparison.
        let snapshot = commit_source_attempt(&state_file, "cams", now).unwrap();
        assert_eq!(snapshot.consecutive_failures, 3);
        let committed = read_state(&state_file);
        assert_eq!(committed.cams.suspended, false);
        assert_eq!(committed.cams.consecutive_failures, 0);
        assert_eq!(
            committed.cams.last_attempt_utc.as_deref(),
            Some("2026-11-13T15:00:00+00:00")
        );
        // A source cancelled before its turn is never committed at all: gfs
        // keeps neither an attempt nor state changes.
        let committed = read_state(&state_file);
        assert_eq!(committed.gfs.last_attempt_utc, None);
        assert_eq!(committed.gfs.suspended, false);
    }

    #[test]
    fn probe_failure_records_failed_rows_without_consuming_the_gate() {
        let workspace = tempfile::tempdir().unwrap();
        let state_file = state_path(workspace.path());
        fs::create_dir_all(state_file.parent().unwrap()).unwrap();
        persist_state(&state_file, &state_with_failures(1, false)).unwrap();
        commit_probe_failure(
            &state_file,
            &["cams", "gfs"],
            "publication probe network failed",
            Some("task-9"),
        )
        .unwrap();
        let committed = read_state(&state_file);
        // No attempt, no breaker movement: only an honest failed row.
        assert_eq!(committed.cams.last_attempt_utc, None);
        assert_eq!(committed.cams.consecutive_failures, 1);
        assert_eq!(committed.cams.suspended, false);
        assert_eq!(
            committed.cams.last_result.as_ref().unwrap()["status"],
            "failed"
        );
        assert_eq!(
            committed.cams.last_result.as_ref().unwrap()["errors"][0],
            "publication probe network failed"
        );
        assert_eq!(
            committed.cams.last_result.as_ref().unwrap()["task_id"],
            "task-9"
        );
        assert_eq!(
            committed.gfs.last_result.as_ref().unwrap()["status"],
            "failed"
        );
    }

    #[test]
    fn round_outcomes_merge_into_the_latest_state_instead_of_overwriting_it() {
        let workspace = tempfile::tempdir().unwrap();
        let state_file = state_path(workspace.path());
        fs::create_dir_all(state_file.parent().unwrap()).unwrap();
        persist_state(&state_file, &state_with_failures(3, true)).unwrap();
        // Mid-round settings save (the manual unlock channel) clears the breaker.
        save(workspace.path(), json!({"settings": settings_enabled()})).unwrap();
        // The round then fails cams: the counter resumes from the unlocked 0
        // instead of resurrecting the pre-round 3.
        let outcomes = vec![RoundOutcome {
            name: "cams".into(),
            status: "failed",
            last_cycle: None,
            coverage_fingerprint: None,
            source: json!({"error": "boom"}),
        }];
        commit_round_outcomes(&state_file, &outcomes, None).unwrap();
        let committed = read_state(&state_file);
        assert_eq!(committed.cams.consecutive_failures, 1);
        assert_eq!(committed.cams.suspended, false);
        assert_eq!(
            committed.cams.last_cycle.as_deref(),
            Some("2026-11-12T00:00:00+00:00")
        );
        // A synced outcome advances the cycle and keeps the breaker cleared.
        let outcomes = vec![RoundOutcome {
            name: "cams".into(),
            status: "synced",
            last_cycle: Some("2026-11-13T12:00:00+00:00".into()),
            coverage_fingerprint: Some("v2:cams:test".into()),
            source: json!({"files": ["a"], "elapsed_ms": 1.0}),
        }];
        commit_round_outcomes(&state_file, &outcomes, None).unwrap();
        let committed = read_state(&state_file);
        assert_eq!(committed.cams.consecutive_failures, 0);
        assert_eq!(committed.cams.suspended, false);
        assert_eq!(
            committed.cams.last_cycle.as_deref(),
            Some("2026-11-13T12:00:00+00:00")
        );
    }

    /// v1.9.6 欠账重试：真实下载失败 + 间隔已过 → 未核验也重试；
    /// 探测失败（probe_only）与上轮成功不触发。
    #[test]
    fn due_retries_after_real_failure_even_when_publication_unverified() {
        // 引擎 chrono 未开 clock feature，用固定时刻代替 Utc::now()。
        let now = Utc.with_ymd_and_hms(2026, 11, 21, 12, 0, 0).unwrap();
        let publication = Publication {
            cycle: None,
            verified: false,
            label: "unverified".into(),
        };
        let mut state = SourceState::default();
        state.last_attempt_utc = Some(time_contract::iso(now - Duration::minutes(61)));
        state.last_result = Some(json!({"status": "failed", "errors": ["boom"]}));
        let (due, reason) = due_for_source(&state, 60, Some(&publication), now, true);
        assert!(due);
        assert_eq!(reason, "retry_after_failure");

        // 探测失败行不触发重试（60s tick 空转保护）。
        state.last_result = Some(json!({"status": "failed", "probe_only": true}));
        let (due, reason) = due_for_source(&state, 60, Some(&publication), now, true);
        assert!(!due);
        assert_eq!(reason, "publication_unverified");

        // 上轮成功（无欠账）也不触发。
        state.last_result = Some(json!({"status": "synced", "errors": []}));
        let (due, reason) = due_for_source(&state, 60, Some(&publication), now, true);
        assert!(!due);
        assert_eq!(reason, "publication_unverified");

        // 间隔未过不重试。
        state.last_result = Some(json!({"status": "failed"}));
        state.last_attempt_utc = Some(time_contract::iso(now - Duration::minutes(5)));
        let (due, reason) = due_for_source(&state, 60, Some(&publication), now, true);
        assert!(!due);
        assert_eq!(reason, "interval_not_elapsed");
    }

    /// v1.9.6：逐文件下载线程池——顺序保序、首错即停、进度失败置取消位。
    #[test]
    fn download_pool_collects_all_in_order() {
        let cancel = AtomicBool::new(false);
        let rows = download_pool(
            &(0..16_u32).collect::<Vec<_>>(),
            &cancel,
            &|_, _, _| Ok(()),
            |hour| Ok((json!({"forecast_hour": hour}), 0)),
        )
        .unwrap();
        let hours: Vec<u32> = rows
            .iter()
            .map(|row| row["forecast_hour"].as_u64().unwrap() as u32)
            .collect();
        assert_eq!(hours, (0..16).collect::<Vec<_>>());
        assert!(!cancel.load(Ordering::Acquire));
    }

    #[test]
    fn download_pool_stops_on_first_error() {
        let cancel = AtomicBool::new(false);
        let result = download_pool(
            &(0..16_u32).collect::<Vec<_>>(),
            &cancel,
            &|_, _, _| Ok(()),
            |hour| {
                if *hour == 3 {
                    Err("boom-3".into())
                } else {
                    Ok((json!({"forecast_hour": hour}), 0))
                }
            },
        );
        assert_eq!(result.unwrap_err(), "boom-3");
    }

    #[test]
    fn download_pool_progress_failure_cancels_the_round() {
        let cancel = AtomicBool::new(false);
        let result = download_pool(
            &(0..16_u32).collect::<Vec<_>>(),
            &cancel,
            &|_, _, _| Err("progress wire down".into()),
            |hour| Ok((json!({"forecast_hour": hour}), 0)),
        );
        assert!(result.is_err());
        assert!(cancel.load(Ordering::Acquire));
    }

    /// ADS 资产总时限随体积放宽、被 remaining 封顶（v1.9.6 回归）。
    #[test]
    fn ads_asset_transfer_budget_scales_and_caps() {
        let asset_transfer_budget = crate::ads::asset_transfer_budget;
        let remaining = std::time::Duration::from_secs(7200);
        // 小资产维持配置超时 120s。
        assert_eq!(
            asset_transfer_budget(remaining, 120.0, 1 << 20),
            std::time::Duration::from_secs(120)
        );
        // 512 MiB / 256 KiB/s ≈ 2048s。
        assert_eq!(
            asset_transfer_budget(remaining, 120.0, 512 * 1024 * 1024),
            std::time::Duration::from_secs(2048)
        );
        assert_eq!(
            asset_transfer_budget(std::time::Duration::from_secs(10), 120.0, 512 * 1024 * 1024),
            std::time::Duration::from_secs(10)
        );
    }
}
