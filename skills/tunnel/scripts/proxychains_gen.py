#!/usr/bin/env python3
"""Generate proxychains4.conf content for SOCKS proxies.

Usage:
  python skills/tunnel/scripts/proxychains_gen.py --type socks5 --host 127.0.0.1 --port 1080 --output proxychains4.conf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate proxychains configuration snippet."
    )
    parser.add_argument("--type", choices=["socks4", "socks5"], required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", help="Output file path. Defaults to stdout.")
    return parser.parse_args()


def build_config(proxy_type: str, host: str, port: int) -> str:
    return "\n".join(
        [
            "strict_chain",
            "proxy_dns",
            "tcp_read_time_out 15000",
            "tcp_connect_time_out 8000",
            "",
            "[ProxyList]",
            f"{proxy_type} {host} {port}",
            "",
        ]
    )


def main() -> int:
    args = parse_args()
    content = build_config(args.type, args.host, args.port)

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        print(f"Wrote {output_path}")
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
