#!/usr/bin/env python3
"""Generate Ligolo-ng proxy/agent setup commands for pivot operations.

Usage:
  python skills/tunnel/scripts/ligolo_setup.py --mode proxy --listen-addr 0.0.0.0:11601 --tun-ip 172.16.250.1 --target-cidr 10.10.0.0/24
"""

from __future__ import annotations

import argparse
import json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ligolo-ng setup helper.")
    parser.add_argument("--mode", choices=["proxy", "agent"], required=True)
    parser.add_argument("--listen-addr", help="Proxy bind address, e.g. 0.0.0.0:11601")
    parser.add_argument(
        "--proxy-addr", help="Proxy address for agent, e.g. 192.168.56.1:11601"
    )
    parser.add_argument(
        "--tun-ip", required=True, help="Operator tunnel interface IP for route command"
    )
    parser.add_argument(
        "--target-cidr", required=True, help="Target subnet reachable via pivot"
    )
    parser.add_argument("--interface", default="ligolo", help="Tunnel interface name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    proxy_addr = args.proxy_addr or "<proxy_ip:11601>"
    listen_addr = args.listen_addr or "0.0.0.0:11601"

    proxy_cmd = f"./proxy -selfcert -laddr {listen_addr}"
    agent_cmd = f"./agent -connect {proxy_addr} -ignore-cert"
    route_cmd = (
        f"sudo ip route add {args.target_cidr} dev {args.interface} src {args.tun_ip}"
    )
    start_cmd = "ligolo-ng> session\nligolo-ng> start"

    payload = {
        "mode": args.mode,
        "commands": {
            "proxy": proxy_cmd,
            "agent": agent_cmd,
            "route_add": route_cmd,
            "ligolo_start": start_cmd,
            "verify": f"ip route | grep {args.target_cidr}",
        },
        "notes": [
            "Run proxy command on operator server.",
            "Run agent command on foothold host.",
            "After session selection in Ligolo, run start.",
        ],
    }

    print(proxy_cmd)
    print(agent_cmd)
    print(route_cmd)
    print(start_cmd)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
