//! Data-plane contract behind the desktop orchestration, plus the production
//! backend routed through engine/src/rpc.rs method arms.
//!
//! The raw-payload planning (desktop_data.check_model_data / prepare_model_data
//! / forecast runs) is still Python-owned — see docs/RUST_MIGRATION_PLAN.md and
//! docs/MOBILE_NATIVE_PLAN.md (P-2/MN1). The engine arms that exist today serve
//! publications; the remaining data-plane calls return explicit migration
//! pending errors instead of pretending to work.

use crate::service::{MobileConfig, OpError, RawProgress};
use serde_json::{json, Value};
use std::sync::atomic::AtomicBool;

pub const CHECK_PENDING_NATIVE: &str = "数据检查的原始参数规划仍由 Python（firesky.desktop_data.check_model_data）承载，尚未迁移为原生臂（原生臂 check_desktop_data 需已解析的 plans 输入）；见 docs/RUST_MIGRATION_PLAN.md 与 docs/MOBILE_NATIVE_PLAN.md。";
pub const PREPARE_PENDING_NATIVE: &str = "数据准备仍由 Python（firesky.desktop_data.prepare_model_data）承载，尚未迁移为原生臂；见 docs/RUST_MIGRATION_PLAN.md 与 docs/MOBILE_NATIVE_PLAN.md。";
pub const FORECAST_PENDING_NATIVE: &str = "预测执行仍由 Python（firesky.web_server.run_forecast_payload）承载，尚未迁移为原生臂；见 docs/RUST_MIGRATION_PLAN.md 与 docs/MOBILE_NATIVE_PLAN.md。";

/// Data-plane operations required by the desktop orchestration.
pub trait DesktopBackend: Send + Sync {
    /// desktop_rpc.py `DesktopService._check(payload, kind, fast, refresh)`.
    fn check(
        &self,
        payload: &Value,
        kind: &str,
        fast: bool,
        refresh: bool,
        progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<Value, OpError>;

    /// desktop_rpc.py `prepare_model_data` / `prepare_nowcast_data` branch.
    fn prepare(
        &self,
        payload: &Value,
        kind: &str,
        progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<(), OpError>;

    /// desktop_rpc.py `_forecast` per-model (or single nowcast) run.
    fn forecast(
        &self,
        payload: &Value,
        kind: &str,
        progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<Value, OpError>;

    /// desktop_rpc.py `data.published_cycles(sources)`.
    fn publications(&self, sources: &[String]) -> Result<Value, OpError>;

    /// desktop_rpc.py `data.apply_publications(report, publications)`.
    fn apply_publications(&self, report: &Value, publications: &Value)
        -> Result<Value, OpError>;
}

/// Production default backend: routes what is native today through the shared
/// engine method arms (`firesky_engine::rpc::dispatch`).
pub struct EngineArmBackend {
    #[allow(dead_code)] // consumed by MN1 when the data plane lands natively
    config: MobileConfig,
}

impl EngineArmBackend {
    pub fn new(config: MobileConfig) -> Self {
        Self { config }
    }
}

impl DesktopBackend for EngineArmBackend {
    fn check(
        &self,
        _payload: &Value,
        _kind: &str,
        _fast: bool,
        _refresh: bool,
        _progress: RawProgress<'_>,
        _cancel: &AtomicBool,
    ) -> Result<Value, OpError> {
        Err(OpError::Message(CHECK_PENDING_NATIVE.to_string()))
    }

    fn prepare(
        &self,
        _payload: &Value,
        _kind: &str,
        _progress: RawProgress<'_>,
        _cancel: &AtomicBool,
    ) -> Result<(), OpError> {
        Err(OpError::Message(PREPARE_PENDING_NATIVE.to_string()))
    }

    fn forecast(
        &self,
        _payload: &Value,
        _kind: &str,
        _progress: RawProgress<'_>,
        _cancel: &AtomicBool,
    ) -> Result<Value, OpError> {
        Err(OpError::Message(FORECAST_PENDING_NATIVE.to_string()))
    }

    fn publications(&self, sources: &[String]) -> Result<Value, OpError> {
        firesky_engine::rpc::dispatch("desktop_publications", json!({ "sources": sources }))
            .map_err(OpError::Message)
    }

    fn apply_publications(
        &self,
        report: &Value,
        publications: &Value,
    ) -> Result<Value, OpError> {
        Ok(apply_publications(report, publications))
    }
}

/// Native port of firesky/desktop_data.py `apply_publications`.
pub fn apply_publications(report: &Value, publications: &Value) -> Value {
    let mut report = report.clone();
    report["publication_pending"] = json!(false);
    let sources = publications.as_object().cloned().unwrap_or_default();
    if let Some(rows) = report
        .get_mut("datasets")
        .and_then(Value::as_array_mut)
    {
        for row in rows.iter_mut() {
            let Some(source) = row.get("source").and_then(Value::as_str) else {
                continue;
            };
            let Some(publication) = sources.get(source) else {
                continue;
            };
            if publication.is_null() {
                continue;
            }
            let label = publication
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or("未核验")
                .to_string();
            let verified = publication
                .get("verified")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let ready = row.get("ready").and_then(Value::as_bool).unwrap_or(false);
            let requested = row
                .get("request_cycle")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let is_latest = verified
                && ready
                && requested
                    .split(" / ")
                    .collect::<std::collections::BTreeSet<_>>()
                    == std::collections::BTreeSet::from([label.as_str()]);
            row["latest_available_cycle"] = json!(label);
            row["latest_available_cycle_utc"] = publication
                .get("cycle")
                .cloned()
                .unwrap_or(Value::Null);
            row["verified"] = json!(verified);
            row["is_latest"] = json!(is_latest);
        }
    }
    let cams = publications.get("cams");
    if cams.and_then(Value::as_object).is_some_and(|cams| {
        cams.get("verified").and_then(Value::as_bool).unwrap_or(false)
    }) {
        report["cams_update_cycle"] = cams
            .and_then(|cams| cams.get("cycle"))
            .cloned()
            .unwrap_or(Value::Null);
    }
    report
}
