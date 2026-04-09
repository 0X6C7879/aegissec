#!/usr/bin/env bash

set -euo pipefail

PROFILE="${1:-core}"
SKILL_VENV="${SKILL_VENV:-/root/.aegissec-skill-tools/venv}"

log() {
  printf '[aegissec-skill-tools] %s\n' "$*"
}

warn() {
  printf '[aegissec-skill-tools] WARNING: %s\n' "$*" >&2
}

ensure_skill_venv() {
  if [[ ! -x "${SKILL_VENV}/bin/python3" ]]; then
    log "Creating skill venv at ${SKILL_VENV}"
    python3 -m venv "${SKILL_VENV}"
  fi

  "${SKILL_VENV}/bin/python3" -m pip install --no-cache-dir --upgrade pip >/dev/null 2>&1 || true
}

install_apt_packages() {
  local packages=("$@")

  if [[ "${#packages[@]}" -eq 0 ]]; then
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    warn "apt-get not found, skip apt extras"
    return
  fi

  log "Installing apt extras: ${packages[*]}"
  apt-get update -y >/dev/null 2>&1 || warn "apt-get update failed"

  for pkg in "${packages[@]}"; do
    if apt-get install -y "${pkg}" >/dev/null 2>&1; then
      log "Installed apt:${pkg}"
    else
      warn "Failed to install apt:${pkg}"
    fi
  done
}

install_pip_packages() {
  local packages=("$@")

  if [[ "${#packages[@]}" -eq 0 ]]; then
    return
  fi

  ensure_skill_venv

  log "Installing pip extras in ${SKILL_VENV}: ${packages[*]}"
  for pkg in "${packages[@]}"; do
    if "${SKILL_VENV}/bin/python3" -m pip install --no-cache-dir "${pkg}" >/dev/null 2>&1; then
      log "Installed pip:${pkg}"
    else
      warn "Failed to install pip:${pkg}"
    fi
  done
}

install_go_tools() {
  local tools=("$@")

  if [[ "${#tools[@]}" -eq 0 ]]; then
    return
  fi

  if ! command -v go >/dev/null 2>&1; then
    warn "go not found, skip go tool installs"
    return
  fi

  log "Installing go tools"
  for tool in "${tools[@]}"; do
    if go install "${tool}" >/dev/null 2>&1; then
      log "Installed go:${tool}"
    else
      warn "Failed to install go:${tool}"
    fi
  done
}

core_apt=(
  feroxbuster
)

core_pip=(
  dirsearch
  semgrep
  wafw00f
  xsstrike
)

core_go=(
  github.com/OJ/gobuster/v3/cmd/gobuster@latest
  github.com/projectdiscovery/dnsx/cmd/dnsx@latest
  github.com/projectdiscovery/httpx/cmd/httpx@latest
  github.com/projectdiscovery/nuclei/v2/cmd/nuclei@latest
  github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
  github.com/hahwul/dalfox/v2@latest
)

full_apt=(
  amass
)

full_pip=(
  jsonschema
  mythril
  pysarif
  sarif-tools
  slither-analyzer
)

full_go=(
  github.com/d3mondev/puredns/v2@latest
  github.com/owasp-amass/amass/v4/cmd/amass@latest
  github.com/praetorian-inc/fingerprintx/cmd/fingerprintx@latest
  github.com/tomnomnom/assetfinder@latest
)

case "${PROFILE}" in
  core)
    install_apt_packages "${core_apt[@]}"
    install_pip_packages "${core_pip[@]}"
    install_go_tools "${core_go[@]}"
    ;;
  full)
    install_apt_packages "${core_apt[@]}" "${full_apt[@]}"
    install_pip_packages "${core_pip[@]}" "${full_pip[@]}"
    install_go_tools "${core_go[@]}" "${full_go[@]}"
    ;;
  *)
    printf 'Unknown skill tool profile: %s\n' "${PROFILE}" >&2
    exit 2
    ;;
esac

log "Skill tool install finished (profile=${PROFILE})"
