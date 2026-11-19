//! Gate ④ host smoke tests: send a hello frame over the in-memory duplex and
//! assert the response carries protocol == 1.
//!
//! Two levels: the C ABI singleton (fsmobile.h contract) and the plain Rust
//! service over the in-memory duplex pipe.

use engine_mobile::{start, FrameSink};
use serde_json::Value;
use std::ffi::{c_char, c_void, CStr, CString};
use std::ptr;
use std::sync::mpsc;
use std::sync::Arc;
use std::time::{Duration, Instant};

fn workspace() -> CString {
    let path = std::env::temp_dir().join("fsmobile-smoke");
    let _ = std::fs::create_dir_all(&path);
    CString::new(path.to_string_lossy().as_bytes()).unwrap()
}

fn c_string(value: &str) -> CString {
    CString::new(value).unwrap()
}

fn last_error() -> String {
    let pointer = engine_mobile::fsm_last_error();
    if pointer.is_null() {
        return "<no error>".to_string();
    }
    unsafe { CStr::from_ptr(pointer) }.to_string_lossy().into_owned()
}

#[test]
fn fsm_start_send_hello_yields_protocol_one() {
    let (sender, receiver) = mpsc::channel::<String>();

    extern "C" fn on_frame(frame: *const c_char, userdata: *mut c_void) {
        let sender = unsafe { &*(userdata as *const mpsc::Sender<String>) };
        let text = unsafe { CStr::from_ptr(frame) }.to_string_lossy().into_owned();
        sender.send(text).expect("smoke receiver alive");
    }

    let sender = Box::new(sender);
    let userdata = Box::into_raw(sender) as *mut c_void;
    let ws = workspace();
    let code = engine_mobile::fsm_start(
        ws.as_ptr(),
        ptr::null(),
        ptr::null(),
        Some(on_frame),
        userdata,
    );
    assert_eq!(code, 0, "fsm_start failed: {}", last_error());

    // Singleton: a second start while running must fail with last_error.
    let code = engine_mobile::fsm_start(
        ws.as_ptr(),
        ptr::null(),
        ptr::null(),
        Some(on_frame),
        userdata,
    );
    assert_eq!(code, -1);
    assert!(last_error().contains("already running"), "{}", last_error());

    let hello = c_string(r#"{"id":"hello","method":"hello"}"#);
    assert_eq!(
        engine_mobile::fsm_send(hello.as_ptr()),
        0,
        "{}",
        last_error()
    );

    let deadline = Instant::now() + Duration::from_secs(30);
    let response = loop {
        let frame = receiver
            .recv_timeout(Duration::from_secs(10))
            .expect("no frame arrived for the hello request");
        let parsed: Value = serde_json::from_str(&frame).expect("frame must be JSON");
        if parsed["id"] == "hello" {
            assert!(Instant::now() < deadline);
            break parsed;
        }
    };
    assert_eq!(response["type"], "result");
    assert_eq!(response["result"]["protocol"], 1);

    // Cooperative cancel of an unknown in-flight request reports not-requested.
    let unknown = c_string("unknown-request");
    assert_eq!(engine_mobile::fsm_cancel(unknown.as_ptr()), 1);

    assert_eq!(engine_mobile::fsm_stop(), 0);
    // After stop the service is closed: sends fail and last_error explains why.
    assert_eq!(engine_mobile::fsm_send(hello.as_ptr()), -1);
    assert!(last_error().contains("not running"), "{}", last_error());
    // fsm_stop is idempotent.
    assert_eq!(engine_mobile::fsm_stop(), 0);

    // Recover the sender box to keep miri/leak checks quiet.
    drop(unsafe { Box::from_raw(userdata as *mut mpsc::Sender<String>) });
}

#[test]
fn desktop_service_over_in_memory_duplex_returns_protocol_one() {
    let workspace = std::env::temp_dir().join("fsmobile-smoke-duplex");
    std::fs::create_dir_all(&workspace).unwrap();
    let (sender, receiver) = mpsc::channel::<String>();
    let sink: FrameSink = Arc::new(move |frame: &str| {
        sender.send(frame.to_string()).expect("smoke receiver alive");
    });
    let mut started = start(&workspace, sink).expect("desktop runtime must start");
    started
        .send(b"{\"id\":\"hello\",\"method\":\"hello\"}\n")
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(30);
    let response = loop {
        let frame = receiver
            .recv_timeout(Duration::from_secs(10))
            .expect("no frame arrived for the hello request");
        let parsed: Value = serde_json::from_str(&frame).expect("frame must be JSON");
        if parsed["id"] == "hello" {
            assert!(Instant::now() < deadline);
            break parsed;
        }
    };
    assert_eq!(response["type"], "result");
    assert_eq!(response["result"]["protocol"], 1);
    // A single frame is exactly one line of JSON.
    let raw: Vec<String> = receiver
        .try_iter()
        .collect();
    assert!(raw.iter().all(|frame| !frame.contains('\n')));
    started.stop();
}
