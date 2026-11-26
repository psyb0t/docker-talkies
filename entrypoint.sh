#!/bin/sh
# talkies entrypoint — prefetches enabled models into a flat per-model
# directory layout, then execs the server.
set -eu

: "${TALKIES_DEVICE:=auto}"
: "${TALKIES_MODELS_FILE:=/app/models.json}"
: "${TALKIES_DATA_DIR:=/data}"
: "${TALKIES_ENABLED_MODELS:=}"

export TALKIES_DEVICE
export TALKIES_MODELS_FILE TALKIES_DATA_DIR
export TALKIES_ENABLED_MODELS

mkdir -p "${TALKIES_DATA_DIR}/models"
mkdir -p "${TALKIES_DATA_DIR}/files"
mkdir -p "${TALKIES_DATA_DIR}/custom-voices"

case "${TALKIES_MODEL_DOWNLOAD:-startup}" in
    startup)
        (
            unset HF_HUB_OFFLINE
            python3 -m talkies.model_weights
        )
        ;;
    on-demand) ;;
    *)
        echo "[entrypoint] TALKIES_MODEL_DOWNLOAD must be startup or on-demand" >&2
        exit 1
        ;;
esac

exec python3 -m talkies
