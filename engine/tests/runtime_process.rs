//! Process acceptance runs the production executable with no Python on PATH.
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};

fn binary() -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_firesky-engine"));
    command.env("PATH", "/firesky-no-external-interpreters");
    command
}
#[test]
fn cli_calls_native_engine_without_python() {
    let result = binary().args(["call", "hello"]).output().unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let value: Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(value["engine"], "firesky-rust");
    assert_eq!(value["source_id"].as_str().unwrap().len(), 64);
}
#[test]
fn desktop_hello_is_native_without_checkout_or_python() {
    let workspace = tempfile::tempdir().unwrap();
    let mut child = binary()
        .args(["desktop", "--workspace"])
        .arg(workspace.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    writeln!(
        child.stdin.take().unwrap(),
        "{}",
        json!({"id":"hello","method":"hello","params":{}})
    )
    .unwrap();
    let result = child.wait_with_output().unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let value: Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(value["type"], "result");
    assert_eq!(value["result"]["engine"], "firesky-rust");
    assert_eq!(value["result"]["runtime_python_used"], false);
}
#[test]
fn cli_rejects_unknown_flags_and_invalid_json() {
    for args in [
        vec!["call", "hello", "--unknown"],
        vec!["call", "hello", "--params", "not-json"],
        vec!["desktop", "--workspace"],
        vec!["http", "--port", "invalid"],
    ] {
        let result = binary().args(args).output().unwrap();
        assert!(!result.status.success());
        assert!(result.stdout.is_empty());
    }
}

struct Server(Child);
impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn read_health(address: std::net::SocketAddr, deadline: Instant) -> Result<String, String> {
    let request =
        b"GET /api/v1/firesky/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n";
    let mut last_error = String::from("no connection attempt completed");
    while Instant::now() < deadline {
        match TcpStream::connect_timeout(&address, Duration::from_millis(100)) {
            Ok(mut stream) => {
                let timeout = Some(Duration::from_millis(500));
                if let Err(error) = stream.set_read_timeout(timeout) {
                    last_error = format!("set read timeout: {error}");
                    continue;
                }
                if let Err(error) = stream.set_write_timeout(timeout) {
                    last_error = format!("set write timeout: {error}");
                    continue;
                }
                if let Err(error) = stream.write_all(request) {
                    last_error = format!("write health request: {error}");
                    std::thread::sleep(Duration::from_millis(25));
                    continue;
                }
                let mut response = String::new();
                match stream.read_to_string(&mut response) {
                    Ok(_) if response.starts_with("HTTP/1.1 200") => return Ok(response),
                    Ok(_) => {
                        last_error = format!("unexpected health response: {response}");
                    }
                    Err(error) => {
                        last_error = format!("read health response: {error}");
                    }
                }
            }
            Err(error) => {
                last_error = format!("connect: {error}");
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Err(last_error)
}

#[test]
fn native_http_starts_without_python() {
    let workspace = tempfile::tempdir().unwrap();
    let socket = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = socket.local_addr().unwrap();
    drop(socket);
    let _server = Server(
        binary()
            .args(["http", "--workspace"])
            .arg(workspace.path())
            .args(["--port", &address.port().to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let deadline = Instant::now() + Duration::from_secs(10);
    let response = read_health(address, deadline)
        .unwrap_or_else(|error| panic!("native HTTP server did not become ready: {error}"));
    let (_, body) = response.split_once("\r\n\r\n").unwrap();
    let value: Value = serde_json::from_str(body).unwrap();
    assert_eq!(value["engine"], "firesky-rust");
    assert_eq!(value["python_required"], false);
}
