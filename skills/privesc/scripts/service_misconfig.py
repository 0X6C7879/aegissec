#!/usr/bin/env python3
"""Generate service misconfiguration discovery commands for Windows privilege escalation.

Usage:
  python skills/privesc/scripts/service_misconfig.py --output ./svc_checks --method accesschk
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Service misconfiguration command generator."
    )
    parser.add_argument(
        "--output", required=True, help="Directory for saved command plan"
    )
    parser.add_argument("--method", choices=["query", "accesschk"], default="query")
    return parser.parse_args()


def command_set(method: str) -> list[str]:
    base = [
        r"wmic service get name,displayname,startmode,pathname,startname | findstr /i /v \"C:\\Windows\\\"",
        r"powershell -c \"Get-WmiObject win32_service | Where-Object {$_.PathName -match '\\s' -and $_.PathName -notmatch '^\"'} | Select Name,PathName\"",
        r"sc qc <service_name>",
        r"icacls \"C:\\Path\\To\\ServiceBinary.exe\"",
    ]
    if method == "accesschk":
        base.extend(
            [
                r"accesschk.exe -uwcqv \"Authenticated Users\" *",
                r"accesschk.exe -ucqv <service_name>",
            ]
        )
    else:
        base.extend(
            [
                r"powershell -c \"Get-Service | ForEach-Object {sc.exe sdshow $_.Name}\"",
                r"powershell -c \"Get-CimInstance Win32_Service | Select Name,StartName,PathName\"",
            ]
        )
    return base


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    commands = command_set(args.method)

    plan_path = output_dir / "service_misconfig_commands.txt"
    plan_path.write_text("\n".join(commands) + "\n", encoding="utf-8")

    payload = {
        "method": args.method,
        "commands": commands,
        "output_file": str(plan_path),
        "look_for": [
            "Unquoted service paths with writable directories",
            "Service binary writable by low-priv user",
            "SERVICE_CHANGE_CONFIG or SERVICE_ALL_ACCESS permissions",
            "Services running as LocalSystem with weak ACLs",
        ],
    }
    for cmd in commands:
        print(cmd)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
