from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import AnyUrl, TypeAdapter
from pytest import MonkeyPatch

from app.core.settings import Settings
from app.main import app


def test_default_mcp_import_targets_cover_supported_sources(tmp_path: Path) -> None:
    from app.compat.mcp.importer import default_mcp_import_targets
    from app.db.models import CompatibilityScope, CompatibilitySource

    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"

    targets = default_mcp_import_targets(repo_root=repo_root, home_dir=home_dir)

    assert [(target.source, target.scope, Path(target.file_path)) for target in targets] == [
        (CompatibilitySource.CLAUDE, CompatibilityScope.PROJECT, repo_root / ".mcp.json"),
        (CompatibilitySource.CLAUDE, CompatibilityScope.USER, home_dir / ".claude.json"),
        (CompatibilitySource.OPENCODE, CompatibilityScope.PROJECT, repo_root / "opencode.json"),
    ]


def test_mcp_import_lists_servers_and_discovered_capabilities(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.compat.mcp.importer import MCPImportTarget
    from app.compat.mcp.models import DiscoveredMCPCapability
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import (
        CompatibilityScope,
        CompatibilitySource,
        MCPCapabilityKind,
        MCPServerStatus,
    )

    project_root = tmp_path / "project"
    home_dir = tmp_path / "home"

    _write_json(
        project_root / ".mcp.json",
        {
            "mcpServers": {
                "claude-local": {
                    "command": "python",
                    "args": ["-m", "demo_stdio"],
                    "env": {"TOKEN": "demo"},
                    "timeout_ms": 4100,
                },
                "claude-remote": {
                    "url": "https://claude.example.test/mcp",
                    "headers": {"Authorization": "Bearer demo-token"},
                },
            }
        },
    )
    _write_json(
        home_dir / ".claude.json",
        {
            "mcpServers": {
                "user-local": {
                    "command": "uvx",
                    "args": ["demo-user-mcp"],
                }
            }
        },
    )
    _write_json(
        project_root / "opencode.json",
        {
            "mcp": {
                "open-local": {
                    "type": "local",
                    "enabled": False,
                    "command": ["node", "server.js"],
                    "env": {"MODE": "dev"},
                },
                "open-remote": {
                    "type": "remote",
                    "url": "https://opencode.example.test/mcp",
                    "headers": {"X-Api-Key": "demo-key"},
                    "timeout_ms": 7200,
                },
            }
        },
    )

    fake_manager = _FakeMCPClientManager(
        capabilities_by_server={
            "claude-local": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.TOOL,
                    name="scan",
                    title="Scan",
                    description="Run a validation scan",
                    uri=None,
                    metadata={},
                    input_schema={"type": "object"},
                    raw_payload={"name": "scan"},
                )
            ],
            "claude-remote": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.PROMPT,
                    name="report",
                    title="Report",
                    description="Generate a report prompt",
                    uri=None,
                    metadata={},
                    input_schema={},
                    raw_payload={"name": "report"},
                )
            ],
            "user-local": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.RESOURCE,
                    name="workspace://notes",
                    title="Notes",
                    description="Workspace notes resource",
                    uri="workspace://notes",
                    metadata={},
                    input_schema={},
                    raw_payload={"uri": "workspace://notes"},
                )
            ],
            "open-remote": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.TOOL,
                    name="collect",
                    title="Collect",
                    description="Collect remote data",
                    uri=None,
                    metadata={},
                    input_schema={"type": "object"},
                    raw_payload={"name": "collect"},
                )
            ],
        }
    )
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    try:
        from app.compat.mcp import service as mcp_service_module

        original_resolver = mcp_service_module.resolve_mcp_import_targets

        def override_resolve_mcp_import_targets(_settings: Settings) -> list[MCPImportTarget]:
            return [
                MCPImportTarget(
                    source=CompatibilitySource.CLAUDE,
                    scope=CompatibilityScope.PROJECT,
                    file_path=(project_root / ".mcp.json").as_posix(),
                ),
                MCPImportTarget(
                    source=CompatibilitySource.CLAUDE,
                    scope=CompatibilityScope.USER,
                    file_path=(home_dir / ".claude.json").as_posix(),
                ),
                MCPImportTarget(
                    source=CompatibilitySource.OPENCODE,
                    scope=CompatibilityScope.PROJECT,
                    file_path=(project_root / "opencode.json").as_posix(),
                ),
            ]

        monkeypatch.setattr(
            mcp_service_module,
            "resolve_mcp_import_targets",
            override_resolve_mcp_import_targets,
        )

        import_response = client.post("/api/mcp/import")
        assert import_response.status_code == 200

        servers = import_response.json()
        assert [(server["source"], server["scope"], server["name"]) for server in servers] == [
            ("claude", "project", "claude-local"),
            ("claude", "project", "claude-remote"),
            ("claude", "user", "user-local"),
            ("opencode", "project", "open-local"),
            ("opencode", "project", "open-remote"),
        ]

        claude_local = next(server for server in servers if server["name"] == "claude-local")
        assert claude_local["transport"] == "stdio"
        assert claude_local["status"] == MCPServerStatus.CONNECTED
        assert claude_local["command"] == "python"
        assert claude_local["args"] == ["-m", "demo_stdio"]
        assert claude_local["timeout_ms"] == 4100
        assert claude_local["capabilities"] == [
            {
                "kind": "tool",
                "name": "scan",
                "title": "Scan",
                "description": "Run a validation scan",
                "uri": None,
                "metadata": {},
                "input_schema": {"type": "object"},
                "raw_payload": {"name": "scan"},
            }
        ]

        open_local = next(server for server in servers if server["name"] == "open-local")
        assert open_local["enabled"] is False
        assert open_local["status"] == MCPServerStatus.INACTIVE
        assert open_local["capabilities"] == []
        assert open_local["command"] == "node"
        assert open_local["args"] == ["server.js"]

        open_remote = next(server for server in servers if server["name"] == "open-remote")
        assert open_remote["transport"] == "http"
        assert open_remote["url"] == "https://opencode.example.test/mcp"
        assert open_remote["headers"] == {"X-Api-Key": "demo-key"}
        assert open_remote["status"] == MCPServerStatus.CONNECTED

        list_response = client.get("/api/mcp/servers")
        assert list_response.status_code == 200
        assert list_response.json() == servers

        detail_response = client.get(f"/api/mcp/servers/{claude_local['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["name"] == "claude-local"
        assert detail_response.json()["capabilities"][0]["name"] == "scan"
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_import_serializes_non_json_capability_payloads(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.compat.mcp.importer import MCPImportTarget
    from app.compat.mcp.models import DiscoveredMCPCapability
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import CompatibilityScope, CompatibilitySource, MCPCapabilityKind

    project_root = tmp_path / "project"
    _write_json(
        project_root / ".mcp.json",
        {
            "mcpServers": {
                "claude-remote": {
                    "url": "https://claude.example.test/mcp",
                }
            }
        },
    )

    demo_url = TypeAdapter(AnyUrl).validate_python("http://127.0.0.1:8765/mcp")
    fake_manager = _FakeMCPClientManager(
        capabilities_by_server={
            "claude-remote": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.RESOURCE,
                    name="remote-status",
                    title="Remote Status",
                    description="Remote demo resource",
                    uri=str(demo_url),
                    metadata={"endpoint": demo_url},
                    input_schema={"endpoint": demo_url},
                    raw_payload={"endpoint": demo_url},
                )
            ]
        }
    )
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    try:
        from app.compat.mcp import service as mcp_service_module

        original_resolver = mcp_service_module.resolve_mcp_import_targets

        def override_resolve_mcp_import_targets(_settings: Settings) -> list[MCPImportTarget]:
            return [
                MCPImportTarget(
                    source=CompatibilitySource.CLAUDE,
                    scope=CompatibilityScope.PROJECT,
                    file_path=(project_root / ".mcp.json").as_posix(),
                )
            ]

        monkeypatch.setattr(
            mcp_service_module,
            "resolve_mcp_import_targets",
            override_resolve_mcp_import_targets,
        )

        import_response = client.post("/api/mcp/import")
        assert import_response.status_code == 200
        capability = import_response.json()[0]["capabilities"][0]
        assert capability["metadata"] == {"endpoint": "http://127.0.0.1:8765/mcp"}
        assert capability["input_schema"] == {"endpoint": "http://127.0.0.1:8765/mcp"}
        assert capability["raw_payload"] == {"endpoint": "http://127.0.0.1:8765/mcp"}
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_toggle_and_refresh_update_server_state(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.compat.mcp.importer import MCPImportTarget
    from app.compat.mcp.models import DiscoveredMCPCapability
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import (
        CompatibilityScope,
        CompatibilitySource,
        MCPCapabilityKind,
        MCPServerStatus,
    )

    project_root = tmp_path / "project"
    _write_json(
        project_root / "opencode.json",
        {
            "mcp": {
                "open-local": {
                    "type": "local",
                    "enabled": False,
                    "command": ["node", "server.js"],
                }
            }
        },
    )

    fake_manager = _FakeMCPClientManager(
        capabilities_by_server={
            "open-local": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.TOOL,
                    name="collect",
                    title="Collect",
                    description="Collect data",
                    uri=None,
                    metadata={},
                    input_schema={"type": "object"},
                    raw_payload={"name": "collect"},
                )
            ]
        }
    )
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    try:
        from app.compat.mcp import service as mcp_service_module

        original_resolver = mcp_service_module.resolve_mcp_import_targets

        def override_resolve_mcp_import_targets(_settings: Settings) -> list[MCPImportTarget]:
            return [
                MCPImportTarget(
                    source=CompatibilitySource.OPENCODE,
                    scope=CompatibilityScope.PROJECT,
                    file_path=(project_root / "opencode.json").as_posix(),
                )
            ]

        monkeypatch.setattr(
            mcp_service_module,
            "resolve_mcp_import_targets",
            override_resolve_mcp_import_targets,
        )

        import_response = client.post("/api/mcp/import")
        assert import_response.status_code == 200
        server_id = import_response.json()[0]["id"]

        toggle_on_response = client.post(
            f"/api/mcp/servers/{server_id}/toggle",
            json={"enabled": True},
        )
        assert toggle_on_response.status_code == 200
        assert toggle_on_response.json()["enabled"] is True
        assert toggle_on_response.json()["status"] == MCPServerStatus.CONNECTED
        assert toggle_on_response.json()["capabilities"][0]["name"] == "collect"

        refresh_response = client.post(f"/api/mcp/servers/{server_id}/refresh")
        assert refresh_response.status_code == 200
        assert refresh_response.json()["status"] == MCPServerStatus.CONNECTED
        assert fake_manager.discover_calls.count("open-local") == 2

        toggle_off_response = client.post(
            f"/api/mcp/servers/{server_id}/toggle",
            json={"enabled": False},
        )
        assert toggle_off_response.status_code == 200
        assert toggle_off_response.json()["enabled"] is False
        assert toggle_off_response.json()["status"] == MCPServerStatus.INACTIVE
        assert fake_manager.shutdown_calls == [server_id]
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


class _FakeMCPClientManager:
    def __init__(self, capabilities_by_server: dict[str, list[object]]) -> None:
        self._capabilities_by_server = capabilities_by_server
        self.discover_calls: list[str] = []
        self.shutdown_calls: list[str] = []

    async def discover(self, server: object) -> list[object]:
        server_name = getattr(server, "name")
        self.discover_calls.append(server_name)
        return list(self._capabilities_by_server.get(server_name, []))

    async def shutdown(self, server_id: str) -> None:
        self.shutdown_calls.append(server_id)


def _write_json(path: Path, content: dict[str, object]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
