# firesky — burning-sunset prediction engine
#
# 常用流程：
#   make install     # Rust 引擎 + 前端构建（运行不需要 Python）
#   make serve       # 启动后端（API + SPA 托管，127.0.0.1:8765）
#   make test        # Rust 重建 + 原生测试 + 现架构契约测试（pytest 需 pytest/numpy）

SHELL := /bin/bash
PROJECT_DIR := $(CURDIR)
PY ?= python3

FRONTEND_BUILD_DIR := apps/shared-web/dist
FIRESKY_IMAGE_TAG ?= local
FIRESKY_DOCKER_PLATFORMS ?= linux/amd64,linux/arm64
FIRESKY_PUSH ?= 0

.DEFAULT_GOAL := help
.PHONY: help install engine frontend build serve serve-frontend test test-rust check health contract docker-build docker-buildx docker-contract up down prune clean desktop-macos desktop-linux-ffi

help:
	@echo "firesky — burning-sunset prediction engine"
	@echo ""
	@echo "  make install    构建 Rust 服务与前端（运行不需要 Python）"
	@echo "  make engine     编译独立 Rust 引擎"
	@echo "  make frontend   npm ci + 构建共享 Web SPA 到 apps/shared-web/dist"
	@echo "  make desktop-macos  构建 SwiftUI + Rust 引擎"
	@echo "  make desktop-linux-ffi  构建 Linux C++ FFI smoke（Linux 主机）"
	@echo "  make build      engine + frontend"
	@echo "  make serve      启动 Rust HTTP 后端 (:8765)"
	@echo "  make serve-frontend  共享 Web 开发模式（vite :5173，/api 代理到 8765）"
	@echo "  make test-rust  Rust 单元、原生进程与 HTTP 契约测试"
	@echo "  make test       重建 Rust 后执行现架构契约测试（需 pytest/numpy）"
	@echo "  make check      共享 Web svelte-check"
	@echo "  make health     探活 GET /api/v1/firesky/health"
	@echo "  make contract   查看原生运行时能力"
	@echo "  make docker-build / docker-buildx / docker-contract / up / down / prune"
	@echo "    docker-buildx 默认 amd64+arm64；多架构发布需 FIRESKY_PUSH=1，单架构本地加载可覆盖 FIRESKY_DOCKER_PLATFORMS"
	@echo "  make clean      清理构建产物（保留缓存）"

# ── 安装 ─────────────────────────────────────────────────────────────────────
install: build

# ── Rust 引擎（含并入的光追评分核心 engine/src/native）─────────────────────
engine:
	./scripts/build_rust_engine.sh

# ── 前端 ─────────────────────────────────────────────────────────────────────
frontend:
	cd apps/shared-web && npm ci --no-audit --no-fund && npm run build

build: engine frontend

desktop-macos:
	bash scripts/build_macos_native.sh

desktop-linux-ffi:
	bash scripts/test_linux_ffi.sh

# ── 运行 ─────────────────────────────────────────────────────────────────────
serve: engine
	@engine/target/release/firesky-engine http --workspace "$(PROJECT_DIR)" --host 127.0.0.1 --port 8765

serve-frontend:
	cd apps/shared-web && npm run dev -- --host 5173

health:
	@curl -fsS http://127.0.0.1:8765/api/v1/firesky/health && echo

contract:
	@curl -fsS http://127.0.0.1:8765/api/v1/firesky/runtime

# ── 测试 ─────────────────────────────────────────────────────────────────────
# 评分核心在 engine/src/native/，随引擎一起编译，无独立 dylib 步骤。
test-rust:
	cargo test --manifest-path engine/Cargo.toml --locked

# 现架构契约测试（Rust IPC / Swift UI），需要 pytest 与 numpy：
#   python3 -m pip install pytest numpy
test: engine test-rust
	@FIRESKY_REQUIRE_RUST_TESTS=1 FIRESKY_RUST_ENGINE_BIN="$(PROJECT_DIR)/engine/target/release/firesky-engine" $(PY) -m pytest tests/ -q

check:
	cd apps/shared-web && npm run check

# ── Docker ───────────────────────────────────────────────────────────────────
docker-build:
	docker build -f docker/Dockerfile -t firesky:$(FIRESKY_IMAGE_TAG) .

docker-buildx:
	@if [[ "$(FIRESKY_DOCKER_PLATFORMS)" == *,* && "$(FIRESKY_PUSH)" != "1" ]]; then \
		echo "multi-platform build requires FIRESKY_PUSH=1 (or set FIRESKY_DOCKER_PLATFORMS to one platform for --load)" >&2; \
		exit 2; \
	fi
	docker buildx build \
		--platform "$(FIRESKY_DOCKER_PLATFORMS)" \
		-f docker/Dockerfile \
		-t firesky:$(FIRESKY_IMAGE_TAG) \
		$(if $(filter 1,$(FIRESKY_PUSH)),--push,--load) \
		.

docker-contract:
	bash scripts/test_docker_contract.sh

up:
	FIRESKY_IMAGE_TAG="$(FIRESKY_IMAGE_TAG)" docker compose up -d --build

down:
	docker compose down

prune:
	@curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/data/prune \
		-H 'Content-Type: application/json' -d '{}' && echo

clean:
	rm -rf $(FRONTEND_BUILD_DIR) apps/shared-web/.vite \
		$(NATIVE_RELEASE_DIR) build .pytest_cache tests/__pycache__
	@echo "已清理构建产物（缓存目录保留）"
