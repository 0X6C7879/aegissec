#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="$REPO_ROOT/.aegissec"
LOG_DIR="$STATE_DIR/logs"
API_PID_FILE="$STATE_DIR/api.pid"
WEB_PID_FILE="$STATE_DIR/web.pid"

UV_PYTHON_VERSION="${UV_PYTHON_VERSION:-3.12}"
PNPM_VERSION="${PNPM_VERSION:-10.15.1}"
API_HOST="${AEGISSEC_API_HOST:-127.0.0.1}"
API_PORT="${AEGISSEC_API_PORT:-8000}"
WEB_HOST="${AEGISSEC_WEB_HOST:-127.0.0.1}"
WEB_PORT="${AEGISSEC_WEB_PORT:-5173}"

readonly SCRIPT_DIR
readonly REPO_ROOT
readonly STATE_DIR
readonly LOG_DIR
readonly API_PID_FILE
readonly WEB_PID_FILE

log() {
  printf '[aegissec-bootstrap] %s\n' "$*"
}

die() {
  printf '[aegissec-bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: bash scripts/bootstrap_ubuntu.sh [install|verify|start|stop|status|all]

Commands:
  install  Install Ubuntu system dependencies, project dependencies, and build the Kali image
  verify   Run backend/frontend checks and verify the Kali image is available
  start    Start the API and web dev servers in the background and wait for health checks
  stop     Stop background API and web dev servers started by this script
  status   Show process status, URLs, and log file locations
  all      Run install + verify + start (default)

Environment overrides:
  UV_PYTHON_VERSION   Python version used by uv (default: 3.12)
  AEGISSEC_API_HOST   API bind host (default: 127.0.0.1)
  AEGISSEC_API_PORT   API bind port (default: 8000)
  AEGISSEC_WEB_HOST   Web bind host (default: 127.0.0.1)
  AEGISSEC_WEB_PORT   Web bind port (default: 5173)
EOF
}

ensure_repo_root() {
  [[ -f "$REPO_ROOT/README.md" ]] || die "README.md not found; please run this script from the project repository"
  [[ -d "$REPO_ROOT/apps/api" ]] || die "apps/api not found; repository layout is incomplete"
  [[ -d "$REPO_ROOT/apps/web" ]] || die "apps/web not found; repository layout is incomplete"
}

ensure_non_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    die "Please run this script as a normal user with sudo privileges, not as root"
  fi
}

ensure_state_dirs() {
  mkdir -p "$STATE_DIR" "$LOG_DIR"
}

append_line_if_missing() {
  local file="$1"
  local line="$2"

  touch "$file"
  if ! grep -Fqx "$line" "$file"; then
    printf '\n%s\n' "$line" >>"$file"
  fi
}

ensure_user_local_bin_on_path() {
  export PATH="$HOME/.local/bin:$PATH"
  append_line_if_missing "$HOME/.profile" 'export PATH="$HOME/.local/bin:$PATH"'
  append_line_if_missing "$HOME/.bashrc" 'export PATH="$HOME/.local/bin:$PATH"'
}

require_sudo() {
  log "Refreshing sudo credentials"
  sudo -v
}

apt_install_base_packages() {
  log "Installing base Ubuntu packages"
  sudo apt-get update
  sudo apt-get install -y \
    bash \
    build-essential \
    ca-certificates \
    curl \
    git \
    procps \
    python3 \
    python3-pip \
    python3-venv \
    wget
}

ensure_uv() {
  ensure_user_local_bin_on_path
  if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ensure_user_local_bin_on_path
  fi

  log "Ensuring Python ${UV_PYTHON_VERSION} is available via uv"
  uv python install "$UV_PYTHON_VERSION"
}

node_major_version() {
  if ! command -v node >/dev/null 2>&1; then
    printf '0\n'
    return
  fi

  node -p "process.versions.node.split('.')[0]"
}

ensure_nodejs() {
  local node_major
  node_major="$(node_major_version)"

  if [[ "$node_major" -lt 20 ]]; then
    log "Installing Node.js 20.x"
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
  else
    log "Node.js $(node --version) already satisfies the requirement"
  fi
}

ensure_pnpm() {
  log "Activating pnpm via corepack"
  corepack enable
  corepack prepare "pnpm@${PNPM_VERSION}" --activate
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker using the requested installer"
    bash <(wget -qO- https://xuanyuan.cloud/docker.sh)
  else
    log "Docker already installed: $(docker --version)"
  fi

  sudo systemctl enable docker >/dev/null 2>&1 || true
  sudo systemctl restart docker
  sudo usermod -aG docker "$USER" >/dev/null 2>&1 || true
}

ensure_env_file() {
  if [[ ! -f "$REPO_ROOT/.env" ]]; then
    log "Creating .env from .env.example"
    cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
  else
    log ".env already exists; keeping current values"
  fi
}

install_project_dependencies() {
  log "Installing API dependencies"
  (
    cd "$REPO_ROOT/apps/api"
    uv sync --python "$UV_PYTHON_VERSION" --all-extras --dev
  )

  log "Installing web dependencies"
  (
    cd "$REPO_ROOT/apps/web"
    corepack pnpm install --frozen-lockfile
  )
}

ensure_kali_image() {
  if sudo docker image inspect aegissec-kali:latest >/dev/null 2>&1; then
    log "Kali image aegissec-kali:latest already exists"
    return
  fi

  log "Building Kali image aegissec-kali:latest"
  (
    cd "$REPO_ROOT"
    sudo docker build -t aegissec-kali:latest ./docker/kali
  )
}

run_verification() {
  log "Running backend verification"
  (
    cd "$REPO_ROOT/apps/api"
    uv sync --python "$UV_PYTHON_VERSION" --all-extras --dev
    uv run ruff check .
    uv run black --check .
    uv run mypy app tests
    uv run pytest
  )

  log "Running frontend verification"
  (
    cd "$REPO_ROOT/apps/web"
    corepack pnpm install --frozen-lockfile
    corepack pnpm lint
    corepack pnpm exec tsc -b
    corepack pnpm build
  )

  ensure_kali_image
}

is_pid_running() {
  local pid_file="$1"

  [[ -f "$pid_file" ]] || return 1

  local pid
  pid="$(cat "$pid_file")"
  [[ -n "$pid" ]] || return 1

  ps -p "$pid" >/dev/null 2>&1
}

start_service() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  local command="$4"

  if is_pid_running "$pid_file"; then
    log "$name is already running with PID $(cat "$pid_file")"
    return
  fi

  log "Starting $name"
  nohup setsid bash -lc "$command" >"$log_file" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" >"$pid_file"
}

stop_service() {
  local name="$1"
  local pid_file="$2"

  if ! is_pid_running "$pid_file"; then
    rm -f "$pid_file"
    log "$name is not running"
    return
  fi

  local pid
  pid="$(cat "$pid_file")"
  log "Stopping $name (PID $pid)"
  kill -TERM -- "-$pid" >/dev/null 2>&1 || kill -TERM "$pid" >/dev/null 2>&1 || true

  for _ in $(seq 1 20); do
    if ! ps -p "$pid" >/dev/null 2>&1; then
      rm -f "$pid_file"
      return
    fi
    sleep 1
  done

  kill -KILL -- "-$pid" >/dev/null 2>&1 || kill -KILL "$pid" >/dev/null 2>&1 || true
  rm -f "$pid_file"
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local timeout_seconds="$3"

  for _ in $(seq 1 "$timeout_seconds"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$label is ready: $url"
      return
    fi
    sleep 1
  done

  die "$label did not become ready within ${timeout_seconds}s. Check logs under $LOG_DIR"
}

start_stack() {
  ensure_state_dirs
  ensure_env_file

  start_service \
    "API server" \
    "$API_PID_FILE" \
    "$LOG_DIR/api.log" \
    "cd '$REPO_ROOT/apps/api' && export PATH='$HOME/.local/bin':\$PATH && uv sync --python '$UV_PYTHON_VERSION' --all-extras --dev >/dev/null && uv run uvicorn app.main:app --reload --host '$API_HOST' --port '$API_PORT'"

  start_service \
    "web server" \
    "$WEB_PID_FILE" \
    "$LOG_DIR/web.log" \
    "cd '$REPO_ROOT/apps/web' && export PATH='$HOME/.local/bin':\$PATH && corepack pnpm install --frozen-lockfile >/dev/null && corepack pnpm dev --host '$WEB_HOST' --port '$WEB_PORT'"

  wait_for_url "http://${API_HOST}:${API_PORT}/health" "API server" 120
  wait_for_url "http://${WEB_HOST}:${WEB_PORT}" "Web server" 120
}

show_status() {
  local api_status="stopped"
  local web_status="stopped"

  if is_pid_running "$API_PID_FILE"; then
    api_status="running (PID $(cat "$API_PID_FILE"))"
  fi

  if is_pid_running "$WEB_PID_FILE"; then
    web_status="running (PID $(cat "$WEB_PID_FILE"))"
  fi

  cat <<EOF
API: ${api_status}
Web: ${web_status}
API URL: http://${API_HOST}:${API_PORT}/health
Web URL: http://${WEB_HOST}:${WEB_PORT}
Logs: $LOG_DIR
EOF
}

install_all() {
  require_sudo
  apt_install_base_packages
  ensure_uv
  ensure_nodejs
  ensure_pnpm
  ensure_docker
  ensure_env_file
  install_project_dependencies
  ensure_kali_image
}

main() {
  local command="${1:-all}"

  ensure_repo_root
  ensure_non_root
  ensure_state_dirs

  case "$command" in
    install)
      install_all
      ;;
    verify)
      ensure_user_local_bin_on_path
      run_verification
      ;;
    start)
      ensure_user_local_bin_on_path
      start_stack
      show_status
      ;;
    stop)
      stop_service "web server" "$WEB_PID_FILE"
      stop_service "API server" "$API_PID_FILE"
      ;;
    status)
      show_status
      ;;
    all)
      install_all
      run_verification
      start_stack
      show_status
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
