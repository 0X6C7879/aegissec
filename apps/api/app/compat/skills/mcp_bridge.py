from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from app.db.models import MCPCapability, MCPCapabilityKind, MCPServer, SkillRecordStatus

from .models import ParsedSkillRecordData, SkillSourceIdentity, SkillSourceKind


def build_mcp_skill_records(
    *,
    servers: list[MCPServer],
    capabilities_by_server_id: dict[str, list[MCPCapability]],
) -> list[ParsedSkillRecordData]:
    records: list[ParsedSkillRecordData] = []
    for server in servers:
        if not server.enabled:
            continue
        for capability in capabilities_by_server_id.get(server.id, []):
            records.append(_build_mcp_skill_record(server=server, capability=capability))
    return records


def read_mcp_skill_markdown(record: ParsedSkillRecordData | object) -> str:
    metadata = _compat_metadata_from_record(record)
    capability_kind = str(metadata.get("mcp_capability_kind") or "unknown")
    server_name = str(metadata.get("mcp_server_name") or "unknown")
    server_id = str(metadata.get("mcp_server_id") or "unknown")
    transport = str(metadata.get("mcp_transport") or "unknown")
    tool_name = str(metadata.get("mcp_capability_name") or getattr(record, "name", "unknown"))
    title = str(metadata.get("mcp_capability_title") or "").strip()
    description = str(getattr(record, "description", "") or "").strip()
    uri = metadata.get("mcp_uri")
    when_to_use = str(metadata.get("when_to_use") or description or "").strip()
    when_to_use_value = when_to_use or "Inspect this MCP capability before routing to MCP surfaces."
    input_schema = getattr(record, "parameter_schema", {})
    if not isinstance(input_schema, dict):
        input_schema = {}

    title_line = title or tool_name
    body_lines = [
        "---",
        f"name: {getattr(record, 'name', tool_name)}",
        f"description: {description or title_line}",
        "compatibility:",
        "  - mcp",
        f"when-to-use: {when_to_use_value}",
        "context: MCP capability bridge",
        "agent: capability-facade",
        "effort: low",
        "allowed-tools:",
        "  - call_mcp_tool",
        "  - read_skill_content",
        "---",
        f"# {title_line}",
        "",
        f"Server: {server_name} ({server_id})",
        f"Capability kind: {capability_kind}",
        f"Transport: {transport}",
    ]
    if uri:
        body_lines.append(f"URI: {uri}")
    if description:
        body_lines.extend(["", description])
    body_lines.extend(
        [
            "",
            "This entry is a conservative MCP compatibility bridge. It is represented in the skill",
            "inventory for discovery and context only. Shell execution is disabled and any real",
            "action must stay on existing MCP/capability routes.",
            "",
            "Input schema:",
            f"```json\n{input_schema}\n```",
        ]
    )
    return "\n".join(body_lines).strip() + "\n"


def _build_mcp_skill_record(
    *, server: MCPServer, capability: MCPCapability
) -> ParsedSkillRecordData:
    server_name_slug = _slugify(server.name) or server.id
    capability_name_slug = (
        _slugify(capability.name or capability.uri or capability.id) or capability.id
    )
    directory_name = f"mcp-{server_name_slug}-{capability_name_slug}"
    capability_path_name = capability.name or capability.uri or capability.id
    entry_file = f"mcp://skills/{server.id}/{capability.kind.value}/{capability_path_name}"
    relative_path = f"{server.id}/{capability.kind.value}/{capability_path_name}"
    description = (capability.description or capability.title or capability.name).strip()
    when_to_use = (
        description or f"Review the {capability.kind.value} capability exposed by {server.name}."
    )
    compat_metadata = {
        "source_kind": SkillSourceKind.MCP.value,
        "dynamic": True,
        "invocable": False,
        "shell_enabled": False,
        "loaded_from": entry_file,
        "mcp_server_id": server.id,
        "mcp_server_name": server.name,
        "mcp_transport": server.transport.value,
        "mcp_capability_kind": capability.kind.value,
        "mcp_capability_name": capability.name,
        "mcp_capability_title": capability.title,
        "mcp_uri": capability.uri,
        "when_to_use": when_to_use,
        "context_hint": "MCP capability bridge",
        "agent": "capability-facade",
        "effort": "low",
    }
    content = read_mcp_skill_markdown(
        ParsedSkillRecordData(
            id="",
            source=server.source,
            scope=server.scope,
            root_dir=f"mcp://skills/{server.id}",
            directory_name=directory_name,
            entry_file=entry_file,
            name=directory_name,
            description=description,
            compatibility=["mcp"],
            metadata={
                "server_id": server.id,
                "server_name": server.name,
                "transport": server.transport.value,
                "capability_kind": capability.kind.value,
            },
            parameter_schema=dict(capability.input_schema_json),
            raw_frontmatter={"_compat": compat_metadata},
            status=SkillRecordStatus.LOADED,
            enabled=True,
            error_message=None,
            content_hash="",
            last_scanned_at=datetime.now(UTC),
        )
    )
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    return ParsedSkillRecordData(
        id=str(uuid5(NAMESPACE_URL, entry_file)),
        source=server.source,
        scope=server.scope,
        root_dir=f"mcp://skills/{server.id}",
        directory_name=directory_name,
        entry_file=entry_file,
        name=directory_name,
        description=description,
        compatibility=["mcp"],
        metadata={
            "server_id": server.id,
            "server_name": server.name,
            "transport": server.transport.value,
            "capability_kind": capability.kind.value,
            "capability_name": capability.name,
            "capability_title": capability.title,
            "uri": capability.uri,
        },
        parameter_schema=dict(capability.input_schema_json),
        raw_frontmatter={"_compat": compat_metadata},
        status=SkillRecordStatus.LOADED,
        enabled=True,
        error_message=None,
        content_hash=content_hash,
        last_scanned_at=now,
        aliases=_build_aliases(server=server, capability=capability),
        user_invocable=False,
        allowed_tools=["call_mcp_tool", "read_skill_content"],
        argument_hint=_build_argument_hint(capability),
        when_to_use=when_to_use,
        context_hint="MCP capability bridge",
        agent="capability-facade",
        effort="low",
        source_identity=SkillSourceIdentity(
            source_kind=SkillSourceKind.MCP,
            source=server.source,
            scope=server.scope,
            source_root=f"mcp://skills/{server.id}",
            relative_path=relative_path,
            fingerprint=content_hash,
        ),
    )


def _build_aliases(*, server: MCPServer, capability: MCPCapability) -> list[str]:
    aliases = [capability.name]
    aliases.append(f"{server.name}:{capability.name}")
    if capability.title and capability.title != capability.name:
        aliases.append(capability.title)
    deduped: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        normalized = alias.strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(alias.strip())
    return deduped


def _build_argument_hint(capability: MCPCapability) -> str | None:
    if capability.kind != MCPCapabilityKind.TOOL:
        return None
    properties = capability.input_schema_json.get("properties")
    if not isinstance(properties, dict) or not properties:
        return None
    parts = [f"--{key} <value>" for key in properties if isinstance(key, str)]
    return " ".join(parts) if parts else None


def _slugify(value: str) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in value)
    collapsed = "-".join(part for part in normalized.split("-") if part)
    return collapsed[:80]


def _compat_metadata_from_record(record: ParsedSkillRecordData | object) -> dict[str, object]:
    raw_frontmatter = getattr(record, "raw_frontmatter", None)
    if raw_frontmatter is None:
        raw_frontmatter = getattr(record, "raw_frontmatter_json", None)
    if not isinstance(raw_frontmatter, dict):
        return {}
    compat_payload = raw_frontmatter.get("_compat")
    return dict(compat_payload) if isinstance(compat_payload, dict) else {}
