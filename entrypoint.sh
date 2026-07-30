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

# On-disk layout: ${TALKIES_DATA_DIR}/models/<slug>/ contains the full
# repo snapshot as plain files — no models--org--repo/snapshots/<hash>
# indirection, no symlinks, no .cache/ blob store. A directory's mere
# existence is the "cached" signal: if it's there we skip the download,
# otherwise we snapshot_download(local_dir=...) the whole repo into it.
#
# HF_HUB_OFFLINE is unset for the prefetch sub-shell only — the server
# process re-inherits whatever the image / env defines (prod defaults to
# HF_HUB_OFFLINE=1, so post-prefetch the server runs fully offline).
echo "[entrypoint] resolving enabled models (TALKIES_ENABLED_MODELS=${TALKIES_ENABLED_MODELS:-<all>})"
(
	unset HF_HUB_OFFLINE
	python3 -c "
import json, os, sys
from pathlib import Path
from huggingface_hub import snapshot_download

with open(os.environ['TALKIES_MODELS_FILE']) as fh:
    reg = json.load(fh)['models']

raw = os.environ.get('TALKIES_ENABLED_MODELS', '').strip()
if raw:
    enabled = [s.strip() for s in raw.split(',') if s.strip()]
    missing = [s for s in enabled if s not in reg]
    if missing:
        print(
            f'[entrypoint] TALKIES_ENABLED_MODELS contains unknown slug(s) '
            f'{missing}; known: {sorted(reg)}',
            file=sys.stderr,
        )
        sys.exit(1)
else:
    enabled = list(reg)

models_root = Path(os.environ['TALKIES_DATA_DIR']) / 'models'
print(f'[entrypoint] prefetching {len(enabled)} model(s) into {models_root}: {enabled}')
for slug in enabled:
    repo = reg[slug]['repo']
    revision = reg[slug].get('revision')
    target = models_root / slug
    # Variant registries may name an exact artifact allowlist. This keeps model
    # families that share a Hugging Face repository from downloading every
    # precision/context variant for each user-visible slug.
    download_patterns = reg[slug].get('download_patterns')
    if download_patterns is not None:
        if (
            not isinstance(download_patterns, list)
            or not download_patterns
            or any(
                not isinstance(pattern, str) or not pattern
                for pattern in download_patterns
            )
        ):
            print(
                f'[entrypoint] invalid download_patterns for {slug}',
                file=sys.stderr,
            )
            sys.exit(1)
    # parakeet.cpp slugs predate generic download_patterns. Keep their current
    # narrow download contract while new registries use the generic field.
    gguf_file = reg[slug].get('gguf_file')
    if target.is_dir() and any(target.iterdir()):
        print(f'[entrypoint] cached: {slug} -> {target}')
    else:
        ref = f'@{revision}' if revision else ''
        print(f'[entrypoint] downloading: {slug} ({repo}{ref}) -> {target}')
        target.mkdir(parents=True, exist_ok=True)
        if download_patterns:
            snapshot_download(
                repo,
                local_dir=str(target),
                revision=revision,
                allow_patterns=download_patterns,
            )
        elif gguf_file:
            snapshot_download(
                repo,
                local_dir=str(target),
                revision=revision,
                allow_patterns=[gguf_file, 'README.md', 'LICENSE', 'config.json'],
            )
        else:
            snapshot_download(repo, local_dir=str(target), revision=revision)
    # Dependency repos go into the standard HF cache (HF_HOME), not into
    # a flat per-slug dir. They're consumed by transformers/AutoTokenizer
    # inside the model's __init__ — those readers only know how to find
    # repos via the HF cache layout, so don't fight it. Server runs with
    # HF_HUB_OFFLINE=1, hence the prefetch here.
    for dep_repo in reg[slug].get('dependencies', []) or []:
        print(f'[entrypoint] prefetching dep for {slug}: {dep_repo}')
        snapshot_download(dep_repo)
print('[entrypoint] prefetch done')
"
)

exec python3 -m talkies
