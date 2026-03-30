#!/usr/bin/env python3
"""Run WinPEAS, save full output, and summarize interesting findings.

Usage:
  python skills/privesc/scripts/winpeas_runner.py --winpeas-path ./winPEASx64.exe --args "quiet cmd" -o ./winpeas_out --grep SeImpersonatePrivilege
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


KEYWORDS = ("Interesting", "YES!", "[+]", "Found", "HIGH", "MEDIUM")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WinPEAS runner and parser.")
    parser.add_argument("--winpeas-path", required=True)
    parser.add_argument("--args", default="", help="Extra arguments passed to WinPEAS")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("--grep", help="Optional regex to filter lines")
    return parser.parse_args()


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    binary = (
        shutil.which(args.winpeas_path)
        if not Path(args.winpeas_path).exists()
        else args.winpeas_path
    )
    command = [binary or args.winpeas_path, *args.args.split()]

    payload: dict[str, object] = {
        "command": command,
        "binary_found": bool(binary or Path(args.winpeas_path).exists()),
        "interesting_count": 0,
        "full_output": str(output_dir / "winpeas_full.txt"),
        "interesting_output": str(output_dir / "winpeas_interesting.txt"),
        "grep_hits": [],
    }

    if not payload["binary_found"]:
        print("[!] WinPEAS binary not found. Printing command only.", file=sys.stderr)
        print(" ".join(command))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    clean_text = strip_ansi(f"{result.stdout}\n{result.stderr}")
    full_path = Path(payload["full_output"])
    full_path.write_text(clean_text, encoding="utf-8")

    interesting = [
        line
        for line in clean_text.splitlines()
        if any(k.lower() in line.lower() for k in KEYWORDS)
    ]
    Path(payload["interesting_output"]).write_text(
        "\n".join(interesting) + "\n", encoding="utf-8"
    )
    payload["interesting_count"] = len(interesting)

    if args.grep:
        pattern = re.compile(args.grep, re.IGNORECASE)
        payload["grep_hits"] = [
            line for line in clean_text.splitlines() if pattern.search(line)
        ]

    payload["return_code"] = result.returncode
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
