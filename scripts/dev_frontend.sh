#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/apps/web"

corepack pnpm install
corepack pnpm dev --host "${AEGISSEC_WEB_HOST:-0.0.0.0}" --port "${AEGISSEC_WEB_PORT:-5173}"
