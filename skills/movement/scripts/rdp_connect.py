#!/usr/bin/env python3
"""Build an xfreerdp command for RDP access and emit JSON with command details.

Does not launch an interactive session by default (--exec to run).

Usage:
  python skills/movement/scripts/rdp_connect.py -d corp.local -u alice -p Passw0rd! --target 10.0.0.25
  python skills/movement/scripts/rdp_connect.py -d corp.local -u alice -H aad3b435:deadbeef --target 10.0.0.25 --restricted-admin --exec
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="xfreerdp command builder.")
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--target", required=True)
    parser.add_argument("--restricted-admin", action="store_true")
    parser.add_argument("--exec", action="store_true", help="Actually launch xfreerdp.")
    parser.add_argument("--port", type=int, default=3389)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    tool = shutil.which("xfreerdp") or shutil.which("xfreerdp3")
    command: list[str] = [tool or "xfreerdp"]
    command.append(f"/v:{args.target}:{args.port}")
    command.append(f"/d:{args.domain}")
    command.append(f"/u:{args.user}")

    if args.nt_hash:
        command.append(f"/pth:{args.nt_hash}")
    else:
        command.append(f"/p:{args.password}")

    if args.restricted_admin:
        command.append("/restricted-admin")

    command.append("/cert:ignore")

    payload = {
        "tool": tool or "xfreerdp",
        "tool_found": bool(tool),
        "restricted_admin": args.restricted_admin,
        "command": command,
    }

    if not tool:
        print("[!] xfreerdp not found. Printing command only.", file=sys.stderr)

    if not args.exec:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not tool:
        print(" ".join(command))
        return 1

    result = subprocess.run(command, check=False)
    payload["return_code"] = result.returncode
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
