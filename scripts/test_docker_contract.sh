#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCKERFILE="$ROOT/docker/Dockerfile"
COMPOSE="$ROOT/docker-compose.yaml"
WORKFLOW="$ROOT/.github/workflows/native-desktop.yml"
MANIFEST="$ROOT/resources/app-data/RESOURCE-MANIFEST.json"

fail() {
    echo "Docker contract: $*" >&2
    exit 1
}

test -s "$DOCKERFILE" || fail "missing docker/Dockerfile"
test -s "$COMPOSE" || fail "missing docker-compose.yaml"
test -s "$WORKFLOW" || fail "missing native-desktop workflow"
test -s "$MANIFEST" || fail "missing application resource manifest"

# The production image must compile Rust, build the shared page, and run only
# the Rust HTTP binary. Keep this check scoped to the runtime stage so builder
# tooling can evolve without accidentally adding it to the shipped image.
for stage in \
    "FROM rust:" \
    "FROM node:" \
    "FROM debian:bookworm-slim AS runtime" \
    "COPY --from=engine-builder" \
    "COPY --from=frontend-builder" \
    "COPY resources/app-data /app/resources/app-data" \
    "FIRESKY_REQUIRE_APP_RESOURCES=1" \
    "FIRESKY_HTTP_ALLOW_REMOTE=1" \
    "FIRESKY_WEB_ROOT=/app/frontend/build" \
    'ENTRYPOINT ["firesky-entrypoint"]' \
    "COPY docker/entrypoint.sh /usr/local/bin/firesky-entrypoint" \
    "FIRESKY_ECCODES_LIBRARY=libeccodes.so.0" \
    "ECCODES_DEFINITION_PATH=/usr/share/eccodes/definitions" \
    "ECCODES_SAMPLES_PATH=/usr/share/eccodes/samples" \
    'CMD ["firesky-engine", "http"'
do
    rg -F -q "$stage" "$DOCKERFILE" || fail "Dockerfile is missing required contract: $stage"
done

runtime_stage="$(
    awk '
        /^FROM debian:bookworm-slim AS runtime/ { in_runtime = 1 }
        in_runtime { print }
    ' "$DOCKERFILE"
)"
if printf '%s\n' "$runtime_stage" | rg -qi '\b(python|pip|uv)\b'; then
    fail "runtime stage contains a Python tool or interpreter"
fi
if printf '%s\n' "$runtime_stage" | rg -q '(^|[[:space:]])root([[:space:]]|$)|USER[[:space:]]+0([[:space:]]|$)'; then
    fail "runtime stage contains an explicit root user"
fi

# The manifest is the immutable resource boundary. Check it with Node so this
# gate works on macOS/Linux CI without requiring Python or a Docker daemon.
command -v node >/dev/null 2>&1 || fail "node is required for manifest validation"
node --input-type=module - "$ROOT" <<'NODE'
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readdirSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";

const root = resolve(process.argv[2]);
const dataRoot = join(root, "resources", "app-data");
const manifestPath = join(dataRoot, "RESOURCE-MANIFEST.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
if (manifest.schema !== "firesky-app-resources-v1"
  || manifest.read_only !== true
  || manifest.user_data_is_external !== true) {
  throw new Error("unsupported application resource manifest");
}
if (!Array.isArray(manifest.files) || manifest.files.length === 0) {
  throw new Error("application resource manifest has no files");
}

const declared = new Set();
for (const entry of manifest.files) {
  if (!entry || typeof entry.path !== "string" || entry.path.length === 0
    || entry.path.startsWith("/") || entry.path.includes("\\")
    || entry.path.split("/").some((part) => part === "" || part === "." || part === "..")) {
    throw new Error(`unsafe application resource path: ${entry?.path}`);
  }
  if (declared.has(entry.path)) throw new Error(`duplicate application resource: ${entry.path}`);
  declared.add(entry.path);
  const path = join(dataRoot, entry.path);
  const stat = lstatSync(path);
  if (!stat.isFile()) throw new Error(`application resource is not a regular file: ${entry.path}`);
  const bytes = readFileSync(path);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  if (stat.size !== entry.bytes || sha256 !== entry.sha256) {
    throw new Error(`application resource checksum mismatch: ${entry.path}`);
  }
}

const discovered = new Set();
function walk(directory) {
  for (const name of readdirSync(directory)) {
    const path = join(directory, name);
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) throw new Error(`application resources must not contain symlinks: ${path}`);
    if (stat.isDirectory()) walk(path);
    else if (stat.isFile() && path !== manifestPath) {
      const rel = relative(dataRoot, path).split(sep).join("/");
      discovered.add(rel);
    } else if (!stat.isFile()) {
      throw new Error(`unsupported application resource entry: ${path}`);
    }
  }
}
walk(dataRoot);
if (discovered.size !== declared.size
  || [...discovered].some((path) => !declared.has(path))) {
  throw new Error("application resource manifest is not a complete file closure");
}
NODE

# Compose must be local by default, preserve all user data in named volumes,
# and keep Redis outside the default service graph.
for contract in \
    '"${FIRESKY_HTTP_HOST:-127.0.0.1}:${FIRESKY_HTTP_PORT:-8765}:8765"' \
    "firesky-cache:/data/cache" \
    "firesky-cams:/data/cams" \
    "firesky-reports:/data/reports" \
    "firesky-logs:/data/logs" \
    "firesky-state:/data/state" \
    'FIRESKY_TASK_HISTORY_DIR: /data/state/tasks' \
    'profiles: ["legacy-cache"]' \
    'http://127.0.0.1:8765/api/v1/firesky/health'
do
    rg -F -q "$contract" "$COMPOSE" || fail "Compose is missing required contract: $contract"
done

# Keep the real target-machine Docker jobs. A syntax-only build or a single
# architecture cannot prove the runtime image works for the release targets.
for contract in \
    "platform: linux/amd64" \
    "platform: linux/arm64" \
    "docker/build-push-action@v6" \
    "load: true" \
    "docker exec firesky-smoke uname -m" \
    "! command -v python && ! command -v python3" \
    "--history-seed" \
    "--history-verify" \
    "docker volume create firesky-smoke-state"
do
    rg -F -q -- "$contract" "$WORKFLOW" || fail "Docker CI is missing required contract: $contract"
done

echo "Docker contract: ok (static; Docker daemon/runtime not exercised)"
