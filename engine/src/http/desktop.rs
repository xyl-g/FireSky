//! Desktop-session jobs for the browser adapter. Cancellation waits for worker exit.
use super::{ApiError, ApiResult, App, JOB_TTL, MAX_JOBS};
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Instant, SystemTime, UNIX_EPOCH},
};

static INSTANCES: AtomicU64 = AtomicU64::new(0);

#[derive(Debug)]
struct Error {
    status: StatusCode,
    code: &'static str,
    message: String,
}

impl From<ApiError> for Error {
    fn from(ApiError(status, message): ApiError) -> Self {
        Self {
            status,
            code: if matches!(
                status,
                StatusCode::BAD_REQUEST | StatusCode::NOT_FOUND | StatusCode::CONFLICT
            ) {
                "invalid_request"
            } else {
                "operation_failed"
            },
            message,
        }
    }
}

impl IntoResponse for Error {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(json!({"error":{"code":self.code,"message":self.message}})),
        )
            .into_response()
    }
}

pub(super) struct Registry {
    instance_id: String,
    jobs: Mutex<BTreeMap<String, Job>>,
}

struct Job {
    status: &'static str,
    cancel: Arc<AtomicBool>,
    completed: Option<Instant>,
    progress_seq: u64,
    progress: Option<Value>,
    result: Option<Value>,
    error: Option<Value>,
}

impl Job {
    fn new(cancel: Arc<AtomicBool>) -> Self {
        Self {
            status: "running",
            cancel,
            completed: None,
            progress_seq: 0,
            progress: None,
            result: None,
            error: None,
        }
    }
}

impl Registry {
    pub(super) fn new() -> Self {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        Self {
            instance_id: format!(
                "desktop-http-{:x}-{stamp:x}-{:x}",
                std::process::id(),
                INSTANCES.fetch_add(1, Ordering::Relaxed)
            ),
            jobs: Mutex::new(BTreeMap::new()),
        }
    }

    fn check_instance(&self, instance_id: &str) -> Result<(), Error> {
        if instance_id != self.instance_id {
            return Err(Error {
                status: StatusCode::CONFLICT,
                code: "engine_exited",
                message: "desktop runtime changed; reconnect and check data again".into(),
            });
        }
        Ok(())
    }

    fn snapshot(&self, request_id: &str) -> ApiResult {
        let mut jobs = self.jobs.lock().unwrap();
        prune(&mut jobs);
        let job = jobs.get(request_id).ok_or_else(missing_job)?;
        let mut value = json!({
            "request_id":request_id,"instance_id":self.instance_id,
            "status":job.status,"progress_seq":job.progress_seq,
        });
        if let Some(progress) = &job.progress {
            value["progress"] = progress.clone();
        }
        if job.status == "done" {
            value["result"] = job.result.clone().unwrap_or(Value::Null);
        }
        if let Some(error) = &job.error {
            value["error"] = error.clone();
        }
        Ok(Json(value))
    }

    #[cfg(test)]
    fn request_cancel(&self, request_id: &str) -> ApiResult {
        self.cancel_with(request_id, || Ok(()))
    }

    fn cancel_with(
        &self,
        request_id: &str,
        persist: impl FnOnce() -> Result<(), String>,
    ) -> ApiResult {
        let mut jobs = self.jobs.lock().unwrap();
        prune(&mut jobs);
        let job = jobs.get_mut(request_id).ok_or_else(missing_job)?;
        let requested = job.completed.is_none();
        if requested {
            job.cancel.store(true, Ordering::Release);
            persist().map_err(|error| ApiError(StatusCode::INTERNAL_SERVER_ERROR, error))?;
        }
        Ok(Json(json!({"requested":requested})))
    }

    fn release(&self, request_id: &str) -> ApiResult {
        let mut jobs = self.jobs.lock().unwrap();
        prune(&mut jobs);
        if jobs
            .get(request_id)
            .is_some_and(|job| job.completed.is_none())
        {
            return Err(ApiError(
                StatusCode::CONFLICT,
                "desktop job is still running".into(),
            ));
        }
        Ok(Json(json!({"released":jobs.remove(request_id).is_some()})))
    }

    fn progress(&self, request_id: &str, progress: Value) -> Result<(), String> {
        let mut jobs = self.jobs.lock().unwrap();
        let job = jobs
            .get_mut(request_id)
            .ok_or("desktop HTTP job disappeared")?;
        if job.cancel.load(Ordering::Acquire) {
            return Err("desktop operation cancelled".into());
        }
        if job.completed.is_none() {
            job.progress_seq = job.progress_seq.saturating_add(1);
            job.progress = Some(progress);
        }
        Ok(())
    }

    fn finish(
        &self,
        request_id: &str,
        mut outcome: Result<Value, String>,
        task: Option<crate::task_history::Task>,
    ) {
        let mut jobs = self.jobs.lock().unwrap();
        if let Some(job) = jobs.get_mut(request_id) {
            let cancelled = job.cancel.load(Ordering::Acquire);
            if let Some(task) = task {
                if let Err(error) = task.finish(&outcome, cancelled) {
                    outcome = Err(format!("task history commit failed: {error}"));
                }
            }
            if cancelled {
                job.status = "cancelled";
                job.error = Some(json!({
                    "code":"cancelled","message":"desktop operation cancelled",
                }));
            } else {
                match outcome {
                    Ok(result) => {
                        job.status = "done";
                        job.result = Some(result);
                    }
                    Err(message) => {
                        job.status = "failed";
                        job.error = Some(json!({"code":"operation_failed","message":message}));
                    }
                }
            }
            job.completed = Some(Instant::now());
        }
    }
}

fn prune(jobs: &mut BTreeMap<String, Job>) {
    jobs.retain(|_, job| job.completed.is_none_or(|done| done.elapsed() <= JOB_TTL));
}

fn missing_job() -> ApiError {
    ApiError(
        StatusCode::NOT_FOUND,
        "desktop job not found or expired".into(),
    )
}

fn allowed_method(method: &str) -> bool {
    matches!(
        method,
        "hello"
            | "search"
            | "metadata"
            | "inventory"
            | "check"
            | "prepare"
            | "forecast"
            | "publications"
            | "dismiss"
            | "network_probe"
            | "cams_credentials"
            | "retention_settings"
            | "save_retention_settings"
            | "profile_query"
            | "task_history"
            | "task_events"
            | "task_forget"
            | "result_restore"
    )
}

fn valid_request_id(id: &str) -> bool {
    id.len() == 36
        && id.bytes().enumerate().all(|(index, byte)| {
            if matches!(index, 8 | 13 | 18 | 23) {
                byte == b'-'
            } else {
                byte.is_ascii_hexdigit()
            }
        })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Start {
    request_id: String,
    instance_id: String,
    method: String,
    params: Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Instance {
    instance_id: String,
}

async fn status(State(app): State<App>) -> Json<Value> {
    Json(json!({
        "instance_id":app.desktop_jobs.instance_id,"running":true,
        "workspace":app.workspace.as_ref(),"protocol":1,"engine":"firesky-rust",
    }))
}

async fn start(
    State(app): State<App>,
    Json(request): Json<Start>,
) -> Result<(StatusCode, Json<Value>), Error> {
    app.desktop_jobs.check_instance(&request.instance_id)?;
    if !valid_request_id(&request.request_id) {
        return Err(ApiError(
            StatusCode::BAD_REQUEST,
            "request_id must be a UUID string".into(),
        )
        .into());
    }
    if !allowed_method(&request.method) {
        return Err(ApiError(
            StatusCode::NOT_FOUND,
            "desktop workflow method is not available over HTTP".into(),
        )
        .into());
    }
    if !request.params.is_object() {
        return Err(ApiError(
            StatusCode::BAD_REQUEST,
            "desktop params must be an object".into(),
        )
        .into());
    }
    let mut jobs = app.desktop_jobs.jobs.lock().unwrap();
    prune(&mut jobs);
    if jobs.contains_key(&request.request_id) {
        return Err(ApiError(
            StatusCode::CONFLICT,
            "desktop request_id already exists".into(),
        )
        .into());
    }
    if jobs.len() >= MAX_JOBS {
        return Err(ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "desktop job registry is full".into(),
        )
        .into());
    }
    let permit = app.slots.clone().try_acquire_owned().map_err(|_| {
        ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "native workers are busy".into(),
        )
    })?;
    let cancel = Arc::new(AtomicBool::new(false));
    let task = app
        .runtime
        .begin_task(&request.request_id, &request.method, &request.params)
        .map_err(|message| ApiError(StatusCode::INTERNAL_SERVER_ERROR, message))?;
    let task_id = task.as_ref().map(|task| task.id().to_owned());
    jobs.insert(request.request_id.clone(), Job::new(cancel.clone()));
    drop(jobs);
    let request_id = request.request_id;
    let response = json!({
        "request_id":request_id,"instance_id":app.desktop_jobs.instance_id,"status":"running",
        "task_id":task_id,
    });
    let job_task_id = task_id.clone();
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        run_tracked_job(&app.desktop_jobs, &request_id, task, |progress| {
            app.runtime.dispatch_cancellable(
                &request.method,
                request.params,
                cancel,
                progress,
                job_task_id.as_deref(),
            )
        });
    });
    Ok((StatusCode::ACCEPTED, Json(response)))
}

#[cfg(test)]
fn run_job(
    registry: &Registry,
    request_id: &str,
    work: impl FnOnce(&(dyn Fn(Value) -> Result<(), String> + Sync)) -> Result<Value, String>,
) {
    run_tracked_job(registry, request_id, None, work);
}

fn run_tracked_job(
    registry: &Registry,
    request_id: &str,
    task: Option<crate::task_history::Task>,
    work: impl FnOnce(&(dyn Fn(Value) -> Result<(), String> + Sync)) -> Result<Value, String>,
) {
    let progress = |value: Value| {
        if let Some(task) = &task {
            task.progress(&value)?;
        }
        registry.progress(request_id, value)
    };
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| work(&progress)))
        .unwrap_or_else(|_| Err("native desktop worker panicked".into()));
    registry.finish(request_id, outcome, task);
}

async fn detail(
    State(app): State<App>,
    Path(request_id): Path<String>,
    Query(instance): Query<Instance>,
) -> Result<Json<Value>, Error> {
    app.desktop_jobs.check_instance(&instance.instance_id)?;
    app.desktop_jobs.snapshot(&request_id).map_err(Error::from)
}

async fn cancel(
    State(app): State<App>,
    Path(request_id): Path<String>,
    Json(instance): Json<Instance>,
) -> Result<Json<Value>, Error> {
    app.desktop_jobs.check_instance(&instance.instance_id)?;
    app.desktop_jobs
        .cancel_with(&request_id, || app.runtime.cancel_history(&request_id))
        .map_err(Error::from)
}

async fn release(
    State(app): State<App>,
    Path(request_id): Path<String>,
    Json(instance): Json<Instance>,
) -> Result<Json<Value>, Error> {
    app.desktop_jobs.check_instance(&instance.instance_id)?;
    app.desktop_jobs.release(&request_id).map_err(Error::from)
}

pub(super) fn routes() -> Router<App> {
    Router::new()
        .route("/desktop/status", get(status))
        .route("/desktop/jobs", post(start))
        .route("/desktop/jobs/{request_id}", get(detail))
        .route("/desktop/jobs/{request_id}/cancel", post(cancel))
        .route("/desktop/jobs/{request_id}/release", post(release))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        body::{to_bytes, Body},
        http::Request,
    };
    use std::sync::mpsc;
    use tower::ServiceExt;

    const ID: &str = "00000000-0000-4000-8000-000000000001";

    fn app() -> (tempfile::TempDir, Router) {
        let dir = tempfile::tempdir().unwrap();
        let runtime = Arc::new(crate::desktop_runtime::Runtime::new(dir.path()).unwrap());
        let router = super::super::router(dir.path(), runtime);
        (dir, router)
    }

    async fn request(
        app: &Router,
        method: &str,
        path: &str,
        value: Option<Value>,
    ) -> (StatusCode, Value) {
        let mut request = Request::builder()
            .method(method)
            .uri(format!("{}{path}", super::super::PREFIX));
        let body = if let Some(value) = value {
            request = request.header("content-type", "application/json");
            Body::from(value.to_string())
        } else {
            Body::empty()
        };
        let response = app
            .clone()
            .oneshot(request.body(body).unwrap())
            .await
            .unwrap();
        let status = response.status();
        let bytes = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
        let value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
        (status, value)
    }

    async fn instance(app: &Router) -> String {
        let (status, value) = request(app, "GET", "/desktop/status", None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(value["running"], true);
        assert_eq!(value["engine"], "firesky-rust");
        value["instance_id"].as_str().unwrap().to_owned()
    }

    async fn terminal(app: &Router, id: &str, instance: &str) -> Value {
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            loop {
                let (status, row) = request(
                    app,
                    "GET",
                    &format!("/desktop/jobs/{id}?instance_id={instance}"),
                    None,
                )
                .await;
                assert_eq!(status, StatusCode::OK);
                if row["status"] != "running" {
                    break row;
                }
                tokio::time::sleep(std::time::Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap()
    }

    #[tokio::test]
    async fn workflow_receipt_survives_release_and_runtime_restart_without_a_session_token() {
        let (dir, app) = app();
        let first_instance = instance(&app).await;
        let (status, ticket) = request(&app, "POST", "/desktop/jobs", Some(json!({
            "request_id":ID,"instance_id":first_instance,"method":"check",
            "params":{"kind":"point","payload":{"lat":91,"lon":120,"target_date":"invalid","api_key":"private"}}
        }))).await;
        assert_eq!(status, StatusCode::ACCEPTED);
        let task_id = ticket["task_id"].as_str().unwrap();
        assert_eq!(
            terminal(&app, ID, &first_instance).await["status"],
            "failed"
        );
        assert_eq!(
            request(
                &app,
                "POST",
                &format!("/desktop/jobs/{ID}/release"),
                Some(json!({"instance_id":first_instance}))
            )
            .await
            .1["released"],
            true
        );
        drop(app);
        let runtime = Arc::new(crate::desktop_runtime::Runtime::new(dir.path()).unwrap());
        let restarted = super::super::router(dir.path(), runtime);
        let next_instance = instance(&restarted).await;
        assert_ne!(first_instance, next_instance);
        let id = "00000000-0000-4000-8000-000000000002";
        let (status, _) = request(&restarted, "POST", "/desktop/jobs", Some(json!({
            "request_id":id,"instance_id":next_instance,"method":"task_history","params":{"limit":10}
        }))).await;
        assert_eq!(status, StatusCode::ACCEPTED);
        let row = terminal(&restarted, id, &next_instance).await;
        assert_eq!(row["status"], "done");
        assert_eq!(row["result"]["tasks"][0]["task_id"], task_id);
        assert_eq!(row["result"]["tasks"][0]["status"], "failed");
        assert_eq!(
            row["result"]["tasks"][0]["retry_request"]["payload"]["lat"],
            91
        );
        assert!(!row["result"].to_string().contains("private"));
        assert!(row["result"]["tasks"][0].get("session").is_none());
        let id = "00000000-0000-4000-8000-000000000003";
        request(
            &restarted,
            "POST",
            "/desktop/jobs",
            Some(json!({
                "request_id":id,"instance_id":next_instance,"method":"task_events",
                "params":{"task_id":task_id,"after_seq":1}
            })),
        )
        .await;
        let events = terminal(&restarted, id, &next_instance).await;
        assert_eq!(events["result"]["events"][0]["event"], "failed");
        assert_eq!(events["result"]["status"], "failed");
    }

    #[tokio::test]
    async fn empty_cache_status_hello_inventory_and_instance_changes() {
        let (_dir, app) = app();
        let instance = instance(&app).await;
        for (offset, method) in ["hello", "inventory"].iter().enumerate() {
            let id = format!("00000000-0000-4000-8000-{:012x}", offset + 1);
            let (status, ticket) = request(
                &app,
                "POST",
                "/desktop/jobs",
                Some(json!({
                    "request_id":id,"instance_id":instance,"method":method,"params":{},
                })),
            )
            .await;
            assert_eq!(status, StatusCode::ACCEPTED);
            assert_eq!(ticket["request_id"], id);
            let path = format!("/desktop/jobs/{id}?instance_id={instance}");
            let value = tokio::time::timeout(std::time::Duration::from_secs(5), async {
                loop {
                    let (status, value) = request(&app, "GET", &path, None).await;
                    assert_eq!(status, StatusCode::OK);
                    if value["status"] != "running" {
                        break value;
                    }
                    tokio::time::sleep(std::time::Duration::from_millis(5)).await;
                }
            })
            .await
            .unwrap();
            assert_eq!(value["status"], "done", "{value}");
            assert!(value.get("params").is_none());
            if *method == "hello" {
                assert_eq!(value["result"]["protocol"], 1);
                assert_eq!(value["result"]["runtime_python_used"], false);
            }
            let (_, cancelled) = request(
                &app,
                "POST",
                &format!("/desktop/jobs/{id}/cancel"),
                Some(json!({"instance_id":instance})),
            )
            .await;
            assert_eq!(cancelled["requested"], false);
        }
        let other = Registry::new();
        assert_ne!(other.instance_id, instance);
    }

    #[tokio::test]
    async fn rejects_unknown_methods_bad_params_ids_instances_and_duplicates() {
        let (_dir, app) = app();
        let instance = instance(&app).await;
        let base = json!({"request_id":ID,"instance_id":instance,"method":"hello","params":{}});
        for (field, value, expected) in [
            ("instance_id", json!("old-runtime"), StatusCode::CONFLICT),
            ("request_id", json!("../request"), StatusCode::BAD_REQUEST),
            ("params", json!([]), StatusCode::BAD_REQUEST),
            ("method", json!("save_cams_key"), StatusCode::NOT_FOUND),
            ("method", json!("arbitrary_method"), StatusCode::NOT_FOUND),
        ] {
            let mut body = base.clone();
            body[field] = value;
            let (status, _) = request(&app, "POST", "/desktop/jobs", Some(body)).await;
            assert_eq!(status, expected, "{field}");
        }
        assert_eq!(
            request(&app, "POST", "/desktop/jobs", Some(base.clone()))
                .await
                .0,
            StatusCode::ACCEPTED
        );
        assert_eq!(
            request(&app, "POST", "/desktop/jobs", Some(base)).await.0,
            StatusCode::CONFLICT
        );
        for (method, path, body) in [
            ("GET", format!("/desktop/jobs/{ID}?instance_id=old"), None),
            (
                "POST",
                format!("/desktop/jobs/{ID}/cancel"),
                Some(json!({"instance_id":"old"})),
            ),
            (
                "POST",
                format!("/desktop/jobs/{ID}/release"),
                Some(json!({"instance_id":"old"})),
            ),
        ] {
            let (status, value) = request(&app, method, &path, body).await;
            assert_eq!(status, StatusCode::CONFLICT);
            assert_eq!(value["error"]["code"], "engine_exited");
        }
        let missing = "ffffffff-ffff-ffff-ffff-ffffffffffff";
        for (method, path, body) in [
            (
                "GET",
                format!("/desktop/jobs/{missing}?instance_id={instance}"),
                None,
            ),
            (
                "POST",
                format!("/desktop/jobs/{missing}/cancel"),
                Some(json!({"instance_id":instance})),
            ),
        ] {
            assert_eq!(
                request(&app, method, &path, body).await.0,
                StatusCode::NOT_FOUND
            );
        }
    }

    #[test]
    fn cancellation_waits_for_worker_exit_and_keeps_real_progress() {
        let registry = Arc::new(Registry::new());
        let cancel = Arc::new(AtomicBool::new(false));
        registry
            .jobs
            .lock()
            .unwrap()
            .insert(ID.into(), Job::new(cancel.clone()));
        let (entered_tx, entered_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let slots = Arc::new(tokio::sync::Semaphore::new(1));
        let permit = slots.clone().try_acquire_owned().unwrap();
        let worker_registry = registry.clone();
        let thread = std::thread::spawn(move || {
            let _permit = permit;
            run_job(&worker_registry, ID, |progress| {
                progress(json!({"stage":"fixture","percent":17}))?;
                entered_tx.send(()).unwrap();
                release_rx.recv().unwrap();
                Ok(json!({"late":"success"}))
            });
        });
        entered_rx.recv().unwrap();
        assert_eq!(registry.request_cancel(ID).unwrap().0["requested"], true);
        assert_eq!(registry.release(ID).unwrap_err().0, StatusCode::CONFLICT);
        assert!(cancel.load(Ordering::Acquire));
        let running = registry.snapshot(ID).unwrap().0;
        assert_eq!(running["status"], "running");
        assert_eq!(running["progress_seq"], 1);
        assert_eq!(running["progress"]["percent"], 17);
        assert!(running.get("result").is_none());
        assert_eq!(slots.available_permits(), 0);
        assert!(registry.progress(ID, json!({"percent":99})).is_err());
        release_tx.send(()).unwrap();
        thread.join().unwrap();
        assert_eq!(slots.available_permits(), 1);
        let finished = registry.snapshot(ID).unwrap().0;
        assert_eq!(finished["status"], "cancelled");
        assert_eq!(finished["error"]["code"], "cancelled");
        assert!(finished.get("result").is_none());
        assert_eq!(registry.request_cancel(ID).unwrap().0["requested"], false);
    }

    #[tokio::test]
    async fn consumed_short_jobs_release_capacity_and_release_is_idempotent() {
        let (_dir, app) = app();
        let instance = instance(&app).await;
        for index in 0..MAX_JOBS + 8 {
            let id = format!("00000000-0000-4000-8000-{index:012x}");
            let (status, _) = request(
                &app,
                "POST",
                "/desktop/jobs",
                Some(json!({
                    "request_id":id,"instance_id":instance,"method":"hello","params":{},
                })),
            )
            .await;
            assert_eq!(status, StatusCode::ACCEPTED);
            tokio::time::timeout(std::time::Duration::from_secs(5), async {
                loop {
                    let (_, value) = request(
                        &app,
                        "GET",
                        &format!("/desktop/jobs/{id}?instance_id={instance}"),
                        None,
                    )
                    .await;
                    if value["status"] != "running" {
                        assert_eq!(value["status"], "done");
                        break;
                    }
                    tokio::time::sleep(std::time::Duration::from_millis(2)).await;
                }
            })
            .await
            .unwrap();
            for expected in [true, false] {
                let (status, value) = request(
                    &app,
                    "POST",
                    &format!("/desktop/jobs/{id}/release"),
                    Some(json!({"instance_id":instance})),
                )
                .await;
                assert_eq!(status, StatusCode::OK);
                assert_eq!(value["released"], expected);
            }
        }
    }

    #[test]
    fn terminal_ttl_is_measured_from_completion_and_panics_finish() {
        let registry = Registry::new();
        let cancel = Arc::new(AtomicBool::new(false));
        let mut old = Job::new(cancel.clone());
        old.completed = Some(Instant::now() - JOB_TTL - std::time::Duration::from_secs(1));
        let mut recent = Job::new(cancel.clone());
        recent.completed = Some(Instant::now());
        let mut jobs = registry.jobs.lock().unwrap();
        jobs.insert("old".into(), old);
        jobs.insert("recent".into(), recent);
        jobs.insert(ID.into(), Job::new(cancel));
        prune(&mut jobs);
        assert!(!jobs.contains_key("old"));
        assert!(jobs.contains_key("recent"));
        assert!(jobs.contains_key(ID));
        drop(jobs);
        run_job(&registry, ID, |_| panic!("fixture worker panic"));
        let value = registry.snapshot(ID).unwrap().0;
        assert_eq!(value["status"], "failed");
        assert_eq!(value["error"]["code"], "operation_failed");
        assert_eq!(value["error"]["message"], "native desktop worker panicked");
    }

    #[tokio::test]
    async fn registry_limit_does_not_remove_active_jobs() {
        let dir = tempfile::tempdir().unwrap();
        let registry = Arc::new(Registry::new());
        for index in 0..MAX_JOBS {
            registry.jobs.lock().unwrap().insert(
                format!("00000000-0000-4000-8000-{index:012x}"),
                Job::new(Arc::new(AtomicBool::new(false))),
            );
        }
        let state = App {
            runtime: Arc::new(crate::desktop_runtime::Runtime::new(dir.path()).unwrap()),
            workspace: Arc::new(dir.path().to_owned()),
            slots: Arc::new(tokio::sync::Semaphore::new(4)),
            jobs: Arc::new(Mutex::new(BTreeMap::new())),
            desktop_jobs: registry.clone(),
        };
        let error = start(
            State(state),
            Json(Start {
                request_id: "ffffffff-ffff-ffff-ffff-ffffffffffff".into(),
                instance_id: registry.instance_id.clone(),
                method: "hello".into(),
                params: json!({}),
            }),
        )
        .await
        .unwrap_err();
        assert_eq!(error.status, StatusCode::TOO_MANY_REQUESTS);
        assert_eq!(registry.jobs.lock().unwrap().len(), MAX_JOBS);
    }
}
