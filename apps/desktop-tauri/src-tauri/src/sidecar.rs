//! Private JSON-lines engine bridge. A generation owns its pipes, pending replies and child.
//! Start acknowledges registration AND a flushed request frame; wait consumes its ticket.
//! No task timeout: cancellation is cooperative. Only process cleanup has a grace period.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex, Weak};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};

pub const PROGRESS_EVENT: &str = "engine-progress";
pub const STATUS_EVENT: &str = "engine-status";
pub const MAX_LINE_BYTES: usize = 1024 * 1024;
pub const SHUTDOWN_GRACE: Duration = Duration::from_secs(3);
// Bounds abandoned start tickets too. A caller must consume each ticket with rpc_wait.
const MAX_TICKETS: usize = 1024;
type Emit = Arc<dyn Fn(&str, Value) + Send + Sync>;
type Reply = Result<Value, RpcError>;

#[derive(Serialize, Clone, Debug)]
pub struct RpcError {
    pub code: String,
    pub message: String,
}
impl RpcError {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}
fn exited() -> RpcError {
    RpcError::new("engine_exited", "引擎已退出，请重新连接。")
}
fn protocol_error(message: impl Into<String>) -> RpcError {
    RpcError::new("operation_failed", message)
}

#[derive(Clone, Serialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct RequestTicket {
    pub request_id: String,
    pub generation: u64,
}
struct Ticket {
    generation: u64,
    receiver: Receiver<Reply>,
}
struct IoState {
    pending: HashMap<String, Sender<Reply>>,
    closed: bool,
}
struct Session {
    generation: u64,
    io: Mutex<IoState>,
    // Serializes complete frames, but never blocks failure/cleanup of pending work.
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
}
impl Session {
    fn start(&self, id: &str, bytes: &[u8], sender: Sender<Reply>) -> Result<(), RpcError> {
        {
            // EOF and registration share this lock. No sender can be inserted
            // just after terminal failure drained a dead generation's map.
            let mut io = self.io.lock().unwrap();
            if io.closed {
                return Err(exited());
            }
            if io.pending.contains_key(id) {
                return Err(RpcError::new("invalid_request", "请求编号仍在执行。"));
            }
            io.pending.insert(id.to_owned(), sender);
        }
        let mut pipe = self.stdin.lock().unwrap();
        if !self.running() {
            return Err(exited());
        }
        let result = pipe.as_mut().ok_or_else(exited).and_then(|stdin| {
            stdin
                .write_all(bytes)
                .and_then(|_| stdin.flush())
                .map_err(|_| exited())
        });
        if result.is_err() {
            self.io.lock().unwrap().pending.remove(id);
        }
        result
    }

    fn close_stdin(&self) {
        // A full pipe may have a blocked writer. Do not wait for its lock before
        // starting bounded child cleanup; killing the direct child unblocks it.
        if let Ok(mut pipe) = self.stdin.try_lock() {
            drop(pipe.take());
        }
    }

    fn close(&self, error: RpcError) {
        {
            let mut io = self.io.lock().unwrap();
            io.closed = true;
            for (_, sender) in io.pending.drain() {
                let _ = sender.send(Err(error.clone()));
            }
        }
        self.close_stdin(); // EOF first whenever no write is in flight.
    }

    fn running(&self) -> bool {
        !self.io.lock().unwrap().closed
    }

    fn reap(&self, grace: Duration) {
        // Keep this lock until reaping completes: restart must not race a reader's
        // cleanup or leave the previous direct child alive. No descendant-tree claim.
        let mut guard = self.child.lock().unwrap();
        if let Some(mut child) = guard.take() {
            cleanup_child_with(&mut child, grace, || self.close_stdin());
            self.close_stdin();
        }
    }
}

#[derive(Clone, Serialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct EngineStatus {
    pub running: bool,
    pub workspace: Option<String>,
    pub python: Option<String>,
    pub engine_binary: Option<String>,
    pub spawn_error: Option<String>,
    pub generation: u64,
}
struct State {
    session: Option<Arc<Session>>,
    status: EngineStatus,
}
struct Core {
    // Serializes spawn/restart/shutdown, not long-running RPC work.
    lifecycle: Mutex<()>,
    state: Mutex<State>,
    tickets: Mutex<HashMap<String, Ticket>>,
    emit: Emit,
}
impl Core {
    fn failed(&self, session: &Session, error: RpcError) {
        session.close(error.clone());
        let mut state = self.state.lock().unwrap();
        if state.status.generation == session.generation && state.status.running {
            state.status.running = false;
            state.status.spawn_error = Some(error.message);
            // Emit under the generation lock so stale readers cannot publish after restart.
            (self.emit)(STATUS_EVENT, json!(state.status));
        }
    }

    fn progress(&self, session: &Session, id: &str, event: Value) {
        let state = self.state.lock().unwrap();
        if state.status.generation == session.generation
            && state.status.running
            && session.io.lock().unwrap().pending.contains_key(id)
        {
            (self.emit)(PROGRESS_EVENT, json!({"requestId": id, "event": event}));
        }
    }
}

pub struct EngineHandle {
    core: Arc<Core>,
    resources: Option<PathBuf>,
    default_workspace: Option<PathBuf>,
}
impl EngineHandle {
    pub fn new(app: AppHandle) -> Self {
        let resources = app.path().resource_dir().ok();
        let default_workspace = app
            .path()
            .app_local_data_dir()
            .ok()
            .map(|path| path.join("Data"));
        let mut handle = Self::with_emitter(Arc::new(move |name, payload| {
            let _ = app.emit(name, payload);
        }));
        handle.resources = resources;
        handle.default_workspace = default_workspace;
        handle
    }
    fn with_emitter(emit: Emit) -> Self {
        Self {
            core: Arc::new(Core {
                lifecycle: Mutex::new(()),
                state: Mutex::new(State {
                    session: None,
                    status: EngineStatus {
                        running: false,
                        workspace: None,
                        python: None,
                        engine_binary: None,
                        spawn_error: None,
                        generation: 0,
                    },
                }),
                tickets: Mutex::new(HashMap::new()),
                emit,
            }),
            resources: None,
            default_workspace: None,
        }
    }

    pub fn spawn(&self) -> Result<(), String> {
        let _guard = self.core.lifecycle.lock().unwrap();
        self.spawn_locked()
    }

    fn spawn_locked(&self) -> Result<(), String> {
        let result = (|| {
            let workspace = resolve_workspace(self.default_workspace.as_deref())?;
            let explicit = std::env::var_os("FIRESKY_RUST_ENGINE_BIN").filter(|v| !v.is_empty());
            let executable = std::env::current_exe().ok();
            let binary = select_engine(
                &workspace,
                self.resources.as_deref(),
                executable.as_deref().and_then(Path::parent),
                explicit.as_deref().map(Path::new),
            )?;
            let mut command = native_command(&binary, &workspace, self.resources.as_deref())?;
            self.launch(
                &mut command,
                workspace.display().to_string(),
                binary.display().to_string(),
            )
        })();
        if let Err(message) = &result {
            self.spawn_failed(message.clone());
        }
        result
    }

    fn spawn_failed(&self, message: String) {
        let mut state = self.core.state.lock().unwrap();
        state.status.running = false;
        state.status.spawn_error = Some(message);
        (self.core.emit)(STATUS_EVENT, json!(state.status));
    }

    // Not an IPC command or an externally configurable recipe. Production calls
    // this ONLY with the validated fixed `firesky-engine desktop` above.
    fn launch(
        &self,
        command: &mut Command,
        workspace: String,
        engine_binary: String,
    ) -> Result<(), String> {
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command
            .spawn()
            .map_err(|e| format!("无法启动引擎进程：{e}"))?;
        let pipes = (child.stdin.take(), child.stdout.take(), child.stderr.take());
        let (Some(stdin), Some(stdout), Some(stderr)) = pipes else {
            cleanup_child(&mut child, Duration::ZERO);
            return Err("无法接管引擎管道。".into());
        };
        if let Err(error) = spawn_stderr_logger(stderr, resolve_log_path()) {
            drop(stdin);
            cleanup_child(&mut child, SHUTDOWN_GRACE);
            return Err(error);
        }
        let session = {
            let mut state = self.core.state.lock().unwrap();
            let generation = state.status.generation + 1;
            let session = Arc::new(Session {
                generation,
                io: Mutex::new(IoState {
                    pending: HashMap::new(),
                    closed: false,
                }),
                stdin: Mutex::new(Some(stdin)),
                child: Mutex::new(Some(child)),
            });
            state.status = EngineStatus {
                generation,
                running: true,
                workspace: Some(workspace),
                python: None,
                engine_binary: Some(engine_binary),
                spawn_error: None,
            };
            state.session = Some(Arc::clone(&session));
            // Fully publish BEFORE starting the reader. A fast EOF cannot be overwritten.
            (self.core.emit)(STATUS_EVENT, json!(state.status));
            session
        };
        let reader_session = Arc::clone(&session);
        let core = Arc::downgrade(&self.core);
        if let Err(error) = spawn_reader(reader_session, core, stdout) {
            self.core.failed(&session, exited());
            session.reap(SHUTDOWN_GRACE);
            return Err(format!("无法启动引擎读取线程：{error}"));
        }
        Ok(())
    }

    pub fn status(&self) -> EngineStatus {
        self.core.state.lock().unwrap().status.clone()
    }

    pub fn restart(&self) -> Result<EngineStatus, RpcError> {
        let _guard = self.core.lifecycle.lock().unwrap();
        self.stop_locked(SHUTDOWN_GRACE);
        self.spawn_locked()
            .map_err(|e| RpcError::new("engine_unavailable", e))?;
        Ok(self.status())
    }

    pub fn start(
        &self,
        request_id: String,
        method: String,
        params: Value,
    ) -> Result<RequestTicket, RpcError> {
        // UUIDs are scoped to one logical request, not a progress event or a process.
        if uuid::Uuid::parse_str(&request_id).is_err() {
            return Err(RpcError::new("invalid_request", "requestId 必须是 UUID。"));
        }
        let params = if params.is_null() { json!({}) } else { params };
        if !params.is_object() {
            return Err(RpcError::new("invalid_request", "params 必须是对象。"));
        }
        let mut bytes =
            serde_json::to_vec(&json!({"id":request_id,"method":method,"params":params}))
                .map_err(|e| RpcError::new("invalid_request", e.to_string()))?;
        bytes.push(b'\n');
        if bytes.len() > MAX_LINE_BYTES {
            return Err(RpcError::new(
                "invalid_request",
                "请求过大，请缩小输入内容。",
            ));
        }
        let session = self
            .core
            .state
            .lock()
            .unwrap()
            .session
            .clone()
            .ok_or_else(|| RpcError::new("engine_unavailable", "引擎未连接，请检查设置。"))?;
        let (sender, receiver) = channel();
        {
            let mut tickets = self.core.tickets.lock().unwrap();
            if tickets.contains_key(&request_id) || tickets.len() >= MAX_TICKETS {
                return Err(RpcError::new(
                    "invalid_request",
                    "请求编号重复或未领取的请求过多。",
                ));
            }
            tickets.insert(
                request_id.clone(),
                Ticket {
                    generation: session.generation,
                    receiver,
                },
            );
        }
        if let Err(error) = session.start(&request_id, &bytes, sender) {
            self.core.tickets.lock().unwrap().remove(&request_id);
            if error.code == "invalid_request" {
                return Err(error);
            }
            self.core.failed(&session, error.clone());
            // Reader normally reaps; write failure also arranges cleanup in case stdout stays open.
            let cleanup = Arc::clone(&session);
            // If thread creation fails, synchronously reap rather than leaking a child.
            if std::thread::Builder::new()
                .name("firesky-engine-cleanup".into())
                .spawn(move || cleanup.reap(SHUTDOWN_GRACE))
                .is_err()
            {
                session.reap(SHUTDOWN_GRACE);
            }
            return Err(error);
        }
        Ok(RequestTicket {
            request_id,
            generation: session.generation,
        })
    }

    fn take_ticket(&self, request_id: &str, generation: u64) -> Result<Receiver<Reply>, RpcError> {
        let mut tickets = self.core.tickets.lock().unwrap();
        if tickets.get(request_id).map(|t| t.generation) != Some(generation) {
            return Err(RpcError::new("invalid_request", "请求票据无效或已领取。"));
        }
        Ok(tickets.remove(request_id).unwrap().receiver)
    }

    pub async fn wait(&self, request_id: String, generation: u64) -> Reply {
        let receiver = self.take_ticket(&request_id, generation)?;
        tauri::async_runtime::spawn_blocking(move || {
            receiver.recv().unwrap_or_else(|_| Err(exited()))
        })
        .await
        .map_err(|_| protocol_error("IPC 等待线程异常退出。"))?
    }

    pub async fn request(&self, method: String, params: Value) -> Reply {
        let ticket = self.start(uuid::Uuid::new_v4().to_string(), method, params)?;
        self.wait(ticket.request_id, ticket.generation).await
    }

    pub async fn cancel(&self, request_id: String) -> Reply {
        // Never send an old generation's cancel into a restarted engine. UUIDs are
        // not reused by clients; requests already completed need no protocol frame.
        let session = self.core.state.lock().unwrap().session.clone();
        let active = session
            .as_ref()
            .is_some_and(|s| s.io.lock().unwrap().pending.contains_key(&request_id));
        if !active {
            return Ok(json!({"requested": false}));
        }
        // Start on THIS session only; a concurrent restart cannot redirect cancel.
        let session = session.unwrap();
        let id = uuid::Uuid::new_v4().to_string();
        let bytes = format!(
            "{}\n",
            json!({"id":id,"method":"cancel","params":{"request_id":request_id}})
        );
        let (sender, receiver) = channel();
        if let Err(error) = session.start(&id, bytes.as_bytes(), sender) {
            self.core.failed(&session, error.clone());
            session.reap(SHUTDOWN_GRACE);
            return Err(error);
        }
        tauri::async_runtime::spawn_blocking(move || {
            receiver.recv().unwrap_or_else(|_| Err(exited()))
        })
        .await
        .map_err(|_| protocol_error("IPC 等待线程异常退出。"))?
    }

    fn stop_locked(&self, grace: Duration) {
        let session = self.core.state.lock().unwrap().session.clone();
        if let Some(session) = session {
            self.core.failed(&session, exited());
            session.reap(grace);
        }
    }
    pub fn shutdown(&self, grace: Duration) {
        let _guard = self.core.lifecycle.lock().unwrap();
        self.stop_locked(grace);
    }
}
impl Drop for EngineHandle {
    fn drop(&mut self) {
        self.shutdown(Duration::ZERO);
    }
}

fn cleanup_child(child: &mut Child, grace: Duration) {
    cleanup_child_with(child, grace, || {});
}
fn cleanup_child_with(child: &mut Child, grace: Duration, mut close_stdin: impl FnMut()) {
    let deadline = Instant::now() + grace;
    loop {
        close_stdin();
        match child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            _ => break,
        }
    }
    let _ = child.kill();
    let _ = child.wait(); // reap direct child, including the kill/exit race
}

// read_until/read_line allocate before checking a limit. fill_buf consumes at most
// MAX_LINE_BYTES bytes and rejects an overlong/incomplete frame without draining it.
fn read_frame(reader: &mut impl BufRead) -> Result<Option<Vec<u8>>, RpcError> {
    let mut line = Vec::new();
    loop {
        let available = reader
            .fill_buf()
            .map_err(|e| protocol_error(format!("引擎读取失败：{e}")))?;
        if available.is_empty() {
            return if line.is_empty() {
                Ok(None)
            } else {
                Err(protocol_error("引擎响应帧未以换行结束。"))
            };
        }
        let newline = available.iter().position(|b| *b == b'\n');
        let count = newline.map_or(available.len(), |n| n + 1);
        if count > MAX_LINE_BYTES - line.len() {
            return Err(protocol_error("引擎响应帧超过 1 MiB。"));
        }
        line.extend_from_slice(&available[..count]);
        reader.consume(count);
        if newline.is_some() {
            return Ok(Some(line));
        }
    }
}

fn spawn_reader(
    session: Arc<Session>,
    core: Weak<Core>,
    stdout: ChildStdout,
) -> std::io::Result<std::thread::JoinHandle<()>> {
    #[cfg(test)]
    tests::thread_start_fault("reader")?;
    std::thread::Builder::new()
        .name("firesky-engine-reader".into())
        .spawn(move || read_loop(session, core, stdout))
}

fn read_loop(session: Arc<Session>, core: Weak<Core>, stdout: ChildStdout) {
    let mut reader = BufReader::new(stdout);
    let error = loop {
        match read_frame(&mut reader) {
            Ok(Some(bytes)) => {
                if let Err(error) = handle_frame(&session, &core, &bytes) {
                    break error;
                }
            }
            Ok(None) => break exited(),
            Err(error) => break error,
        }
    };
    if let Some(core) = core.upgrade() {
        core.failed(&session, error);
    } else {
        session.close(error);
    }
    drop(reader); // Stop a malformed-frame writer from filling an unread stdout pipe.
    session.reap(SHUTDOWN_GRACE);
}

fn handle_frame(session: &Session, core: &Weak<Core>, bytes: &[u8]) -> Result<(), RpcError> {
    let frame: Value = serde_json::from_slice(bytes)
        .map_err(|e| protocol_error(format!("引擎响应 JSON 无效：{e}")))?;
    let id = frame
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| protocol_error("引擎响应 id 无效。"))?;
    let kind = frame
        .get("type")
        .and_then(Value::as_str)
        .ok_or_else(|| protocol_error("引擎响应 type 无效。"))?;
    let reply = match kind {
        "progress" => {
            let event = frame
                .get("event")
                .filter(|v| v.is_object())
                .ok_or_else(|| protocol_error("引擎进度帧无效。"))?;
            if let Some(core) = core.upgrade() {
                core.progress(session, id, event.clone());
            }
            return Ok(());
        }
        "result" => Ok(frame
            .get("result")
            .ok_or_else(|| protocol_error("引擎结果帧缺少 result。"))?
            .clone()),
        "error" => {
            let error = frame
                .get("error")
                .ok_or_else(|| protocol_error("引擎错误帧缺少 error。"))?;
            let code = error
                .get("code")
                .and_then(Value::as_str)
                .filter(|c| matches!(*c, "invalid_request" | "operation_failed" | "cancelled"))
                .ok_or_else(|| protocol_error("引擎错误码无效。"))?;
            let message = error
                .get("message")
                .and_then(Value::as_str)
                .ok_or_else(|| protocol_error("引擎错误信息无效。"))?;
            Err(RpcError::new(code, message))
        }
        _ => return Err(protocol_error("引擎响应类型未知。")),
    };
    // Structurally valid replies for completed/unknown IDs may arrive after cancel.
    if let Some(sender) = session.io.lock().unwrap().pending.remove(id) {
        let _ = sender.send(reply);
    }
    Ok(())
}

fn spawn_stderr_logger(stderr: ChildStderr, log_path: PathBuf) -> Result<(), String> {
    #[cfg(test)]
    tests::thread_start_fault("stderr").map_err(|e| e.to_string())?;
    std::thread::Builder::new()
        .name("firesky-engine-stderr".into())
        .spawn(move || {
            if let Some(dir) = log_path.parent() {
                let _ = std::fs::create_dir_all(dir);
            }
            let file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(log_path);
            // Continue draining even if the log file cannot be opened/written. No
            // unbounded stderr line allocation, and no full pipe deadlocking the engine.
            let mut output: Box<dyn Write> = match file {
                Ok(file) => Box::new(file),
                Err(_) => Box::new(std::io::sink()),
            };
            let mut reader = BufReader::new(stderr);
            let mut buffer = [0; 8192];
            use std::io::Read;
            loop {
                match reader.read(&mut buffer) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        if output.write_all(&buffer[..n]).is_err() {
                            output = Box::new(std::io::sink());
                        }
                    }
                }
            }
        })
        .map_err(|e| format!("无法启动引擎日志线程：{e}"))?;
    Ok(())
}

fn resolve_workspace(default: Option<&Path>) -> Result<PathBuf, String> {
    if let Ok(value) = std::env::var("FIRESKY_WORKSPACE") {
        if !value.trim().is_empty() {
            let path = PathBuf::from(value.trim());
            return if path.is_dir() {
                path.canonicalize().map_err(|e| e.to_string())
            } else {
                Err("FIRESKY_WORKSPACE 指向的目录不存在，请检查设置。".into())
            };
        }
    }
    if !cfg!(debug_assertions) {
        if let Some(path) = default {
            std::fs::create_dir_all(path).map_err(|e| e.to_string())?;
            return path.canonicalize().map_err(|e| e.to_string());
        }
    }
    let mut roots = vec![PathBuf::from(env!("CARGO_MANIFEST_DIR"))];
    if let Ok(cwd) = std::env::current_dir() {
        roots.insert(0, cwd);
    }
    for root in roots {
        for ancestor in root.ancestors() {
            if ancestor.join("engine/Cargo.toml").is_file() {
                return Ok(ancestor.to_path_buf());
            }
        }
    }
    if let Some(path) = default {
        std::fs::create_dir_all(path).map_err(|e| e.to_string())?;
        return path.canonicalize().map_err(|e| e.to_string());
    }
    Err("未找到 FireSky 数据目录，请设置 FIRESKY_WORKSPACE。".into())
}
fn native_command(
    binary: &Path,
    workspace: &Path,
    resources: Option<&Path>,
) -> Result<Command, String> {
    let mut command = Command::new(binary);
    command
        .arg("desktop")
        .arg("--workspace")
        .arg(workspace)
        .current_dir(workspace);
    configure_app_resources(&mut command, resources)?;
    configure_decoder(&mut command, resources)?;
    Ok(command)
}

fn configure_app_resources(command: &mut Command, resources: Option<&Path>) -> Result<(), String> {
    let Some(resources) = resources else {
        return Ok(());
    };
    let root = resources.join("app-data");
    if root.is_dir() || !cfg!(debug_assertions) {
        command.env("FIRESKY_RESOURCE_DIR", &root);
        command.env("FIRESKY_REQUIRE_APP_RESOURCES", "1");
    }
    Ok(())
}

fn select_engine(
    workspace: &Path,
    resources: Option<&Path>,
    executable_dir: Option<&Path>,
    explicit: Option<&Path>,
) -> Result<PathBuf, String> {
    if let Some(path) = explicit {
        if !path.is_file() || !is_executable(path) {
            return Err("指定的 Rust 引擎不可执行，请检查 FIRESKY_RUST_ENGINE_BIN。".into());
        }
        return path.canonicalize().map_err(|e| e.to_string());
    }
    let name = if cfg!(windows) {
        "firesky-engine.exe"
    } else {
        "firesky-engine"
    };
    let mut candidates = Vec::new();
    if let Some(path) = resources {
        candidates.push(path.join("native").join(name));
        candidates.push(path.join(name));
    }
    if let Some(path) = executable_dir {
        candidates.push(path.join("Resources").join(name));
        candidates.push(path.join(name));
    }
    candidates.push(workspace.join("engine/target/release").join(name));
    candidates
        .into_iter()
        .find(|path| path.is_file() && is_executable(path))
        .ok_or_else(|| "Rust 引擎不可用，请构建引擎或设置 FIRESKY_RUST_ENGINE_BIN。".to_owned())?
        .canonicalize()
        .map_err(|e| e.to_string())
}
fn configure_decoder(command: &mut Command, resources: Option<&Path>) -> Result<(), String> {
    let Some(resources) = resources else {
        return Ok(());
    };
    let root = resources.join("native");
    let candidates = [
        root.join("lib/libeccodes.dylib"),
        root.join("eccodes/bin/eccodes.dll"),
        root.join("eccodes/eccodes.dll"),
    ];
    if let Some(library) = candidates.into_iter().find(|path| path.is_file()) {
        if std::env::var_os("FIRESKY_ECCODES_LIBRARY").is_none() {
            command.env("FIRESKY_ECCODES_LIBRARY", &library);
        }
        #[cfg(windows)]
        {
            let mut paths = vec![library.parent().unwrap().to_owned()];
            paths.extend(std::env::split_paths(
                &std::env::var_os("PATH").unwrap_or_default(),
            ));
            command.env(
                "PATH",
                std::env::join_paths(paths).map_err(|e| e.to_string())?,
            );
        }
        for (variable, name) in [
            ("ECCODES_DEFINITION_PATH", "definitions"),
            ("ECCODES_SAMPLES_PATH", "samples"),
        ] {
            let paths = [
                root.join("eccodes").join(name),
                root.join("eccodes/share/eccodes").join(name),
            ];
            if let Some(path) = paths.into_iter().find(|path| path.is_dir()) {
                if std::env::var_os(variable).is_none() {
                    command.env(variable, path);
                }
            }
        }
    }
    Ok(())
}
#[cfg(unix)]
fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path)
        .map(|m| m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}
#[cfg(not(unix))]
fn is_executable(_path: &Path) -> bool {
    true
}
fn resolve_log_path() -> PathBuf {
    if let Ok(value) = std::env::var("FIRESKY_NATIVE_LOG_DIR") {
        if !value.trim().is_empty() {
            return PathBuf::from(value.trim()).join("engine.log");
        }
    }
    let base = if cfg!(target_os = "windows") {
        std::env::var("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("C:\\ProgramData"))
    } else {
        std::env::var("HOME")
            .map(|home| {
                if cfg!(target_os = "macos") {
                    PathBuf::from(home).join("Library/Application Support")
                } else {
                    PathBuf::from(home).join(".local/share")
                }
            })
            .unwrap_or_else(|_| PathBuf::from("/tmp"))
    };
    base.join("FireSky/engine.log")
}

#[cfg(test)]
mod tests;
