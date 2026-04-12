from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "aegissec-kali:latest"
DEFAULT_CONTEXT = REPO_ROOT
DEFAULT_DOCKERFILE = REPO_ROOT / "docker" / "kali" / "Dockerfile"
INSTALLER_SCRIPT = REPO_ROOT / "scripts" / "install_ctf_tools.sh"
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT / ".env.local")


def load_env_defaults() -> dict[str, str]:
    values: dict[str, str] = {}

    for env_file in ENV_FILES:
        if not env_file.exists():
            continue

        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()

    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the local aegissec Kali Docker image."
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Docker image tag to build. Defaults to AEGISSEC_KALI_IMAGE from .env/.env.local or aegissec-kali:latest.",
    )
    parser.add_argument(
        "--context",
        default=str(DEFAULT_CONTEXT),
        help="Docker build context. Defaults to repository root.",
    )
    parser.add_argument(
        "--file",
        default=str(DEFAULT_DOCKERFILE),
        help="Dockerfile path. Defaults to docker/kali/Dockerfile under the repo root.",
    )
    parser.add_argument(
        "--pull",
        action="store_true",
        help="Always attempt to pull a newer base image before building.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not use Docker layer cache while building.",
    )
    parser.add_argument(
        "--install-ctf-tools",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable scripts/install_ctf_tools.sh during image build.",
    )
    parser.add_argument(
        "--ctf-install-mode",
        default=None,
        help="Mode passed to install_ctf_tools.sh (default: python).",
    )
    parser.add_argument(
        "--install-skill-tools",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable additional skill-oriented tools during image build.",
    )
    parser.add_argument(
        "--skill-tool-profile",
        choices=["lean", "core", "full"],
        default=None,
        help="Skill extra tool profile (lean, core, or full).",
    )
    parser.add_argument(
        "--install-gcp-tools",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable Google Cloud CLI installation during image build.",
    )
    parser.add_argument(
        "--install-browser-tools",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable browser tooling (Google Chrome) during image build.",
    )
    return parser


def parse_env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_defaults = load_env_defaults()
    image_tag = (
        args.tag
        or os.environ.get("AEGISSEC_KALI_IMAGE")
        or env_defaults.get("AEGISSEC_KALI_IMAGE", DEFAULT_IMAGE)
    )
    context_path = Path(args.context).resolve()
    dockerfile_path = Path(args.file).resolve()

    install_ctf_tools = args.install_ctf_tools
    if install_ctf_tools is None:
        install_ctf_tools = parse_env_bool(
            os.environ.get("AEGISSEC_KALI_INSTALL_CTF_TOOLS")
            or env_defaults.get("AEGISSEC_KALI_INSTALL_CTF_TOOLS"),
            default=True,
        )

    install_skill_tools = args.install_skill_tools
    if install_skill_tools is None:
        install_skill_tools = parse_env_bool(
            os.environ.get("AEGISSEC_KALI_INSTALL_SKILL_TOOLS")
            or env_defaults.get("AEGISSEC_KALI_INSTALL_SKILL_TOOLS"),
            default=True,
        )

    ctf_install_mode = (
        args.ctf_install_mode
        or os.environ.get("AEGISSEC_KALI_CTF_INSTALL_MODE")
        or env_defaults.get("AEGISSEC_KALI_CTF_INSTALL_MODE", "python")
    )
    skill_tool_profile = (
        args.skill_tool_profile
        or os.environ.get("AEGISSEC_KALI_SKILL_TOOL_PROFILE")
        or env_defaults.get("AEGISSEC_KALI_SKILL_TOOL_PROFILE", "lean")
    )

    install_gcp_tools = args.install_gcp_tools
    if install_gcp_tools is None:
        install_gcp_tools = parse_env_bool(
            os.environ.get("AEGISSEC_KALI_INSTALL_GCP_TOOLS")
            or env_defaults.get("AEGISSEC_KALI_INSTALL_GCP_TOOLS"),
            default=False,
        )

    install_browser_tools = args.install_browser_tools
    if install_browser_tools is None:
        install_browser_tools = parse_env_bool(
            os.environ.get("AEGISSEC_KALI_INSTALL_BROWSER_TOOLS")
            or env_defaults.get("AEGISSEC_KALI_INSTALL_BROWSER_TOOLS"),
            default=False,
        )

    installer_sha = "unknown"
    if install_ctf_tools:
        if not INSTALLER_SCRIPT.exists():
            parser.error(f"Required installer script is missing: {INSTALLER_SCRIPT}")
        installer_sha = sha256_of_file(INSTALLER_SCRIPT)

    if not context_path.exists():
        parser.error(f"Docker build context does not exist: {context_path}")
    if not dockerfile_path.exists():
        parser.error(f"Dockerfile does not exist: {dockerfile_path}")

    command = ["docker", "build", "-t", image_tag, "-f", str(dockerfile_path)]
    command.extend(["--build-arg", f"INSTALL_CTF_TOOLS={1 if install_ctf_tools else 0}"])
    command.extend(["--build-arg", f"CTF_INSTALL_MODE={ctf_install_mode}"])
    command.extend(["--build-arg", f"INSTALL_SKILL_TOOLS={1 if install_skill_tools else 0}"])
    command.extend(["--build-arg", f"SKILL_TOOL_PROFILE={skill_tool_profile}"])
    command.extend(["--build-arg", f"INSTALL_GCP_TOOLS={1 if install_gcp_tools else 0}"])
    command.extend([
        "--build-arg",
        f"INSTALL_BROWSER_TOOLS={1 if install_browser_tools else 0}",
    ])
    command.extend(["--build-arg", f"CTF_INSTALLER_SHA={installer_sha}"])
    if args.pull:
        command.append("--pull")
    if args.no_cache:
        command.append("--no-cache")
    command.append(str(context_path))

    print(f"==> Building Kali image: {image_tag}")
    print("==> Command:", " ".join(command))
    subprocess.run(command, cwd=REPO_ROOT, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
