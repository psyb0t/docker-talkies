PORT ?= 8000

DEV_IMAGE := psyb0t/talkies-dev:latest
CPU_IMAGE := psyb0t/talkies:local
CUDA_IMAGE := psyb0t/talkies:local-cuda

PYPROJECT := pyproject.toml
BUMP_HOST := bash scripts/bump_exclude_newer.sh $(PYPROJECT)

UID := $(shell id -u)
GID := $(shell id -g)

# Sandboxed dev container — all dev-side commands run inside this so the host
# stays clean. The dev image is light: python + uv + lint/format/test tools.
# Heavy ML deps (torch + nemo_toolkit) live ONLY in the prod images — they're
# multi-GB and CPU/CUDA-variant-specific, so there's no single dev install
# that makes sense. Unit tests stub the ML backends.
DEV_RUN := docker run --rm \
	-u $(UID):$(GID) \
	-e HOME=/tmp \
	-v $(PWD):/work \
	-w /work \
	$(DEV_IMAGE)

DEV_RUN_TTY := docker run --rm -it \
	-u $(UID):$(GID) \
	-e HOME=/tmp \
	-v $(PWD):/work \
	-w /work \
	$(DEV_IMAGE)

.PHONY: help dev-image shell \
        build build-cuda build-all \
        run run-cuda \
        test test-unit test-integration test-streaming test-streaming-custom \
        lint format check clean \
        pkg-lock pkg-upgrade pkg-add pkg-remove pkg-update \
        compile-heavy

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# -----------------------------------------------------------------------------
# Dev container — every other target depends on this.
# -----------------------------------------------------------------------------

dev-image: ## Build/refresh the sandboxed dev image
	docker build -f Dockerfile.dev -t $(DEV_IMAGE) .

shell: dev-image ## Drop into a shell inside the dev container
	$(DEV_RUN_TTY) bash

# -----------------------------------------------------------------------------
# Package management — uv inside the dev container.
# Every mutation moves [tool.uv] exclude-newer to UTC midnight seven days ago,
# preserving the supply-chain age gate while keeping its cutoff current.
# -----------------------------------------------------------------------------

pkg-lock: dev-image ## Refresh uv.lock (honors current exclude-newer)
	$(DEV_RUN) uv lock

pkg-upgrade: dev-image ## Bump exclude-newer + refresh lock with newest pins
	$(BUMP_HOST)
	$(DEV_RUN) uv lock --upgrade

pkg-add: dev-image ## Add a package (usage: make pkg-add PKG=name[==ver])
	@test -n "$(PKG)" || (echo "usage: make pkg-add PKG=name[==ver]" >&2; exit 1)
	$(BUMP_HOST)
	$(DEV_RUN) uv add --no-sync $(PKG)

pkg-remove: dev-image ## Remove a package (usage: make pkg-remove PKG=name)
	@test -n "$(PKG)" || (echo "usage: make pkg-remove PKG=name" >&2; exit 1)
	$(BUMP_HOST)
	$(DEV_RUN) uv remove --no-sync $(PKG)

pkg-update: dev-image ## Upgrade ONE package (usage: make pkg-update PKG=name)
	@test -n "$(PKG)" || (echo "usage: make pkg-update PKG=name" >&2; exit 1)
	$(BUMP_HOST)
	$(DEV_RUN) uv lock --upgrade-package $(PKG)

compile-heavy: dev-image ## Recompile hash-locked requirements-heavy-{cpu,cuda}.txt from scripts/heavy-deps-*.in
	$(DEV_RUN) bash scripts/compile-heavy-deps.sh

# -----------------------------------------------------------------------------
# Production image builds.
# -----------------------------------------------------------------------------

build: ## Build the CPU production image
	docker build -f Dockerfile -t $(CPU_IMAGE) .

build-cuda: ## Build the CUDA production image
	docker build -f Dockerfile.cuda -t $(CUDA_IMAGE) .

build-all: build build-cuda ## Build both production images

# -----------------------------------------------------------------------------
# Local run targets.
# -----------------------------------------------------------------------------

run: build ## Run CPU image locally (uses ~/.talkies-data for models + voices + files)
	mkdir -p $$HOME/.talkies-data
	docker run --rm -it \
		-v $$HOME/.talkies-data:/data \
		-e TALKIES_DEVICE=cpu \
		-e HF_HUB_OFFLINE=0 \
		-p $(PORT):8000 \
		$(CPU_IMAGE)

run-cuda: build-cuda ## Run CUDA image locally (requires --gpus all support)
	mkdir -p $$HOME/.talkies-data
	docker run --rm -it --gpus all \
		-v $$HOME/.talkies-data:/data \
		-e TALKIES_DEVICE=cuda \
		-e HF_HUB_OFFLINE=0 \
		-p $(PORT):8000 \
		$(CUDA_IMAGE)

# -----------------------------------------------------------------------------
# Test / lint / format — all inside the dev container.
# -----------------------------------------------------------------------------

test: test-unit ## Run unit tests (fast, offline, no GPU)

test-unit: dev-image ## Run unit tests in the dev container
	$(DEV_RUN) pytest tests -v --ignore=tests/integration

# Integration suite — needs a real CUDA host. Runs on the host (NOT inside
# the dev container) because it spawns sibling docker containers and pokes
# the talkies HTTP port directly. Builds the CUDA image first unless
# TALKIES_SKIP_BUILD=1.
test-integration: ## Run CUDA integration tests (host-side, needs --gpus all)
	@bash tests/integration/run.sh

test-streaming: build ## Run the CPU-native real WebSocket streaming ASR test
	TALKIES_SKIP_BUILD=1 bash tests/integration/e2e_streaming_asr.sh

test-streaming-custom: build ## Run real Sherpa-ONNX and Vosk WebSocket streaming tests
	TALKIES_SKIP_BUILD=1 bash tests/integration/e2e_streaming_custom_backends.sh

lint: dev-image ## Lint python sources
	$(DEV_RUN) flake8 src
	$(DEV_RUN) mypy src

format: dev-image ## Format python sources
	$(DEV_RUN) isort src
	$(DEV_RUN) black src

check: lint test ## Lint + tests

clean: ## Remove build / cache artifacts (host-side)
	docker rmi $(CPU_IMAGE) $(CUDA_IMAGE) 2>/dev/null || true
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .venv
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
