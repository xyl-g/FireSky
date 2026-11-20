// 临时二分：逐个调用引擎方法，找出 Windows 上 os error 1 的来源。
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";

const engine = process.argv[2];
const workspace = process.argv[3];
const appData = process.argv[4];
mkdirSync(workspace, { recursive: true });

const child = spawn(engine, ["desktop", "--workspace", workspace], {
    cwd: workspace,
    env: {
        ...process.env,
        FIRESKY_RESOURCE_DIR: appData,
        FIRESKY_REQUIRE_APP_RESOURCES: "1",
    },
    stdio: ["pipe", "pipe", "pipe"],
});
child.stderr.on("data", (c) => {
    const t = c.toString().trim();
    if (t) console.error(`[stderr] ${t.slice(0, 300)}`);
});

const pending = new Map();
let buf = "";
child.stdout.on("data", (chunk) => {
    buf += chunk.toString();
    for (;;) {
        const idx = buf.indexOf("\n");
        if (idx < 0) break;
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (!line) continue;
        let obj;
        try { obj = JSON.parse(line); } catch { continue; }
        if (obj.id && pending.has(obj.id)) {
            const res = pending.get(obj.id);
            pending.delete(obj.id);
            res(obj);
        }
    }
});

function call(method, params, timeoutMs = 120_000) {
    const id = `r${Math.random().toString(36).slice(2)}`;
    return new Promise((resolve) => {
        const timer = setTimeout(() => resolve({ error: { message: "driver timeout" } }), timeoutMs);
        pending.set(id, (obj) => {
            clearTimeout(timer);
            resolve(obj);
        });
        child.stdin.write(`${JSON.stringify({ id, method, params: params ?? {} })}\n`);
    });
}

const methods = [
    ["inventory", undefined],
    ["task_history", { limit: 10 }],
    ["data_sync_settings", undefined],
    ["retention_settings", undefined],
    ["search", { query: "西湖" }],
];
for (const [method, params] of methods) {
    const r = await call(method, params);
    console.log(`${method} -> ${r.error ? `ERROR ${r.error.message}` : `ok ${JSON.stringify(r.result).slice(0, 110)}`}`);
}
const payload = {
    name: "杭州", lat: 30.25, lon: 120.25, elevation_m: 20,
    timezone_name: "Asia/Shanghai", target_date: new Date(Date.now() + 864e5).toISOString().slice(0, 10),
    phenomenon: "dusk", model_source: "both", workers: 1,
    forecast_mode: "model", satellite_source: "off",
    ecmwf_source: "ecmwf", elevation_source: "auto",
};
const check = await call("check", { kind: "point", payload });
console.log(`check -> ${check.error ? `ERROR ${check.error.message}` : "ok"}`);
if (!check.error) {
    for (const row of check.result?.report?.datasets ?? []) {
        console.log(`  ${row.source}: ready=${row.ready} missing=${row.missing_count ?? 0}`);
    }
}
child.kill();
process.exit(0);
