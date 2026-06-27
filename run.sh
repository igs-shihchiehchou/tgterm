#!/usr/bin/env bash
# Load .env and start the bot via uv.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "No .env found. Copy .env.example to .env and fill it in." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

exec uv run tgterm
