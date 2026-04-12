#!/usr/bin/env bash

set -euo pipefail

PROFILE="${1:-lean}"
SKILL_VENV="${SKILL_VENV:-/root/.aegissec-skill-tools/venv}"
LOCAL_BIN_DIR="${LOCAL_BIN_DIR:-/usr/local/bin}"
KUBEAUDIT_VERSION="${KUBEAUDIT_VERSION:-0.22.2}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
GOPROXY="${GOPROXY:-https://goproxy.cn,direct}"
GITHUB_PROXY_PREFIX="${GITHUB_PROXY_PREFIX-https://gh-proxy.org}"
PREWARM_NUCLEI_TEMPLATES="${PREWARM_NUCLEI_TEMPLATES:-0}"

log() {
  printf '[aegissec-skill-tools] %s\n' "$*"
}

warn() {
  printf '[aegissec-skill-tools] WARNING: %s\n' "$*" >&2
}

proxy_github_url() {
  local url="$1"
  local proxy_prefix="${GITHUB_PROXY_PREFIX%/}"

  if [[ -z "${proxy_prefix}" ]]; then
    printf '%s\n' "${url}"
    return
  fi

  case "${url}" in
    https://github.com/*|https://raw.githubusercontent.com/*)
      printf '%s/%s\n' "${proxy_prefix}" "${url}"
      ;;
    *)
      printf '%s\n' "${url}"
      ;;
  esac
}

ensure_skill_venv() {
  if [[ ! -x "${SKILL_VENV}/bin/python3" ]]; then
    log "Creating skill venv at ${SKILL_VENV}"
    python3 -m venv "${SKILL_VENV}"
  fi

  "${SKILL_VENV}/bin/python3" -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" --upgrade pip >/dev/null 2>&1 || true
}

install_apt_packages() {
  local packages=("$@")
  local to_install=()

  if [[ "${#packages[@]}" -eq 0 ]]; then
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    warn "apt-get not found, skip apt extras"
    return
  fi

  for pkg in "${packages[@]}"; do
    if dpkg -s "${pkg}" >/dev/null 2>&1; then
      log "Skip already installed apt:${pkg}"
    else
      to_install+=("${pkg}")
    fi
  done

  if [[ "${#to_install[@]}" -eq 0 ]]; then
    return
  fi

  log "Installing apt extras: ${to_install[*]}"
  apt-get update -y -o Acquire::Retries=5 >/dev/null 2>&1 || warn "apt-get update failed"

  for pkg in "${to_install[@]}"; do
    if apt-get install -y -o Acquire::Retries=5 --fix-missing "${pkg}" >/dev/null 2>&1; then
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
    if "${SKILL_VENV}/bin/python3" -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" "${pkg}" >/dev/null 2>&1; then
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
    if GOPROXY="${GOPROXY}" go install "${tool}" >/dev/null 2>&1; then
      log "Installed go:${tool}"
    else
      warn "Failed to install go:${tool}"
    fi
  done
}

install_cargo_tools() {
  local tools=("$@")

  if [[ "${#tools[@]}" -eq 0 ]]; then
    return
  fi

  if ! command -v cargo >/dev/null 2>&1; then
    warn "cargo not found, skip cargo tool installs"
    return
  fi

  install -d -m 0755 "${LOCAL_BIN_DIR}"

  log "Installing cargo tools"
  for tool in "${tools[@]}"; do
    if cargo install --locked --root /root/.local "${tool}" >/dev/null 2>&1; then
      log "Installed cargo:${tool}"
    else
      warn "Failed to install cargo:${tool}"
    fi
  done
}

install_release_tarball_binary() {
  local name="$1"
  local url="$2"
  local archive_member="$3"
  local tmpdir
  local archive
  local resolved_url
  local source_path

  if command -v "${name}" >/dev/null 2>&1; then
    log "Skip already installed binary:${name}"
    return
  fi

  tmpdir="$(mktemp -d)"
  archive="${tmpdir}/${name}.tar.gz"
  resolved_url="$(proxy_github_url "${url}")"

  if ! wget -qO "${archive}" "${resolved_url}"; then
    warn "Failed to download ${name} from ${resolved_url}"
    rm -rf "${tmpdir}"
    return
  fi

  if ! tar -xzf "${archive}" -C "${tmpdir}"; then
    warn "Failed to extract ${name} archive"
    rm -rf "${tmpdir}"
    return
  fi

  source_path="$(find "${tmpdir}" -type f \( -name "${archive_member}" -o -name "${name}" \) | head -n 1)"
  if [[ -z "${source_path}" ]]; then
    warn "Could not locate ${archive_member} inside ${name} archive"
    rm -rf "${tmpdir}"
    return
  fi

  install -d -m 0755 "${LOCAL_BIN_DIR}"
  if install -m 0755 "${source_path}" "${LOCAL_BIN_DIR}/${name}"; then
    log "Installed binary:${name}"
  else
    warn "Failed to install binary:${name}"
  fi

  rm -rf "${tmpdir}"
}

install_additional_release_tools() {
  local arch
  local kubeaudit_arch
  local kubeaudit_url

  arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "${arch}" in
    amd64|x86_64)
      kubeaudit_arch="amd64"
      ;;
    arm64|aarch64)
      kubeaudit_arch="arm64"
      ;;
    *)
      warn "Unsupported architecture for kubeaudit preinstall: ${arch}"
      return
      ;;
  esac

  kubeaudit_url="https://github.com/Shopify/kubeaudit/releases/download/v${KUBEAUDIT_VERSION}/kubeaudit_${KUBEAUDIT_VERSION}_linux_${kubeaudit_arch}.tar.gz"

  install_release_tarball_binary \
    "kubeaudit" \
    "${kubeaudit_url}" \
    "kubeaudit"

  if ! command -v kubeaudit >/dev/null 2>&1; then
    if command -v go >/dev/null 2>&1; then
      log "Falling back to go install for kubeaudit@v${KUBEAUDIT_VERSION}"
      if GOBIN="${LOCAL_BIN_DIR}" GOPROXY="${GOPROXY}" go install "github.com/Shopify/kubeaudit/cmd@v${KUBEAUDIT_VERSION}" >/dev/null 2>&1; then
        log "Installed go:kubeaudit@v${KUBEAUDIT_VERSION}"
      else
        warn "Failed fallback go install for kubeaudit@v${KUBEAUDIT_VERSION}"
      fi
    else
      warn "go not found, cannot use kubeaudit fallback installer"
    fi
  fi
}

create_compatibility_aliases() {
  local alias_name
  local target_name
  local target_path
  local pair
  local impacket_aliases=(
    "GetNPUsers.py:impacket-GetNPUsers"
    "GetUserSPNs.py:impacket-GetUserSPNs"
    "psexec.py:impacket-psexec"
    "secretsdump.py:impacket-secretsdump"
    "smbexec.py:impacket-smbexec"
    "ticketConverter.py:impacket-ticketConverter"
    "wmiexec.py:impacket-wmiexec"
  )

  install -d -m 0755 "${LOCAL_BIN_DIR}"

  for pair in "${impacket_aliases[@]}"; do
    alias_name="${pair%%:*}"
    target_name="${pair##*:}"
    if ! target_path="$(command -v "${target_name}" 2>/dev/null)"; then
      continue
    fi
    ln -sf "${target_path}" "${LOCAL_BIN_DIR}/${alias_name}"
    log "Created compatibility alias ${alias_name} -> ${target_name}"
  done
}

prewarm_tool_state() {
  if [[ "${PREWARM_NUCLEI_TEMPLATES}" != "1" ]]; then
    log "Skip nuclei template prewarm (set PREWARM_NUCLEI_TEMPLATES=1 to enable)"
    return
  fi

  if command -v nuclei >/dev/null 2>&1; then
    log "Prewarming nuclei templates"
    if nuclei -update-templates >/dev/null 2>&1; then
      log "Prewarmed nuclei templates in /root/nuclei-templates"
    else
      warn "Failed to prewarm nuclei templates"
    fi
  fi
}

lean_apt=(
)

lean_pip=(
  kube-hunter
  roadrecon
  semgrep
)

core_apt=(
  gobuster
)

core_pip=(
  "${lean_pip[@]}"
  dirsearch
  wafw00f
  xsstrike
)

core_go=(
  github.com/aquasecurity/kubectl-who-can/cmd/kubectl-who-can@latest
  github.com/projectdiscovery/dnsx/cmd/dnsx@latest
  github.com/projectdiscovery/httpx/cmd/httpx@latest
  github.com/projectdiscovery/nuclei/v2/cmd/nuclei@latest
  github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
  github.com/hahwul/dalfox/v2@latest
  github.com/ropnop/kerbrute@latest
)

core_cargo=(
  rustscan
)

full_apt=()

full_pip=()

full_go=(
  github.com/d3mondev/puredns/v2@latest
  github.com/owasp-amass/amass/v4/cmd/amass@latest
  github.com/praetorian-inc/fingerprintx/cmd/fingerprintx@latest
  github.com/tomnomnom/assetfinder@latest
)

case "${PROFILE}" in
  lean)
    install_apt_packages "${lean_apt[@]}"
    install_pip_packages "${lean_pip[@]}"
    ;;
  core)
    install_apt_packages "${core_apt[@]}"
    install_pip_packages "${core_pip[@]}"
    install_go_tools "${core_go[@]}"
    install_cargo_tools "${core_cargo[@]}"
    install_additional_release_tools
    ;;
  full)
    install_apt_packages "${core_apt[@]}" "${full_apt[@]}"
    install_pip_packages "${core_pip[@]}" "${full_pip[@]}"
    install_go_tools "${core_go[@]}" "${full_go[@]}"
    install_cargo_tools "${core_cargo[@]}"
    install_additional_release_tools
    ;;
  *)
    printf 'Unknown skill tool profile: %s\n' "${PROFILE}" >&2
    exit 2
    ;;
esac

create_compatibility_aliases

prewarm_tool_state

log "Skill tool install finished (profile=${PROFILE})"
