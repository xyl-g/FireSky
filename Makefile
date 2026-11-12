# firesky — burning-sunset prediction engine
#
# 常用流程：
#   make install     # Rust 引擎 + 前端构建（运行不需要 Python）
#   make serve       # 启动后端（API + SPA 托管，127.0.0.1:8765）
#   make test        # Rust 重建 + 原生测试 + Python/Rust 差分

SHELL := /bin/bash
PROJECT_DIR := $(CURDIR)
VENV ?= $(PROJECT_DIR)/.venv
PY ?= $(VENV)/bin/python
PIP ?= $(VENV)/bin/pip
UV ?= $(shell command -v uv 2>/dev/null)

NATIVE_RELEASE_DIR := native/target/release
FRONTEND_BUILD_DIR := frontend/build
FIRESKY_IMAGE_TAG ?= local
FIRESKY_DOCKER_PLATFORMS ?= linux/amd64,linux/arm64
FIRESKY_PUSH ?= 0

.DEFAULT_GOAL := help
.PHONY: help install legacy-install engine native frontend build serve serve-python serve-frontend test test-rust check health contract docker-build docker-buildx docker-contract up down prune clean desktop-macos desktop-linux-ffi

help:
	@echo "firesky — burning-sunset prediction engine"
	@echo ""
	@echo "  make install    构建 Rust 服务与前端（运行不需要 Python）"
	@echo "  make legacy-install  安装 Python 对照测试与旧入口依赖"
	@echo "  make engine     编译独立 Rust 引擎"
	@echo "  make native     编译 Rust 光追评分器并发布 _cpu 变体"
	@echo "  make frontend   npm ci + 构建 SvelteKit 前端到 frontend/build"
	@echo "  make desktop-macos  构建 SwiftUI + Rust 引擎"
	@echo "  make desktop-linux-ffi  构建 Linux C++ FFI smoke（Linux 主机）"
	@echo "  make build      engine + frontend"
	@echo "  make serve      启动 Rust HTTP 后端 (:8765)"
	@echo "  make serve-python  显式启动旧 Python 后端作对照"
	@echo "  make serve-frontend  前端开发模式（vite :5173，/api 代理到 8765）"
	@echo "  make test-rust  Rust 单元、原生进程与 HTTP 契约测试"
	@echo "  make test       重建 Rust 后执行严格 Python/Rust 对照测试"
	@echo "  make check      前端 svelte-check"
	@echo "  make health     探活 GET /api/v1/firesky/health"
	@echo "  make contract   查看原生运行时能力"
	@echo "  make docker-build / docker-buildx / docker-contract / up / down / prune"
	@echo "    docker-buildx 默认 amd64+arm64；多架构发布需 FIRESKY_PUSH=1，单架构本地加载可覆盖 FIRESKY_DOCKER_PLATFORMS"
	@echo "  make clean      清理构建产物（保留 .venv 与缓存）"

# ── 安装 ─────────────────────────────────────────────────────────────────────
$(VENV)/bin/python:
	python3 -m venv $(VENV)

install: build

legacy-install: $(VENV)/bin/python
	@if [ -n "$(UV)" ]; then \
		uv pip install --python $(PY) -e ".[dev,cams,nowcast,ec]" ; \
	else \
		$(PIP) install -q -e ".[dev,cams,nowcast,ec]" ; \
	fi
	@$(MAKE) --no-print-directory native
	@$(MAKE) --no-print-directory frontend

# ── 原生评分器 ───────────────────────────────────────────────────────────────
native:
	./scripts/build_native_variants.sh

engine:
	./scripts/build_rust_engine.sh

# ── 前端 ─────────────────────────────────────────────────────────────────────
frontend:
	cd frontend && npm ci --no-audit --no-fund && npm run build

build: engine frontend

desktop-macos:
	bash scripts/build_macos_native.sh

desktop-linux-ffi:
	bash scripts/test_linux_ffi.sh

# ── 运行 ─────────────────────────────────────────────────────────────────────
serve: engine
	@engine/target/release/firesky-engine http --workspace "$(PROJECT_DIR)" --host 127.0.0.1 --port 8765

serve-python:
	@$(PY) -m uvicorn firesky.api:create_app --factory \
		--host 127.0.0.1 --port 8765

serve-frontend:
	cd frontend && npm run dev -- --host 5173

health:
	@curl -fsS http://127.0.0.1:8765/api/v1/firesky/health && echo

contract:
	@curl -fsS http://127.0.0.1:8765/api/v1/firesky/runtime

# ── 测试 ─────────────────────────────────────────────────────────────────────
# 注意：改过 native/src 后必须先 make native，否则 pytest 加载旧 dylib。
test-rust:
	cargo test --manifest-path engine/Cargo.toml --locked

test: native engine test-rust
	@FIRESKY_REQUIRE_RUST_TESTS=1 FIRESKY_RUST_ENGINE_BIN="$(PROJECT_DIR)/engine/target/release/firesky-engine" $(PY) -m pytest tests/ -q

check:
	cd frontend && npm run check

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
	rm -rf $(FRONTEND_BUILD_DIR) frontend/.svelte-kit frontend/node_modules/.vite \
		$(NATIVE_RELEASE_DIR) build .pytest_cache src/firesky/__pycache__ tests/__pycache__
	@echo "已清理构建产物（.venv 与缓存目录保留）"
