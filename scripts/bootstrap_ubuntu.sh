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
API_HOST="${AEGISSEC_API_HOST:-0.0.0.0}"
API_PORT="${AEGISSEC_API_PORT:-8000}"
WEB_HOST="${AEGISSEC_WEB_HOST:-0.0.0.0}"
WEB_PORT="${AEGISSEC_WEB_PORT:-5173}"
KALI_IMAGE_TAG="${AEGISSEC_KALI_IMAGE:-aegissec-kali:latest}"
KALI_INSTALL_CTF_TOOLS="${AEGISSEC_KALI_INSTALL_CTF_TOOLS:-1}"
KALI_CTF_INSTALL_MODE="${AEGISSEC_KALI_CTF_INSTALL_MODE:-all}"
KALI_INSTALL_SKILL_TOOLS="${AEGISSEC_KALI_INSTALL_SKILL_TOOLS:-1}"
KALI_SKILL_TOOL_PROFILE="${AEGISSEC_KALI_SKILL_TOOL_PROFILE:-core}"
KALI_FORCE_REBUILD="${AEGISSEC_KALI_FORCE_REBUILD:-0}"
KALI_DOCKERFILE_PATH="$REPO_ROOT/docker/kali/Dockerfile"
KALI_INSTALL_SCRIPT_PATH="$REPO_ROOT/scripts/install_ctf_tools.sh"

readonly SCRIPT_DIR
readonly REPO_ROOT
readonly STATE_DIR
readonly LOG_DIR
readonly API_PID_FILE
readonly WEB_PID_FILE
readonly KALI_DOCKERFILE_PATH
readonly KALI_INSTALL_SCRIPT_PATH

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

This script is intended for a fresh Ubuntu 24 host and bootstraps the full local
development stack from system packages to background services.

Commands:
  install  Install Ubuntu system dependencies, project dependencies, and build the Kali image
  verify   Run the canonical project checks and verify the Kali image is available
  start    Start the API and web dev servers in the background and wait for health checks
  stop     Stop background API and web dev servers started by this script
  status   Show process status, URLs, and log file locations
  all      Run install + verify + start (default)

Environment overrides:
  UV_PYTHON_VERSION   Python version used by uv (default: 3.12)
  AEGISSEC_API_HOST   API bind host (default: 0.0.0.0)
  AEGISSEC_API_PORT   API bind port (default: 8000)
  AEGISSEC_WEB_HOST   Web bind host (default: 0.0.0.0)
  AEGISSEC_WEB_PORT   Web bind port (default: 5173)
  AEGISSEC_KALI_IMAGE                 Kali image tag (default: aegissec-kali:latest)
  AEGISSEC_KALI_INSTALL_CTF_TOOLS     Install scripts/install_ctf_tools.sh in image build (default: 1)
  AEGISSEC_KALI_CTF_INSTALL_MODE      install_ctf_tools mode (default: all)
  AEGISSEC_KALI_INSTALL_SKILL_TOOLS   Install additional tools for existing skills (default: 1)
  AEGISSEC_KALI_SKILL_TOOL_PROFILE    Skill tool profile: core|full (default: core)
  AEGISSEC_KALI_FORCE_REBUILD         Always rebuild Kali image (default: 0)
EOF
}

ensure_repo_root() {
  [[ -f "$REPO_ROOT/README.md" ]] || die "README.md not found; please run this script from the project repository"
  [[ -d "$REPO_ROOT/apps/api" ]] || die "apps/api not found; repository layout is incomplete"
  [[ -d "$REPO_ROOT/apps/web" ]] || die "apps/web not found; repository layout is incomplete"
}

ensure_supported_ubuntu() {
  [[ -f /etc/os-release ]] || die "Cannot determine operating system; /etc/os-release is missing"

  # shellcheck disable=SC1091
  . /etc/os-release

  [[ "${ID:-}" == "ubuntu" ]] || die "This bootstrap script only supports Ubuntu hosts"
  [[ "${VERSION_ID:-}" == 24* ]] || die "This bootstrap script targets Ubuntu 24.x; detected ${VERSION_ID:-unknown}"
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
    apt-transport-https \
    bash \
    build-essential \
    ca-certificates \
    curl \
    git \
    gnupg \
    lsb-release \
    pipx \
    procps \
    python3 \
    python-is-python3 \
    python3-pip \
    python3-venv \
    software-properties-common \
    wget
}

ensure_uv() {
  ensure_user_local_bin_on_path
  if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv via pipx"
    pipx install uv
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
  local node_repo_file="/etc/apt/sources.list.d/nodesource.list"
  local node_keyring="/etc/apt/keyrings/nodesource.gpg"
  local architecture
  node_major="$(node_major_version)"

  if [[ "$node_major" -lt 20 ]]; then
    architecture="$(dpkg --print-architecture)"
    log "Installing Node.js 20.x from the official NodeSource apt repository"
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --dearmor --yes -o "$node_keyring"
    sudo chmod a+r "$node_keyring"
    printf 'deb [arch=%s signed-by=%s] https://deb.nodesource.com/node_20.x nodistro main\n' \
      "$architecture" \
      "$node_keyring" | sudo tee "$node_repo_file" >/dev/null
    sudo apt-get update
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
  local docker_repo_file="/etc/apt/sources.list.d/docker.list"
  local docker_keyring="/etc/apt/keyrings/docker.asc"
  local architecture
  local ubuntu_codename

  if ! command -v docker >/dev/null 2>&1; then
    architecture="$(dpkg --print-architecture)"
    ubuntu_codename="$({
      # shellcheck disable=SC1091
      . /etc/os-release
      printf '%s' "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    })"
    [[ -n "$ubuntu_codename" ]] || die "Could not determine Ubuntu codename for Docker apt repository"

    log "Installing Docker from the official Docker apt repository"
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o "$docker_keyring"
    sudo chmod a+r "$docker_keyring"
    printf 'deb [arch=%s signed-by=%s] https://download.docker.com/linux/ubuntu %s stable\n' \
      "$architecture" \
      "$docker_keyring" \
      "$ubuntu_codename" | sudo tee "$docker_repo_file" >/dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  else
    log "Docker already installed: $(docker --version)"
  fi

  if sudo systemctl list-unit-files docker.service >/dev/null 2>&1; then
    sudo systemctl enable docker >/dev/null 2>&1 || true
    if ! sudo systemctl restart docker; then
      if sudo docker info >/dev/null 2>&1; then
        log "Docker daemon is reachable even though docker.service restart failed; continuing"
      else
        die "Docker appears installed, but docker.service could not be restarted"
      fi
    fi
  elif sudo docker info >/dev/null 2>&1; then
    log "Docker daemon is reachable; skipping systemctl management because docker.service is unavailable"
  else
    die "Docker appears installed, but docker.service is unavailable and the daemon is not reachable"
  fi

  sudo usermod -aG docker "$USER" >/dev/null 2>&1 || true
  log "Docker group membership has been refreshed for $USER; open a new shell or run 'newgrp docker' if you want docker commands without sudo"
}

sha256_file() {
  local file="$1"
  sha256sum "$file" | awk '{print $1}'
}

docker_label_or_empty() {
  local image="$1"
  local label="$2"
  local value

  value="$(sudo docker image inspect --format "{{ index .Config.Labels \"${label}\" }}" "$image" 2>/dev/null || true)"
  if [[ "$value" == "<no value>" ]]; then
    value=""
  fi
  printf '%s\n' "$value"
}

kali_image_matches_requested_profile() {
  local desired_installer_sha="$1"

  if ! sudo docker image inspect "$KALI_IMAGE_TAG" >/dev/null 2>&1; then
    return 1
  fi

  local image_ctf_tools
  local image_ctf_mode
  local image_skill_tools
  local image_skill_profile
  local image_installer_sha

  image_ctf_tools="$(docker_label_or_empty "$KALI_IMAGE_TAG" "aegissec.install_ctf_tools")"
  image_ctf_mode="$(docker_label_or_empty "$KALI_IMAGE_TAG" "aegissec.ctf_install_mode")"
  image_skill_tools="$(docker_label_or_empty "$KALI_IMAGE_TAG" "aegissec.install_skill_tools")"
  image_skill_profile="$(docker_label_or_empty "$KALI_IMAGE_TAG" "aegissec.skill_tool_profile")"
  image_installer_sha="$(docker_label_or_empty "$KALI_IMAGE_TAG" "aegissec.ctf_installer_sha")"

  [[ "$image_ctf_tools" == "$KALI_INSTALL_CTF_TOOLS" ]] || return 1
  [[ "$image_ctf_mode" == "$KALI_CTF_INSTALL_MODE" ]] || return 1
  [[ "$image_skill_tools" == "$KALI_INSTALL_SKILL_TOOLS" ]] || return 1
  [[ "$image_skill_profile" == "$KALI_SKILL_TOOL_PROFILE" ]] || return 1

  if [[ "$KALI_INSTALL_CTF_TOOLS" == "1" ]]; then
    [[ "$image_installer_sha" == "$desired_installer_sha" ]] || return 1
  fi

  return 0
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
  local installer_sha=""

  if [[ "$KALI_INSTALL_CTF_TOOLS" == "1" ]]; then
    [[ -f "$KALI_INSTALL_SCRIPT_PATH" ]] || die "Missing $KALI_INSTALL_SCRIPT_PATH required for Kali preinstall"
    installer_sha="$(sha256_file "$KALI_INSTALL_SCRIPT_PATH")"
  fi

  if [[ "$KALI_FORCE_REBUILD" != "1" ]] && kali_image_matches_requested_profile "$installer_sha"; then
    log "Kali image $KALI_IMAGE_TAG already exists and matches requested tool profile"
    return
  fi

  if [[ "$KALI_FORCE_REBUILD" == "1" ]]; then
    log "Forcing Kali image rebuild because AEGISSEC_KALI_FORCE_REBUILD=1"
  elif sudo docker image inspect "$KALI_IMAGE_TAG" >/dev/null 2>&1; then
    log "Existing Kali image does not match requested tool profile; rebuilding"
  fi

  log "Building Kali image $KALI_IMAGE_TAG"
  log "Kali preinstall config: ctf_tools=$KALI_INSTALL_CTF_TOOLS, ctf_mode=$KALI_CTF_INSTALL_MODE, skill_tools=$KALI_INSTALL_SKILL_TOOLS, skill_profile=$KALI_SKILL_TOOL_PROFILE"
  (
    cd "$REPO_ROOT"
    sudo docker build \
      --build-arg INSTALL_CTF_TOOLS="$KALI_INSTALL_CTF_TOOLS" \
      --build-arg CTF_INSTALL_MODE="$KALI_CTF_INSTALL_MODE" \
      --build-arg INSTALL_SKILL_TOOLS="$KALI_INSTALL_SKILL_TOOLS" \
      --build-arg SKILL_TOOL_PROFILE="$KALI_SKILL_TOOL_PROFILE" \
      --build-arg CTF_INSTALLER_SHA="$installer_sha" \
      -t "$KALI_IMAGE_TAG" \
      -f "$KALI_DOCKERFILE_PATH" \
      .
  )
}

run_verification() {
  ensure_user_local_bin_on_path
  ensure_env_file

  log "Running canonical project verification via scripts/check.py"
  (
    cd "$REPO_ROOT"
    python3 scripts/check.py
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

access_host_for_bind_host() {
  local bind_host="$1"

  case "$bind_host" in
    "0.0.0.0")
      printf '127.0.0.1\n'
      ;;
    "::"|"[::]")
      printf 'localhost\n'
      ;;
    *)
      printf '%s\n' "$bind_host"
      ;;
  esac
}

start_stack() {
  ensure_state_dirs
  ensure_env_file
  local api_access_host
  local api_command
  local web_command
  local web_access_host
  api_access_host="$(access_host_for_bind_host "$API_HOST")"
  web_access_host="$(access_host_for_bind_host "$WEB_HOST")"

  printf -v api_command "cd %q && export PATH=%q:\$PATH && uv sync --python %q --all-extras --dev >/dev/null && uv run uvicorn app.main:app --reload --host %q --port %q" \
    "$REPO_ROOT/apps/api" \
    "$HOME/.local/bin" \
    "$UV_PYTHON_VERSION" \
    "$API_HOST" \
    "$API_PORT"

  printf -v web_command "cd %q && export PATH=%q:\$PATH && corepack pnpm install --frozen-lockfile >/dev/null && corepack pnpm dev --host %q --port %q" \
    "$REPO_ROOT/apps/web" \
    "$HOME/.local/bin" \
    "$WEB_HOST" \
    "$WEB_PORT"

  start_service \
    "API server" \
    "$API_PID_FILE" \
    "$LOG_DIR/api.log" \
    "$api_command"

  start_service \
    "web server" \
    "$WEB_PID_FILE" \
    "$LOG_DIR/web.log" \
    "$web_command"

  wait_for_url "http://${api_access_host}:${API_PORT}/health" "API server" 120
  wait_for_url "http://${web_access_host}:${WEB_PORT}" "Web server" 120
}

show_status() {
  local api_status="stopped"
  local web_status="stopped"
  local api_access_host
  local web_access_host
  api_access_host="$(access_host_for_bind_host "$API_HOST")"
  web_access_host="$(access_host_for_bind_host "$WEB_HOST")"

  if is_pid_running "$API_PID_FILE"; then
    api_status="running (PID $(cat "$API_PID_FILE"))"
  fi

  if is_pid_running "$WEB_PID_FILE"; then
    web_status="running (PID $(cat "$WEB_PID_FILE"))"
  fi

  cat <<EOF
API: ${api_status}
Web: ${web_status}
API URL: http://${api_access_host}:${API_PORT}/health
Web URL: http://${web_access_host}:${WEB_PORT}
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
  ensure_supported_ubuntu
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
