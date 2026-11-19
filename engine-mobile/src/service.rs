//! In-process host for the native desktop JSON-lines service (blueprint MN1).
//!
//! The wire loop, orchestration, sessions, cooperative cancellation and task
//! history are the desktop `desktop` subcommand itself
//! (`firesky_engine::desktop_runtime::serve_runtime`) running over the
//! in-memory duplex instead of stdin/stdout — frame parity with the macOS
//! native baseline holds by construction. This module only adapts framing
//! (newline-delimited writes → one callback per complete frame) and mirrors
//! in-flight request ids so `fsm_cancel` keeps its synchronous 0/1 contract.

use crate::pipe::{self, PipeWriter};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::io::{self, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

/// Frame sink: one serialized JSON frame per call, without trailing newline.
pub type FrameSink = Arc<dyn Fn(&str) + Send + Sync>;

/// Running service handle; `stop` mirrors desktop EOF semantics.
pub struct Started {
    pub writer: PipeWriter,
    /// Mirror of in-flight request ids for the synchronous cancel contract.
    /// Admission is optimistic (see `send`); terminal frames retire ids, so
    /// rejected requests (invalid/duplicate/busy) self-heal via their error
    /// frames. `hello`/`cancel` never enter the desktop active map.
    active: Arc<Mutex<BTreeSet<String>>>,
    cancel_sequence: AtomicU64,
    thread: Option<JoinHandle<()>>,
}

/// Write adapter: desktop `send()` writes one JSON line + '\n' + flush per
/// frame. Buffer until newline, then deliver the complete line to the sink;
/// result/error frames also retire their id from the cancel mirror.
struct FrameWriter {
    sink: FrameSink,
    active: Arc<Mutex<BTreeSet<String>>>,
    buffer: Vec<u8>,
}

impl Write for FrameWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.buffer.extend_from_slice(bytes);
        while let Some(position) = self.buffer.iter().position(|&byte| byte == b'\n') {
            let line: Vec<u8> = self.buffer.drain(..=position).collect();
            let text = String::from_utf8_lossy(&line[..line.len() - 1]);
            if let Ok(value) = serde_json::from_str::<Value>(&text) {
                if matches!(value["type"].as_str(), Some("result" | "error")) {
                    if let Some(id) = value["id"].as_str() {
                        self.active.lock().unwrap().remove(id);
                    }
                }
            }
            (self.sink)(&text);
        }
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        // Frames are newline-terminated by the desktop wire loop; a partial
        // buffer is never a complete frame and must not be delivered early.
        Ok(())
    }
}

/// Start the in-process desktop service (wire reader + 4 bounded workers).
///
/// The runtime is constructed here so workspace/resource failures surface
/// synchronously to `fsm_start` instead of dying silently on the thread.
pub fn start(workspace: &Path, sink: FrameSink) -> Result<Started, String> {
    let runtime = firesky_engine::desktop_runtime::Runtime::new(workspace)?;
    let (writer, reader) = pipe::channel();
    let active = Arc::new(Mutex::new(BTreeSet::new()));
    let output = FrameWriter {
        sink,
        active: active.clone(),
        buffer: Vec::new(),
    };
    let thread = std::thread::Builder::new()
        .name("fsmobile-desktop".into())
        .spawn(move || {
            if let Err(error) =
                firesky_engine::desktop_runtime::serve_runtime(reader, output, runtime)
            {
                eprintln!("engine-mobile: desktop service exited: {error}");
            }
        })
        .map_err(|error| format!("spawn engine-mobile service thread: {error}"))?;
    Ok(Started {
        writer,
        active,
        cancel_sequence: AtomicU64::new(0),
        thread: Some(thread),
    })
}

impl Started {
    /// Feed one request frame (newline-terminated), mirroring its id for the
    /// synchronous cancel contract before the wire loop admits it.
    pub fn send(&self, bytes: &[u8]) -> io::Result<()> {
        if let Ok(value) = serde_json::from_slice::<Value>(bytes) {
            let method = value["method"].as_str().unwrap_or_default();
            if let Some(id) = value["id"].as_str() {
                if !id.is_empty() && !matches!(method, "hello" | "cancel") {
                    self.active.lock().unwrap().insert(id.to_string());
                }
            }
        }
        self.writer.push(bytes)
    }

    /// Cooperative cancel by request id: 0 set, 1 unknown, -1 input closed.
    /// Implemented as the wire `cancel` method (fsmobile.h contract), so the
    /// desktop flag + task-history cancellation path is reused verbatim; the
    /// synthetic request's result frame carries an id the host never issued.
    pub fn cancel(&self, request_id: &str) -> i32 {
        if !self.active.lock().unwrap().contains(request_id) {
            return 1;
        }
        let sequence = self.cancel_sequence.fetch_add(1, Ordering::Relaxed);
        let mut frame = json!({
            "id": format!("__fsmobile_cancel_{sequence}"),
            "method": "cancel",
            "params": {"request_id": request_id},
        })
        .to_string();
        frame.push('\n');
        match self.writer.push(frame.as_bytes()) {
            Ok(()) => 0,
            Err(_) => -1,
        }
    }

    /// Close the input (desktop EOF): the service cancels in-flight requests,
    /// drains queued work and joins its workers; then join the service thread.
    pub fn stop(&mut self) {
        self.writer.close();
        if let Some(thread) = self.thread.take() {
            if let Err(error) = thread.join() {
                eprintln!("engine-mobile: service thread join failed: {error:?}");
            }
        }
        self.active.lock().unwrap().clear();
    }
}

impl Drop for Started {
    fn drop(&mut self) {
        // EOF unblocks the wire loop even if `stop` was never called.
        self.writer.close();
    }
}
