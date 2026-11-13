/**
 * 页内 fetch 拦截 mock：把原 Tauri invoke fixture 转写到 HTTP 任务通道。
 * 与 src/lib/browser-transport.ts 的协议对齐：
 *   GET /status 握手 → POST /jobs 注册（__rpcSubmit）→ GET /jobs/{id} 轮询
 *   → POST /jobs/{id}/cancel、/release。
 * 各 spec 在 addInitScript 里嵌入本源码并定义 __rpcStatus/__rpcSubmit/__rpcCancelJob。
 * job 对象带 resolve/reject/progress，与旧 fixture 的 ticket 语义一致（含 settled 标记）。
 */
export const IN_PAGE_RPC_MOCK = String.raw`
(() => {
  const PREFIX = "/api/v1/firesky/desktop";
  const jobs = new Map();
  const reply = (value) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
  const realFetch = window.fetch.bind(window);
  window.__rpcJob = (method, params, id) => {
    const job = {
      id, method, params, settled: false, seq: 0, progressQueue: [],
      resolve(value) { this.settled = true; this.result = value; },
      reject(error) { this.settled = true; this.error = error; },
      progress(event) { this.progressQueue.push(event); },
    };
    jobs.set(id, job);
    return job;
  };
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!url.includes(PREFIX)) return realFetch(input, init);
    const sub = url.slice(url.indexOf(PREFIX) + PREFIX.length).split("?")[0];
    const verb = (init && init.method) || "GET";
    const body = init && init.body ? JSON.parse(init.body) : null;
    if (sub === "/status") {
      return reply({ instance_id: "browser-fixture", running: true, protocol: 1, engine: "firesky-rust", workspace: "/fixture", ...(window.__rpcStatus ? window.__rpcStatus() : {}) });
    }
    if (sub === "/jobs" && verb === "POST") {
      const job = window.__rpcJob(body.method, body.params, body.request_id);
      let direct = false;
      if (typeof window.__rpcDirect === "function") {
        try {
          const value = window.__rpcDirect(body.method, body.params);
          // Promise 视为「尚未完成」：留给后续 finish 钩子 resolve，对齐旧 fixture 语义。
          if (value !== undefined && !(value instanceof Promise)) { job.resolve(value); direct = true; }
        } catch (error) {
          job.reject({ code: error && error.code ? error.code : "operation_failed", message: error instanceof Error ? error.message : String(error) });
        }
      }
      // 已由 __rpcDirect 同步应答的任务不再回调 __rpcSubmit（fixture 只处理长任务）。
      if (!direct) window.__rpcSubmit(job);
      return reply({ request_id: job.id, instance_id: body.instance_id, status: "running" });
    }
    const parts = sub.split("/").filter(Boolean);
    const job = jobs.get(parts[1]);
    if (!job) return reply({ released: true });
    if (parts[2] === "release") { jobs.delete(job.id); return reply({ released: true }); }
    if (parts[2] === "cancel") {
      return reply({ requested: window.__rpcCancelJob ? window.__rpcCancelJob(job) : true });
    }
    if (job.progressQueue.length) {
      job.seq += 1;
      return reply({ request_id: job.id, instance_id: "browser-fixture", status: "running", progress_seq: job.seq, progress: job.progressQueue.shift() });
    }
    if (!job.settled) {
      return reply({ request_id: job.id, instance_id: "browser-fixture", status: "running", progress_seq: job.seq });
    }
    if (job.error && job.error.code === "cancelled") {
      return reply({ request_id: job.id, instance_id: "browser-fixture", status: "cancelled", progress_seq: job.seq });
    }
    if (job.error) {
      return reply({ request_id: job.id, instance_id: "browser-fixture", status: "failed", progress_seq: job.seq, error: job.error });
    }
    return reply({ request_id: job.id, instance_id: "browser-fixture", status: "done", progress_seq: job.seq, result: job.result });
  };
})();
`;
