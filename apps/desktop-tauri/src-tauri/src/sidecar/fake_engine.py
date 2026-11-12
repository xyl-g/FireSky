"""Deterministic stdlib-only fixture, launched exclusively by Rust #[cfg(test)]."""
import json
import os
import sys
import time

mode = sys.argv[1]
marker = sys.argv[2] if len(sys.argv) > 2 else None
active = set()


def send(frame):
    sys.stdout.write(json.dumps(frame) + "\n")
    sys.stdout.flush()


if mode == "quick-exit":
    sys.exit(0)

for line in sys.stdin:
    request = json.loads(line)
    request_id = request["id"]
    method = request["method"]
    if method == "hello":
        send({"id": request_id, "type": "result", "result": {"protocol": 1}})
    elif method == "work":
        active.add(request_id)
        if request["params"].get("progress"):
            send({"id": request_id, "type": "progress", "event": {"percent": 23, "stage": "fixture"}})
    elif method == "cancel":
        target = request["params"]["request_id"]
        requested = target in active
        if requested:
            active.remove(target)
            send({"id": target, "type": "error", "error": {"code": "cancelled", "message": "fixture cancelled"}})
        send({"id": request_id, "type": "result", "result": {"requested": requested}})
    elif method == "error":
        send({"id": request_id, "type": "error", "error": {"code": "operation_failed", "message": "fixture failed"}})
    elif method == "bad-json":
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
    elif method == "bad-result":
        send({"id": request_id, "type": "result"})
    elif method == "bad-error":
        send({"id": request_id, "type": "error", "error": {"code": "cancelled"}})
    elif method == "oversize":
        sys.stdout.write("x" * (1024 * 1024 + 1))
        sys.stdout.flush()
    elif method == "incomplete":
        sys.stdout.write('{"id":')
        sys.stdout.flush()
        sys.exit(0)
    elif method == "exit":
        sys.exit(0)
    elif method == "stdout-eof":
        # Keep the direct child alive until the bridge notices stdout EOF and
        # closes stdin. The marker proves graceful EOF cleanup actually ran.
        os.close(1)
    elif method == "stall-input":
        # Acknowledge that future writes cannot be consumed, then leave both
        # output pipes open. Cleanup must not wait on a full stdin writer lock.
        send({"id": request_id, "type": "result", "result": {"ready": True}})
        while True:
            time.sleep(60)
    elif method == "ignore-stdin-eof":
        send({"id": request_id, "type": "result", "result": {"ready": True}})
        while True:
            time.sleep(60)

if marker:
    with open(marker, "w", encoding="utf-8") as output:
        output.write("stdin EOF\n")
