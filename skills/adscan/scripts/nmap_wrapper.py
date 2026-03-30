#!/usr/bin/env python3
"""Wrap nmap with predefined AD-focused scan profiles and emit structured JSON.

Usage:
  python skills/adscan/scripts/nmap_wrapper.py --target 10.0.0.0/24 --profile ad-discovery -o ./recon
  python skills/adscan/scripts/nmap_wrapper.py --target 10.0.0.10 --profile smb-enum -o ./recon
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROFILES: dict[str, list[str]] = {
    "ad-discovery": [
        "-sV",
        "-p",
        "88,135,139,389,443,445,464,593,636,3268,3269,3389,5985,9389",
        "--open",
    ],
    "smb-enum": [
        "-sV",
        "-p",
        "139,445",
        "--script",
        "smb-security-mode,smb2-security-mode,smb-os-discovery",
        "--open",
    ],
    "full": ["-sV", "-sC", "-p-", "--open", "-T4"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nmap AD scan profile wrapper.")
    parser.add_argument("--target", required=True, help="IP, CIDR, or hostname")
    parser.add_argument(
        "--profile",
        required=True,
        choices=list(PROFILES.keys()),
        help="Scan profile: " + ", ".join(PROFILES.keys()),
    )
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    return parser.parse_args()


def build_command(args: argparse.Namespace, tool: str, xml_out: Path) -> list[str]:
    cmd = [tool] + PROFILES[args.profile] + ["-oX", str(xml_out), args.target]
    return cmd


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    xml_out = output_dir / f"nmap_{args.profile}.xml"

    tool = shutil.which("nmap")
    tool_name = tool or "nmap"
    command = build_command(args, tool_name, xml_out)

    payload: dict[str, object] = {
        "tool": tool_name,
        "tool_found": bool(tool),
        "profile": args.profile,
        "profile_flags": PROFILES[args.profile],
        "target": args.target,
        "output_dir": str(output_dir),
        "xml_output": str(xml_out),
        "command": command,
    }

    if not tool:
        print("[!] nmap not found. Printing command only.", file=sys.stderr)
        print(" ".join(command))
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    payload["return_code"] = result.returncode
    payload["stdout"] = result.stdout.strip()
    payload["stderr"] = result.stderr.strip()
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
