/**
 * 共享 Web Playwright fixture：把原 Tauri invoke mock 转写为 HTTP 任务通道 mock。
 * 语义与 apps/shared-web/src/lib/browser-transport.ts 对齐：
 * /status 握手 → POST /jobs 注册 → GET /jobs/{id} 轮询 → /cancel、/release。
 * result(jobMethod) 由各 spec 注入；pending/cancelled/failed 状态由 hooks 控制。
 */
export function createRpcState({ results = {}, status = {} } = {}) {
  const jobs = new Map();
  const requests = [];
  const state = {
    jobs, requests,
    pendingPrepare: false,
    cancelRequested: false,
    cancelComplete: false,
    failureByMethod: {},
    status: { running: true, protocol: 1, engine: "firesky-rust", workspace: "/fixture", ...status },
    result(method) {
      if (method in results) return results[method];
      throw new Error(`Unexpected fixture method ${method}`);
    },
    active(method) {
      return [...jobs.values()].findLast((job) => job.method === method && !job.settled);
    },
    finish(method, value) {
      const job = state.active(method);
      if (job) { job.settled = true; job.result = value; }
    },
    fail(method, code = "operation_failed", message = "fixture failure") {
      const job = state.active(method);
      if (job) { job.settled = true; job.error = { code, message }; }
    },
  };
  return state;
}

/** Install Playwright route interception for the desktop HTTP task channel. */
export async function installRpcFixture(page, state) {
  await page.route("**/api/v1/firesky/desktop/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1/firesky/desktop", "");
    const body = route.request().method() === "POST" ? route.request().postDataJSON() : null;
    if (path === "/status") {
      return route.fulfill({ json: { instance_id: "browser-fixture", ...state.status } });
    }
    if (path === "/jobs") {
      state.requests.push(body);
      state.jobs.set(body.request_id, { ...body, cancelRequested: false });
      return route.fulfill({ json: { request_id: body.request_id, instance_id: body.instance_id, status: "running" } });
    }
    const [, , id, action] = path.split("/");
    const job = state.jobs.get(id);
    if (action === "release") {
      state.jobs.delete(id);
      return route.fulfill({ json: { released: true } });
    }
    if (action === "cancel") {
      job.cancelRequested = true;
      return route.fulfill({ json: { requested: true } });
    }
    if (!job) throw new Error(`Unknown fixture job ${id}`);
    const pending = state.pendingPrepare && job.method === "prepare" && !job.settled;
    const cancelled = job.cancelRequested && state.cancelComplete;
    const failed = Boolean(state.failureByMethod[job.method]);
    const error = failed
      ? { code: "operation_failed", message: `fixture ${job.method} failed` }
      : null;
    return route.fulfill({ json: {
      request_id: id, instance_id: job.instance_id,
      status: cancelled ? "cancelled" : failed ? "failed" : pending ? "running" : "done",
      progress_seq: pending ? 1 : 2,
      progress: pending ? { percent: 0, stage: "download", message: "fixture HTTP download" } : { percent: 100, stage: cancelled ? "cancelled" : "done" },
      ...(cancelled || pending ? {} : failed ? { error } : { result: job.settled ? job.result : state.result(job.method) }),
    } });
  });
}
