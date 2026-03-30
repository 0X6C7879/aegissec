#!/usr/bin/env python3
"""Run NetExec SMB CVE modules against a target and emit structured JSON.

Usage:
  python skills/adscan/scripts/vulnscan.py -d corp.local -u alice -p Passw0rd! --target 10.0.0.10 --module zerologon
  python skills/adscan/scripts/vulnscan.py -d corp.local -u alice -H aabbcc:1122dd --target 10.0.0.10 --module nopac
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

MODULES = ("zerologon", "petitpotam", "nopac", "webdav")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NetExec SMB CVE module wrapper.")
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--module",
        required=True,
        choices=MODULES,
        help="CVE module: " + ", ".join(MODULES),
    )
    return parser.parse_args()


def build_command(args: argparse.Namespace, tool: str) -> list[str]:
    cmd = [tool, "smb", args.target, "-d", args.domain, "-u", args.user]
    if args.password:
        cmd.extend(["-p", args.password])
    else:
        cmd.extend(["-H", args.nt_hash])
    cmd.extend(["-M", args.module])
    return cmd


def main() -> int:
    args = parse_args()

    tool = shutil.which("netexec") or shutil.which("nxc")
    tool_name = tool or "netexec"
    command = build_command(args, tool_name)

    payload: dict[str, object] = {
        "tool": tool_name,
        "tool_found": bool(tool),
        "module": args.module,
        "target": args.target,
        "command": command,
    }

    if not tool:
        print("[!] netexec/nxc not found. Printing command only.", file=sys.stderr)
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
