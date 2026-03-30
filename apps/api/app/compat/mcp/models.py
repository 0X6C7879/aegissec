from __future__ import annotations

from dataclasses import dataclass, field

from app.db.models import CompatibilityScope, CompatibilitySource, MCPCapabilityKind, MCPTransport


@dataclass(slots=True)
class ImportedMCPServer:
    id: str
    name: str
    source: CompatibilitySource
    scope: CompatibilityScope
    transport: MCPTransport
    enabled: bool
    command: str | None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 5000
    config_path: str = ""


@dataclass(slots=True)
class DiscoveredMCPCapability:
    kind: MCPCapabilityKind
    name: str
    title: str | None
    description: str | None
    uri: str | None
    metadata: dict[str, object] = field(default_factory=dict)
    input_schema: dict[str, object] = field(default_factory=dict)
    raw_payload: dict[str, object] = field(default_factory=dict)
