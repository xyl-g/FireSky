"""Bounded, serialized persistent transport for native desktop cache/export checks."""
import atexit
import hashlib
import json
import os
import queue
import subprocess
import threading

from firesky.rust_grid import EngineOperationError, engine_binary

MAX_LINE = 1024 * 1024


class Client:
    def __init__(self):
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(8)
        self.process = None
        self.identity = None
        self.messages = None
        self.threads = []
        self.stderr = b""
        self.sequence = 0

    def _stop(self):
        process, self.process = self.process, None
        self.identity = None
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        for thread in self.threads:
            thread.join(timeout=1)
        process.stdout.close()
        process.stderr.close()
        self.threads = []

    def close(self):
        with self.lock:
            self._stop()

    def _start(self, binary, identity, environment):
        self._stop()
        process = subprocess.Popen([str(binary), "serve"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        self.process, self.identity = process, identity
        messages = self.messages = queue.Queue(maxsize=2)
        self.stderr = b""
        def publish(value):
            try:
                messages.put_nowait(value)
            except queue.Full:
                pass
        def read_output():
            try:
                while True:
                    line = process.stdout.readline(MAX_LINE+1)
                    if not line:
                        publish(RuntimeError("Rust desktop preflight engine closed its output"))
                        return
                    if len(line)>MAX_LINE or not line.endswith(b"\n"):
                        publish(RuntimeError("Rust desktop preflight response exceeds its framing bound"))
                        return
                    try:
                        publish(json.loads(line))
                    except (ValueError, UnicodeError):
                        publish(RuntimeError("Rust desktop preflight engine returned invalid JSON"))
                        return
            except (OSError, ValueError) as exc:
                publish(RuntimeError(f"Rust desktop preflight output failed: {exc}"))
        def read_diagnostics():
            try:
                while chunk := process.stderr.read1(4096):
                    self.stderr = (self.stderr+chunk)[-8192:]
            except (OSError, ValueError):
                pass
        self.threads = [threading.Thread(target=read_output, daemon=True),
                        threading.Thread(target=read_diagnostics, daemon=True)]
        for thread in self.threads:
            thread.start()

    def call(self, method, params):
        if method not in {
            "check_desktop_data",
            "desktop_context",
            "desktop_nowcast_context",
            "desktop_nowcast_gfs_plan",
            "desktop_nowcast_prior",
            "desktop_nowcast_prior_batch",
            "desktop_nowcast_prior_shared_batch",
            "desktop_nowcast_prior_decoded_batch",
            "desktop_nowcast_calibrated_retrieval",
            "desktop_nowcast_adjustment",
            "desktop_nowcast_assessment",
            "desktop_nowcast_prepare",
            "desktop_nowcast_hsd_cache",
            "desktop_nowcast_hsd_export",
            "desktop_nowcast_hsd_motion",
            "desktop_nowcast_hsd_advection",
            "desktop_nowcast_hsd_retrieval",
            "score_observer_fields",
            "score_nowcast_fields",
            "desktop_inventory",
            "desktop_location",
            "desktop_publications",
            "desktop_prepare",
            "desktop_session",
            "forecast_cached_gfs_point_to_files",
            "forecast_cached_ecmwf_point_to_files",
            "build_stored_frames_to_files",
            "build_stored_cross_section",
        }:
            raise ValueError("persistent desktop transport does not accept this operation")
        if not self.slots.acquire(blocking=False):
            raise RuntimeError("too many active Rust desktop preflight requests")
        try:
            with self.lock:
                binary = engine_binary()
                stat = binary.stat()
                environment = dict(os.environ)
                env_hash = hashlib.sha256(json.dumps(sorted(environment.items())).encode()).digest()
                identity = (str(binary), stat.st_ino, stat.st_size, stat.st_mtime_ns, env_hash)
                self.sequence += 1
                request_id = str(self.sequence)
                payload = (json.dumps(dict(id=request_id,method=method,params=params),
                    allow_nan=False)+"\n").encode("utf-8")
                if len(payload)>MAX_LINE:
                    raise ValueError("Rust desktop preflight request exceeds 1 MiB")
                if self.process is None or self.identity != identity:
                    try:
                        self._start(binary, identity, environment)
                    except OSError as exc:
                        self._stop()
                        raise RuntimeError(f"Rust desktop preflight engine failed to start: {exc}") from exc
                # A dead existing child fails this call; the next independent
                # check may connect anew. Never retry the failed operation.
                try:
                    self.process.stdin.write(payload)
                    self.process.stdin.flush()
                    message = self.messages.get()
                    if isinstance(message, Exception):
                        raise message
                    if not isinstance(message, dict) or message.get("id")!=request_id:
                        raise RuntimeError("Rust desktop preflight response identity mismatch")
                    if message.get("type")=="error":
                        error = message.get("error")
                        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
                            raise RuntimeError("Rust desktop preflight engine returned an invalid error")
                        raise EngineOperationError(error["message"])
                    if message.get("type")!="result" or "result" not in message:
                        raise RuntimeError("Rust desktop preflight engine returned an invalid response")
                    return message["result"]
                except EngineOperationError:
                    raise
                except (OSError, RuntimeError) as exc:
                    self._stop()
                    diagnostics = self.stderr.decode("utf-8", errors="replace")[-2000:]
                    detail = f"{exc}\n{diagnostics}" if diagnostics else str(exc)
                    raise RuntimeError(f"Rust desktop preflight transport failed: {detail}") from exc
        finally:
            self.slots.release()


_client = Client()


def _after_fork():
    global _client
    _client = Client()


def close():
    _client.close()


def call_engine(method, params, *, timeout_seconds=None):
    if timeout_seconds is not None:
        raise ValueError("desktop cache checks retain native operation bounds, not a subprocess deadline")
    return _client.call(method, params)


atexit.register(close)
if hasattr(os, "register_at_fork"):
    # Join pipe readers before forking, so the child cannot inherit their
    # buffered-stream locks or share an existing protocol connection.
    os.register_at_fork(before=close, after_in_child=_after_fork)
