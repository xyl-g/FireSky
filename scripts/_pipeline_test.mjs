// 临时验收驱动：通过 JSONL 开发协议驱动发布包里的 firesky-engine.exe，
// 走 hello → metadata → network_probe → check → prepare → forecast 全链。
// 用法：node scripts/_pipeline_test.mjs ENGINE_EXE WORKSPACE APPDATA_DIR ECCODES_DIR [STAGE]
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";

const [enginePath, workspace, appDataDir, eccodesDir] = process.argv.slice(2);
const stage = process.argv[5] ?? "full";
if (!enginePath || !workspace || !appDataDir || !eccodesDir) {
    console.error("usage: _pipeline_test.mjs ENGINE WORKSPACE APPDATA ECCODES [full|probe|noprep]");
    process.exit(2);
}
mkdirSync(workspace, { recursive: true });

const child = spawn(enginePath, ["desktop", "--workspace", workspace], {
    cwd: workspace,
    stdio: ["pipe", "pipe", "pipe"],
    env: {
        ...process.env,
        FIRESKY_RESOURCE_DIR: appDataDir,
        FIRESKY_REQUIRE_APP_RESOURCES: "1",
        FIRESKY_ECCODES_LIBRARY: `${eccodesDir}\\bin\\eccodes.dll`,
        PATH: `${eccodesDir}\\bin;${process.env.PATH ?? ""}`,
    },
});
child.stderr.on("data", (chunk) => {
    const text = chunk.toString().trim();
    if (text) console.error(`[engine stderr] ${text.slice(0, 400)}`);
});

let pendingId = 0;
const pending = new Map();
child.stdout.on("data", (chunk) => {
    for (const line of chunk.toString().split(/\r?\n/)) {
        if (!line.trim()) continue;
        let obj;
        try { obj = JSON.parse(line); } catch { console.error(`[non-json] ${line.slice(0, 200)}`); continue; }
        if (obj.type === "progress") {
            const e = obj.event ?? {};
            console.log(`  progress: ${e.source ?? ""} ${e.stage ?? ""} ${e.percent ?? 0}% ${e.message ?? ""}`.trimEnd());
            continue;
        }
        const resolver = pending.get(obj.id);
        if (resolver) {
            pending.delete(obj.id);
            resolver(obj);
        } else {
            console.error(`[unmatched id] ${obj.id}`);
        }
    }
});

function call(method, params, timeoutMs = 600_000) {
    const id = `req-${++pendingId}`;
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            pending.delete(id);
            reject(new Error(`${method} timeout after ${timeoutMs}ms`));
        }, timeoutMs);
        pending.set(id, (obj) => {
            clearTimeout(timer);
            resolve(obj);
        });
        child.stdin.write(`${JSON.stringify({ id, method, params: params ?? {} })}\n`);
    });
}

function summarize(name, obj) {
    const text = JSON.stringify(obj);
    console.log(`=== ${name}: ${obj.error ? `ERROR ${obj.error.message}` : "ok"} (${text.length} bytes)`);
    return obj;
}

try {
    const hello = summarize("hello", await call("hello", {}));
    console.log(`  protocol=${hello.result?.protocol} engine=${hello.result?.engine}`);
    console.log(`  capabilities=${(hello.result?.capabilities ?? []).join(",")}`);
    console.log(`  cams=${JSON.stringify(hello.result?.cams_credentials)}`);

    const meta = summarize("metadata", await call("metadata", {
        lat: 30.25, lon: 120.25, elevation_source: "auto",
    }, 120_000));
    console.log(`  elevation=${meta.result?.elevation_m} tz=${meta.result?.timezone_name} source=${meta.result?.source}`);

    if (stage !== "probe") {
        const tomorrow = new Date(Date.now() + 24 * 3600 * 1000).toISOString().slice(0, 10);
        const payload = {
            name: "杭州", lat: 30.25, lon: 120.25, elevation_m: 20,
            timezone_name: "Asia/Shanghai", target_date: tomorrow,
            phenomenon: "dusk", model_source: "both", workers: 1,
            forecast_mode: "model", satellite_source: "off",
            ecmwf_source: "ecmwf", elevation_source: "auto",
        };
        console.log(`=== check (target_date=${tomorrow})`);
        const check = summarize("check", await call("check", { kind: "point", payload }, 120_000));
        const rows = check.result?.report?.datasets ?? [];
        for (const row of rows) {
            console.log(`  ${row.source}: ready=${row.ready} verified=${row.verified} latest=${row.is_latest} missing=${row.missing_count ?? 0}`);
        }
        console.log(`  ready=${check.result?.report?.ready}`);

        if (stage === "noprep") process.exit(0);

        console.log("=== prepare (downloading; may take minutes)");
        const prepare = summarize("prepare", await call("prepare", { session: check.result?.session }, 1_500_000));
        console.log(`  prepared=${prepare.result?.report?.ready}`);
        for (const row of prepare.result?.report?.datasets ?? []) {
            console.log(`  ${row.source}: ready=${row.ready} verified=${row.verified} latest=${row.is_latest}`);
        }
        process.exit(0);
    }
} catch (error) {
    console.error(`FAILED: ${error.message}`);
    process.exitCode = 1;
} finally {
    child.kill();
}
