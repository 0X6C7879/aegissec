#!/usr/bin/env python3
"""List and print ETW patching snippets for defensive research and lab use.

Usage:
  python skills/bypass/scripts/etw_patch.py --show etweventwrite --format powershell
"""

from __future__ import annotations

import argparse


SNIPPETS = {
    "nttracecontrol": {
        "description": "Patch NtTraceControl path to reduce ETW telemetry.",
        "powershell": "# Patch NtTraceControl in ntdll via Add-Type and VirtualProtect, then write stub bytes.",
        "csharp": "// Resolve NtTraceControl with GetProcAddress and patch function body to immediate return.",
    },
    "etweventwrite": {
        "description": "Patch EtwEventWrite to return success without logging event payload.",
        "powershell": "# Locate EtwEventWrite in ntdll and patch with: xor rax,rax; ret (x64).",
        "csharp": "// Use unsafe pointer patching on EtwEventWrite to neutralize writes.",
    },
    "provider-disable-reg": {
        "description": "Disable selected providers through registry policy where applicable.",
        "powershell": "reg add HKLM\\Software\\Policies\\Microsoft\\Windows\\EventLog /v DisableEventLog /t REG_DWORD /d 1 /f",
        "csharp": "// Use Microsoft.Win32.Registry to set provider policy keys for telemetry reduction tests.",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ETW patch reference snippet manager.")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--show", help="Snippet name")
    parser.add_argument(
        "--format", choices=["powershell", "csharp"], default="powershell"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        for name, meta in SNIPPETS.items():
            print(f"{name}: {meta['description']}")
        return 0

    if args.show:
        item = SNIPPETS.get(args.show)
        if not item:
            print(f"Unknown snippet: {args.show}")
            return 1
        print(f"# {args.show} ({args.format})")
        print(item[args.format])
        return 0

    print("Use --list or --show <name>.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
