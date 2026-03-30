#!/usr/bin/env python3
"""Collect BloodHound data with bloodhound-python and summarize artifacts.

Usage:
  python skills/adscan/scripts/bloodhound_collect.py -d corp.local -u alice -p Passw0rd! --dc-ip 10.0.0.10 -c All -o ./bh --zip
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "scripts"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BloodHound collection wrapper.")
    parser.add_argument("-d", "--domain", required=True)
    parser.add_argument("-u", "--user", required=True)
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("-p", "--password")
    auth.add_argument("-H", "--hash", dest="nt_hash")
    parser.add_argument("--dc-ip", required=True)
    parser.add_argument(
        "-c",
        "--collection",
        default="All",
        help="Collection methods (All, Session, DCOnly, etc)",
    )
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--zip", action="store_true", help="Zip output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    tool = shutil.which("bloodhound-python")
    command = [
        tool or "bloodhound-python",
        "-d",
        args.domain,
        "-u",
        args.user,
        "-c",
        args.collection,
        "-ns",
        args.dc_ip,
        "--dns-tcp",
        "--outputdirectory",
        str(output_dir),
    ]
    if args.password:
        command.extend(["-p", args.password])
    else:
        command.extend(["--hashes", args.nt_hash])
    if args.zip:
        command.append("--zip")

    payload: dict[str, object] = {
        "tool": tool or "bloodhound-python",
        "tool_found": bool(tool),
        "command": command,
        "collection": args.collection,
        "output_dir": str(output_dir),
        "output_files": [],
        "import_hint": "Upload generated JSON/ZIP into BloodHound CE > Administration > File Ingest.",
    }

    if not tool:
        print(
            "[!] bloodhound-python not found. Printing command only.", file=sys.stderr
        )
        print(" ".join(command))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    (output_dir / "bloodhound_stdout_stderr.txt").write_text(
        f"{result.stdout}\n{result.stderr}".strip() + "\n",
        encoding="utf-8",
    )
    payload["return_code"] = result.returncode
    payload["output_files"] = [
        str(path) for path in sorted(output_dir.glob("*")) if path.is_file()
    ]
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
