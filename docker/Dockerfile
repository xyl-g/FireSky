# syntax=docker/dockerfile:1.7

# ── Stage 1: native ray-traced scorer (Rust, CPU features) ──────────────────
FROM rust:1.91-bookworm AS native-builder
WORKDIR /build/native
COPY native/Cargo.toml native/Cargo.lock ./
COPY native/src ./src
RUN cargo build --release --locked

# ── Stage 2: minimal SvelteKit frontend ─────────────────────────────────────
FROM node:22-bookworm AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend .
RUN npm run build

# ── Stage 3: runtime ────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FIRESKY_CONFIG=/app/etc/web_server.yaml \
    FIRESKY_NATIVE_VARIANT=cpu \
    FIRESKY_NATIVE_LIBRARY_PATH=/app/native/libfiresky_native_cpu.so

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 libeccodes0 wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md README.en.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

COPY --from=native-builder /build/native/target/release/libfiresky_native.so \
    /app/native/libfiresky_native_cpu.so
COPY --from=frontend-builder /build/frontend/build /app/frontend/build
COPY docker/web_server.yaml /app/etc/web_server.yaml

RUN useradd --system --uid 10001 --create-home firesky \
    && mkdir -p /data/cache/firesky /data/cams /data/reports /data/logs \
    && chown -R firesky:users /app /data

USER 10001
WORKDIR /data
EXPOSE 8765
CMD ["uvicorn", "firesky.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
