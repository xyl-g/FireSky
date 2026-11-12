use super::*;
use std::io::Cursor;
use std::sync::mpsc::RecvTimeoutError;

// Fault injection is thread-local and compiled only into this unit-test binary.
// There is no environment variable, command or RPC path to enable it in production.
thread_local! {
    static THREAD_FAULT: std::cell::Cell<Option<&'static str>> = const { std::cell::Cell::new(None) };
}
pub(super) fn thread_start_fault(stage: &str) -> std::io::Result<()> {
    if THREAD_FAULT.with(|fault| fault.get() == Some(stage)) {
        Err(std::io::Error::other("fixture thread creation failure"))
    } else {
        Ok(())
    }
}

const TEST_DEADLINE: Duration = Duration::from_secs(8);
type Events = Arc<Mutex<Vec<(String, Value)>>>;

fn engine() -> (EngineHandle, Events) {
    let events: Events = Arc::new(Mutex::new(Vec::new()));
    let sink = Arc::clone(&events);
    (
        EngineHandle::with_emitter(Arc::new(move |name, value| {
            sink.lock().unwrap().push((name.to_owned(), value));
        })),
        events,
    )
}
fn fixture(mode: &str, marker: Option<&Path>) -> Command {
    let workspace = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let python = std::env::var_os("FIRESKY_TEST_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            workspace.join(if cfg!(windows) {
                ".venv/Scripts/python.exe"
            } else {
                ".venv/bin/python"
            })
        });
    let mut command = Command::new(python);
    command
        .arg("-I")
        .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/sidecar/fake_engine.py"))
        .arg(mode);
    if let Some(marker) = marker {
        command.arg(marker);
    }
    command
}

#[test]
fn native_launcher_prefers_resources_and_rejects_invalid_explicit_binary() {
    let scratch = std::env::temp_dir().join(format!("firesky-launcher-{}", uuid::Uuid::new_v4()));
    let resources = scratch.join("resources");
    let workspace = scratch.join("workspace");
    let bundled = resources.join("native").join(if cfg!(windows) {
        "firesky-engine.exe"
    } else {
        "firesky-engine"
    });
    std::fs::create_dir_all(bundled.parent().unwrap()).unwrap();
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::write(&bundled, b"native fixture").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&bundled, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    assert_eq!(
        select_engine(&workspace, Some(&resources), None, None).unwrap(),
        bundled.canonicalize().unwrap()
    );
    assert!(select_engine(
        &workspace,
        Some(&resources),
        None,
        Some(&scratch.join("missing"))
    )
    .is_err());
    std::fs::remove_dir_all(scratch).unwrap();
}

#[test]
fn native_desktop_launcher_roundtrips_hello_without_python_workspace() {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let explicit = std::env::var_os("FIRESKY_RUST_ENGINE_BIN");
    let binary = select_engine(&repository, None, None, explicit.as_deref().map(Path::new))
        .expect("build firesky-engine or set FIRESKY_RUST_ENGINE_BIN for the native launcher test");
    let scratch = std::env::temp_dir().join(format!("firesky-native-rpc-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir(&scratch).unwrap();
    let (engine, _) = engine();
    let mut command = native_command(&binary, &scratch, None).unwrap();
    assert_eq!(
        command.get_args().collect::<Vec<_>>(),
        vec![
            std::ffi::OsStr::new("desktop"),
            std::ffi::OsStr::new("--workspace"),
            scratch.as_os_str(),
        ]
    );
    engine
        .launch(
            &mut command,
            scratch.display().to_string(),
            binary.display().to_string(),
        )
        .unwrap();
    let hello = reply(&engine, start(&engine, "hello", json!({}))).unwrap();
    assert_eq!(hello["engine"], "firesky-rust");
    assert_eq!(hello["runtime_python_used"], false);
    assert!(engine.status().python.is_none());
    assert_eq!(
        engine.status().engine_binary,
        Some(binary.display().to_string())
    );
    engine.shutdown(SHUTDOWN_GRACE);
    assert_reaped(&session(&engine));
    std::fs::remove_dir_all(scratch).unwrap();
}
fn launch(engine: &EngineHandle, mode: &str, marker: Option<&Path>) {
    let _guard = engine.core.lifecycle.lock().unwrap();
    engine.stop_locked(Duration::from_millis(100));
    engine
        .launch(
            &mut fixture(mode, marker),
            "fixture".into(),
            "test-only".into(),
        )
        .unwrap();
}
fn start(engine: &EngineHandle, method: &str, params: Value) -> RequestTicket {
    engine
        .start(uuid::Uuid::new_v4().to_string(), method.into(), params)
        .unwrap()
}
fn reply(engine: &EngineHandle, ticket: RequestTicket) -> Reply {
    engine
        .take_ticket(&ticket.request_id, ticket.generation)
        .unwrap()
        .recv_timeout(TEST_DEADLINE)
        .expect("pending reply hung")
}
fn until(mut predicate: impl FnMut() -> bool) {
    let deadline = Instant::now() + TEST_DEADLINE;
    while !predicate() {
        assert!(Instant::now() < deadline, "fixture observation timed out");
        std::thread::sleep(Duration::from_millis(2));
    }
}
fn session(engine: &EngineHandle) -> Arc<Session> {
    engine.core.state.lock().unwrap().session.clone().unwrap()
}
fn assert_reaped(session: &Session) {
    assert!(session.child.lock().unwrap().is_none());
}

#[test]
fn partial_startup_thread_failures_close_stdin_and_reap_child() {
    for stage in ["stderr", "reader"] {
        let (engine, _) = engine();
        let marker = std::env::temp_dir().join(format!("firesky-partial-{}", uuid::Uuid::new_v4()));
        THREAD_FAULT.with(|fault| fault.set(Some(stage)));
        let result = engine.launch(
            &mut fixture("serve", Some(&marker)),
            "fixture".into(),
            "test-only".into(),
        );
        THREAD_FAULT.with(|fault| fault.set(None));
        assert!(result.is_err(), "{stage}");
        // launch's cleanup is synchronous: fixture reached EOF and the direct
        // child has been waited, not merely dropped or signalled.
        assert!(
            marker.is_file(),
            "{stage}: did not give stdin EOF cleanup a grace period"
        );
        assert!(!engine.status().running);
        if let Some(child) = engine.core.state.lock().unwrap().session.clone() {
            assert_reaped(&child);
        }
        std::fs::remove_file(marker).unwrap();
    }
}

#[test]
fn hello_progress_and_protocol_errors_round_trip() {
    let (engine, events) = engine();
    launch(&engine, "serve", None);
    assert_eq!(
        reply(&engine, start(&engine, "hello", json!({}))).unwrap(),
        json!({"protocol": 1})
    );
    let work = start(&engine, "work", json!({"progress":true}));
    until(|| {
        events.lock().unwrap().iter().any(|(name, payload)| {
            name == PROGRESS_EVENT
                && payload["requestId"] == work.request_id
                && payload["event"]["percent"] == 23
        })
    });
    let error = reply(&engine, start(&engine, "error", json!({}))).unwrap_err();
    assert_eq!(error.code, "operation_failed");
    assert_eq!(error.message, "fixture failed");
    let cancellation =
        tauri::async_runtime::block_on(engine.cancel(work.request_id.clone())).unwrap();
    assert_eq!(cancellation, json!({"requested":true}));
    assert_eq!(reply(&engine, work).unwrap_err().code, "cancelled");
    assert!(events
        .lock()
        .unwrap()
        .iter()
        .any(|(n, p)| n == STATUS_EVENT && p["running"] == true));
}

#[test]
fn cancellation_before_first_progress_is_registered_and_ordered() {
    let (engine, events) = engine();
    launch(&engine, "serve", None);
    let ticket = start(&engine, "work", json!({}));
    assert!(!events
        .lock()
        .unwrap()
        .iter()
        .any(|(name, _)| name == PROGRESS_EVENT));
    assert_eq!(
        tauri::async_runtime::block_on(engine.cancel(ticket.request_id.clone())).unwrap(),
        json!({"requested":true})
    );
    assert_eq!(reply(&engine, ticket).unwrap_err().code, "cancelled");
    assert_eq!(
        tauri::async_runtime::block_on(engine.cancel(uuid::Uuid::new_v4().to_string())).unwrap(),
        json!({"requested":false})
    );
}

#[test]
fn convenience_request_and_wait_commands_preserve_results_and_errors() {
    let (engine, _) = engine();
    launch(&engine, "serve", None);
    assert_eq!(
        tauri::async_runtime::block_on(engine.request("hello".into(), json!({}))).unwrap()
            ["protocol"],
        1
    );
    let ticket = start(&engine, "error", json!({}));
    assert_eq!(
        tauri::async_runtime::block_on(engine.wait(ticket.request_id.clone(), ticket.generation))
            .unwrap_err()
            .message,
        "fixture failed"
    );
    assert!(engine
        .take_ticket(&ticket.request_id, ticket.generation)
        .is_err());
}

#[test]
fn eof_and_all_invalid_frames_fail_every_pending_request() {
    for method in [
        "exit",
        "bad-json",
        "bad-result",
        "bad-error",
        "oversize",
        "incomplete",
    ] {
        let (engine, _) = engine();
        launch(&engine, "serve", None);
        let first = start(&engine, "work", json!({}));
        let second = start(&engine, method, json!({}));
        let error = reply(&engine, second).unwrap_err();
        assert_eq!(
            error.code,
            if method == "exit" {
                "engine_exited"
            } else {
                "operation_failed"
            },
            "{method}"
        );
        assert_eq!(reply(&engine, first).unwrap_err().code, error.code);
        until(|| !engine.status().running);
        assert!(engine.status().spawn_error.is_some());
        assert!(engine
            .start(uuid::Uuid::new_v4().to_string(), "hello".into(), json!({}))
            .is_err());
        engine.shutdown(Duration::ZERO);
        assert_reaped(&session(&engine));
    }
}

#[test]
fn quick_exit_cannot_be_overwritten_by_startup_publication_or_hang_a_ticket() {
    for _ in 0..12 {
        let (engine, events) = engine();
        launch(&engine, "quick-exit", None);
        if let Ok(ticket) =
            engine.start(uuid::Uuid::new_v4().to_string(), "hello".into(), json!({}))
        {
            assert_eq!(reply(&engine, ticket).unwrap_err().code, "engine_exited");
        }
        until(|| !engine.status().running);
        let statuses: Vec<bool> = events
            .lock()
            .unwrap()
            .iter()
            .filter(|(n, _)| n == STATUS_EVENT)
            .map(|(_, p)| p["running"].as_bool().unwrap())
            .collect();
        assert_eq!(statuses, vec![true, false]);
        engine.shutdown(Duration::ZERO);
        assert_reaped(&session(&engine));
    }
}

#[test]
fn restart_is_serialized_and_stale_readers_cannot_touch_new_generation() {
    let (engine, events) = engine();
    let engine = Arc::new(engine);
    launch(&engine, "serve", None);
    let old = session(&engine);
    let old_ticket = start(&engine, "work", json!({}));
    let workers: Vec<_> = (0..3)
        .map(|_| {
            let engine = Arc::clone(&engine);
            std::thread::spawn(move || launch(&engine, "serve", None))
        })
        .collect();
    for worker in workers {
        worker.join().unwrap();
    }
    assert_eq!(engine.status().generation, 4);
    assert_reaped(&old);
    assert_eq!(
        reply(&engine, old_ticket).unwrap_err().code,
        "engine_exited"
    );
    let current = start(&engine, "work", json!({}));
    let receiver = engine
        .take_ticket(&current.request_id, current.generation)
        .unwrap();
    let count = events.lock().unwrap().len();
    // Simulate already queued callbacks from the old reader after restart.
    handle_frame(
        &old,
        &Arc::downgrade(&engine.core),
        format!(
            "{}\n",
            json!({"id":current.request_id,"type":"progress","event":{"percent":99}})
        )
        .as_bytes(),
    )
    .unwrap();
    handle_frame(
        &old,
        &Arc::downgrade(&engine.core),
        format!(
            "{}\n",
            json!({"id":current.request_id,"type":"result","result":"stale"})
        )
        .as_bytes(),
    )
    .unwrap();
    engine.core.failed(&old, exited());
    assert_eq!(events.lock().unwrap().len(), count);
    assert!(matches!(
        receiver.try_recv(),
        Err(std::sync::mpsc::TryRecvError::Empty)
    ));
    assert!(engine.status().running);
    assert_eq!(
        tauri::async_runtime::block_on(engine.cancel(current.request_id)).unwrap()["requested"],
        true
    );
    assert_eq!(
        receiver
            .recv_timeout(TEST_DEADLINE)
            .unwrap()
            .unwrap_err()
            .code,
        "cancelled"
    );
}

#[test]
fn stdout_eof_closes_stdin_and_reaps_child_gracefully() {
    let marker = std::env::temp_dir().join(format!("firesky-eof-{}", uuid::Uuid::new_v4()));
    let (engine, _) = engine();
    launch(&engine, "serve", Some(&marker));
    let child = session(&engine);
    let ticket = start(&engine, "stdout-eof", json!({}));
    assert_eq!(reply(&engine, ticket).unwrap_err().code, "engine_exited");
    until(|| marker.is_file());
    engine.shutdown(Duration::ZERO);
    assert_reaped(&child);
    assert_eq!(std::fs::read_to_string(&marker).unwrap(), "stdin EOF\n");
    std::fs::remove_file(marker).unwrap();
}

#[test]
fn shutdown_closes_stdin_before_bounded_kill_and_reaps_direct_child() {
    let marker = std::env::temp_dir().join(format!("firesky-shutdown-{}", uuid::Uuid::new_v4()));
    let (engine, _) = engine();
    launch(&engine, "serve", Some(&marker));
    let child = session(&engine);
    let work = start(&engine, "work", json!({}));
    engine.shutdown(Duration::from_secs(1));
    assert_eq!(reply(&engine, work).unwrap_err().code, "engine_exited");
    assert_reaped(&child);
    assert!(marker.is_file(), "graceful stdin EOF was skipped");
    std::fs::remove_file(marker).unwrap();

    launch(&engine, "serve", None);
    let child = session(&engine);
    assert_eq!(
        reply(&engine, start(&engine, "ignore-stdin-eof", json!({}))).unwrap(),
        json!({"ready":true})
    );
    let before = Instant::now();
    engine.shutdown(Duration::from_millis(40));
    assert!(before.elapsed() < Duration::from_secs(2));
    assert_reaped(&child);
}

#[test]
fn stalled_input_writer_cannot_block_pending_failure_or_bounded_shutdown() {
    let (engine, _) = engine();
    let engine = Arc::new(engine);
    launch(&engine, "serve", None);
    let child = session(&engine);
    let pending = start(&engine, "work", json!({}));
    assert_eq!(
        reply(&engine, start(&engine, "stall-input", json!({}))).unwrap(),
        json!({"ready":true})
    );
    let (sender, receiver) = channel();
    let writer_engine = Arc::clone(&engine);
    let writer = std::thread::spawn(move || {
        let result = writer_engine.start(
            uuid::Uuid::new_v4().to_string(),
            "hello".into(),
            json!({"padding":"x".repeat(900_000)}),
        );
        sender.send(result).unwrap();
    });
    until(|| child.stdin.try_lock().is_err());
    let before = Instant::now();
    engine.shutdown(Duration::from_millis(40));
    assert!(before.elapsed() < Duration::from_secs(2));
    assert_reaped(&child);
    assert_eq!(reply(&engine, pending).unwrap_err().code, "engine_exited");
    assert_eq!(
        receiver
            .recv_timeout(TEST_DEADLINE)
            .expect("blocked writer survived direct-child kill")
            .unwrap_err()
            .code,
        "engine_exited"
    );
    writer.join().unwrap();
    assert!(engine.core.tickets.lock().unwrap().is_empty());
}

#[test]
fn bounded_reader_rejects_oversize_partial_utf8_and_invalid_terminal_shapes() {
    assert!(read_frame(&mut Cursor::new(vec![b'x'; MAX_LINE_BYTES + 1])).is_err());
    assert!(read_frame(&mut Cursor::new(b"partial")).is_err());
    assert_eq!(
        read_frame(&mut Cursor::new(b"\n")).unwrap(),
        Some(vec![b'\n'])
    );
    let mut exact = vec![b' '; MAX_LINE_BYTES];
    exact[MAX_LINE_BYTES - 1] = b'\n';
    assert_eq!(
        read_frame(&mut Cursor::new(exact)).unwrap().unwrap().len(),
        MAX_LINE_BYTES
    );
    let (engine, _) = engine();
    launch(&engine, "serve", None);
    let child = session(&engine);
    for bytes in [
        b"\xff\n".as_slice(),
        b"{}\n",
        b"{\"id\":null,\"type\":\"result\",\"result\":0}\n",
        b"{\"id\":\"x\",\"type\":\"unknown\"}\n",
    ] {
        assert!(handle_frame(&child, &Arc::downgrade(&engine.core), bytes).is_err());
    }
}

#[test]
fn invalid_params_oversize_duplicate_and_wrong_ticket_do_not_break_connection() {
    let (engine, _) = engine();
    launch(&engine, "serve", None);
    let id = uuid::Uuid::new_v4().to_string();
    assert!(engine.start(id.clone(), "hello".into(), json!([])).is_err());
    assert!(engine
        .start(
            id.clone(),
            "hello".into(),
            json!({"huge":"x".repeat(MAX_LINE_BYTES)})
        )
        .is_err());
    let ticket = engine.start(id.clone(), "work".into(), json!({})).unwrap();
    assert!(engine.take_ticket(&id, ticket.generation + 1).is_err());
    let receiver = engine.take_ticket(&id, ticket.generation).unwrap();
    assert!(engine.start(id.clone(), "hello".into(), json!({})).is_err());
    assert!(engine.status().running);
    assert_eq!(
        tauri::async_runtime::block_on(engine.cancel(id)).unwrap()["requested"],
        true
    );
    assert!(!matches!(
        receiver.recv_timeout(TEST_DEADLINE),
        Err(RecvTimeoutError::Timeout)
    ));
}
