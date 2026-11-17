//! Acceptance matrix ported from tests/test_desktop_rpc.py — the orchestration
//! semantics are exercised against a stub backend exactly like the Python
//! fixtures monkeypatch `_check` / `prepare_model_data` / `_forecast`.

use super::*;
use std::sync::mpsc;

fn report(ready: bool) -> Value {
    let datasets: Vec<Value> = ["gfs", "ecmwf", "cams"]
        .iter()
        .map(|source| {
            json!({
                "source": source, "ready": ready,
                "missing_count": if ready { 0 } else { 2 },
                "request_cycle": "2026-11-08 00Z", "verified": false, "is_latest": false,
            })
        })
        .collect();
    json!({
        "ready": ready,
        "model_cycles": {"gfs": "2026-11-08T00:00:00+00:00",
                         "ec": "2026-11-08T00:00:00+00:00"},
        "datasets": datasets,
    })
}

struct StubBackend {
    /// Canned `_check` reports; the last one repeats.
    reports: Mutex<Vec<Value>>,
    prepare_calls: Mutex<Vec<Value>>,
    forecast_calls: Mutex<Vec<String>>,
    forecast_errors: Mutex<HashMap<String, String>>,
    prepare_error: Mutex<Option<String>>,
    /// Block prepare until released or cancelled.
    hold_prepare: Arc<(Mutex<bool>, Condvar)>,
    /// Block forecast until cancelled or released.
    hold_forecast: Arc<(Mutex<bool>, Condvar)>,
}

impl StubBackend {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            reports: Mutex::new(vec![report(true)]),
            prepare_calls: Mutex::new(Vec::new()),
            forecast_calls: Mutex::new(Vec::new()),
            forecast_errors: Mutex::new(HashMap::new()),
            prepare_error: Mutex::new(None),
            hold_prepare: Arc::new((Mutex::new(false), Condvar::new())),
            hold_forecast: Arc::new((Mutex::new(false), Condvar::new())),
        })
    }

    /// Replace the canned report: every subsequent `_check` returns it
    /// (mirrors the Python fixtures monkeypatching `_check`).
    fn set_report(&self, value: Value) {
        *self.reports.lock().unwrap() = vec![value];
    }

    fn next_report(&self) -> Value {
        self.reports.lock().unwrap()[0].clone()
    }

    fn wait_while_held(hold: &Arc<(Mutex<bool>, Condvar)>, cancel: &AtomicBool) {
        let (lock, signal) = &**hold;
        let mut held = lock.lock().unwrap();
        while *held && !cancel.load(Ordering::Acquire) {
            let (guard, _) = signal.wait_timeout(held, Duration::from_millis(10)).unwrap();
            held = guard;
        }
        signal.notify_all();
    }
}

impl DesktopBackend for StubBackend {
    fn check(
        &self,
        _payload: &Value,
        _kind: &str,
        _fast: bool,
        _refresh: bool,
        _progress: RawProgress<'_>,
        _cancel: &AtomicBool,
    ) -> Result<Value, OpError> {
        Ok(self.next_report())
    }

    fn prepare(
        &self,
        payload: &Value,
        _kind: &str,
        _progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<(), OpError> {
        // Record the call before holding so tests can observe the start.
        self.prepare_calls.lock().unwrap().push(payload.clone());
        Self::wait_while_held(&self.hold_prepare, cancel);
        if let Some(message) = self.prepare_error.lock().unwrap().clone() {
            return Err(OpError::Message(message));
        }
        Ok(())
    }

    fn forecast(
        &self,
        payload: &Value,
        kind: &str,
        progress: RawProgress<'_>,
        cancel: &AtomicBool,
    ) -> Result<Value, OpError> {
        if kind == "nowcast" {
            progress(json!({"percent": 40, "stage": "score", "message": "CAMS HSD"}))?;
            return Ok(json!({"peak_quality_index": 1.25, "nowcast": {"source": "stub"}}));
        }
        let model = payload["model_source"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        self.forecast_calls.lock().unwrap().push(model.clone());
        Self::wait_while_held(&self.hold_forecast, cancel);
        if let Some(message) = self.forecast_errors.lock().unwrap().get(&model).cloned() {
            if message == "<cancelled>" {
                return Err(OpError::Cancelled);
            }
            return Err(OpError::Message(message));
        }
        progress(json!({
            "percent": 50, "stage": "download",
            "message": "/secret/token CAMS download",
        }))?;
        Ok(json!({"quality_index": 1.25}))
    }

    fn publications(&self, _sources: &[String]) -> Result<Value, OpError> {
        Ok(json!({}))
    }

    fn apply_publications(
        &self,
        report: &Value,
        publications: &Value,
    ) -> Result<Value, OpError> {
        Ok(crate::backend::apply_publications(report, publications))
    }
}

fn noop_progress(_: Value) -> Result<(), Cancelled> {
    Ok(())
}

struct Harness {
    started: Started,
    backend: Arc<StubBackend>,
}

fn harness() -> Harness {
    let backend = StubBackend::new();
    let (sender, _receiver) = mpsc::channel::<String>();
    let sink: FrameSink = Arc::new(move |frame: &str| {
        let _ = sender.send(frame.to_string());
    });
    let started = start(
        MobileConfig {
            workspace_root: std::env::temp_dir().join("fsmobile-service-tests"),
            ..Default::default()
        },
        backend.clone(),
        sink,
    );
    Harness { started, backend }
}

impl Harness {
    fn inner(&self) -> &Arc<Inner> {
        &self.started.inner
    }

    fn check(&self, kind: &str, payload: Value) -> String {
        self.inner()
            .dispatch(
                "check",
                &json!({"kind": kind, "payload": payload}),
                &noop_progress,
                &AtomicBool::new(false),
            )
            .unwrap()["session"]
            .as_str()
            .unwrap()
            .to_string()
    }

    fn expire(&self, token: &str) {
        let sessions = self.inner().sessions.lock().unwrap();
        let mut created = sessions.get(token).unwrap().created.lock().unwrap();
        *created = Instant::now()
            .checked_sub(Duration::from_secs(SESSION_SECONDS + 1))
            .unwrap();
    }
}

fn dispatch_error(harness: &Harness, method: &str, params: Value) -> String {
    match harness.inner().dispatch(method, &params, &noop_progress, &AtomicBool::new(false)) {
        Ok(value) => panic!("{method} unexpectedly succeeded: {value}"),
        Err(OpError::Message(message)) => message,
        Err(OpError::Cancelled) => "cancelled".to_string(),
    }
}

#[test]
fn hello_advertises_protocol_one_and_v1_capabilities() {
    let mut harness = harness();
    let hello = harness
        .inner()
        .dispatch("hello", &json!({}), &noop_progress, &AtomicBool::new(false))
        .unwrap();
    assert_eq!(hello["protocol"], 1);
    assert_eq!(hello["engine"], "firesky");
    assert_eq!(
        hello["capabilities"],
        json!(["point", "inventory", "network_probe"])
    );
    assert!(hello["cams_credentials"].is_object());
    assert!(hello["workspace"].is_string());
    harness.started.stop();
}

#[test]
fn normalized_snapshot_and_confirmation() {
    let mut harness = harness();
    let original = json!({"name": "before", "model_source": "gfs", "spacing_deg": 2});
    let token = harness.check("regional", original.clone());
    let payload = {
        let sessions = harness.inner().sessions.lock().unwrap();
        let cell = sessions.get(&token).unwrap();
        let payload = cell.payload.lock().unwrap().clone();
        payload
    };
    assert_eq!(payload["model_source"], "both");
    assert_eq!(payload["spacing_deg"], 0.25);
    assert_eq!(payload["workers"], 1);
    assert_eq!(payload["_ec_cycle_override"], "2026-11-08T00:00:00+00:00");
    assert_eq!(payload["_gfs_cycle_override"], "2026-11-08T00:00:00+00:00");
    // The snapshot is deep: later caller mutation cannot reach the session.
    assert_eq!(payload["name"], "before");
    assert_eq!(
        dispatch_error(&harness, "forecast", json!({"session": &token})),
        "请先确认数据，再开始预测。"
    );
    let confirmed = harness
        .inner()
        .dispatch(
            "forecast",
            &json!({"session": &token, "confirmed": true}),
            &noop_progress,
            &AtomicBool::new(false),
        )
        .unwrap();
    assert_eq!(confirmed["model_results"]["gfs"]["quality_index"], 1.25);
    // Single-use: the token is consumed after one forecast.
    assert_eq!(
        dispatch_error(
            &harness,
            "forecast",
            json!({"session": &token, "confirmed": true})
        ),
        EXPIRED
    );
    harness.started.stop();
}

#[test]
fn missing_and_strict_validation_never_predict() {
    let mut harness = harness();
    // Ready at check time, then the strict recheck fails → 校验未通过.
    let token = harness.check("point", json!({}));
    harness.backend.set_report(report(false));
    assert!(dispatch_error(
        &harness,
        "forecast",
        json!({"session": &token, "confirmed": true})
    )
    .contains("校验"));
    // Not ready from the start → 先下载.
    let token = harness.check("point", json!({}));
    assert!(dispatch_error(
        &harness,
        "forecast",
        json!({"session": &token, "confirmed": true})
    )
    .contains("先下载"));
    assert!(harness.backend.forecast_calls.lock().unwrap().is_empty());
    harness.started.stop();
}

#[test]
fn prepare_strictly_rechecks_without_forecast() {
    let mut harness = harness();
    let token = harness.check("point", json!({}));
    let response = harness
        .inner()
        .dispatch(
            "prepare",
            &json!({"session": &token}),
            &noop_progress,
            &AtomicBool::new(false),
        )
        .unwrap();
    assert_eq!(response["report"]["ready"], true);
    assert_eq!(harness.backend.prepare_calls.lock().unwrap().len(), 1);
    let consumed = {
        let sessions = harness.inner().sessions.lock().unwrap();
        sessions
            .get(&token)
            .unwrap()
            .consumed
            .load(Ordering::Acquire)
    };
    assert!(!consumed);
    harness.started.stop();
}

#[test]
fn failed_prepare_invalidates_previous_readiness() {
    let mut harness = harness();
    let token = harness.check("point", json!({}));
    *harness.backend.prepare_error.lock().unwrap() = Some("partial download".into());
    assert_eq!(
        dispatch_error(&harness, "prepare", json!({"session": &token})),
        "partial download"
    );
    let ready = {
        let sessions = harness.inner().sessions.lock().unwrap();
        let cell = sessions.get(&token).unwrap();
        let ready = cell.report.lock().unwrap()["ready"].as_bool().unwrap();
        ready
    };
    assert!(!ready);
    harness.started.stop();
}

#[test]
fn stale_tokens_cannot_start() {
    let mut harness = harness();
    // dismiss
    let token = harness.check("point", json!({}));
    harness
        .inner()
        .dispatch(
            "dismiss",
            &json!({"session": &token}),
            &noop_progress,
            &AtomicBool::new(false),
        )
        .unwrap();
    assert_eq!(
        dispatch_error(
            &harness,
            "forecast",
            json!({"session": &token, "confirmed": true})
        ),
        EXPIRED
    );
    // expiry
    let token = harness.check("point", json!({}));
    harness.expire(&token);
    assert_eq!(
        dispatch_error(
            &harness,
            "forecast",
            json!({"session": &token, "confirmed": true})
        ),
        EXPIRED
    );
    harness.started.stop();
}

#[test]
fn pair_independent_results_and_partial_failure() {
    let mut harness = harness();
    harness
        .backend
        .forecast_errors
        .lock()
        .unwrap()
        .insert("ec".into(), "EC unavailable".into());
    let token = harness.check("point", json!({}));
    let events = std::cell::RefCell::new(Vec::new());
    let progress = |event: Value| -> Result<(), Cancelled> {
        events.borrow_mut().push(event);
        Ok(())
    };
    let result = harness
        .inner()
        .dispatch(
            "forecast",
            &json!({"session": &token, "confirmed": true}),
            &progress,
            &AtomicBool::new(false),
        )
        .unwrap();
    assert_eq!(
        harness.backend.forecast_calls.lock().unwrap().as_slice(),
        ["gfs", "ec"]
    );
    assert_eq!(result["model_results"]["gfs"]["quality_index"], 1.25);
    assert_eq!(result["model_errors"]["ec"], "EC unavailable");
    // forecast 链：score 起点 + 每模型链 remap（index*50 + p//2）。
    // 注意：这里的 progress 闭包位于脱敏层之前（redact 在 wire 层）。
    let events = events.into_inner();
    assert_eq!(events[0]["stage"], "score");
    assert_eq!(events[0]["percent"], 0);
    assert_eq!(events[1], json!({"percent": 0, "model_source": "gfs"}));
    assert_eq!(events[2]["percent"], 25);
    assert_eq!(events[2]["model_source"], "gfs");
    assert_eq!(events[3], json!({"percent": 50, "model_source": "ec"}));
    harness.started.stop();
}

#[test]
fn pair_preserves_cancellation() {
    let mut harness = harness();
    harness
        .backend
        .forecast_errors
        .lock()
        .unwrap()
        .insert("gfs".into(), "<cancelled>".into());
    let token = harness.check("point", json!({}));
    let outcome = harness.inner().dispatch(
        "forecast",
        &json!({"session": &token, "confirmed": true}),
        &noop_progress,
        &AtomicBool::new(false),
    );
    assert_eq!(outcome.unwrap_err(), OpError::Cancelled);
    harness.started.stop();
}

#[test]
fn nowcast_report_appends_hsd_row() {
    let mut harness = harness();
    harness.backend.set_report(json!({
        "ready": true,
        "satellite_frames": 2,
        "datasets": [
            {"source": "gfs", "ready": true},
            {"source": "cams", "ready": true},
        ],
    }));
    let response = harness
        .inner()
        .dispatch(
            "check",
            &json!({"kind": "nowcast", "payload": {"lat": 30.25, "lon": 120.25}}),
            &noop_progress,
            &AtomicBool::new(false),
        )
        .unwrap();
    let rows = response["report"]["datasets"].as_array().unwrap();
    assert_eq!(rows.last().unwrap()["source"], "hsd");
    assert_eq!(rows.last().unwrap()["ready"], true);
    assert_eq!(rows.last().unwrap()["missing_count"], 0);
    harness.started.stop();
}

#[test]
fn check_window_caps_at_32_sessions() {
    let mut harness = harness();
    for _ in 0..32 {
        harness.check("point", json!({}));
    }
    let overflow = harness
        .inner()
        .dispatch(
            "check",
            &json!({"kind": "point", "payload": {}}),
            &noop_progress,
            &AtomicBool::new(false),
        )
        .unwrap_err();
    assert_eq!(
        overflow,
        OpError::Message("检查窗口过多，请关闭后重试。".into())
    );
    harness.started.stop();
}

#[test]
fn compute_lock_is_single_flight() {
    let mut harness = harness();
    let token_a = harness.check("point", json!({}));
    let token_b = harness.check("point", json!({}));
    *harness.backend.hold_prepare.0.lock().unwrap() = true;
    let backend = harness.backend.clone();
    let inner = harness.inner().clone();
    let token = token_a.clone();
    let worker = std::thread::spawn(move || {
        inner
            .dispatch(
                "prepare",
                &json!({"session": token}),
                &noop_progress,
                &AtomicBool::new(false),
            )
            .unwrap();
    });
    // Wait until prepare holds the compute lock.
    let deadline = Instant::now() + Duration::from_secs(5);
    while harness.backend.prepare_calls.lock().unwrap().is_empty() {
        assert!(Instant::now() < deadline, "prepare never started");
        std::thread::sleep(Duration::from_millis(5));
    }
    // A concurrent forecast on a different session hits the compute lock
    // (its own session op-lock is free; both gates are non-blocking).
    let error = dispatch_error(
        &harness,
        "forecast",
        json!({"session": &token_b, "confirmed": true}),
    );
    assert_eq!(error, "已有下载或预测任务正在运行。");
    // Release and let the prepare finish.
    *backend.hold_prepare.0.lock().unwrap() = false;
    backend.hold_prepare.1.notify_all();
    worker.join().unwrap();
    // After completion the lock is free again: a fresh forecast attempt gets
    // past the compute gate (it fails later only because token_b was consumed
    // by nothing; use a fresh session to prove the gate is open).
    let token_c = harness.check("point", json!({}));
    let outcome = harness.inner().dispatch(
        "forecast",
        &json!({"session": &token_c, "confirmed": true}),
        &noop_progress,
        &AtomicBool::new(false),
    );
    assert!(outcome.is_ok());
    harness.started.stop();
}

#[test]
fn session_operation_lock_is_non_blocking() {
    let mut harness = harness();
    let token = harness.check("point", json!({}));
    *harness.backend.hold_prepare.0.lock().unwrap() = true;
    let inner = harness.inner().clone();
    let worker_token = token.clone();
    let worker = std::thread::spawn(move || {
        inner.dispatch(
            "prepare",
            &json!({"session": worker_token}),
            &noop_progress,
            &AtomicBool::new(false),
        )
    });
    // Wait until the worker acquired the op lock.
    std::thread::sleep(Duration::from_millis(100));
    assert!(!worker.is_finished(), "prepare finished too early");
    let error = dispatch_error(&harness, "prepare", json!({"session": &token}));
    assert_eq!(error, "当前数据检查仍在处理，请稍候。");
    *harness.backend.hold_prepare.0.lock().unwrap() = false;
    harness.backend.hold_prepare.1.notify_all();
    worker.join().unwrap().unwrap();
    harness.started.stop();
}

#[test]
fn unknown_methods_reuse_engine_arms() {
    let mut harness = harness();
    let error = dispatch_error(&harness, "bogus_method", json!({}));
    assert!(
        error.contains("unsupported method: bogus_method"),
        "unexpected: {error}"
    );
    harness.started.stop();
}

#[test]
fn publications_merge_is_pure_native() {
    let report = json!({
        "ready": true,
        "publication_pending": true,
        "datasets": [
            {"source": "gfs", "ready": true, "request_cycle": "2026-11-08 00Z",
             "latest_available_cycle": "2026-11-08 00Z", "latest_available_cycle_utc": null,
             "verified": false, "is_latest": false},
        ],
    });
    let publications = json!({
        "gfs": {"cycle": "2026-11-08 06Z", "label": "2026-11-08 06Z", "verified": true},
    });
    let merged = crate::backend::apply_publications(&report, &publications);
    assert_eq!(merged["publication_pending"], false);
    let row = &merged["datasets"][0];
    assert_eq!(row["latest_available_cycle"], "2026-11-08 06Z");
    assert_eq!(row["latest_available_cycle_utc"], "2026-11-08 06Z");
    assert_eq!(row["verified"], true);
    // requested cycle differs from the new label → not latest.
    assert_eq!(row["is_latest"], false);
}

#[test]
fn redaction_keeps_only_safe_event_fields() {
    let event = json!({
        "percent": 25, "stage": "download",
        "message": "/secret/token CAMS download", "model_source": "",
    });
    let safe = redact(&event, "forecast");
    assert_eq!(
        safe,
        json!({"percent": 25, "stage": "download", "source": "CAMS"})
    );
    let remapped = json!({"percent": 80, "stage": "score", "model_source": "ec"});
    assert_eq!(
        redact(&remapped, "forecast"),
        json!({"percent": 80, "stage": "score", "source": "EC"})
    );
}

// ---- wire-level tests (serve loop port) ------------------------------------

struct Wire {
    started: Started,
    writer: crate::pipe::PipeWriter,
    receiver: mpsc::Receiver<String>,
    backend: Arc<StubBackend>,
    seen: Arc<Mutex<Vec<String>>>,
}

fn wire() -> Wire {
    let backend = StubBackend::new();
    let (sender, receiver) = mpsc::channel::<String>();
    let seen = Arc::new(Mutex::new(Vec::new()));
    let seen_sink = seen.clone();
    let sink: FrameSink = Arc::new(move |frame: &str| {
        seen_sink.lock().unwrap().push(frame.to_string());
        let _ = sender.send(frame.to_string());
    });
    let started = start(
        MobileConfig {
            workspace_root: std::env::temp_dir().join("fsmobile-wire-tests"),
            ..Default::default()
        },
        backend.clone(),
        sink,
    );
    let writer = started.writer.clone();
    Wire {
        started,
        writer,
        receiver,
        backend,
        seen,
    }
}

impl Wire {
    fn send(&self, line: &str) {
        self.writer.push(format!("{line}\n").as_bytes()).unwrap();
    }

    /// Next frame that is not a progress event (progress frames are ignored).
    fn next_signal(&self) -> Value {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let frame = self
                .receiver
                .recv_timeout(Duration::from_secs(10))
                .expect("timed out waiting for a frame");
            let parsed: Value = serde_json::from_str(&frame).expect("frame must be valid JSON");
            if parsed["type"] != "progress" {
                return parsed;
            }
            assert!(Instant::now() < deadline, "no signal frame arrived");
        }
    }

    fn wait_for(&self, predicate: impl Fn() -> bool, context: &str) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !predicate() {
            assert!(Instant::now() < deadline, "{context}");
            std::thread::sleep(Duration::from_millis(5));
        }
    }
}

#[test]
fn wire_hello_over_in_memory_duplex_returns_protocol_one() {
    let mut wire = wire();
    wire.send(r#"{"id":"hello","method":"hello"}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["id"], "hello");
    assert_eq!(frame["type"], "result");
    assert_eq!(frame["result"]["protocol"], 1);
    wire.started.stop();
}

#[test]
fn wire_rejects_malformed_frames() {
    let mut wire = wire();
    wire.send("not json");
    let frame = wire.next_signal();
    assert_eq!(frame["id"], Value::Null);
    assert_eq!(frame["error"]["code"], "invalid_request");

    wire.send(r#"{"id":"","method":"hello"}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["error"]["message"], "无效请求编号。");

    wire.send(r#"{"id":"x","method":"hello","params":[]}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["id"], "x");
    assert_eq!(frame["error"]["message"], "无效参数。");

    wire.send(r#"{"id":"h","method":"hello"}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["result"]["protocol"], 1);
    wire.started.stop();
}

#[test]
fn oversized_line_is_drained_and_next_request_survives() {
    let mut wire = wire();
    let mut line = "x".repeat(MAX_LINE_BYTES + 20);
    line.push('\n');
    wire.writer.push(line.as_bytes()).unwrap();
    wire.send(r#"{"id":"next","method":"hello"}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["type"], "error");
    assert_eq!(frame["error"]["message"], "请求过大。");
    let frame = wire.next_signal();
    assert_eq!(frame["id"], "next");
    assert_eq!(frame["result"]["protocol"], 1);
    wire.started.stop();
}

#[test]
fn wire_cancel_and_progress_redaction() {
    let mut wire = wire();
    wire.send(r#"{"id":"c0","method":"check","params":{"kind":"point","payload":{}}}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["type"], "result");
    let token = frame["result"]["session"].as_str().unwrap().to_string();
    // Hold the forecast chain so the request stays active.
    *wire.backend.hold_forecast.0.lock().unwrap() = true;
    wire.send(
        &json!({"id": "a", "method": "forecast",
                "params": {"session": token, "confirmed": true}})
        .to_string(),
    );
    wire.wait_for(
        || !wire.backend.forecast_calls.lock().unwrap().is_empty(),
        "forecast never started",
    );
    wire.send(r#"{"id":"c","method":"cancel","params":{"request_id":"a"}}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["id"], "c");
    assert_eq!(frame["type"], "result");
    assert_eq!(frame["result"]["requested"], true);
    // The blocked chain observes cancellation and fails with a cancelled
    // error; the secret message is never forwarded.
    let frame = wire.next_signal();
    assert_eq!(frame["id"], "a");
    assert_eq!(frame["error"]["code"], "cancelled");
    assert!(
        !wire.seen.lock().unwrap().iter().any(|frame| frame.contains("/secret")),
        "progress redaction leaked the secret message"
    );
    wire.started.stop();
}

#[test]
fn wire_duplicate_request_id_is_rejected() {
    let mut wire = wire();
    *wire.backend.hold_forecast.0.lock().unwrap() = true;
    wire.send(r#"{"id":"c0","method":"check","params":{"kind":"point","payload":{}}}"#);
    let frame = wire.next_signal();
    let token = frame["result"]["session"].as_str().unwrap().to_string();
    wire.send(
        &json!({"id": "a", "method": "forecast",
                "params": {"session": token, "confirmed": true}})
        .to_string(),
    );
    wire.wait_for(
        || !wire.backend.forecast_calls.lock().unwrap().is_empty(),
        "forecast never started",
    );
    wire.send(r#"{"id":"a","method":"hello"}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["error"]["code"], "invalid_request");
    assert_eq!(frame["error"]["message"], "重复请求编号。");
    // Clean up: cancel the held forecast.
    wire.send(r#"{"id":"c","method":"cancel","params":{"request_id":"a"}}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["result"]["requested"], true);
    wire.started.stop();
}

#[test]
fn wire_unknown_active_cancel_reports_not_requested() {
    let mut wire = wire();
    wire.send(r#"{"id":"c","method":"cancel","params":{"request_id":"nope"}}"#);
    let frame = wire.next_signal();
    assert_eq!(frame["type"], "result");
    assert_eq!(frame["result"]["requested"], false);
    wire.started.stop();
}
