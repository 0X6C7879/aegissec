#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/apps/api"

uv sync --all-extras --dev
uv run uvicorn app.main:app --reload --host "${AEGISSEC_API_HOST:-127.0.0.1}" --port "${AEGISSEC_API_PORT:-8000}"
