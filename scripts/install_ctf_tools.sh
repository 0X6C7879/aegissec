#!/usr/bin/env bash
# shellcheck disable=SC2024  # redirects target user-owned log file, not sudo
# Bootstrap competition-focused tooling for Kali runtime.
#
# Usage:
#   bash scripts/install_ctf_tools.sh [OPTIONS] MODE
#
# Modes:
#   python, apt, brew, gems, go, manual, all, --verify
#
# Options:
#   --dry-run   Show what would be installed without installing
#   --force     Reinstall packages even if already present
#
# Examples:
#   bash scripts/install_ctf_tools.sh all
#   bash scripts/install_ctf_tools.sh --dry-run all
#   bash scripts/install_ctf_tools.sh --force python
#   bash scripts/install_ctf_tools.sh --verify

set -euo pipefail

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

DRY_RUN=false
FORCE=false
MODE=""
FAILED=()
OPTIONAL_FAILED=()
SUCCEEDED=()
SKIPPED=()
LOG_DIR="${HOME}/.ctf-tools"
LOG_FILE=""
CTF_VENV="${HOME}/.ctf-tools/venv"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --force) FORCE=true; shift ;;
    -*) if [ -z "$MODE" ]; then MODE="$1"; shift; else echo "Unknown option: $1" >&2; exit 2; fi ;;
    *) MODE="$1"; shift ;;
  esac
done
MODE="${MODE:-all}"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

setup_logging() {
  mkdir -p "$LOG_DIR"
  LOG_FILE="${LOG_DIR}/install-$(date +%Y-%m-%d_%H%M%S).log"
  log_info "Logging to $LOG_FILE"
}

log_info() { echo "==> $*" | tee -a "${LOG_FILE:-/dev/null}"; }
log_warn() { echo "WARNING: $*" | tee -a "${LOG_FILE:-/dev/null}" >&2; }
log_error() { echo "ERROR: $*" | tee -a "${LOG_FILE:-/dev/null}" >&2; }
log_detail() { echo "    $*" >> "${LOG_FILE:-/dev/null}"; }

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log_error "'$cmd' is required but not found in PATH"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Skip-if-installed checks
# ---------------------------------------------------------------------------

# Check if a Python module is importable.
py_module_installed() {
  python3 -c "import $1" 2>/dev/null
}

# Check if an apt package is installed.
apt_pkg_installed() {
  dpkg -s "$1" >/dev/null 2>&1
}

# Check if a Homebrew formula is installed.
brew_pkg_installed() {
  brew list --formula "$1" >/dev/null 2>&1
}

# Check if a Ruby gem is installed.
gem_installed() {
  gem list -i "^${1}$" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# Pip package list — name=version:import_module
#
# Format: "pip_name==version:import_name"
# The import_name is used for skip-if-installed checks.
# ---------------------------------------------------------------------------

PIP_PACKAGES=(
  "flask-unsign==1.2.1:flask_unsign"
  "sqlmap==1.10.3:sqlmap"
  "scapy==2.7.0:scapy"
)

# ---------------------------------------------------------------------------
# Installers
# ---------------------------------------------------------------------------

install_python() {
  require_cmd python3 || return 1

  local pip_flags=()
  local pip_index_url="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

  # PEP 668: prefer creating a dedicated venv over --user
  if python3 -c "import sysconfig; marker = sysconfig.get_path('stdlib') + '/EXTERNALLY-MANAGED'; open(marker)" 2>/dev/null; then
    if [ -z "${VIRTUAL_ENV:-}" ]; then
      if [ "$DRY_RUN" = true ]; then
        log_info "PEP 668 detected — would create virtualenv at $CTF_VENV"
      else
        log_info "PEP 668 detected — creating virtualenv at $CTF_VENV"
        python3 -m venv "$CTF_VENV" 2>>"${LOG_FILE:-/dev/null}" || {
          log_warn "venv creation failed — falling back to --user"
          pip_flags+=(--user)
        }
        if [ -d "$CTF_VENV" ] && [ -z "${pip_flags[*]:-}" ]; then
          # shellcheck disable=SC1091
          source "$CTF_VENV/bin/activate"
          log_info "Activated virtualenv: $CTF_VENV"
          log_info "To reuse: source $CTF_VENV/bin/activate"
        fi
      fi
    fi
  fi

  # First pass: collect packages that need installing
  local to_install=()
  local to_install_display=()
  for entry in "${PIP_PACKAGES[@]}"; do
    local spec="${entry%%:*}"
    local mod="${entry##*:}"
    local name="${spec%%==*}"

    if [ "$FORCE" = false ] && py_module_installed "$mod"; then
      SKIPPED+=("pip:$name")
      continue
    fi
    to_install+=("$spec")
    to_install_display+=("$name")
  done

  if [ ${#to_install[@]} -eq 0 ]; then
    log_info "Python: all ${#PIP_PACKAGES[@]} packages already installed"
    return 0
  fi

  log_info "Python: ${#to_install[@]}/${#PIP_PACKAGES[@]} packages to install (${#SKIPPED[@]} skipped)"

  if [ "$DRY_RUN" = true ]; then
    log_info "Would install: ${to_install_display[*]}"
    return 0
  fi

  # Try batch install first (pip handles parallelism internally)
  log_info "Attempting batch install of ${#to_install[@]} packages"
  if python3 -m pip install -i "$pip_index_url" "${pip_flags[@]}" "${to_install[@]}" >>"$LOG_FILE" 2>&1; then
    for entry in "${to_install_display[@]}"; do
      SUCCEEDED+=("pip:$entry")
    done
    log_info "Batch install succeeded"
    return 0
  fi

  # Batch failed — fall back to one-by-one
  log_warn "Batch install failed — falling back to individual installs"
  for entry in "${PIP_PACKAGES[@]}"; do
    local spec="${entry%%:*}"
    local mod="${entry##*:}"
    local name="${spec%%==*}"

    if [ "$FORCE" = false ] && py_module_installed "$mod"; then
      continue
    fi

    if python3 -m pip install -i "$pip_index_url" "${pip_flags[@]}" "$spec" >>"$LOG_FILE" 2>&1; then
      SUCCEEDED+=("pip:$name")
    else
      log_warn "pip install failed: $name"
      log_detail "Failed command: python3 -m pip install ${pip_flags[*]} $spec"
      FAILED+=("pip:$name")
    fi
  done
}

install_apt() {
  require_cmd apt-get || return 1

  local packages=(
    nmap whois dnsutils tshark john hashcat
  )
  local optional_packages=()

  # Collect packages that need installing
  local to_install=()
  for pkg in "${packages[@]}"; do
    if [ "$FORCE" = false ] && apt_pkg_installed "$pkg"; then
      SKIPPED+=("apt:$pkg")
      continue
    fi
    to_install+=("$pkg")
  done

  local optional_to_install=()
  for pkg in "${optional_packages[@]}"; do
    if [ "$FORCE" = false ] && apt_pkg_installed "$pkg"; then
      SKIPPED+=("apt:$pkg")
      continue
    fi
    optional_to_install+=("$pkg")
  done

  if [ ${#to_install[@]} -eq 0 ]; then
    log_info "apt: all ${#packages[@]} packages already installed"
  else
    log_info "apt: ${#to_install[@]}/${#packages[@]} packages to install (${#SKIPPED[@]} skipped)"
  fi

  if [ ${#optional_to_install[@]} -eq 0 ]; then
    log_info "apt(optional): all ${#optional_packages[@]} packages already installed"
  else
    log_info "apt(optional): ${#optional_to_install[@]}/${#optional_packages[@]} packages to install"
  fi

  if [ ${#to_install[@]} -eq 0 ] && [ ${#optional_to_install[@]} -eq 0 ]; then
    return 0
  fi

  if [ "$DRY_RUN" = true ]; then
    if [ ${#to_install[@]} -gt 0 ]; then
      log_info "Would install: ${to_install[*]}"
    fi
    if [ ${#optional_to_install[@]} -gt 0 ]; then
      log_info "Would install optional: ${optional_to_install[*]}"
    fi
    return 0
  fi

  log_info "Updating apt package lists"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -o Acquire::Retries=5 -q >>"$LOG_FILE" 2>&1 || log_warn "apt-get update failed"

  for pkg in "${to_install[@]}"; do
    if sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q -o Acquire::Retries=5 --fix-missing "$pkg" >>"$LOG_FILE" 2>&1; then
      SUCCEEDED+=("apt:$pkg")
    else
      log_warn "apt install failed: $pkg"
      FAILED+=("apt:$pkg")
    fi
  done

  for pkg in "${optional_to_install[@]}"; do
    if sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q -o Acquire::Retries=5 --fix-missing "$pkg" >>"$LOG_FILE" 2>&1; then
      SUCCEEDED+=("apt:$pkg")
    else
      log_warn "optional apt install failed: $pkg (manual fallback remains supported)"
      OPTIONAL_FAILED+=("apt:$pkg")
    fi
  done
}

install_brew() {
  require_cmd brew || return 1

  local packages=(
    nmap whois bind wireshark john-jumbo hashcat
  )

  # Collect packages that need installing
  local to_install=()
  for pkg in "${packages[@]}"; do
    if [ "$FORCE" = false ] && brew_pkg_installed "$pkg"; then
      SKIPPED+=("brew:$pkg")
      continue
    fi
    to_install+=("$pkg")
  done

  if [ ${#to_install[@]} -eq 0 ]; then
    log_info "brew: all ${#packages[@]} packages already installed"
    return 0
  fi

  log_info "brew: ${#to_install[@]}/${#packages[@]} packages to install (${#SKIPPED[@]} skipped)"

  if [ "$DRY_RUN" = true ]; then
    log_info "Would install: ${to_install[*]}"
    return 0
  fi

  for pkg in "${to_install[@]}"; do
    if brew install "$pkg" >>"$LOG_FILE" 2>&1; then
      SUCCEEDED+=("brew:$pkg")
    else
      log_warn "brew install failed: $pkg"
      FAILED+=("brew:$pkg")
    fi
  done
}

install_gems() {
  if ! command -v gem >/dev/null 2>&1; then
    log_warn "gem not found — skipping Ruby gem installs (install Ruby to enable)"
    SKIPPED+=(gem:wpscan)
    return 0
  fi

  local packages=(wpscan)

  local to_install=()
  for pkg in "${packages[@]}"; do
    if [ "$FORCE" = false ] && gem_installed "$pkg"; then
      SKIPPED+=("gem:$pkg")
      continue
    fi
    to_install+=("$pkg")
  done

  if [ ${#to_install[@]} -eq 0 ]; then
    log_info "gems: all ${#packages[@]} gems already installed"
    return 0
  fi

  log_info "gems: ${#to_install[@]}/${#packages[@]} to install"

  if [ "$DRY_RUN" = true ]; then
    log_info "Would install: ${to_install[*]}"
    return 0
  fi

  local gem_source="${GEM_SOURCE:-https://gems.ruby-china.com}"

  for pkg in "${to_install[@]}"; do
    if gem install --source "$gem_source" "$pkg" >>"$LOG_FILE" 2>&1; then
      SUCCEEDED+=("gem:$pkg")
    else
      log_warn "gem install failed: $pkg"
      FAILED+=("gem:$pkg")
    fi
  done
}

install_go() {
  if ! command -v go >/dev/null 2>&1; then
    log_warn "go not found — skipping Go tool installs (install Go to enable)"
    SKIPPED+=(go:ffuf)
    return 0
  fi

  if [ "$FORCE" = false ] && command -v ffuf >/dev/null 2>&1; then
    log_info "go: ffuf already installed"
    SKIPPED+=(go:ffuf)
    return 0
  fi

  if [ "$DRY_RUN" = true ]; then
    log_info "Would install: ffuf"
    return 0
  fi

  local go_proxy="${GOPROXY:-https://goproxy.cn,direct}"

  log_info "Installing Go tools"
  if GOPROXY="$go_proxy" go install github.com/ffuf/ffuf/v2@latest >>"$LOG_FILE" 2>&1; then
    SUCCEEDED+=(go:ffuf)
  else
    log_warn "go install failed: ffuf"
    FAILED+=(go:ffuf)
  fi
}

print_manual() {
  local github_proxy_prefix="${GITHUB_PROXY_PREFIX-https://gh-proxy.org}"
  local ligolo_url="https://github.com/nicocha30/ligolo-ng"

  if [ -n "$github_proxy_prefix" ]; then
    ligolo_url="${github_proxy_prefix%/}/${ligolo_url}"
  fi

  cat <<EOF
Manual installs (cannot be automated reliably):
  Burp Suite Community — https://portswigger.net/burp/communitydownload
  Ligolo-ng            — ${ligolo_url}
  Neo4j Desktop        — https://neo4j.com/download/ (for local BloodHound analysis)
EOF
}

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

verify() {
  local missing=()
  local found=()

  # Activate ctf-tools venv if it exists (packages were installed there)
  if [ -d "$CTF_VENV/bin" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$CTF_VENV/bin/activate" 2>/dev/null && log_info "Using virtualenv: $CTF_VENV"
  fi

  local -a checks=(
    python3 nmap whois dig tshark john hashcat curl jq ffuf gem go
  )

  log_info "Verifying tool availability"
  for cmd in "${checks[@]}"; do
    if command -v "$cmd" >/dev/null 2>&1; then
      found+=("$cmd")
    else
      missing+=("$cmd")
    fi
  done

  # Python modules — use the same map from PIP_PACKAGES
  for entry in "${PIP_PACKAGES[@]}"; do
    local mod="${entry##*:}"
    local spec="${entry%%:*}"
    local name="${spec%%==*}"
    if python3 -c "import $mod" 2>/dev/null; then
      found+=("py:$name")
    else
      missing+=("py:$name")
    fi
  done

  echo ""
  echo "Found: ${#found[@]} tools/modules"
  echo "Missing: ${#missing[@]} tools/modules"
  if [ ${#missing[@]} -gt 0 ]; then
    echo ""
    echo "Missing:"
    for m in "${missing[@]}"; do
      echo "  - $m"
    done
  fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print_summary() {
  echo ""
  echo "========================================"
  echo " Install Summary"
  echo "========================================"
  echo " Installed: ${#SUCCEEDED[@]}"
  echo " Skipped:   ${#SKIPPED[@]} (already present)"
  echo " Failed:    ${#FAILED[@]}"
  if [ ${#FAILED[@]} -gt 0 ]; then
    echo ""
    echo " Failed packages:"
    for f in "${FAILED[@]}"; do
      echo "   - $f"
    done
  fi
  if [ ${#OPTIONAL_FAILED[@]} -gt 0 ]; then
    echo ""
    echo " Optional packages not installed automatically:"
    for f in "${OPTIONAL_FAILED[@]}"; do
      echo "   - $f"
    done
  fi
  echo "========================================"
  if [ -n "${LOG_FILE:-}" ]; then
    echo " Full log: $LOG_FILE"
    echo "========================================"
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Set up logging for install modes (not verify/manual/dry-run)
if [ "$DRY_RUN" = false ] && [ "$MODE" != "--verify" ] && [ "$MODE" != "manual" ]; then
  setup_logging
fi

case "$MODE" in
  python) install_python; print_summary ;;
  apt) install_apt; print_summary ;;
  brew) install_brew; print_summary ;;
  gems) install_gems; print_summary ;;
  go) install_go; print_summary ;;
  manual) print_manual ;;
  --verify) verify ;;
  all)
    install_python
    if command -v apt-get >/dev/null 2>&1; then
      install_apt
    elif command -v brew >/dev/null 2>&1; then
      install_brew
    else
      log_warn "Skip OS package install: neither apt nor brew was found."
    fi
    install_gems
    install_go
    print_manual
    print_summary
    ;;
  *)
    log_error "Unknown mode: $MODE"
    echo "Usage: $0 [--dry-run] [--force] {python|apt|brew|gems|go|manual|all|--verify}" >&2
    exit 2
    ;;
esac

# Exit with failure if any packages failed to install
if [ ${#FAILED[@]} -gt 0 ]; then
  exit 1
fi
