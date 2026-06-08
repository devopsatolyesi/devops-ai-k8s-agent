#!/usr/bin/env bash
set -euo pipefail

export PIONEER_MODEL="${PIONEER_MODEL:-pioneer-fast}"
export PIONEER_MAX_TOKENS="${PIONEER_MAX_TOKENS:-700}"
export PIONEER_TEMPERATURE="${PIONEER_TEMPERATURE:-0.2}"
export AI_ENABLED="${AI_ENABLED:-false}"
export AI_MIN_SEVERITY="${AI_MIN_SEVERITY:-Medium}"
export SCAN_INTERVAL_SECONDS="${SCAN_INTERVAL_SECONDS:-600}"
export STORAGE_PATH="${STORAGE_PATH:-/tmp/ai-kube-agent.sqlite3}"

uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" --reload
