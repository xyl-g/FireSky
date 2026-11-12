# syntax=docker/dockerfile:1.7

# ── Stage 1: independent native runtime ────────────────────────────────────
FROM rust:1.91-bookworm AS engine-builder
WORKDIR /build
COPY native ./native
COPY engine/Cargo.toml engine/Cargo.lock engine/build.rs ./engine/
COPY engine/src ./engine/src
RUN cargo build --manifest-path engine/Cargo.toml --release --locked

# ── Stage 2: shared Tauri/browser frontend ──────────────────────────────────
FROM node:22-bookworm AS frontend-builder
WORKDIR /build/apps/desktop-tauri
COPY apps/desktop-tauri/package.json apps/desktop-tauri/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/desktop-tauri .
RUN npm run build

# ── Stage 3: runtime ────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

ENV FIRESKY_WORKSPACE=/app \
    FIRESKY_HTTP_ALLOW_REMOTE=1 \
    FIRESKY_RESOURCE_DIR=/app/resources/app-data \
    FIRESKY_REQUIRE_APP_RESOURCES=1 \
    FIRESKY_ECCODES_LIBRARY=libeccodes.so.0 \
    ECCODES_DEFINITION_PATH=/usr/share/eccodes/definitions \
    ECCODES_SAMPLES_PATH=/usr/share/eccodes/samples \
    FIRESKY_CACHE_ROOT=/data/cache/firesky \
    FIRESKY_GFS_CACHE_DIR=/data/cache/firesky/gfs \
    FIRESKY_ECMWF_CACHE_DIR=/data/cache/firesky/ecmwf \
    FIRESKY_CAMS_CACHE_DIR=/data/cams \
    FIRESKY_REPORT_DIR=/data/reports/web \
    FIRESKY_HSD_CACHE_DIR=/data/cache/firesky/himawari-hsd \
    FIRESKY_POINT_CACHE_DIR=/data/cache/firesky/point-forecasts \
    FIRESKY_REGIONAL_CACHE_DIR=/data/cache/firesky/regional-forecasts \
    FIRESKY_MAP_CACHE_DIR=/data/cache/firesky/map-frames \
    FIRESKY_TASK_HISTORY_DIR=/data/state/tasks

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libeccodes0 libbz2-1.0 wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=engine-builder /build/engine/target/release/firesky-engine /usr/local/bin/firesky-engine
COPY --from=frontend-builder /build/apps/desktop-tauri/dist /app/frontend/build
COPY resources/app-data /app/resources/app-data
COPY docker/entrypoint.sh /usr/local/bin/firesky-entrypoint

RUN useradd --system --uid 10001 --create-home firesky \
    && mkdir -p /data/cache/firesky /data/cams /data/reports /data/logs /data/state \
    && chown -R firesky:users /app /data \
    && chmod 0755 /usr/local/bin/firesky-entrypoint \
    && chmod -R a-w /app/resources/app-data

USER 10001
WORKDIR /data
EXPOSE 8765
ENTRYPOINT ["firesky-entrypoint"]
CMD ["firesky-engine", "http", "--workspace", "/app", "--host", "0.0.0.0", "--port", "8765"]
