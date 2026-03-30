#!/usr/bin/env python3
"""Generate Chisel server/client SOCKS pivot commands.

Usage:
  python skills/tunnel/scripts/chisel_pivot.py --mode client --server-addr 192.168.56.10 --server-port 8080 --reverse
"""

from __future__ import annotations

import argparse
import json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chisel SOCKS5 tunnel command builder."
    )
    parser.add_argument("--mode", choices=["server", "client"], required=True)
    parser.add_argument("--server-addr", required=True, help="Server IP/hostname")
    parser.add_argument("--server-port", type=int, default=8080)
    parser.add_argument("--socks-port", type=int, default=1080)
    parser.add_argument("--reverse", action="store_true", help="Enable reverse mode")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reverse_flag = " --reverse" if args.reverse else ""

    server_cmd = f"chisel server -p {args.server_port}{reverse_flag} --socks5"
    client_target = f"{args.server_addr}:{args.server_port}"
    if args.reverse:
        client_cmd = f"chisel client {client_target} R:socks"
        proxy_host = "127.0.0.1"
        proxy_port = args.socks_port
    else:
        client_cmd = f"chisel client {client_target} socks"
        proxy_host = args.server_addr
        proxy_port = args.socks_port

    proxychains_snippet = "\n".join(
        [
            "strict_chain",
            "proxy_dns",
            "[ProxyList]",
            f"socks5 {proxy_host} {proxy_port}",
        ]
    )

    payload = {
        "mode": args.mode,
        "commands": {"server": server_cmd, "client": client_cmd},
        "proxychains_snippet": proxychains_snippet,
    }

    print(server_cmd)
    print(client_cmd)
    print(proxychains_snippet)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
