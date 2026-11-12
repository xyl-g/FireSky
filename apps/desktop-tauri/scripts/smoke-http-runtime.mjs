import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Upstream-network-free smoke: real jobs and optional history persistence, no weather downloads. */
export async function smokeHttpRuntime(base, {
  fetcher = fetch, pause = (ms) => new Promise((yes) => setTimeout(yes, ms)), attempts = 100,
  history = null, token = null,
} = {}) {
  const origin = new URL(base);
  const request = async (path, body) => {
    const response = await fetcher(new URL(path, origin), {
      headers: {
        ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { method: "POST", body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error(`HTTP runtime smoke request failed: ${path} (${response.status})`);
    return response;
  };
  const health = await (await request("/api/v1/firesky/health")).json();
  if (health.status !== "ok" || health.engine !== "firesky-rust" || health.python_required !== false) throw new Error("Rust health contract mismatch");
  const html = await (await request("/")).text();
  const asset = html.match(/<script\b[^>]*\bsrc=["'](\/assets\/[^"']+\.js)["']/)?.[1];
  if (!/<title>FireSky<\/title>/i.test(html) || !asset) throw new Error("Shared SPA entrypoint missing");
  const script = await (await request(asset)).text();
  if (script.length < 100 || /^\s*<!doctype html/i.test(script)) throw new Error("Shared SPA JavaScript missing");
  const status = await (await request("/api/v1/firesky/desktop/status")).json();
  if (status.running !== true || status.protocol !== 1 || status.engine !== "firesky-rust" || typeof status.instance_id !== "string") {
    throw new Error("Desktop HTTP instance contract mismatch");
  }
  const job = async (method, params, expected = "done") => {
    const id = randomUUID(), prefix = `/api/v1/firesky/desktop/jobs/${id}`;
    const ack = await (await request("/api/v1/firesky/desktop/jobs", {
      request_id: id, instance_id: status.instance_id, method, params,
    })).json();
    if (ack.request_id !== id || ack.instance_id !== status.instance_id || ack.status !== "running") throw new Error("Desktop HTTP registration mismatch");
    for (let index = 0; index < attempts; ++index) {
      const row = await (await request(`${prefix}?instance_id=${encodeURIComponent(status.instance_id)}`)).json();
      if (row.request_id !== id || row.instance_id !== status.instance_id) throw new Error("Desktop HTTP job identity mismatch");
      if (row.status !== "running") {
        if (row.status !== expected) throw new Error(`Desktop HTTP ${method} failed: ${row.status}`);
        const released = await (await request(`${prefix}/release`, { instance_id: status.instance_id })).json();
        if (released.released !== true) throw new Error("Desktop HTTP job release failed");
        return { ack, row, id };
      }
      await pause(100);
    }
    throw new Error(`Desktop HTTP ${method} timed out`);
  };
  const { row } = await job("hello", {});
  if (row.result?.protocol !== 1 || row.result?.engine !== "firesky-rust" || row.result?.runtime_python_used !== false) {
    throw new Error("Python-free desktop hello mismatch");
  }
  const result = { spa: true, engine: row.result.engine, runtime_python_used: false, instance: status.instance_id };
  if (history !== null) {
    if (!["seed", "verify"].includes(history.mode)) throw new Error("Unknown task history smoke mode");
    const seed = history.mode === "seed" ? await job("check", {
      kind: "point", payload: { lat: 91, lon: 120, target_date: "invalid" },
    }, "failed") : null;
    const page = (await job("task_history", { limit: 100 })).row.result;
    if (page?.schema !== "firesky-task-history-v1" || !Array.isArray(page.tasks)
      || page.automatic_restart !== false || !Number.isSafeInteger(page.total) || page.issues?.length !== 0) {
      throw new Error("Task history persistence contract mismatch");
    }
    const taskId = seed?.ack.task_id ?? history.receipt?.task_id;
    const task = page.tasks.find((item) => item.task_id === taskId);
    if (!task || !/^[a-f0-9]{64}$/.test(task.task_id) || task.method !== "check" || task.status !== "failed"
      || task.retry_request?.kind !== "point" || task.retry_request?.payload?.lat !== 91
      || !Number.isSafeInteger(task.event_seq) || task.event_seq < 2 || typeof task.updated_at_utc !== "string") {
      throw new Error("Persisted failed task missing or changed");
    }
    const receipt = {
      schema: "firesky-history-smoke-v1", previous_instance: status.instance_id,
      task_id: task.task_id, request_id: task.request_id, status: task.status,
      event_seq: task.event_seq, updated_at_utc: task.updated_at_utc, total: page.total,
    };
    if (seed) {
      if (task.request_id !== seed.id) throw new Error("Persisted failed task identity mismatch");
    } else {
      const previous = history.receipt;
      if (previous?.schema !== receipt.schema || previous.previous_instance === status.instance_id
        || ["task_id", "request_id", "status", "event_seq", "updated_at_utc", "total"]
          .some((field) => previous[field] !== receipt[field])) {
        throw new Error("Task history changed or automatically restarted after container recreation");
      }
    }
    result.task_history = receipt;
  }
  return result;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [base, mode, receiptPath] = process.argv.slice(2);
    if (!base || (mode && (!["--history-seed", "--history-verify"].includes(mode) || !receiptPath))) {
      throw new Error("usage: smoke-http-runtime.mjs BASE_URL [--history-seed|--history-verify RECEIPT_FILE]");
    }
    const history = mode === "--history-seed" ? { mode: "seed" } : mode === "--history-verify"
      ? { mode: "verify", receipt: JSON.parse(readFileSync(receiptPath, "utf8")) } : null;
    const token = process.env.FIRESKY_HTTP_TOKEN_FILE
      ? readFileSync(process.env.FIRESKY_HTTP_TOKEN_FILE, "utf8").trim()
      : process.env.FIRESKY_HTTP_TOKEN ?? null;
    const result = await smokeHttpRuntime(base, { history, token });
    if (mode === "--history-seed") writeFileSync(receiptPath, `${JSON.stringify(result.task_history, null, 2)}\n`);
    console.log(JSON.stringify(result));
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}
