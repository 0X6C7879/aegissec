from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import AnyUrl, TypeAdapter
from pytest import MonkeyPatch

from app.core.settings import Settings
from app.main import app
from tests.utils import api_data


def test_default_mcp_import_targets_cover_supported_sources(tmp_path: Path) -> None:
    from app.compat.mcp.importer import default_mcp_import_targets
    from app.db.models import CompatibilityScope, CompatibilitySource

    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"

    targets = default_mcp_import_targets(repo_root=repo_root, home_dir=home_dir)

    discovered = {(target.source, target.scope, Path(target.file_path)) for target in targets}
    assert {
        (CompatibilitySource.CLAUDE, CompatibilityScope.PROJECT, repo_root / ".mcp.json"),
        (CompatibilitySource.CLAUDE, CompatibilityScope.USER, home_dir / ".claude.json"),
        (CompatibilitySource.OPENCODE, CompatibilityScope.PROJECT, repo_root / "opencode.json"),
        (
            CompatibilitySource.OPENCODE,
            CompatibilityScope.PROJECT,
            repo_root / ".opencode" / "mcp.json",
        ),
        (CompatibilitySource.OPENCODE, CompatibilityScope.PROJECT, repo_root / ".opencode.json"),
        (
            CompatibilitySource.OPENCODE,
            CompatibilityScope.USER,
            home_dir / ".config" / "opencode" / "opencode.json",
        ),
        (
            CompatibilitySource.OPENCODE,
            CompatibilityScope.USER,
            home_dir / ".config" / "opencode" / "mcp.json",
        ),
        (
            CompatibilitySource.AGENTS,
            CompatibilityScope.PROJECT,
            repo_root / ".agents" / "mcp.json",
        ),
        (CompatibilitySource.AGENTS, CompatibilityScope.USER, home_dir / ".agents" / "mcp.json"),
    }.issubset(discovered)


def test_importer_supports_opencode_and_agents_adjacent_variants(tmp_path: Path) -> None:
    from app.compat.mcp.importer import MCPImportTarget, import_mcp_servers
    from app.db.models import CompatibilityScope, CompatibilitySource

    project_root = tmp_path / "repo"
    _write_json(
        project_root / ".opencode" / "mcp.json",
        {
            "mcp": {
                "op-remote": {
                    "type": "remote",
                    "endpoint": "https://remote.example.test/mcp",
                    "http_headers": {"X-Test": "1"},
                    "timeout": "7000",
                }
            }
        },
    )
    _write_json(
        project_root / ".agents" / "mcp.json",
        {
            "agents": {
                "mcpServers": {
                    "agent-local": {
                        "cmd": "python",
                        "arguments": ["-m", "agent_server"],
                        "environment": {"MODE": "test"},
                    }
                }
            }
        },
    )

    imported = import_mcp_servers(
        [
            MCPImportTarget(
                source=CompatibilitySource.OPENCODE,
                scope=CompatibilityScope.PROJECT,
                file_path=(project_root / ".opencode" / "mcp.json").as_posix(),
            ),
            MCPImportTarget(
                source=CompatibilitySource.AGENTS,
                scope=CompatibilityScope.PROJECT,
                file_path=(project_root / ".agents" / "mcp.json").as_posix(),
            ),
        ]
    )
    by_name = {server.name: server for server in imported}
    assert by_name["op-remote"].url == "https://remote.example.test/mcp"
    assert by_name["op-remote"].headers == {"X-Test": "1"}
    assert by_name["op-remote"].timeout_ms == 7000
    assert by_name["agent-local"].command == "python"
    assert by_name["agent-local"].args == ["-m", "agent_server"]
    assert by_name["agent-local"].env == {"MODE": "test"}


def test_importer_normalizes_windows_package_manager_stdio_commands(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    import app.compat.mcp.importer as importer
    from app.compat.mcp.importer import MCPImportTarget, import_mcp_servers
    from app.db.models import CompatibilityScope, CompatibilitySource

    project_root = tmp_path / "repo"
    _write_json(
        project_root / "opencode.json",
        {"mcp": {"shim-local": {"type": "local", "command": ["npx", "demo-mcp"]}}},
    )

    monkeypatch.setattr(importer.os, "name", "nt", raising=False)

    imported = import_mcp_servers(
        [
            MCPImportTarget(
                source=CompatibilitySource.OPENCODE,
                scope=CompatibilityScope.PROJECT,
                file_path=(project_root / "opencode.json").as_posix(),
            )
        ]
    )

    assert imported[0].command == "npx.cmd"
    assert imported[0].args == ["demo-mcp"]


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

    from app.compat.mcp import service as mcp_service_module

    original_resolver = mcp_service_module.resolve_mcp_import_targets

    try:

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

        servers = api_data(import_response)
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
        assert "open-local" not in fake_manager.discover_calls

        open_remote = next(server for server in servers if server["name"] == "open-remote")
        assert open_remote["transport"] == "http"
        assert open_remote["url"] == "https://opencode.example.test/mcp"
        assert open_remote["headers"] == {"X-Api-Key": "demo-key"}
        assert open_remote["status"] == MCPServerStatus.CONNECTED

        list_response = client.get("/api/mcp/servers")
        assert list_response.status_code == 200
        assert api_data(list_response) == servers

        detail_response = client.get(f"/api/mcp/servers/{claude_local['id']}")
        assert detail_response.status_code == 200
        detail_payload = api_data(detail_response)
        assert detail_payload["name"] == "claude-local"
        assert detail_payload["capabilities"][0]["name"] == "scan"
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

    from app.compat.mcp import service as mcp_service_module

    original_resolver = mcp_service_module.resolve_mcp_import_targets

    try:

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
        capability = api_data(import_response)[0]["capabilities"][0]
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

    from app.compat.mcp import service as mcp_service_module

    original_resolver = mcp_service_module.resolve_mcp_import_targets

    try:

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
        server_id = api_data(import_response)[0]["id"]

        toggle_on_response = client.post(
            f"/api/mcp/servers/{server_id}/toggle",
            json={"enabled": True},
        )
        assert toggle_on_response.status_code == 200
        toggle_on_payload = api_data(toggle_on_response)
        assert toggle_on_payload["enabled"] is True
        assert toggle_on_payload["status"] == MCPServerStatus.CONNECTED
        assert toggle_on_payload["capabilities"][0]["name"] == "collect"

        refresh_response = client.post(f"/api/mcp/servers/{server_id}/refresh")
        assert refresh_response.status_code == 200
        assert api_data(refresh_response)["status"] == MCPServerStatus.CONNECTED
        assert fake_manager.discover_calls.count("open-local") == 2

        toggle_off_response = client.post(
            f"/api/mcp/servers/{server_id}/toggle",
            json={"enabled": False},
        )
        assert toggle_off_response.status_code == 200
        toggle_off_payload = api_data(toggle_off_response)
        assert toggle_off_payload["enabled"] is False
        assert toggle_off_payload["status"] == MCPServerStatus.INACTIVE
        assert fake_manager.shutdown_calls == [server_id]
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_health_check_updates_last_known_health_non_blocking_list(
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
        project_root / "opencode.json",
        {"mcp": {"open-local": {"type": "local", "command": ["node", "server.js"]}}},
    )

    class _HealthFakeManager(_FakeMCPClientManager):
        async def check_health(self, server: object) -> object:
            del server
            return type("Health", (), {"status": "ok", "latency_ms": 12, "error": None})()

    fake_manager = _HealthFakeManager(
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

    from app.compat.mcp import service as mcp_service_module

    original_resolver = mcp_service_module.resolve_mcp_import_targets

    try:
        monkeypatch.setattr(
            mcp_service_module,
            "resolve_mcp_import_targets",
            lambda _settings: [
                MCPImportTarget(
                    source=CompatibilitySource.OPENCODE,
                    scope=CompatibilityScope.PROJECT,
                    file_path=(project_root / "opencode.json").as_posix(),
                )
            ],
        )

        imported = client.post("/api/mcp/import")
        assert imported.status_code == 200
        server_id = api_data(imported)[0]["id"]

        listed_before = client.get("/api/mcp/servers")
        assert listed_before.status_code == 200
        assert api_data(listed_before)[0]["health_status"] in {None, "ok"}

        health = client.post(f"/api/mcp/servers/{server_id}/health")
        assert health.status_code == 200
        health_payload = api_data(health)
        assert health_payload["health_status"] == "ok"
        assert health_payload["health_latency_ms"] == 12
        assert health_payload["health_error"] is None

        listed_after = client.get("/api/mcp/servers")
        assert listed_after.status_code == 200
        after_payload = api_data(listed_after)[0]
        assert after_payload["health_status"] == "ok"
        assert after_payload["health_latency_ms"] == 12
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_refresh_disabled_server_skips_discovery(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.compat.mcp.importer import MCPImportTarget
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import CompatibilityScope, CompatibilitySource, MCPServerStatus

    project_root = tmp_path / "project"
    _write_json(
        project_root / "opencode.json",
        {
            "mcp": {
                "open-local": {"type": "local", "enabled": False, "command": ["node", "server.js"]}
            }
        },
    )

    fake_manager = _FakeMCPClientManager(capabilities_by_server={"open-local": []})
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    from app.compat.mcp import service as mcp_service_module

    original_resolver = mcp_service_module.resolve_mcp_import_targets

    try:
        monkeypatch.setattr(
            mcp_service_module,
            "resolve_mcp_import_targets",
            lambda _settings: [
                MCPImportTarget(
                    source=CompatibilitySource.OPENCODE,
                    scope=CompatibilityScope.PROJECT,
                    file_path=(project_root / "opencode.json").as_posix(),
                )
            ],
        )

        imported = client.post("/api/mcp/import")
        assert imported.status_code == 200
        server = api_data(imported)[0]
        assert fake_manager.discover_calls == []

        refreshed = client.post(f"/api/mcp/servers/{server['id']}/refresh")
        assert refreshed.status_code == 200
        refreshed_payload = api_data(refreshed)
        assert refreshed_payload["enabled"] is False
        assert refreshed_payload["status"] == MCPServerStatus.INACTIVE
        assert fake_manager.discover_calls == []
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_import_is_non_destructive_and_preserves_last_known_capabilities(
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
    from app.db.repositories.mcp import STALE_IMPORTED_SERVER_ERROR

    project_root = tmp_path / "project"
    config_path = project_root / "opencode.json"
    _write_json(
        config_path,
        {"mcp": {"open-local": {"type": "local", "command": ["node", "server.js"]}}},
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

    from app.compat.mcp import service as mcp_service_module

    original_resolver = mcp_service_module.resolve_mcp_import_targets

    try:
        monkeypatch.setattr(
            mcp_service_module,
            "resolve_mcp_import_targets",
            lambda _settings: [
                MCPImportTarget(
                    source=CompatibilitySource.OPENCODE,
                    scope=CompatibilityScope.PROJECT,
                    file_path=config_path.as_posix(),
                )
            ],
        )

        first_import = client.post("/api/mcp/import")
        assert first_import.status_code == 200
        first_server = api_data(first_import)[0]
        assert first_server["capabilities"][0]["name"] == "collect"
        first_imported_at = first_server["imported_at"]

        _write_json(config_path, {"mcp": {}})
        second_import = client.post("/api/mcp/import")
        assert second_import.status_code == 200
        second_server = next(
            server for server in api_data(second_import) if server["id"] == first_server["id"]
        )
        assert second_server["enabled"] is False
        assert second_server["status"] == MCPServerStatus.INACTIVE
        assert second_server["last_error"] == STALE_IMPORTED_SERVER_ERROR
        assert second_server["health_status"] == "error"
        assert second_server["health_error"] == STALE_IMPORTED_SERVER_ERROR
        assert second_server["capabilities"][0]["name"] == "collect"
        assert second_server["imported_at"] == first_imported_at

        _write_json(
            config_path,
            {"mcp": {"open-local": {"type": "local", "command": ["node", "server.js"]}}},
        )
        third_import = client.post("/api/mcp/import")
        assert third_import.status_code == 200
        third_server = next(
            server for server in api_data(third_import) if server["id"] == first_server["id"]
        )
        assert third_server["enabled"] is True
        assert third_server["status"] == MCPServerStatus.CONNECTED
        assert third_server["last_error"] is None
        assert third_server["health_status"] == "ok"
        assert third_server["health_error"] is None
        assert third_server["capabilities"][0]["name"] == "collect"
        assert third_server["imported_at"] == first_imported_at

        fake_manager.discover_failures["open-local"] = RuntimeError(
            "MCP server 'open-local' failed to discover capabilities: discover boom"
        )

        fourth_import = client.post("/api/mcp/import")
        assert fourth_import.status_code == 200
        fourth_server = next(
            server for server in api_data(fourth_import) if server["id"] == first_server["id"]
        )
        assert fourth_server["status"] == MCPServerStatus.ERROR
        assert (
            fourth_server["last_error"]
            == "MCP server 'open-local' failed to discover capabilities: discover boom"
        )
        assert (
            fourth_server["health_error"]
            == "MCP server 'open-local' failed to discover capabilities: discover boom"
        )
        assert fourth_server["capabilities"][0]["name"] == "collect"
        assert fourth_server["imported_at"] == first_imported_at
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_invoke_disabled_server_returns_conflict(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.compat.mcp.importer import MCPImportTarget
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import CompatibilityScope, CompatibilitySource

    project_root = tmp_path / "project"
    _write_json(
        project_root / "opencode.json",
        {
            "mcp": {
                "open-local": {"type": "local", "enabled": False, "command": ["node", "server.js"]}
            }
        },
    )

    fake_manager = _FakeMCPClientManager(capabilities_by_server={"open-local": []})
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    from app.compat.mcp import service as mcp_service_module

    original_resolver = mcp_service_module.resolve_mcp_import_targets

    try:
        monkeypatch.setattr(
            mcp_service_module,
            "resolve_mcp_import_targets",
            lambda _settings: [
                MCPImportTarget(
                    source=CompatibilitySource.OPENCODE,
                    scope=CompatibilityScope.PROJECT,
                    file_path=(project_root / "opencode.json").as_posix(),
                )
            ],
        )

        imported = client.post("/api/mcp/import")
        assert imported.status_code == 200
        server_id = api_data(imported)[0]["id"]

        invoke_response = client.post(
            f"/api/mcp/servers/{server_id}/tools/collect/invoke",
            json={"arguments": {"target": "demo"}},
        )
        assert invoke_response.status_code == 409
        assert api_data(invoke_response)["detail"] == "MCP server 'open-local' is disabled."
        assert fake_manager.tool_calls == []
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


@pytest.mark.parametrize(
    ("capability_kind", "tool_name", "expected_detail"),
    [
        ("tool", "missing", "MCP tool 'missing' is not registered for server 'manual-demo'."),
        (
            "prompt",
            "report",
            "MCP capability 'report' on server 'manual-demo' is not an invokable tool.",
        ),
    ],
)
def test_mcp_invoke_validates_tool_existence_and_kind(
    client: TestClient,
    capability_kind: str,
    tool_name: str,
    expected_detail: str,
) -> None:
    from app.compat.mcp.models import DiscoveredMCPCapability
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import MCPCapabilityKind

    fake_manager = _FakeMCPClientManager(
        capabilities_by_server={
            "manual-demo": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind(capability_kind),
                    name="report" if capability_kind == "prompt" else "manual_tool",
                    title="Capability",
                    description="Capability",
                    uri=None,
                    metadata={},
                    input_schema={"type": "object"},
                    raw_payload={
                        "name": "report" if capability_kind == "prompt" else "manual_tool"
                    },
                )
            ]
        }
    )
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    try:
        register_response = client.post(
            "/api/mcp/register",
            json={
                "name": "manual-demo",
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "manual_mcp"],
            },
        )
        assert register_response.status_code == 200
        server_id = api_data(register_response)["id"]

        invoke_response = client.post(
            f"/api/mcp/servers/{server_id}/tools/{tool_name}/invoke",
            json={"arguments": {"target": "demo"}},
        )
        assert invoke_response.status_code == 400
        assert api_data(invoke_response)["detail"] == expected_detail
        assert fake_manager.tool_calls == []
    finally:
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_invoke_upstream_failure_preserves_error_and_health_updates(client: TestClient) -> None:
    from app.compat.mcp.models import DiscoveredMCPCapability
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import MCPCapabilityKind, MCPServerStatus

    fake_manager = _FakeMCPClientManager(
        capabilities_by_server={
            "manual-demo": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.TOOL,
                    name="manual_tool",
                    title="Manual Tool",
                    description="Manual tool invocation",
                    uri=None,
                    metadata={},
                    input_schema={"type": "object"},
                    raw_payload={"name": "manual_tool"},
                )
            ]
        }
    )
    fake_manager.tool_failures[("manual-demo", "manual_tool")] = RuntimeError(
        "MCP server 'manual-demo' failed to call tool 'manual_tool': upstream boom"
    )
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    try:
        register_response = client.post(
            "/api/mcp/register",
            json={
                "name": "manual-demo",
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "manual_mcp"],
            },
        )
        assert register_response.status_code == 200
        server_id = api_data(register_response)["id"]

        invoke_response = client.post(
            f"/api/mcp/servers/{server_id}/tools/manual_tool/invoke",
            json={"arguments": {"target": "demo"}},
        )
        assert invoke_response.status_code == 502
        assert (
            api_data(invoke_response)["detail"]
            == "MCP server 'manual-demo' failed to call tool 'manual_tool': upstream boom"
        )

        server_detail = client.get(f"/api/mcp/servers/{server_id}")
        assert server_detail.status_code == 200
        detail_payload = api_data(server_detail)
        assert detail_payload["status"] == MCPServerStatus.ERROR
        assert (
            detail_payload["last_error"]
            == "MCP server 'manual-demo' failed to call tool 'manual_tool': upstream boom"
        )
        assert detail_payload["health_status"] == "error"
        assert (
            detail_payload["health_error"]
            == "MCP server 'manual-demo' failed to call tool 'manual_tool': upstream boom"
        )
    finally:
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_manual_registration_invocation_and_import_preserves_manual_servers(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.compat.mcp.importer import MCPImportTarget
    from app.compat.mcp.models import DiscoveredMCPCapability
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import CompatibilityScope, CompatibilitySource, MCPCapabilityKind
    from app.db.repositories.mcp import STALE_IMPORTED_SERVER_ERROR

    project_root = tmp_path / "project"
    _write_json(
        project_root / "opencode.json",
        {
            "mcp": {
                "open-local": {
                    "type": "local",
                    "command": ["node", "server.js"],
                }
            }
        },
    )

    fake_manager = _FakeMCPClientManager(
        capabilities_by_server={
            "manual-demo": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.TOOL,
                    name="manual_tool",
                    title="Manual Tool",
                    description="Manual tool invocation",
                    uri=None,
                    metadata={},
                    input_schema={"type": "object"},
                    raw_payload={"name": "manual_tool"},
                )
            ],
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
            ],
        },
        tool_results={
            ("manual-demo", "manual_tool"): {
                "content": [{"type": "text", "text": "manual-ok"}],
                "isError": False,
            }
        },
    )
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    from app.compat.mcp import service as mcp_service_module

    original_resolver = mcp_service_module.resolve_mcp_import_targets

    try:

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

        manual_register_response = client.post(
            "/api/mcp/register",
            json={
                "name": "manual-demo",
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "manual_mcp"],
            },
        )
        assert manual_register_response.status_code == 200
        manual_server = api_data(manual_register_response)
        assert manual_server["name"] == "manual-demo"
        assert manual_server["config_path"].startswith("manual://")

        invoke_response = client.post(
            f"/api/mcp/servers/{manual_server['id']}/tools/manual_tool/invoke",
            json={"arguments": {"target": "demo"}},
        )
        assert invoke_response.status_code == 200
        invoke_payload = api_data(invoke_response)
        assert invoke_payload["result"]["content"][0]["text"] == "manual-ok"

        import_response = client.post("/api/mcp/import")
        assert import_response.status_code == 200
        servers = api_data(import_response)
        names = [server["name"] for server in servers]
        assert "manual-demo" in names
        assert "open-local" in names

        _write_json(project_root / "opencode.json", {"mcp": {}})
        stale_import_response = client.post("/api/mcp/import")
        assert stale_import_response.status_code == 200
        stale_servers = api_data(stale_import_response)

        stale_manual_server = next(
            server for server in stale_servers if server["id"] == manual_server["id"]
        )
        assert stale_manual_server["config_path"].startswith("manual://")
        assert stale_manual_server["enabled"] is True
        assert stale_manual_server["last_error"] is None

        stale_imported_server = next(
            server for server in stale_servers if server["name"] == "open-local"
        )
        assert stale_imported_server["enabled"] is False
        assert stale_imported_server["status"] == "inactive"
        assert stale_imported_server["last_error"] == STALE_IMPORTED_SERVER_ERROR
        assert stale_imported_server["health_error"] == STALE_IMPORTED_SERVER_ERROR
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_delete_manual_server_removes_it_and_returns_not_found_for_missing_detail(
    client: TestClient,
) -> None:
    from app.compat.mcp.models import DiscoveredMCPCapability
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import MCPCapabilityKind

    fake_manager = _FakeMCPClientManager(
        capabilities_by_server={
            "manual-demo": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.TOOL,
                    name="manual_tool",
                    title="Manual Tool",
                    description="Manual tool invocation",
                    uri=None,
                    metadata={},
                    input_schema={"type": "object"},
                    raw_payload={"name": "manual_tool"},
                )
            ]
        }
    )
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    try:
        register_response = client.post(
            "/api/mcp/register",
            json={
                "name": "manual-demo",
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "manual_mcp"],
            },
        )
        assert register_response.status_code == 200
        server_id = api_data(register_response)["id"]

        delete_response = client.delete(f"/api/mcp/servers/{server_id}")
        assert delete_response.status_code == 204
        assert fake_manager.shutdown_calls == [server_id]

        detail_response = client.get(f"/api/mcp/servers/{server_id}")
        assert detail_response.status_code == 404

        list_response = client.get("/api/mcp/servers")
        assert list_response.status_code == 200
        assert all(server["id"] != server_id for server in api_data(list_response))
    finally:
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_delete_imported_or_stale_servers_removes_them_completely(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.compat.mcp.importer import MCPImportTarget
    from app.compat.mcp.models import DiscoveredMCPCapability
    from app.compat.mcp.service import get_mcp_client_manager
    from app.db.models import CompatibilityScope, CompatibilitySource, MCPCapabilityKind

    project_root = tmp_path / "project"
    config_path = project_root / "opencode.json"
    _write_json(
        config_path,
        {"mcp": {"open-local": {"type": "local", "command": ["node", "server.js"]}}},
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

    from app.compat.mcp import service as mcp_service_module

    original_resolver = mcp_service_module.resolve_mcp_import_targets

    try:
        monkeypatch.setattr(
            mcp_service_module,
            "resolve_mcp_import_targets",
            lambda _settings: [
                MCPImportTarget(
                    source=CompatibilitySource.OPENCODE,
                    scope=CompatibilityScope.PROJECT,
                    file_path=config_path.as_posix(),
                )
            ],
        )

        imported_response = client.post("/api/mcp/import")
        assert imported_response.status_code == 200
        active_server_id = api_data(imported_response)[0]["id"]

        delete_active_response = client.delete(f"/api/mcp/servers/{active_server_id}")
        assert delete_active_response.status_code == 204

        detail_after_active_delete = client.get(f"/api/mcp/servers/{active_server_id}")
        assert detail_after_active_delete.status_code == 404

        reimport_response = client.post("/api/mcp/import")
        assert reimport_response.status_code == 200
        restored_server_id = api_data(reimport_response)[0]["id"]

        _write_json(config_path, {"mcp": {}})
        stale_import_response = client.post("/api/mcp/import")
        assert stale_import_response.status_code == 200
        stale_server = next(
            server
            for server in api_data(stale_import_response)
            if server["id"] == restored_server_id
        )
        assert stale_server["status"] == "inactive"

        delete_stale_response = client.delete(f"/api/mcp/servers/{restored_server_id}")
        assert delete_stale_response.status_code == 204

        detail_after_stale_delete = client.get(f"/api/mcp/servers/{restored_server_id}")
        assert detail_after_stale_delete.status_code == 404

        list_response = client.get("/api/mcp/servers")
        assert list_response.status_code == 200
        assert all(server["id"] != restored_server_id for server in api_data(list_response))
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


def test_mcp_delete_missing_server_returns_not_found(client: TestClient) -> None:
    delete_response = client.delete("/api/mcp/servers/missing-server")
    assert delete_response.status_code == 404
    assert api_data(delete_response)["detail"] == "MCP server not found"


class _FakeMCPClientManager:
    def __init__(
        self,
        capabilities_by_server: dict[str, list[object]],
        tool_results: dict[tuple[str, str], dict[str, object]] | None = None,
    ) -> None:
        self._capabilities_by_server = capabilities_by_server
        self._tool_results = tool_results or {}
        self.discover_failures: dict[str, Exception] = {}
        self.tool_failures: dict[tuple[str, str], Exception] = {}
        self.discover_calls: list[str] = []
        self.shutdown_calls: list[str] = []
        self.tool_calls: list[tuple[str, str, dict[str, object]]] = []

    async def discover(self, server: object) -> list[object]:
        server_name = getattr(server, "name")
        self.discover_calls.append(server_name)
        if server_name in self.discover_failures:
            raise self.discover_failures[server_name]
        return list(self._capabilities_by_server.get(server_name, []))

    async def shutdown(self, server_id: str) -> None:
        self.shutdown_calls.append(server_id)

    async def call_tool(
        self,
        server: object,
        *,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        server_name = getattr(server, "name")
        self.tool_calls.append((server_name, tool_name, dict(arguments)))
        if (server_name, tool_name) in self.tool_failures:
            raise self.tool_failures[(server_name, tool_name)]
        return dict(self._tool_results.get((server_name, tool_name), {"content": []}))

    def error_message(self, exc: Exception) -> str:
        return str(exc)


def _write_json(path: Path, content: dict[str, object]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
