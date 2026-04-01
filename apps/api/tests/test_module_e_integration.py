from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.compat.skills.models import SkillScanRoot
from app.db.models import CompatibilityScope, CompatibilitySource, MCPCapabilityKind
from app.main import app
from tests.utils import api_data


def test_module_e_acceptance_smoke_covers_workspace_history_graph_and_runtime(
    client: TestClient,
) -> None:
    health_response = client.get("/api/health")
    assert health_response.status_code == 200
    assert api_data(health_response)["status"] == "ok"

    project_response = client.post(
        "/api/projects",
        json={"name": "Module E Project", "description": "integration smoke"},
    )
    assert project_response.status_code == 201
    project_id = api_data(project_response)["id"]

    session_response = client.post(
        "/api/sessions",
        json={
            "title": "Module E Session",
            "project_id": project_id,
            "goal": "Validate module E acceptance path",
            "scenario_type": "web",
        },
    )
    assert session_response.status_code == 201
    session_id = api_data(session_response)["id"]

    chat_response = client.post(
        f"/api/sessions/{session_id}/chat",
        json={"content": "Generate a safe validation plan."},
    )
    assert chat_response.status_code == 200
    assert api_data(chat_response)["assistant_message"]["role"] == "assistant"

    workflow_start = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )
    assert workflow_start.status_code == 201
    run_id = api_data(workflow_start)["id"]

    for graph_type in ("task", "evidence", "causal"):
        graph_response = client.get(f"/api/sessions/{session_id}/graphs/{graph_type}")
        assert graph_response.status_code == 200
        assert api_data(graph_response)["graph_type"] == graph_type

    export_response = client.get(f"/api/workflows/{run_id}/export")
    replay_response = client.get(f"/api/workflows/{run_id}/replay")
    history_response = client.get(f"/api/sessions/{session_id}/history")
    runtime_status = client.get("/api/runtime/status")

    assert export_response.status_code == 200
    assert replay_response.status_code == 200
    assert history_response.status_code == 200
    assert runtime_status.status_code == 200
    assert api_data(export_response)["run"]["id"] == run_id
    assert api_data(replay_response)["run_id"] == run_id
    assert history_response.json()["meta"]["pagination"]["total"] >= 1
    assert "runtime" in api_data(runtime_status)


def test_module_e_acceptance_smoke_covers_skills_and_mcp_imports(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills" / "demo" / "SKILL.md",
        """---
name: demo
description: Demo skill
compatibility: [opencode]
---
# Demo

Demo body.
""",
    )

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(tmp_path / "skills"),
            )
        ],
    )

    skills_scan = client.post("/api/skills/scan")
    assert skills_scan.status_code == 200
    skills_payload = api_data(skills_scan)
    assert len(skills_payload) == 1
    skill_id = skills_payload[0]["id"]

    enable_response = client.post(f"/api/skills/{skill_id}/enable")
    detail_response = client.get(f"/api/skills/{skill_id}")
    assert enable_response.status_code == 200
    assert detail_response.status_code == 200
    assert api_data(detail_response)["name"] == "demo"

    from app.compat.mcp import service as mcp_service_module
    from app.compat.mcp.importer import MCPImportTarget
    from app.compat.mcp.models import DiscoveredMCPCapability
    from app.compat.mcp.service import get_mcp_client_manager

    project_root = tmp_path / "project"
    _write_json(
        project_root / "opencode.json",
        {
            "mcp": {
                "demo-server": {
                    "type": "remote",
                    "url": "https://example.test/mcp",
                    "headers": {"X-Demo": "1"},
                    "timeout_ms": 3200,
                }
            }
        },
    )

    fake_manager = _FakeMCPClientManager(
        capabilities_by_server={
            "demo-server": [
                DiscoveredMCPCapability(
                    kind=MCPCapabilityKind.TOOL,
                    name="ping",
                    title="Ping",
                    description="Ping demo server",
                    uri=None,
                    metadata={},
                    input_schema={"type": "object"},
                    raw_payload={"name": "ping"},
                )
            ]
        }
    )
    app.dependency_overrides[get_mcp_client_manager] = lambda: fake_manager

    original_resolver = mcp_service_module.resolve_mcp_import_targets
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

    try:
        import_response = client.post("/api/mcp/import")
        assert import_response.status_code == 200
        server_id = api_data(import_response)[0]["id"]

        list_response = client.get("/api/mcp/servers")
        detail_response = client.get(f"/api/mcp/servers/{server_id}")
        refresh_response = client.post(f"/api/mcp/servers/{server_id}/refresh")
        disable_response = client.post(f"/api/mcp/servers/{server_id}/disable")
        enable_response = client.post(f"/api/mcp/servers/{server_id}/enable")

        assert list_response.status_code == 200
        assert detail_response.status_code == 200
        assert refresh_response.status_code == 200
        assert disable_response.status_code == 200
        assert enable_response.status_code == 200
        assert api_data(detail_response)["capabilities"][0]["name"] == "ping"
    finally:
        mcp_service_module.resolve_mcp_import_targets = original_resolver
        app.dependency_overrides.pop(get_mcp_client_manager, None)


class _FakeMCPClientManager:
    def __init__(self, capabilities_by_server: dict[str, list[object]]) -> None:
        self._capabilities_by_server = capabilities_by_server

    async def discover(self, server: object) -> list[object]:
        return list(self._capabilities_by_server.get(getattr(server, "name"), []))

    async def shutdown(self, server_id: str) -> None:
        del server_id

    async def call_tool(
        self,
        server: object,
        *,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        del server, tool_name, arguments
        return {"content": []}


def _write_skill(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, content: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
