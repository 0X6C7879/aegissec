from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.core.settings import REPO_ROOT
from app.db.models import CompatibilityScope, CompatibilitySource, MCPTransport

from .models import ImportedMCPServer

DEFAULT_TIMEOUT_MS = 5000
WINDOWS_STDIO_PACKAGE_MANAGER_SHIMS = {
    "bun",
    "bunx",
    "npm",
    "npx",
    "pnpm",
    "pnpx",
    "uvx",
    "yarn",
    "yarnpkg",
}


@dataclass(slots=True)
class MCPImportTarget:
    source: CompatibilitySource
    scope: CompatibilityScope
    file_path: str


def default_mcp_import_targets(
    repo_root: Path = REPO_ROOT,
    home_dir: Path = Path.home(),
) -> list[MCPImportTarget]:
    resolved_repo_root = repo_root.resolve()
    resolved_home_dir = home_dir.resolve()
    return [
        MCPImportTarget(
            source=CompatibilitySource.CLAUDE,
            scope=CompatibilityScope.PROJECT,
            file_path=(resolved_repo_root / ".mcp.json").as_posix(),
        ),
        MCPImportTarget(
            source=CompatibilitySource.CLAUDE,
            scope=CompatibilityScope.USER,
            file_path=(resolved_home_dir / ".claude.json").as_posix(),
        ),
        MCPImportTarget(
            source=CompatibilitySource.OPENCODE,
            scope=CompatibilityScope.PROJECT,
            file_path=(resolved_repo_root / "opencode.json").as_posix(),
        ),
        MCPImportTarget(
            source=CompatibilitySource.OPENCODE,
            scope=CompatibilityScope.PROJECT,
            file_path=(resolved_repo_root / ".opencode" / "mcp.json").as_posix(),
        ),
        MCPImportTarget(
            source=CompatibilitySource.OPENCODE,
            scope=CompatibilityScope.PROJECT,
            file_path=(resolved_repo_root / ".opencode.json").as_posix(),
        ),
        MCPImportTarget(
            source=CompatibilitySource.OPENCODE,
            scope=CompatibilityScope.USER,
            file_path=(resolved_home_dir / ".config" / "opencode" / "opencode.json").as_posix(),
        ),
        MCPImportTarget(
            source=CompatibilitySource.OPENCODE,
            scope=CompatibilityScope.USER,
            file_path=(resolved_home_dir / ".config" / "opencode" / "mcp.json").as_posix(),
        ),
        MCPImportTarget(
            source=CompatibilitySource.AGENTS,
            scope=CompatibilityScope.PROJECT,
            file_path=(resolved_repo_root / ".agents" / "mcp.json").as_posix(),
        ),
        MCPImportTarget(
            source=CompatibilitySource.AGENTS,
            scope=CompatibilityScope.USER,
            file_path=(resolved_home_dir / ".agents" / "mcp.json").as_posix(),
        ),
    ]


def configured_mcp_import_targets(paths: list[str]) -> list[MCPImportTarget]:
    targets: list[MCPImportTarget] = []
    for raw_path in paths:
        resolved_path = Path(raw_path).expanduser().resolve()
        source, scope = _infer_target_metadata(resolved_path)
        targets.append(
            MCPImportTarget(
                source=source,
                scope=scope,
                file_path=resolved_path.as_posix(),
            )
        )
    return targets


def import_mcp_servers(targets: list[MCPImportTarget]) -> list[ImportedMCPServer]:
    imported: list[ImportedMCPServer] = []
    for target in targets:
        if not os.path.isfile(target.file_path):
            continue

        with open(target.file_path, encoding="utf-8") as config_file:
            payload = json.load(config_file)
        imported.extend(_parse_servers_from_payload(target, payload))

    imported.sort(key=lambda server: (server.source, server.scope, server.name, server.config_path))
    return imported


def _parse_servers_from_payload(
    target: MCPImportTarget, payload: object
) -> list[ImportedMCPServer]:
    if not isinstance(payload, dict):
        return []

    parsed: list[ImportedMCPServer] = []
    seen_names: set[str] = set()
    for server_map, opencode_type in _extract_server_maps(payload):
        for name, raw_config in server_map.items():
            if not isinstance(name, str) or not isinstance(raw_config, dict) or name in seen_names:
                continue
            seen_names.add(name)
            parsed.append(
                _build_server(
                    name=name,
                    source=target.source,
                    scope=target.scope,
                    config_path=target.file_path,
                    raw_config=raw_config,
                    opencode_type=opencode_type,
                )
            )
    return parsed


def _extract_server_maps(payload: dict[str, object]) -> list[tuple[dict[str, object], str | None]]:
    mappings: list[tuple[dict[str, object], str | None]] = []

    for key in ("mcpServers", "mcp_servers"):
        maybe_map = payload.get(key)
        if isinstance(maybe_map, dict):
            mappings.append((maybe_map, None))

    opencode_map = payload.get("mcp")
    if isinstance(opencode_map, dict):
        mappings.append((opencode_map, "auto"))

    opencode_payload = payload.get("opencode")
    if isinstance(opencode_payload, dict):
        nested_mcp = opencode_payload.get("mcp")
        if isinstance(nested_mcp, dict):
            mappings.append((nested_mcp, "auto"))

    agent_payload = payload.get("agents")
    if isinstance(agent_payload, dict):
        nested_mcp_servers = agent_payload.get("mcpServers")
        if isinstance(nested_mcp_servers, dict):
            mappings.append((nested_mcp_servers, None))

    return mappings


def _build_server(
    *,
    name: str,
    source: CompatibilitySource,
    scope: CompatibilityScope,
    config_path: str,
    raw_config: dict[str, object],
    opencode_type: str | None,
) -> ImportedMCPServer:
    transport = _infer_transport(raw_config, opencode_type)
    command, args = _normalize_command(raw_config, opencode_type)
    env_payload = raw_config.get("env")
    if env_payload is None:
        env_payload = raw_config.get("environment")
    env = _normalize_string_map(env_payload)
    raw_url = raw_config.get("url")
    if raw_url is None:
        raw_url = raw_config.get("endpoint")
    url = raw_url if isinstance(raw_url, str) else None
    headers_payload = raw_config.get("headers")
    if headers_payload is None:
        headers_payload = raw_config.get("http_headers")
    headers = _normalize_string_map(headers_payload)
    timeout_payload = raw_config.get("timeout_ms")
    if timeout_payload is None:
        timeout_payload = raw_config.get("timeout")
    timeout_ms = _normalize_timeout_ms(timeout_payload)
    raw_enabled = raw_config.get("enabled")
    enabled = raw_enabled if isinstance(raw_enabled, bool) else True

    return ImportedMCPServer(
        id=_server_id(source=source, scope=scope, config_path=config_path, name=name),
        name=name,
        source=source,
        scope=scope,
        transport=transport,
        enabled=enabled,
        command=command,
        args=args,
        env=env,
        url=url,
        headers=headers,
        timeout_ms=timeout_ms,
        config_path=config_path,
    )


def _server_id(
    *,
    source: CompatibilitySource,
    scope: CompatibilityScope,
    config_path: str,
    name: str,
) -> str:
    digest = sha256(f"{source}:{scope}:{config_path}:{name}".encode()).hexdigest()
    return digest[:16]


def _infer_target_metadata(path: Path) -> tuple[CompatibilitySource, CompatibilityScope]:
    path_string = path.as_posix()
    repo_scope = (
        CompatibilityScope.PROJECT
        if path_string.startswith(REPO_ROOT.as_posix())
        else CompatibilityScope.USER
    )
    if "/.config/opencode/" in path_string or "/.opencode/" in path_string:
        return CompatibilitySource.OPENCODE, repo_scope
    if path.name == "opencode.json":
        return CompatibilitySource.OPENCODE, CompatibilityScope.PROJECT
    if path.name == ".opencode.json":
        return CompatibilitySource.OPENCODE, CompatibilityScope.PROJECT
    if path.name == "mcp.json" and "/.agents/" in path_string:
        scope = (
            CompatibilityScope.PROJECT
            if path_string.startswith(REPO_ROOT.as_posix())
            else CompatibilityScope.USER
        )
        return CompatibilitySource.AGENTS, scope
    if path.name == "mcp.json" and "/.opencode/" in path_string:
        return CompatibilitySource.OPENCODE, CompatibilityScope.PROJECT
    if path.name == ".mcp.json":
        return CompatibilitySource.CLAUDE, CompatibilityScope.PROJECT
    if path.name == ".claude.json":
        return CompatibilitySource.CLAUDE, CompatibilityScope.USER
    return CompatibilitySource.CLAUDE, CompatibilityScope.PROJECT


def _infer_transport(raw_config: dict[str, object], opencode_type: str | None) -> MCPTransport:
    normalized_type = (
        raw_config.get("type") if isinstance(raw_config.get("type"), str) else opencode_type
    )
    if normalized_type == "remote":
        return MCPTransport.HTTP
    if normalized_type == "local":
        return MCPTransport.STDIO
    if isinstance(raw_config.get("url"), str) or isinstance(raw_config.get("endpoint"), str):
        return MCPTransport.HTTP
    return MCPTransport.STDIO


def _normalize_command(
    raw_config: dict[str, object],
    opencode_type: str | None,
) -> tuple[str | None, list[str]]:
    normalized_type = (
        raw_config.get("type") if isinstance(raw_config.get("type"), str) else opencode_type
    )
    command_payload = raw_config.get("command")
    if command_payload is None:
        command_payload = raw_config.get("cmd")
    if normalized_type == "local" and isinstance(command_payload, list):
        values = [item for item in command_payload if isinstance(item, str)]
        if not values:
            return None, []
        return _normalize_stdio_command(values[0]), values[1:]

    if isinstance(command_payload, list):
        values = [item for item in command_payload if isinstance(item, str)]
        if not values:
            return None, []
        return _normalize_stdio_command(values[0]), values[1:]

    if isinstance(command_payload, str):
        args_payload = raw_config.get("args")
        if args_payload is None:
            args_payload = raw_config.get("arguments")
        return _normalize_stdio_command(command_payload), _normalize_string_list(args_payload)

    return None, []


def _normalize_stdio_command(command: str) -> str:
    if os.name != "nt":
        return command
    if command.lower() not in WINDOWS_STDIO_PACKAGE_MANAGER_SHIMS:
        return command
    if "/" in command or "\\" in command:
        return command
    if _command_has_suffix(command):
        return command
    return f"{command}.cmd"


def _command_has_suffix(command: str) -> bool:
    stem, separator, suffix = command.rpartition(".")
    return bool(stem and separator and suffix)


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _normalize_string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str):
            normalized[key] = item
    return normalized


def _normalize_timeout_ms(value: object) -> int:
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            return DEFAULT_TIMEOUT_MS
    if isinstance(value, int) and value > 0:
        return value
    return DEFAULT_TIMEOUT_MS
