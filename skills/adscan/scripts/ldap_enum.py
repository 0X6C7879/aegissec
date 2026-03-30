#!/usr/bin/env python3
"""Enumerate LDAP objects (users/groups/computers/OUs/GPOs) via ldapsearch or NetExec.

Usage:
  python skills/adscan/scripts/ldap_enum.py -d corp.local -u alice -p Passw0rd! --dc-ip 10.0.0.10 --query all -o ./ldap
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "scripts"))

QUERIES = {
    "users": "(objectClass=user)",
    "groups": "(objectClass=group)",
    "computers": "(objectClass=computer)",
    "ous": "(objectClass=organizationalUnit)",
    "gpos": "(objectClass=groupPolicyContainer)",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LDAP enumeration wrapper.")
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--dc-ip", required=True)
    parser.add_argument(
        "--query",
        choices=["users", "groups", "computers", "ous", "gpos", "all"],
        default="all",
    )
    parser.add_argument("-o", "--output", required=True)
    return parser.parse_args()


def domain_to_base_dn(domain: str) -> str:
    return ",".join(f"DC={part}" for part in domain.split("."))


def run_ldapsearch(
    args: argparse.Namespace, category: str, output_dir: Path
) -> tuple[int, Path]:
    out_file = output_dir / f"ldap_{category}.ldif"
    ldapsearch = shutil.which("ldapsearch")
    if not ldapsearch:
        return (127, out_file)

    base_dn = domain_to_base_dn(args.domain)
    bind_dn = f"{args.user}@{args.domain}"
    command = [
        ldapsearch,
        "-x",
        "-LLL",
        "-H",
        f"ldap://{args.dc_ip}",
        "-D",
        bind_dn,
        "-b",
        base_dn,
        QUERIES[category],
    ]
    if args.password:
        command.extend(["-w", args.password])
    else:
        command.extend(["-y", "/dev/null"])

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    out_file.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    return (result.returncode, out_file)


def run_netexec(
    args: argparse.Namespace, category: str, output_dir: Path
) -> tuple[int, Path, list[str]]:
    tool = shutil.which("netexec") or shutil.which("crackmapexec")
    out_file = output_dir / f"netexec_ldap_{category}.txt"
    if not tool:
        return (127, out_file, ["netexec", "ldap", args.dc_ip])

    command = [tool, "ldap", args.dc_ip, "-d", args.domain, "-u", args.user]
    if args.password:
        command.extend(["-p", args.password])
    else:
        command.extend(["-H", args.nt_hash])
    if category == "users":
        command.append("--users")
    elif category == "groups":
        command.append("--groups")
    elif category == "computers":
        command.append("--computers")
    else:
        command.append("--trusted-for-delegation")

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    out_file.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    return (result.returncode, out_file, command)


def count_entries(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return sum(1 for line in text.splitlines() if line.lower().startswith("dn:"))


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    categories = list(QUERIES) if args.query == "all" else [args.query]
    summary: dict[str, int] = {}
    outputs: dict[str, str] = {}
    backend = "ldapsearch"
    failed = 0

    for category in categories:
        rc, out_file = run_ldapsearch(args, category, output_dir)
        if rc == 127:
            backend = "netexec"
            rc, out_file, _ = run_netexec(args, category, output_dir)
        if rc != 0:
            failed += 1
        outputs[category] = str(out_file)
        summary[category] = count_entries(out_file) if out_file.exists() else 0

    payload = {
        "query": args.query,
        "backend": backend,
        "counts": summary,
        "output_files": outputs,
        "failed_categories": failed,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
