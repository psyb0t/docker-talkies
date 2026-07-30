#!/bin/bash
# Real WebSocket tests for the optional Sherpa-ONNX and Vosk streaming engines.
# The pinned custom registry makes each test reproducible while keeping those
# models out of Talkies' default image catalog.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR="$script_dir"
readonly REGISTRY_FILE="${SCRIPT_DIR}/streaming-custom-models.json"
readonly CUSTOM_CACHE_DIR="${SCRIPT_DIR}/../../.e2e-cache-streaming-custom"
readonly SHERPA_FIXTURE="${CUSTOM_CACHE_DIR}/models/sherpa-stream-test/test_wavs/0.wav"
readonly SHERPA_EXPECTED_TEXT="AFTER EARLY NIGHTFALL THE YELLOW LAMPS WOULD LIGHT UP HERE AND THERE THE SQUALID QUARTER OF THE BROTHELS"
readonly SHERPA_EXPECTED_WORDS="after,nightfall,lamps"

run_stream_test() {
    local slug="$1"
    local fixture_args=()
    if [ "$slug" = "sherpa-stream-test" ]; then
        fixture_args=(
            "FIXTURE_MP3=$SHERPA_FIXTURE"
            "EXPECTED_TEXT=$SHERPA_EXPECTED_TEXT"
            "EXPECTED_WORDS_CSV=$SHERPA_EXPECTED_WORDS"
        )
    fi
    echo "[custom-stream] testing ${slug}"
    env \
        "HARNESS_MODELS_FILE=$REGISTRY_FILE" \
        "HARNESS_CACHE_DIR=$CUSTOM_CACHE_DIR" \
        "HARNESS_IMAGE=${HARNESS_IMAGE:-psyb0t/talkies:local}" \
        HARNESS_DEVICE="cpu" \
        HARNESS_USE_GPU="0" \
        "STREAM_MODELS=$slug" \
        "ASR_SLUG=$slug" \
        "${fixture_args[@]}" \
        bash "${SCRIPT_DIR}/e2e_streaming_asr.sh"
}

run_stream_test sherpa-stream-test
run_stream_test vosk-stream-test
