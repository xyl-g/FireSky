//! C ABI implementing include/fsmobile.h — singleton in-process service.
//!
//! `fsm_start` spawns the wire/worker threads; `fsm_send` feeds the in-memory
//! duplex; frames flow out through the host callback (serialized by a write
//! lock, matching desktop_rpc.py's `write_lock`). `fsm_stop` mirrors desktop
//! EOF semantics: cancel everything in flight, wait for workers, return.

use crate::service::{self, FrameSink};
use std::ffi::{c_char, c_void, CStr, CString};
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::{Arc, Mutex, OnceLock};

/// Frame callback type from fsmobile.h.
type FrameCallback = extern "C" fn(frame: *const c_char, userdata: *mut c_void);

/// Borrow the running instance for the duration of the guard.
fn with_instance<T>(
    field_error: &str,
    read: impl FnOnce(&service::Started) -> T,
) -> Result<T, ()> {
    let guard = instance_slot().lock().unwrap();
    match guard.as_ref() {
        Some(started) => Ok(read(started)),
        None => {
            drop(guard);
            set_last_error(field_error);
            Err(())
        }
    }
}

static INSTANCE: OnceLock<Mutex<Option<service::Started>>> = OnceLock::new();

fn instance_slot() -> &'static Mutex<Option<service::Started>> {
    INSTANCE.get_or_init(|| Mutex::new(None))
}

thread_local! {
    static LAST_ERROR: std::cell::RefCell<Option<CString>> =
        const { std::cell::RefCell::new(None) };
}

fn set_last_error(message: &str) {
    let sanitized = message.replace('\0', " ");
    let stored =
        CString::new(sanitized).unwrap_or_else(|_| CString::new("engine-mobile error").unwrap());
    LAST_ERROR.with(|slot| *slot.borrow_mut() = Some(stored));
}

fn clear_last_error() {
    LAST_ERROR.with(|slot| *slot.borrow_mut() = None);
}

/// `*mut c_void` is not Send; the callback may fire on worker threads, so the
/// context pointer needs a Send wrapper (calls themselves are serialized).
#[derive(Clone, Copy)]
struct Userdata(*mut c_void);
unsafe impl Send for Userdata {}
unsafe impl Sync for Userdata {}

/// Host callback state kept behind an opaque struct so the wire sink closure
/// captures the whole (Send+Sync) state instead of the raw pointer field.
struct CallbackSink {
    callback: FrameCallback,
    userdata: Userdata,
    /// desktop_rpc.py `write_lock` — at most one frame in flight at a time.
    write_lock: Mutex<()>,
}

impl CallbackSink {
    fn deliver(&self, frame: &str) {
        let Ok(c_frame) = CString::new(frame) else {
            return;
        };
        let _permit = self.write_lock.lock().unwrap();
        (self.callback)(c_frame.as_ptr(), self.userdata.0);
    }
}

fn read_string(pointer: *const c_char, field: &str) -> Result<String, String> {
    if pointer.is_null() {
        return Ok(String::new());
    }
    let bytes = unsafe { CStr::from_ptr(pointer) }.to_bytes();
    std::str::from_utf8(bytes)
        .map(str::to_string)
        .map_err(|_| format!("{field} must be UTF-8"))
}

/// `int fsm_start(...)` — see include/fsmobile.h.
#[no_mangle]
pub extern "C" fn fsm_start(
    workspace_root: *const c_char,
    definitions_path: *const c_char,
    cache_root: *const c_char,
    callback: Option<FrameCallback>,
    userdata: *mut std::ffi::c_void,
) -> i32 {
    clear_last_error();
    let fail = |message: &str| -> i32 {
        set_last_error(message);
        -1
    };
    let Some(callback) = callback else {
        return fail("fsm_start requires a frame callback");
    };
    let workspace = match read_string(workspace_root, "workspace_root") {
        Ok(value) => value,
        Err(message) => return fail(&message),
    };
    if workspace.is_empty() {
        return fail("workspace_root must not be empty");
    }
    let definitions = match read_string(definitions_path, "definitions_path") {
        Ok(value) => value,
        Err(message) => return fail(&message),
    };
    let cache = match read_string(cache_root, "cache_root") {
        Ok(value) => value,
        Err(message) => return fail(&message),
    };
    if !definitions.is_empty() {
        // MM0 坑 10：单数变量名，且必须先于引擎初始化（此处线程尚未启动）。
        std::env::set_var("ECCODES_DEFINITION_PATH", &definitions);
    }
    if !cache.is_empty() {
        // 桌面 Runtime 的库存目录契约（FIRESKY_*_CACHE_DIR）；NULL/空则
        // 保留引擎默认 <workspace>/.cache/{gfs,ecmwf,cams}。
        let root = PathBuf::from(&cache);
        std::env::set_var("FIRESKY_GFS_CACHE_DIR", root.join("gfs"));
        std::env::set_var("FIRESKY_ECMWF_CACHE_DIR", root.join("ecmwf"));
        std::env::set_var("FIRESKY_CAMS_CACHE_DIR", root.join("cams"));
    }
    let sink: FrameSink = {
        let state = Arc::new(CallbackSink {
            callback,
            userdata: Userdata(userdata),
            write_lock: Mutex::new(()),
        });
        Arc::new(move |frame: &str| state.deliver(frame))
    };
    let mut started = match service::start(Path::new(&workspace), sink) {
        Ok(started) => started,
        Err(message) => return fail(&message),
    };
    let mut slot = instance_slot().lock().unwrap();
    if slot.is_some() {
        drop(slot);
        started.stop();
        return fail("engine-mobile service is already running (call fsm_stop first)");
    }
    *slot = Some(started);
    0
}

/// `int fsm_send(const char *line)` — see include/fsmobile.h.
#[no_mangle]
pub extern "C" fn fsm_send(line: *const c_char) -> i32 {
    clear_last_error();
    let fail = |message: &str| -> i32 {
        set_last_error(message);
        -1
    };
    let Some(line) = (unsafe { line.as_ref() }) else {
        return fail("fsm_send requires a frame");
    };
    let bytes = unsafe { CStr::from_ptr(line) }.to_bytes();
    let Ok(text) = std::str::from_utf8(bytes) else {
        return fail("fsm_send frame must be UTF-8");
    };
    // 容忍契约中的结尾换行；内嵌换行视为违反单行契约。
    let text = text.strip_suffix('\n').unwrap_or(text);
    if text.contains('\n') {
        return fail("fsm_send requires a single-line frame");
    }
    let mut frame = text.as_bytes().to_vec();
    frame.push(b'\n');
    let outcome = with_instance("engine-mobile service is not running", |started| {
        started.send(&frame)
    });
    match outcome {
        Ok(Ok(())) => 0,
        Ok(Err(error)) => fail(&format!("engine-mobile input closed: {error}")),
        Err(()) => -1,
    }
}

/// `int fsm_cancel(const char *request_id)` — see include/fsmobile.h.
#[no_mangle]
pub extern "C" fn fsm_cancel(request_id: *const c_char) -> i32 {
    clear_last_error();
    let Some(request_id) = (unsafe { request_id.as_ref() }) else {
        set_last_error("fsm_cancel requires a request id");
        return -1;
    };
    let request_id = unsafe { CStr::from_ptr(request_id) }.to_string_lossy().into_owned();
    match with_instance("engine-mobile service is not running", |started| {
        started.cancel(&request_id)
    }) {
        Ok(status) => status,
        Err(()) => -1,
    }
}

/// `int fsm_stop(void)` — see include/fsmobile.h.
#[no_mangle]
pub extern "C" fn fsm_stop() -> i32 {
    clear_last_error();
    let Some(started) = instance_slot().lock().unwrap().take() else {
        return 0;
    };
    let mut started = started;
    started.stop();
    0
}

/// `const char *fsm_last_error(void)` — see include/fsmobile.h.
#[no_mangle]
pub extern "C" fn fsm_last_error() -> *const c_char {
    LAST_ERROR.with(|slot| {
        slot.borrow()
            .as_ref()
            .map(|text| text.as_ptr())
            .unwrap_or(ptr::null())
    })
}
