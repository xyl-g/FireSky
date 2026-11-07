# firesky — burning-sunset prediction engine
#
# 常用流程：
#   make install     # venv + 依赖 + 原生库 + 前端构建（首次一条龙）
#   make serve       # 启动后端（API + SPA 托管，127.0.0.1:8765）
#   make test        # pytest（引擎不变量 + 后端契约）

SHELL := /bin/bash
PROJECT_DIR := $(CURDIR)
VENV ?= $(PROJECT_DIR)/.venv
PY ?= $(VENV)/bin/python
PIP ?= $(VENV)/bin/pip
UV ?= $(shell command -v uv 2>/dev/null)

NATIVE_RELEASE_DIR := native/target/release
FRONTEND_BUILD_DIR := frontend/build

.DEFAULT_GOAL := help
.PHONY: help install native frontend build serve serve-frontend test check health contract docker-build up down prune clean

help:
	@echo "firesky — burning-sunset prediction engine"
	@echo ""
	@echo "  make install    安装依赖（venv + pip[dev,cams,nowcast,ec]）并构建原生库与前端"
	@echo "  make native     编译 Rust 光追评分器并发布 _cpu 变体"
	@echo "  make frontend   npm ci + 构建 SvelteKit 前端到 frontend/build"
	@echo "  make build      native + frontend"
	@echo "  make serve      启动后端 uvicorn firesky.api:create_app (:8765)"
	@echo "  make serve-frontend  前端开发模式（vite :5173，/api 代理到 8765）"
	@echo "  make test       pytest（引擎不变量 + 后端契约）"
	@echo "  make check      前端 svelte-check"
	@echo "  make health     探活 GET /api/v1/firesky/health"
	@echo "  make contract   查看区域预测版本契约"
	@echo "  make docker-build / up / down / prune"
	@echo "  make clean      清理构建产物（保留 .venv 与缓存）"

# ── 安装 ─────────────────────────────────────────────────────────────────────
$(VENV)/bin/python:
	python3 -m venv $(VENV)

install: $(VENV)/bin/python
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

# ── 前端 ─────────────────────────────────────────────────────────────────────
frontend:
	cd frontend && npm ci --no-audit --no-fund && npm run build

build: native frontend

# ── 运行 ─────────────────────────────────────────────────────────────────────
serve:
	@$(PY) -m uvicorn firesky.api:create_app --factory \
		--host 127.0.0.1 --port 8765

serve-frontend:
	cd frontend && npm run dev -- --host 5173

health:
	@curl -fsS http://127.0.0.1:8765/api/v1/firesky/health && echo

contract:
	@curl -fsS http://127.0.0.1:8765/api/v1/firesky/forecasts/national/contract | python3 -m json.tool

# ── 测试 ─────────────────────────────────────────────────────────────────────
# 注意：改过 native/src 后必须先 make native，否则 pytest 加载旧 dylib。
test: native
	@$(PY) -m pytest tests/ -q

check:
	cd frontend && npm run check

# ── Docker ───────────────────────────────────────────────────────────────────
docker-build:
	docker build -f docker/Dockerfile -t firesky:${FIRESKY_IMAGE_TAG:-local} .

up:
	FIRESKY_IMAGE_TAG=${FIRESKY_IMAGE_TAG:-local} docker compose up -d --build

down:
	docker compose down

prune:
	@curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/data/prune \
		-H 'Content-Type: application/json' -d '{}' && echo

clean:
	rm -rf $(FRONTEND_BUILD_DIR) frontend/.svelte-kit frontend/node_modules/.vite \
		$(NATIVE_RELEASE_DIR) build .pytest_cache src/firesky/__pycache__ tests/__pycache__
	@echo "已清理构建产物（.venv 与缓存目录保留）"
