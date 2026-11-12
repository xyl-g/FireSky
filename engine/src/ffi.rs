//! Stable C ABI for native platform clients.
//!
//! The ABI deliberately keeps Rust ownership private. Native clients exchange
//! UTF-8 JSON envelopes and opaque engine/task handles; all returned strings
//! must be released with [`firesky_ffi_string_free`]. JSON is a versioned DTO
//! boundary, not an invitation to pass Rust values or pointers across FFI.

use crate::desktop_runtime::Runtime;
use serde_json::{json, Value};
use std::{
    ffi::{CStr, CString},
    os::raw::{c_char, c_void},
    panic::{catch_unwind, AssertUnwindSafe},
    path::Path,
    ptr,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
};

/// Increment when an exported symbol, ownership rule or envelope shape changes.
pub const FIRESKY_FFI_ABI_VERSION: u32 = 1;
const MAX_WORKSPACE_BYTES: usize = 4096;
const MAX_METHOD_BYTES: usize = 128;
const MAX_JSON_BYTES: usize = 8 * 1024 * 1024;

pub const FIRESKY_FFI_OK: i32 = 0;
pub const FIRESKY_FFI_INVALID_ARGUMENT: i32 = 1;
pub const FIRESKY_FFI_INVALID_UTF8: i32 = 2;
pub const FIRESKY_FFI_INVALID_JSON: i32 = 3;
pub const FIRESKY_FFI_RUNTIME_ERROR: i32 = 4;
pub const FIRESKY_FFI_CANCELLED: i32 = 5;
pub const FIRESKY_FFI_BUSY: i32 = 6;
pub const FIRESKY_FFI_PANIC: i32 = 255;

/// Progress callback invoked on the worker thread.
///
/// The callback receives a temporary NUL-terminated UTF-8 string valid only
/// for the duration of the call. A client must copy it before returning.
pub type FireskyProgressCallback =
    unsafe extern "C" fn(event_json: *const c_char, user_data: *mut c_void);

/// Opaque runtime handle owned by a native client.
#[repr(C)]
pub struct FireskyEngineHandle {
    runtime: Arc<Runtime>,
}

type TaskOutcome = Result<String, FfiError>;

/// Opaque asynchronous operation handle.
#[repr(C)]
pub struct FireskyTaskHandle {
    cancel: Arc<AtomicBool>,
    join: Mutex<Option<JoinHandle<TaskOutcome>>>,
}

#[derive(Debug)]
struct FfiError {
    code: i32,
    message: String,
}

impl FfiError {
    fn new(code: i32, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    fn argument(message: impl Into<String>) -> Self {
        Self::new(FIRESKY_FFI_INVALID_ARGUMENT, message)
    }

    fn runtime(message: impl Into<String>) -> Self {
        let message = message.into();
        let code = if message.to_ascii_lowercase().contains("cancel") {
            FIRESKY_FFI_CANCELLED
        } else if message.contains("busy") || message.contains("active") {
            FIRESKY_FFI_BUSY
        } else {
            FIRESKY_FFI_RUNTIME_ERROR
        };
        Self::new(code, message)
    }
}

fn error_envelope(method: Option<&str>, error: &FfiError) -> String {
    serde_json::to_string(&json!({
        "contract": "firesky-ffi-error-v1",
        "abi_version": FIRESKY_FFI_ABI_VERSION,
        "ok": false,
        "method": method,
        "error": {
            "code": error.code,
            "message": error.message,
        },
    }))
    .unwrap_or_else(|_| {
        r#"{"contract":"firesky-ffi-error-v1","abi_version":1,"ok":false,"error":{"code":255,"message":"FFI error serialization failed"}}"#.to_owned()
    })
}

fn success_envelope(method: &str, result: Value) -> Result<String, FfiError> {
    serde_json::to_string(&json!({
        "contract": "firesky-ffi-call-v1",
        "abi_version": FIRESKY_FFI_ABI_VERSION,
        "ok": true,
        "method": method,
        "result": result,
    }))
    .map_err(|error| FfiError::new(FIRESKY_FFI_RUNTIME_ERROR, error.to_string()))
}

fn event_envelope(method: &str, event: Value) -> Result<String, FfiError> {
    serde_json::to_string(&json!({
        "contract": "firesky-ffi-event-v1",
        "abi_version": FIRESKY_FFI_ABI_VERSION,
        "method": method,
        "event": event,
    }))
    .map_err(|error| FfiError::new(FIRESKY_FFI_RUNTIME_ERROR, error.to_string()))
}

unsafe fn clear_output<T>(output: *mut *mut T) {
    if !output.is_null() {
        *output = ptr::null_mut();
    }
}

unsafe fn read_c_string(
    pointer: *const c_char,
    label: &str,
    limit: usize,
    allow_null: bool,
) -> Result<String, FfiError> {
    if pointer.is_null() {
        if allow_null {
            return Ok(String::new());
        }
        return Err(FfiError::argument(format!("{label} is null")));
    }
    let bytes = CStr::from_ptr(pointer).to_bytes();
    if bytes.len() > limit {
        return Err(FfiError::argument(format!(
            "{label} exceeds the {limit}-byte limit"
        )));
    }
    std::str::from_utf8(bytes)
        .map(str::to_owned)
        .map_err(|_| FfiError::new(FIRESKY_FFI_INVALID_UTF8, format!("{label} is not UTF-8")))
}

fn write_owned_string(output: *mut *mut c_char, value: String) -> Result<(), FfiError> {
    if output.is_null() {
        return Err(FfiError::argument("output string pointer is null"));
    }
    let string =
        CString::new(value).map_err(|_| FfiError::runtime("output contains a NUL byte"))?;
    unsafe {
        *output = string.into_raw();
    }
    Ok(())
}

fn write_error(output: *mut *mut c_char, error: &FfiError, method: Option<&str>) {
    if output.is_null() {
        return;
    }
    let _ = write_owned_string(output, error_envelope(method, error));
}

fn finish(
    result: Result<(), FfiError>,
    error_output: *mut *mut c_char,
    method: Option<&str>,
) -> i32 {
    match result {
        Ok(()) => FIRESKY_FFI_OK,
        Err(error) => {
            let code = error.code;
            write_error(error_output, &error, method);
            code
        }
    }
}

fn ffi_guard<F>(error_output: *mut *mut c_char, method: Option<&str>, operation: F) -> i32
where
    F: FnOnce() -> Result<(), FfiError> + std::panic::UnwindSafe,
{
    let result = catch_unwind(AssertUnwindSafe(operation)).unwrap_or_else(|_| {
        Err(FfiError::new(
            FIRESKY_FFI_PANIC,
            "Rust FFI operation panicked",
        ))
    });
    finish(result, error_output, method)
}

unsafe fn handle_ref<'a>(
    handle: *mut FireskyEngineHandle,
) -> Result<&'a FireskyEngineHandle, FfiError> {
    handle
        .as_ref()
        .ok_or_else(|| FfiError::argument("engine handle is null"))
}

fn parse_params(pointer: *const c_char) -> Result<Value, FfiError> {
    let text = unsafe { read_c_string(pointer, "params", MAX_JSON_BYTES, true)? };
    if text.is_empty() {
        return Ok(json!({}));
    }
    let value: Value = serde_json::from_str(&text)
        .map_err(|error| FfiError::new(FIRESKY_FFI_INVALID_JSON, error.to_string()))?;
    if !value.is_object() {
        return Err(FfiError::argument("params JSON must be an object"));
    }
    Ok(value)
}

fn call_runtime(
    runtime: &Runtime,
    method: &str,
    params: Value,
    cancel: Arc<AtomicBool>,
    callback: Option<FireskyProgressCallback>,
    user_data: usize,
) -> TaskOutcome {
    let progress = |event: Value| -> Result<(), String> {
        let Some(callback) = callback else {
            return Ok(());
        };
        let envelope = event_envelope(method, event).map_err(|error| error.message)?;
        let c_string = CString::new(envelope).map_err(|_| "progress contains NUL".to_owned())?;
        let callback_result = catch_unwind(AssertUnwindSafe(|| unsafe {
            callback(c_string.as_ptr(), user_data as *mut c_void);
        }));
        if callback_result.is_err() {
            return Err("progress callback panicked".into());
        }
        Ok(())
    };
    runtime
        .dispatch_cancellable(method, params, cancel, &progress)
        .map_err(FfiError::runtime)
        .and_then(|result| success_envelope(method, result))
}

/// Returns the C ABI and envelope contract version.
#[no_mangle]
pub extern "C" fn firesky_ffi_abi_version() -> u32 {
    FIRESKY_FFI_ABI_VERSION
}

/// Opens a runtime for a writable workspace.
#[no_mangle]
pub unsafe extern "C" fn firesky_engine_open(
    workspace: *const c_char,
    out_handle: *mut *mut FireskyEngineHandle,
    out_error: *mut *mut c_char,
) -> i32 {
    clear_output(out_handle);
    clear_output(out_error);
    ffi_guard(out_error, None, || {
        let workspace = read_c_string(workspace, "workspace", MAX_WORKSPACE_BYTES, false)?;
        if workspace.trim().is_empty() {
            return Err(FfiError::argument("workspace is empty"));
        }
        let runtime = Runtime::new(Path::new(&workspace)).map_err(FfiError::runtime)?;
        if out_handle.is_null() {
            return Err(FfiError::argument("engine output handle pointer is null"));
        }
        *out_handle = Box::into_raw(Box::new(FireskyEngineHandle {
            runtime: Arc::new(runtime),
        }));
        Ok(())
    })
}

/// Releases an engine handle. A null handle is accepted.
#[no_mangle]
pub unsafe extern "C" fn firesky_engine_free(handle: *mut FireskyEngineHandle) {
    if !handle.is_null() {
        drop(Box::from_raw(handle));
    }
}

/// Synchronously dispatches one versioned desktop operation.
#[no_mangle]
pub unsafe extern "C" fn firesky_engine_call(
    handle: *mut FireskyEngineHandle,
    method: *const c_char,
    params_json: *const c_char,
    out_response: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    clear_output(out_response);
    clear_output(out_error);
    ffi_guard(out_error, None, || {
        let handle = handle_ref(handle)?;
        let method = read_c_string(method, "method", MAX_METHOD_BYTES, false)?;
        if method.trim().is_empty() {
            return Err(FfiError::argument("method is empty"));
        }
        let params = parse_params(params_json)?;
        let response = handle
            .runtime
            .dispatch(&method, params)
            .map_err(FfiError::runtime)
            .and_then(|result| success_envelope(&method, result))?;
        write_owned_string(out_response, response)
    })
}

/// Starts one asynchronous operation. Progress is delivered only through the
/// callback; completion is retrieved with `firesky_task_join`.
#[no_mangle]
pub unsafe extern "C" fn firesky_engine_start(
    handle: *mut FireskyEngineHandle,
    method: *const c_char,
    params_json: *const c_char,
    callback: Option<FireskyProgressCallback>,
    user_data: *mut c_void,
    out_task: *mut *mut FireskyTaskHandle,
    out_error: *mut *mut c_char,
) -> i32 {
    clear_output(out_task);
    clear_output(out_error);
    ffi_guard(out_error, None, || {
        let handle = handle_ref(handle)?;
        let method = read_c_string(method, "method", MAX_METHOD_BYTES, false)?;
        if method.trim().is_empty() {
            return Err(FfiError::argument("method is empty"));
        }
        let params = parse_params(params_json)?;
        if out_task.is_null() {
            return Err(FfiError::argument("task output handle pointer is null"));
        }
        let cancel = Arc::new(AtomicBool::new(false));
        let task_cancel = cancel.clone();
        let runtime = handle.runtime.clone();
        let callback_data = user_data as usize;
        let worker_method = method.clone();
        let join = thread::Builder::new()
            .name(format!("firesky-ffi-{method}"))
            .spawn(move || {
                call_runtime(
                    &runtime,
                    &worker_method,
                    params,
                    task_cancel,
                    callback,
                    callback_data,
                )
            })
            .map_err(|error| FfiError::runtime(error.to_string()))?;
        *out_task = Box::into_raw(Box::new(FireskyTaskHandle {
            cancel,
            join: Mutex::new(Some(join)),
        }));
        Ok(())
    })
}

/// Requests cooperative cancellation. The worker remains alive until it exits.
#[no_mangle]
pub unsafe extern "C" fn firesky_task_cancel(
    task: *mut FireskyTaskHandle,
    out_error: *mut *mut c_char,
) -> i32 {
    clear_output(out_error);
    ffi_guard(out_error, None, || {
        let task = task
            .as_ref()
            .ok_or_else(|| FfiError::argument("task handle is null"))?;
        task.cancel.store(true, Ordering::Release);
        Ok(())
    })
}

/// Waits for completion and returns an owned response or error envelope.
#[no_mangle]
pub unsafe extern "C" fn firesky_task_join(
    task: *mut FireskyTaskHandle,
    out_response: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    clear_output(out_response);
    clear_output(out_error);
    ffi_guard(out_error, None, || {
        let task = task
            .as_ref()
            .ok_or_else(|| FfiError::argument("task handle is null"))?;
        let join = task
            .join
            .lock()
            .map_err(|_| FfiError::runtime("task join lock is poisoned"))?
            .take()
            .ok_or_else(|| FfiError::argument("task has already been joined"))?;
        let outcome = join
            .join()
            .map_err(|_| FfiError::new(FIRESKY_FFI_PANIC, "Rust FFI worker panicked"))?;
        match outcome {
            Ok(response) => write_owned_string(out_response, response),
            Err(error) => Err(error),
        }
    })
}

/// Cancels and joins a task before releasing its memory. A null task is
/// accepted. This function may block until an in-flight transfer cooperates.
#[no_mangle]
pub unsafe extern "C" fn firesky_task_free(task: *mut FireskyTaskHandle) {
    if task.is_null() {
        return;
    }
    let task = Box::from_raw(task);
    task.cancel.store(true, Ordering::Release);
    let join = task.join.lock().ok().and_then(|mut guard| guard.take());
    if let Some(join) = join {
        let _ = join.join();
    }
}

/// Frees a string returned by this module. Passing null is a no-op.
#[no_mangle]
pub unsafe extern "C" fn firesky_ffi_string_free(value: *mut c_char) {
    if !value.is_null() {
        drop(CString::from_raw(value));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        ffi::CString,
        sync::{
            atomic::{AtomicUsize, Ordering},
            Arc,
        },
    };

    fn workspace() -> CString {
        CString::new(env!("CARGO_MANIFEST_DIR")).unwrap()
    }

    fn parse_owned(pointer: *mut c_char) -> Value {
        assert!(!pointer.is_null());
        let value = unsafe { CStr::from_ptr(pointer) }
            .to_str()
            .unwrap()
            .to_owned();
        unsafe { firesky_ffi_string_free(pointer) };
        serde_json::from_str(&value).unwrap()
    }

    #[test]
    fn abi_and_sync_hello_use_versioned_envelopes() {
        assert_eq!(firesky_ffi_abi_version(), 1);
        let workspace = workspace();
        let mut handle = ptr::null_mut();
        let mut error = ptr::null_mut();
        let code = unsafe { firesky_engine_open(workspace.as_ptr(), &mut handle, &mut error) };
        assert_eq!(code, FIRESKY_FFI_OK, "{:?}", parse_owned(error));
        let method = CString::new("hello").unwrap();
        let mut response = ptr::null_mut();
        let mut call_error = ptr::null_mut();
        let code = unsafe {
            firesky_engine_call(
                handle,
                method.as_ptr(),
                ptr::null(),
                &mut response,
                &mut call_error,
            )
        };
        assert_eq!(code, FIRESKY_FFI_OK, "{:?}", parse_owned(call_error));
        let envelope = parse_owned(response);
        assert_eq!(envelope["contract"], "firesky-ffi-call-v1");
        assert_eq!(envelope["abi_version"], 1);
        assert_eq!(envelope["ok"], true);
        assert_eq!(envelope["result"]["engine"], "firesky-rust");
        unsafe { firesky_engine_free(handle) };
    }

    #[test]
    fn invalid_json_returns_owned_error_envelope() {
        let workspace = workspace();
        let mut handle = ptr::null_mut();
        let mut open_error = ptr::null_mut();
        assert_eq!(
            unsafe { firesky_engine_open(workspace.as_ptr(), &mut handle, &mut open_error) },
            FIRESKY_FFI_OK,
            "{:?}",
            parse_owned(open_error)
        );
        let method = CString::new("hello").unwrap();
        let params = CString::new("[]").unwrap();
        let mut response = ptr::null_mut();
        let mut error = ptr::null_mut();
        let code = unsafe {
            firesky_engine_call(
                handle,
                method.as_ptr(),
                params.as_ptr(),
                &mut response,
                &mut error,
            )
        };
        assert_eq!(code, FIRESKY_FFI_INVALID_ARGUMENT);
        assert!(response.is_null());
        let envelope = parse_owned(error);
        assert_eq!(envelope["contract"], "firesky-ffi-error-v1");
        assert_eq!(envelope["ok"], false);
        unsafe { firesky_engine_free(handle) };
    }

    #[test]
    fn async_hello_join_and_cancel_are_single_owner_operations() {
        let workspace = workspace();
        let mut handle = ptr::null_mut();
        let mut open_error = ptr::null_mut();
        assert_eq!(
            unsafe { firesky_engine_open(workspace.as_ptr(), &mut handle, &mut open_error) },
            FIRESKY_FFI_OK,
            "{:?}",
            parse_owned(open_error)
        );
        let method = CString::new("hello").unwrap();
        let mut task = ptr::null_mut();
        let mut error = ptr::null_mut();
        assert_eq!(
            unsafe {
                firesky_engine_start(
                    handle,
                    method.as_ptr(),
                    ptr::null(),
                    None,
                    ptr::null_mut(),
                    &mut task,
                    &mut error,
                )
            },
            FIRESKY_FFI_OK,
            "{:?}",
            parse_owned(error)
        );
        let mut response = ptr::null_mut();
        let mut join_error = ptr::null_mut();
        assert_eq!(
            unsafe { firesky_task_join(task, &mut response, &mut join_error) },
            FIRESKY_FFI_OK,
            "{:?}",
            parse_owned(join_error)
        );
        assert_eq!(parse_owned(response)["ok"], true);
        let mut second_response = ptr::null_mut();
        let mut second_error = ptr::null_mut();
        assert_eq!(
            unsafe { firesky_task_join(task, &mut second_response, &mut second_error) },
            FIRESKY_FFI_INVALID_ARGUMENT
        );
        unsafe {
            firesky_ffi_string_free(second_error);
            firesky_task_free(task);
            firesky_engine_free(handle);
        }
    }

    #[test]
    fn callback_receives_versioned_event_without_owning_memory() {
        static CALLS: AtomicUsize = AtomicUsize::new(0);
        unsafe extern "C" fn callback(event: *const c_char, data: *mut c_void) {
            assert!(!event.is_null());
            assert!(!data.is_null());
            let value: Value =
                serde_json::from_str(CStr::from_ptr(event).to_str().unwrap()).unwrap();
            assert_eq!(value["contract"], "firesky-ffi-event-v1");
            CALLS.fetch_add(1, Ordering::Relaxed);
        }
        let workspace = workspace();
        let mut handle = ptr::null_mut();
        let mut open_error = ptr::null_mut();
        assert_eq!(
            unsafe { firesky_engine_open(workspace.as_ptr(), &mut handle, &mut open_error) },
            FIRESKY_FFI_OK,
            "{:?}",
            parse_owned(open_error)
        );
        let method = CString::new("prepare").unwrap();
        let params = CString::new(
            r#"{"kind":"point","payload":{"lat":0.0,"lon":0.0,"target_date":"2026-11-09"}}"#,
        )
        .unwrap();
        let mut task = ptr::null_mut();
        let mut error = ptr::null_mut();
        let marker = Arc::new(AtomicUsize::new(1));
        let marker_ptr = Arc::into_raw(marker.clone()) as *mut c_void;
        let code = unsafe {
            firesky_engine_start(
                handle,
                method.as_ptr(),
                params.as_ptr(),
                Some(callback),
                marker_ptr,
                &mut task,
                &mut error,
            )
        };
        assert_eq!(code, FIRESKY_FFI_OK, "{:?}", parse_owned(error));
        unsafe { firesky_task_free(task) };
        unsafe { drop(Arc::from_raw(marker_ptr as *const AtomicUsize)) };
        assert_eq!(CALLS.load(Ordering::Relaxed), 0);
        unsafe { firesky_engine_free(handle) };
    }
}
