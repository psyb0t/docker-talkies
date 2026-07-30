#!/usr/bin/env bash
# Bump [tool.uv] exclude-newer to the UTC midnight seven days ago.
# Called by Makefile pkg-* targets before any dependency mutation so the
# supply-chain age gate excludes the newest seven-day attack window.
set -euo pipefail

PYPROJECT="${1:-pyproject.toml}"
CUTOFF="$(date -u -d '7 days ago' +%Y-%m-%dT00:00:00Z)"

if [ ! -f "$PYPROJECT" ]; then
    echo "bump_exclude_newer: $PYPROJECT not found" >&2
    exit 1
fi

if ! grep -qE '^exclude-newer\s*=' "$PYPROJECT"; then
    echo "bump_exclude_newer: no exclude-newer line in $PYPROJECT" >&2
    exit 1
fi

tmp="$(mktemp)"
sed -E "s|^(exclude-newer\s*=\s*).*|\1\"${CUTOFF}\"|" "$PYPROJECT" >"$tmp"
mv "$tmp" "$PYPROJECT"

echo "exclude-newer -> ${CUTOFF}"
