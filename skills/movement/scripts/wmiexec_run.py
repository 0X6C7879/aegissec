#!/usr/bin/env python3
"""Execute a command remotely through Impacket WMIExec and save output.

Usage:
  python skills/movement/scripts/wmiexec_run.py -d corp.local -u alice -p Passw0rd! --target 10.0.0.25 --command "whoami" -o ./wmi.txt
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WMIExec wrapper.")
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--target", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("-o", "--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_file = Path(args.output).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    tool = shutil.which("impacket-wmiexec") or shutil.which("wmiexec.py")
    target = f"{args.domain}/{args.user}@{args.target}"
    command = [tool or "impacket-wmiexec", target]
    if args.password:
        command.extend(["-password", args.password])
    else:
        command.extend(["-hashes", args.nt_hash])
    command.append(args.command)

    payload = {
        "tool": tool or "impacket-wmiexec",
        "tool_found": bool(tool),
        "command": command,
        "output_file": str(output_file),
    }

    if not tool:
        print("[!] wmiexec not found. Printing command only.", file=sys.stderr)
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
