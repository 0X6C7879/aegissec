from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "aegissec-kali:latest"
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
        default=str(REPO_ROOT / "docker" / "kali"),
        help="Docker build context. Defaults to docker/kali under the repo root.",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Optional Dockerfile path. Defaults to <context>/Dockerfile.",
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
    return parser


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
    dockerfile_path = (
        Path(args.file).resolve() if args.file else context_path / "Dockerfile"
    )

    if not context_path.exists():
        parser.error(f"Docker build context does not exist: {context_path}")
    if not dockerfile_path.exists():
        parser.error(f"Dockerfile does not exist: {dockerfile_path}")

    command = ["docker", "build", "-t", image_tag, "-f", str(dockerfile_path)]
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
