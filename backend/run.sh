#!/usr/bin/env bash
# Start the ClassMate API in dev mode (auto-reload).
# Override host/port via env: PORT=4000 ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
exec uv run uvicorn app.main:app --reload --host "${HOST:-0.0.0.0}" --port "${PORT:-3001}"
