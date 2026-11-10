//! Private native-client IPC for location and read-only data inventory.
use crate::{
    desktop_inventory, location, request_file,
    rpc::{self, Line},
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    io::{self, BufRead, Write},
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
};

#[derive(Clone, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    location: location::Config,
    inventory: desktop_inventory::Config,
    allow_download: bool,
    workers: usize,
    max_active_requests: usize,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            location: location::Config::default(),
            inventory: desktop_inventory::Config::default(),
            allow_download: true,
            workers: 4,
            max_active_requests: 16,
        }
    }
}
impl Settings {
    pub fn load(path: &Path) -> Result<Self, String> {
        let (settings, _): (Self, _) = request_file::read(path, 64 * 1024)?;
        Ok(settings)
    }
    fn validate(&mut self) -> Result<(), String> {
        if !(1..=8).contains(&self.workers)
            || !(self.workers..=64).contains(&self.max_active_requests)
        {
            return Err("desktop IPC requires 1..8 workers and workers..64 active requests".into());
        }
        self.inventory.validate()?;
        self.location.validate()
    }
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
fn result(id: &str, value: Value) -> Value {
    json!({"id":id,"type":"result","result":value})
}
fn send(output: &Mutex<impl Write>, message: &Value) -> io::Result<()> {
    let mut output = output
        .lock()
        .map_err(|_| io::Error::other("IPC output lock poisoned"))?;
    serde_json::to_writer(&mut *output, message)?;
    output.write_all(b"\n")?;
    output.flush()
}
struct Job {
    request: Request,
    cancellation: Arc<AtomicBool>,
}
type Active = Mutex<BTreeMap<String, Arc<AtomicBool>>>;

pub fn serve(
    mut input: impl BufRead,
    output: impl Write + Send,
    mut settings: Settings,
) -> io::Result<()> {
    settings.validate().map_err(io::Error::other)?;
    let output = Mutex::new(output);
    let active = Active::default();
    let (sender, receiver) = mpsc::sync_channel::<Job>(settings.max_active_requests);
    let receiver = Mutex::new(receiver);
    thread::scope(|scope| {
        let mut handles = Vec::new();
        for _ in 0..settings.workers {
            let (receiver, active, output, settings) = (&receiver, &active, &output, &settings);
            handles.push(scope.spawn(move || -> io::Result<()> {
                loop {
                    let job = receiver
                        .lock()
                        .map_err(|_| io::Error::other("IPC queue poisoned"))?
                        .recv();
                    let Ok(Job {
                        request,
                        cancellation,
                    }) = job
                    else {
                        return Ok(());
                    };
                    let value = if cancellation.load(Ordering::Acquire) {
                        Err("desktop operation cancelled".into())
                    } else if request.method == "inventory" {
                        desktop_inventory::execute(&settings.inventory, cancellation.clone())
                    } else {
                        location::desktop_dispatch(
                            &request.method,
                            &request.params,
                            &settings.location,
                            settings.allow_download,
                            cancellation.clone(),
                        )
                    };
                    // Holding this lock linearizes completion versus cancel/ID reuse.
                    let mut active = active
                        .lock()
                        .map_err(|_| io::Error::other("IPC state poisoned"))?;
                    let message = if cancellation.load(Ordering::Acquire) {
                        error(
                            json!(request.id),
                            "cancelled",
                            "desktop operation cancelled",
                        )
                    } else {
                        match value {
                            Ok(value) => result(&request.id, value),
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
                let parsed = serde_json::from_slice::<Value>(&line);
                let id = parsed
                    .as_ref()
                    .ok()
                    .and_then(|v| v.get("id"))
                    .filter(|v| v.as_str().is_some_and(|s| !s.is_empty() && s.len() <= 128))
                    .cloned()
                    .unwrap_or(Value::Null);
                let request = parsed
                    .ok()
                    .and_then(|v| serde_json::from_value::<Request>(v).ok());
                let Some(request) = request.filter(|r| {
                    !r.id.is_empty()
                        && r.id.len() <= 128
                        && !r.method.is_empty()
                        && r.method.len() <= 64
                        && r.params.is_object()
                }) else {
                    send(
                        &output,
                        &error(
                            id,
                            "invalid_request",
                            "expected id, method and object params",
                        ),
                    )?;
                    continue;
                };
                let mut state = active
                    .lock()
                    .map_err(|_| io::Error::other("IPC state poisoned"))?;
                if state.contains_key(&request.id) {
                    send(
                        &output,
                        &error(
                            json!(request.id),
                            "invalid_request",
                            "duplicate active request id",
                        ),
                    )?;
                } else if request.method == "cancel" {
                    let target = request.params.get("request_id").and_then(Value::as_str);
                    let cancellation = target.and_then(|id| state.get(id));
                    if let Some(cancel) = cancellation {
                        cancel.store(true, Ordering::Release);
                    }
                    send(
                        &output,
                        &result(&request.id, json!({"requested":cancellation.is_some()})),
                    )?;
                } else if request.method == "hello" {
                    send(
                        &output,
                        &result(
                            &request.id,
                            json!({
                                "protocol":1,"engine":"firesky-rust-location",
                                "capabilities":["search","metadata","inventory","cancel"],
                                "runtime_python_used":false,"production_desktop_complete":false,
                            }),
                        ),
                    )?;
                } else if !matches!(request.method.as_str(), "search" | "metadata" | "inventory") {
                    send(
                        &output,
                        &error(
                            json!(request.id),
                            "unsupported_method",
                            "this process implements desktop location and inventory methods only",
                        ),
                    )?;
                } else if state.len() >= settings.max_active_requests {
                    send(
                        &output,
                        &error(
                            json!(request.id),
                            "busy",
                            "too many active desktop requests",
                        ),
                    )?;
                } else {
                    let cancellation = Arc::new(AtomicBool::new(false));
                    state.insert(request.id.clone(), cancellation.clone());
                    if let Err(failed) = sender.try_send(Job {
                        request,
                        cancellation,
                    }) {
                        let (mpsc::TrySendError::Full(job) | mpsc::TrySendError::Disconnected(job)) =
                            failed;
                        state.remove(&job.request.id);
                        send(
                            &output,
                            &error(json!(job.request.id), "busy", "desktop queue unavailable"),
                        )?;
                    }
                }
            }
            Ok(())
        })();
        {
            let state = active
                .lock()
                .map_err(|_| io::Error::other("IPC state poisoned"))?;
            for cancel in state.values() {
                cancel.store(true, Ordering::Release);
            }
        }
        drop(sender);
        let mut completion = Ok(());
        for handle in handles {
            match handle.join() {
                Ok(Ok(())) => {}
                Ok(Err(error)) => completion = Err(error),
                Err(_) => completion = Err(io::Error::other("desktop worker panicked")),
            }
        }
        reading.and(completion)
    })
}
