#!/usr/bin/env python3
"""Detect locally available AD pentest tooling for the adscan skill."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

# Import shared helpers from skills/shared/scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "scripts"))
from toolchain_common import ToolStatus, detect_tools


TOOLS: list[tuple[str, str, list[str], str]] = [
    ("nmap", "nmap", ["nmap", "--version"], "recon"),
    ("massdns", "massdns", ["massdns", "-h"], "recon"),
    ("netexec", "netexec", ["netexec", "--version"], "orchestrator"),
    ("crackmapexec", "crackmapexec", ["crackmapexec", "--version"], "orchestrator"),
    ("kerbrute", "kerbrute", ["kerbrute", "--help"], "recon"),
    ("impacket-secretsdump", "secretsdump.py", ["secretsdump.py", "-h"], "impacket"),
    ("impacket-ntlmrelayx", "ntlmrelayx.py", ["ntlmrelayx.py", "-h"], "impacket"),
    ("impacket-psexec", "psexec.py", ["psexec.py", "-h"], "impacket"),
    ("impacket-wmiexec", "wmiexec.py", ["wmiexec.py", "-h"], "impacket"),
    ("certipy", "certipy", ["certipy", "-h"], "adcs"),
    (
        "bloodhound-python",
        "bloodhound-python",
        ["bloodhound-python", "-h"],
        "bloodhound",
    ),
    ("rusthound-ce", "rusthound-ce", ["rusthound-ce", "--version"], "bloodhound"),
    ("sharphound", "SharpHound.exe", ["SharpHound.exe", "--help"], "bloodhound"),
    ("responder", "Responder.py", ["Responder.py", "-h"], "relay"),
    ("coercer", "coercer", ["coercer", "-h"], "relay"),
    ("coercer-py", "Coercer.py", ["Coercer.py", "-h"], "relay"),
    ("hashcat", "hashcat", ["hashcat", "--version"], "cracking"),
    ("john", "john", ["john", "--list=build-info"], "cracking"),
    ("smbmap", "smbmap", ["smbmap", "-h"], "shares"),
    ("snaffler", "Snaffler.exe", ["Snaffler.exe", "-h"], "shares"),
    ("rclone", "rclone", ["rclone", "version"], "shares"),
    ("evil-winrm", "evil-winrm", ["evil-winrm", "-h"], "lateral"),
]


def main() -> int:
    statuses = detect_tools(TOOLS)

    payload = {
        "summary": {
            "available": sum(1 for item in statuses if item.available),
            "missing": sum(1 for item in statuses if not item.available),
        },
        "tools": [asdict(item) for item in statuses],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
