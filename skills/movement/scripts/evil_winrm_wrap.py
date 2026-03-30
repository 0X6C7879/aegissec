#!/usr/bin/env python3
"""Build Evil-WinRM command lines with interactive session guidance.

Usage:
  python .opencode/skills/movement/scripts/evil_winrm_wrap.py -H 10.0.0.25 -u alice -p Passw0rd!
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evil-WinRM command builder with interactive session guidance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Password authentication
  python evil_winrm_wrap.py -H 10.0.0.25 -u alice -p Passw0rd!

  # NTLM hash authentication
  python evil_winrm_wrap.py -H 10.0.0.25 -u alice --hash 31d6cfe0d16ae931b73c59d7e0c089c0

  # SSL/TLS connection
  python evil_winrm_wrap.py -H 10.0.0.25 -u alice -p Passw0rd! --ssl
        """,
    )
    parser.add_argument("-H", "--host", required=True, help="Target hostname or IP")
    parser.add_argument("-u", "--user", required=True, help="Username")
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password", help="Password")
    auth.add_argument("--hash", help="NTLM hash")
    parser.add_argument(
        "--port", type=int, default=5985, help="WinRM port (default: 5985)"
    )
    parser.add_argument("--ssl", action="store_true", help="Use SSL/TLS")
    parser.add_argument("--domain", help="Domain name")
    parser.add_argument("--upload-local", help="Optional local file path to upload")
    parser.add_argument("--upload-remote", help="Optional remote file path for upload")
    return parser.parse_args()


EVIL_WINRM_COMMANDS = {
    "information": [
        {"cmd": "menu", "desc": "Show available commands"},
        {"cmd": "pwd", "desc": "Print current directory"},
        {"cmd": "ls", "desc": "List files (alias: dir, ll, ls)"},
        {"cmd": "cd <path>", "desc": "Change directory"},
    ],
    "file_operations": [
        {"cmd": "get <remote> [local]", "desc": "Download file"},
        {"cmd": "put <local> [remote]", "desc": "Upload file"},
        {"cmd": "rm <file>", "desc": "Delete file"},
        {"cmd": "mkdir <dir>", "desc": "Create directory"},
    ],
    "execution": [
        {"cmd": "shell <command>", "desc": "Execute command (alias: !, run)"},
        {"cmd": "services", "desc": "List services"},
        {"cmd": "processes", "desc": "List processes"},
        {"cmd": "upload <local> [remote]", "desc": "Upload file"},
        {"cmd": "download <remote> [local]", "desc": "Download file"},
    ],
    "persistence": [
        {"cmd": "persistence", "desc": "Setup persistence (requires elevated session)"},
        {"cmd": "mimikatz", "desc": "Run mimikatz (requires elevated session)"},
        {"cmd": "patch", "desc": "Patch legitimate binaries"},
    ],
    "powerups": [
        {"cmd": "launcher", "desc": "Generate encoded payload"},
        {
            "cmd": "load",
            "desc": "Load module (PowerShell/Rubeus/...). See: gci -Path $env:PSModulePath -Recurse",
        },
    ],
}

EVIL_WINRM_HELP = """
======================================================================
Evil-WinRM Interactive Session Guide
======================================================================

[Connection Established]

Available command categories:

* Basic Navigation
  pwd                    - Print working directory
  cd <path>              - Change directory  
  ls                     - List files (alias: dir, ll)
  menu                   - Show all available commands

* File Transfer
  get <remote> [local]  - Download file from target
  put <local> [remote]  - Upload file to target
  upload <local>        - Upload file (alias for put)
  download <remote>     - Download file (alias for get)

* Command Execution
  shell <command>        - Execute command
  ! <command>            - Shortcut for shell
  run <command>         - Shortcut for shell

* Recon
  services              - List all Windows services
  processes             - List running processes
  users                 - List local users
  groups                - List local groups
  logons                - List users with active sessions
  info                  - Get system information

* PowerShell
  ps                    - Start PowerShell session

* Tools (when loaded)
  mimikatz              - Run credential dumping
  persistence           - Setup persistence
  load <module>         - Load PowerShell module

======================================================================

Pro Tips:
  * Tab completion works for commands and paths
  * Use '?' for help on any command: ? get
  * Exit with 'exit' or Ctrl+C
  * History available with Up/Down arrows
  * For non-interactive commands: exit and use direct command

======================================================================
"""


def build_upload_workflow(args: argparse.Namespace) -> dict[str, Any]:
    local_path = args.upload_local or "<local-file>"
    remote_path = args.upload_remote or "C:\\Windows\\Temp\\<file>"
    local_size_bytes = None
    if args.upload_local:
        candidate = Path(args.upload_local)
        if candidate.exists() and candidate.is_file():
            local_size_bytes = candidate.stat().st_size

    return {
        "terminal_focus_required": True,
        "progress_visibility": "Keep the terminal focused until the upload finishes. Do not switch away or interrupt a large transfer mid-stream.",
        "upload_command": f"upload {local_path} {remote_path}",
        "size_verification": {
            "required": True,
            "method": "size",
            "local_size_bytes": local_size_bytes,
            "remote_commands": [
                f"Get-Item '{remote_path}' | Select-Object FullName,Length",
                f"dir '{remote_path}'",
                f"powershell -c \"(Get-Item '{remote_path}').Length\"",
            ],
            "compare_rule": "Treat the upload as successful only when the remote file size matches the local file size.",
        },
    }


def build_evil_command(args: argparse.Namespace) -> list[str]:
    cmd = ["evil-winrm", "-i", args.host, "-u", args.user, "-P", str(args.port)]
    if args.ssl:
        cmd.append("-S")
    if args.domain:
        cmd.extend(["--domain", args.domain])
    if args.password:
        cmd.extend(["-p", args.password])
    else:
        cmd.extend(["-H", args.hash])
    return cmd


def build_netexec_command(args: argparse.Namespace) -> list[str]:
    cmd = ["netexec", "winrm", args.host, "-u", args.user]
    if args.password:
        cmd.extend(["-p", args.password])
    else:
        cmd.extend(["-H", args.hash])
    return cmd


def main() -> int:
    args = parse_args()

    evil_cmd = build_evil_command(args)
    netexec_cmd = build_netexec_command(args)
    upload_workflow = build_upload_workflow(args)

    print("=" * 70)
    print("Evil-WinRM Command Builder")
    print("=" * 70)

    print("\n[*] Run this command to start an interactive session:")
    print(f"\n  {' '.join(evil_cmd)}\n")

    print("[*] Validation command (run first to verify access):")
    print(f"\n  {' '.join(netexec_cmd)}\n")

    print("-" * 70)
    print("Interactive Session Commands Reference:")
    print("-" * 70)
    print(EVIL_WINRM_HELP)

    print("-" * 70)
    print("Large File Upload Discipline:")
    print("-" * 70)
    print("Do not switch away from the terminal while upload is in progress.")
    print("Wait for the upload command to finish before running anything else.")
    print(f"Suggested upload command: {upload_workflow['upload_command']}")
    print("Verify the remote file size after upload:")
    for command in upload_workflow["size_verification"]["remote_commands"]:
        print(f"  {command}")

    result = {
        "evil_winrm_command": " ".join(evil_cmd),
        "netexec_check_command": " ".join(netexec_cmd),
        "interactive_commands": EVIL_WINRM_COMMANDS,
        "interactive_help": EVIL_WINRM_HELP.strip(),
        "upload_workflow": upload_workflow,
        "usage_note": "Run the evil-winrm command in an interactive terminal/shell. "
        "Use the commands above once connected.",
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
