#!/usr/bin/env python3
"""Generate Havoc or Mythic listener configuration snippets and setup commands.

Usage:
  python skills/c2/scripts/modern_c2_setup.py --framework havoc --lhost 10.0.0.5 --lport 443
  python skills/c2/scripts/modern_c2_setup.py --framework mythic --lhost 10.0.0.5 --lport 7443
"""

from __future__ import annotations

import argparse
import json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Modern C2 setup helper.")
    parser.add_argument("--framework", choices=["havoc", "mythic"], required=True)
    parser.add_argument("--lhost", required=True)
    parser.add_argument("--lport", type=int, required=True)
    return parser.parse_args()


def _havoc_config(lhost: str, lport: int) -> dict:
    config_snippet = {
        "Listeners": [
            {
                "Name": "http-listener",
                "Protocol": "http",
                "CallbackHosts": [lhost],
                "BindPort": lport,
                "Secure": lport == 443,
            }
        ]
    }
    commands = [
        "./havoc server --profile havoc.yaotl",
        f"# In Havoc client: Listeners -> Add -> HTTP -> {lhost}:{lport}",
    ]
    return {"config_snippet": config_snippet, "commands": commands}


def _mythic_config(lhost: str, lport: int) -> dict:
    commands = [
        "cd /opt/Mythic && sudo ./mythic-cli start",
        f"# In Mythic UI: Payloads -> Create -> Select C2 Profile -> configure lhost={lhost} lport={lport}",
        f"sudo ./mythic-cli install github https://github.com/MythicC2Profiles/http",
        f"# C2 Profile http: callback_host={lhost} callback_port={lport}",
    ]
    config_snippet = {
        "c2_profile": "http",
        "parameters": {
            "callback_host": lhost,
            "callback_port": lport,
            "encrypted_exchange_check": True,
        },
    }
    return {"config_snippet": config_snippet, "commands": commands}


def main() -> int:
    args = parse_args()

    if args.framework == "havoc":
        data = _havoc_config(args.lhost, args.lport)
    else:
        data = _mythic_config(args.lhost, args.lport)

    for cmd in data["commands"]:
        print(cmd)

    payload = {
        "framework": args.framework,
        "lhost": args.lhost,
        "lport": args.lport,
        "config_snippet": data["config_snippet"],
        "commands": data["commands"],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
