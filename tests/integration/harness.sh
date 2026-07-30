#!/bin/bash
# shellcheck shell=bash
# Container lifecycle harness for talkies integration tests.
#
# Each test_*.sh / e2e_*.sh file sources this, declares the model slugs it
# needs, calls harness_start to spawn its own --rm container on an ephemeral
# port, runs its checks via harness_run_tests, and the EXIT
# trap tears the container down. No shared state between files, no global
# orchestrator required — invoke any test file directly:
#
#     bash tests/integration/test_endpoints.sh
#     bash tests/integration/test_speech.sh
#     bash tests/integration/e2e_kokoro_nvidia.sh
#
# Env knobs (sane defaults; override only when needed):
#   HARNESS_IMAGE          docker image (default psyb0t/talkies:local-cuda)
#   HARNESS_CACHE_DIR      host dir for /data mount (default $REPO_ROOT/.e2e-cache).
#                          Reused across runs to skip the ~1 GB prefetch.
#   HARNESS_DEVICE         Talkies device to pass into the container (default cuda).
#   HARNESS_USE_GPU        1 to add --gpus all and require an NVIDIA runtime;
#                          0 for CPU-only images (default 1).
#   HARNESS_READY_TIMEOUT  seconds to wait for /healthz (default 900)
#   HARNESS_KEEP=1         leave container running on exit (debug)
#   HARNESS_MODELS_FILE    optional custom model registry mounted at
#                          /app/models.json (must be a readable local file).
#
# Sets for callers (read-only contract):
#   HARNESS_PORT           ephemeral host port the container is mapped to
#   TALKIES_BASE_URL       http://127.0.0.1:$HARNESS_PORT — consume from
#                          curl-based helpers in common.sh
#   HARNESS_ENABLED_MODELS comma-separated slugs the container is serving
#   HARNESS_CONTAINER      docker container name (for debugging)

HARNESS_IMAGE="${HARNESS_IMAGE:-psyb0t/talkies:local-cuda}"
HARNESS_DEVICE="${HARNESS_DEVICE:-cuda}"
HARNESS_USE_GPU="${HARNESS_USE_GPU:-1}"
HARNESS_READY_TIMEOUT="${HARNESS_READY_TIMEOUT:-900}"
HARNESS_MODELS_FILE="${HARNESS_MODELS_FILE:-}"

# Default cache dir — resolved relative to the repo root, NOT pwd of the
# caller, so the same dir is reused whether you `cd` into tests/ first or
# not. We resolve once at source time.
_HARNESS_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HARNESS_CACHE_DIR="${HARNESS_CACHE_DIR:-${_HARNESS_REPO_ROOT}/.e2e-cache}"

# Populated by harness_start.
HARNESS_PORT=""
TALKIES_BASE_URL=""
HARNESS_ENABLED_MODELS=""
HARNESS_CONTAINER=""

# Snapshot the calling script's positional args BEFORE the e2e file's
# `set -e` / source chain potentially clobbers $@. Any args passed to
# `bash e2e_foo.sh <test_a> <test_b>` become the per-test whitelist
# consumed inside harness_run_tests.
HARNESS_TEST_FILTER=("$@")

# Substring-or-exact match against HARNESS_TEST_FILTER.
_harness_test_matches() {
    local t="$1" pat
    for pat in "${HARNESS_TEST_FILTER[@]}"; do
        [ "$t" = "$pat" ] && return 0
        case "$t" in *"$pat"*) return 0 ;; esac
    done
    return 1
}

# ── pre-flight ───────────────────────────────────────────────────────────────

harness_preflight() {
    local bin
    for bin in docker curl jq python3; do
        command -v "$bin" >/dev/null 2>&1 || {
            echo "FATAL: $bin not on PATH" >&2
            return 2
        }
    done
    if [ "$HARNESS_USE_GPU" != "0" ] &&
        ! docker info 2>/dev/null | grep -qiE "nvidia|cdi:"; then
        echo "FATAL: docker daemon has no NVIDIA runtime — needs --gpus all" >&2
        return 2
    fi
    if ! docker image inspect "$HARNESS_IMAGE" >/dev/null 2>&1; then
        echo "FATAL: image $HARNESS_IMAGE not on host — build it first (make build-cuda)" >&2
        return 2
    fi
    if [ -n "$HARNESS_MODELS_FILE" ] && [ ! -f "$HARNESS_MODELS_FILE" ]; then
        echo "FATAL: HARNESS_MODELS_FILE is not a file: $HARNESS_MODELS_FILE" >&2
        return 2
    fi
    mkdir -p "$HARNESS_CACHE_DIR"
    return 0
}

# ── container lifecycle ──────────────────────────────────────────────────────

# Pick a free ephemeral port via the kernel. Tiny race window between
# bind+close and docker -p — if it does collide, docker run fails fast and
# the caller bails.
_harness_pick_port() {
    python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

_harness_cleanup() {
    local rc=$?
    if [ "${HARNESS_KEEP:-0}" = "1" ] && [ -n "$HARNESS_CONTAINER" ]; then
        echo ""
        echo "[harness] HARNESS_KEEP=1 — leaving ${HARNESS_CONTAINER} on port ${HARNESS_PORT}"
        echo "          logs: docker logs -f ${HARNESS_CONTAINER}"
        echo "          rm:   docker rm -f ${HARNESS_CONTAINER}"
        return $rc
    fi
    if [ -n "$HARNESS_CONTAINER" ]; then
        echo ""
        echo "[harness] tearing down ${HARNESS_CONTAINER}"
        docker rm -f "$HARNESS_CONTAINER" >/dev/null 2>&1 || true
    fi
    return $rc
}

# harness_start <models_csv>
# Spawns the container, waits /healthz, sets the read-only contract vars.
# Exits the calling script on any setup failure (set -eo pipefail propagates).
harness_start() {
    local models="$1"
    if [ -z "$models" ]; then
        echo "FATAL: harness_start needs a comma-separated model list" >&2
        return 2
    fi

    harness_preflight || return $?

    HARNESS_PORT="$(_harness_pick_port)"
    TALKIES_BASE_URL="http://127.0.0.1:${HARNESS_PORT}"
    HARNESS_ENABLED_MODELS="$models"
    HARNESS_CONTAINER="talkies-e2e-$$-${RANDOM}"
    export TALKIES_BASE_URL HARNESS_PORT HARNESS_ENABLED_MODELS HARNESS_CONTAINER

    trap _harness_cleanup EXIT

    echo "[harness] starting ${HARNESS_CONTAINER}"
    echo "          image:  ${HARNESS_IMAGE}"
    echo "          port:   ${HARNESS_PORT}"
    echo "          cache:  ${HARNESS_CACHE_DIR}"
    echo "          models: ${HARNESS_ENABLED_MODELS}"

    # Forward the log level into the container when the caller sets it, so
    # DEBUG body-logging can be exercised from a test / smoke run. Defaults
    # to the image's own default (info) when unset.
    local log_level_env=()
    if [ -n "${TALKIES_LOG_LEVEL:-}" ]; then
        log_level_env=(-e "TALKIES_LOG_LEVEL=${TALKIES_LOG_LEVEL}")
    fi

    local gpu_args=()
    if [ "$HARNESS_USE_GPU" != "0" ]; then
        gpu_args=(--gpus all)
    fi

    local models_file_args=()
    if [ -n "$HARNESS_MODELS_FILE" ]; then
        models_file_args=(-v "${HARNESS_MODELS_FILE}:/app/models.json:ro")
    fi

    docker run -d --rm "${gpu_args[@]}" \
        --name "$HARNESS_CONTAINER" \
        -v "${HARNESS_CACHE_DIR}:/data" \
        -e TALKIES_DEVICE="$HARNESS_DEVICE" \
        -e TALKIES_ENABLED_MODELS="${HARNESS_ENABLED_MODELS}" \
        "${log_level_env[@]}" \
        "${models_file_args[@]}" \
        -p "${HARNESS_PORT}:8000" \
        "$HARNESS_IMAGE" >/dev/null

    echo "[harness] waiting for /healthz (timeout ${HARNESS_READY_TIMEOUT}s)..."
    local i
    for ((i = 0; i < HARNESS_READY_TIMEOUT; i += 2)); do
        if curl -sf --max-time 5 "${TALKIES_BASE_URL}/healthz" >/dev/null 2>&1; then
            echo "[harness] /healthz ok (after ${i}s)"
            return 0
        fi
        if ! docker inspect -f '{{.State.Running}}' "$HARNESS_CONTAINER" 2>/dev/null |
            grep -q true; then
            echo "[harness] container exited during boot — last 80 lines:" >&2
            docker logs --tail 80 "$HARNESS_CONTAINER" >&2 2>&1 || true
            return 1
        fi
        sleep 2
    done
    echo "[harness] /healthz never came up in ${HARNESS_READY_TIMEOUT}s. Last logs:" >&2
    docker logs --tail 80 "$HARNESS_CONTAINER" >&2 2>&1 || true
    return 1
}

# ── test runner ──────────────────────────────────────────────────────────────

# harness_run_tests <test_func> [<test_func> ...]
# Invokes each named bash function, counts pass/fail, prints a summary, and
# returns 0 only if every test passed.
#
# Per-test filter — any positional args passed to the calling e2e file land
# in HARNESS_TEST_FILTER (set at source time) and act as a whitelist.
# Match: exact function name OR substring. Empty filter = run them all.
# Lets operators re-run just the failed cases without re-cycling the harness:
#
#     bash e2e_qwen3_modes.sh test_qwen3_clone_icl_1_7b
#     bash e2e_qwen3_modes.sh sampling          # substring → both sampling tests
harness_run_tests() {
    local pass=0 fail=0 skipped=0
    local failed=() skip=()
    local t
    for t in "$@"; do
        if [ "${#HARNESS_TEST_FILTER[@]}" -gt 0 ] && ! _harness_test_matches "$t"; then
            skipped=$((skipped + 1))
            skip+=("$t")
            continue
        fi
        echo ""
        echo "──[ $t ]──"
        if "$t"; then
            pass=$((pass + 1))
        else
            fail=$((fail + 1))
            failed+=("$t")
        fi
    done
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  $(basename "${BASH_SOURCE[1]:-suite}"): pass=$pass fail=$fail skip=$skipped total=$((pass + fail + skipped))"
    if [ "$fail" -ne 0 ]; then
        echo "  failed:"
        for t in "${failed[@]}"; do
            echo "    - $t"
        done
    fi
    if [ "$skipped" -ne 0 ] && [ "${HARNESS_VERBOSE:-0}" = "1" ]; then
        echo "  skipped by filter:"
        for t in "${skip[@]}"; do
            echo "    - $t"
        done
    fi
    echo "═══════════════════════════════════════════════════════════"
    [ "$fail" -eq 0 ]
}
