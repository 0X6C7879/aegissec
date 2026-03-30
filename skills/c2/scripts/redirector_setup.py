#!/usr/bin/env python3
"""Generate socat or iptables redirector commands for C2 traffic forwarding.

Usage:
  python skills/c2/scripts/redirector_setup.py --tool socat --lport 443 --rhost 10.0.0.5 --rport 4443
  python skills/c2/scripts/redirector_setup.py --tool iptables --lport 443 --rhost 10.0.0.5 --rport 4443
"""

from __future__ import annotations

import argparse
import json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Redirector command generator.")
    parser.add_argument("--tool", choices=["socat", "iptables"], required=True)
    parser.add_argument("--lport", type=int, required=True)
    parser.add_argument("--rhost", required=True)
    parser.add_argument("--rport", type=int, required=True)
    return parser.parse_args()


def _socat_commands(lport: int, rhost: str, rport: int) -> list[str]:
    fwd = f"socat TCP-LISTEN:{lport},fork,reuseaddr TCP:{rhost}:{rport}"
    background = f"socat TCP-LISTEN:{lport},fork,reuseaddr TCP:{rhost}:{rport} &"
    return [fwd, background]


def _iptables_commands(lport: int, rhost: str, rport: int) -> list[str]:
    return [
        "sysctl -w net.ipv4.ip_forward=1",
        f"iptables -t nat -A PREROUTING -p tcp --dport {lport} -j DNAT --to-destination {rhost}:{rport}",
        f"iptables -t nat -A POSTROUTING -j MASQUERADE",
        f"iptables -A FORWARD -p tcp -d {rhost} --dport {rport} -j ACCEPT",
    ]


def main() -> int:
    args = parse_args()

    if args.tool == "socat":
        commands = _socat_commands(args.lport, args.rhost, args.rport)
    else:
        commands = _iptables_commands(args.lport, args.rhost, args.rport)

    for cmd in commands:
        print(cmd)

    payload = {
        "tool": args.tool,
        "lport": args.lport,
        "rhost": args.rhost,
        "rport": args.rport,
        "commands": commands,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
