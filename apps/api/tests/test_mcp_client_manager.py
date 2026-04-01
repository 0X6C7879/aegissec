from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import pytest
from pydantic import AnyUrl, TypeAdapter

from app.compat.mcp.client_manager import MCPClientManager
from app.compat.mcp.models import ImportedMCPServer
from app.db.models import CompatibilityScope, CompatibilitySource, MCPCapabilityKind, MCPTransport


class _FakeSession:
    def __init__(self, read_stream: object, write_stream: object) -> None:
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.initialize_calls = 0
        self.list_tools_calls = 0
        self.list_resources_calls = 0
        self.list_resource_templates_calls = 0
        self.list_prompts_calls = 0
        self.call_tool_calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def list_tools(self) -> object:
        self.list_tools_calls += 1
        return type(
            "ListToolsResult",
            (),
            {
                "tools": [
                    type(
                        "Tool",
                        (),
                        {
                            "name": "scan",
                            "title": "Scan",
                            "description": "Run a validation scan",
                            "inputSchema": {"type": "object"},
                            "model_dump": lambda self, mode="python": {
                                "name": self.name,
                                "title": self.title,
                                "description": self.description,
                                "inputSchema": self.inputSchema,
                            },
                        },
                    )()
                ]
            },
        )()

    async def list_resources(self) -> object:
        self.list_resources_calls += 1
        uri = TypeAdapter(AnyUrl).validate_python("https://example.test/resources/notes")
        return type(
            "ListResourcesResult",
            (),
            {
                "resources": [
                    type(
                        "Resource",
                        (),
                        {
                            "name": "notes-resource",
                            "title": "Notes",
                            "description": "Workspace notes resource",
                            "uri": uri,
                            "model_dump": lambda self, mode="python": {
                                "name": self.name,
                                "title": self.title,
                                "description": self.description,
                                "uri": str(self.uri) if mode == "json" else self.uri,
                            },
                        },
                    )()
                ]
            },
        )()

    async def list_resource_templates(self) -> object:
        self.list_resource_templates_calls += 1
        return type(
            "ListResourceTemplatesResult",
            (),
            {
                "resourceTemplates": [
                    type(
                        "ResourceTemplate",
                        (),
                        {
                            "name": "workspace-template",
                            "title": "Workspace Template",
                            "description": "Template resource",
                            "uriTemplate": "workspace://{name}",
                            "model_dump": lambda self, mode="python": {
                                "name": self.name,
                                "title": self.title,
                                "description": self.description,
                                "uriTemplate": self.uriTemplate,
                            },
                        },
                    )()
                ]
            },
        )()

    async def list_prompts(self) -> object:
        self.list_prompts_calls += 1
        return type(
            "ListPromptsResult",
            (),
            {
                "prompts": [
                    type(
                        "Prompt",
                        (),
                        {
                            "name": "report",
                            "title": "Report",
                            "description": "Generate a report prompt",
                            "model_dump": lambda self, mode="python": {
                                "name": self.name,
                                "title": self.title,
                                "description": self.description,
                            },
                        },
                    )()
                ]
            },
        )()

    async def close(self) -> None:
        self.closed = True

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        self.call_tool_calls.append((tool_name, dict(arguments)))
        return type(
            "CallToolResult",
            (),
            {
                "model_dump": lambda self, mode="python": {
                    "content": [{"type": "text", "text": "ok"}],
                    "isError": False,
                }
            },
        )()


class _FakeAsyncClient:
    def __init__(self, *, headers: dict[str, str], timeout: float) -> None:
        self.headers = headers
        self.timeout = timeout


@pytest.mark.anyio
async def test_discover_stdio_server_initializes_session_and_maps_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    session = _FakeSession(read_stream="stdio-read", write_stream="stdio-write")

    class _FakeServerParameters:
        def __init__(self, **kwargs: object) -> None:
            captured["stdio_params"] = kwargs

    @asynccontextmanager
    async def fake_stdio_client(server_params: object) -> AsyncIterator[tuple[object, object]]:
        captured["stdio_client_params"] = server_params
        yield ("stdio-read", "stdio-write")

    def fake_client_session(read_stream: object, write_stream: object) -> _FakeSession:
        assert read_stream == "stdio-read"
        assert write_stream == "stdio-write"
        return session

    monkeypatch.setattr(
        "app.compat.mcp.client_manager.StdioServerParameters", _FakeServerParameters
    )
    monkeypatch.setattr("app.compat.mcp.client_manager.stdio_client", fake_stdio_client)
    monkeypatch.setattr("app.compat.mcp.client_manager.ClientSession", fake_client_session)

    manager = MCPClientManager()
    server = ImportedMCPServer(
        id="stdio-server",
        name="stdio-server",
        source=CompatibilitySource.CLAUDE,
        scope=CompatibilityScope.PROJECT,
        transport=MCPTransport.STDIO,
        enabled=True,
        command="python",
        args=["-m", "demo_stdio"],
        env={"TOKEN": "demo"},
        timeout_ms=4100,
        config_path="/tmp/.mcp.json",
    )

    capabilities = await manager.discover(server)

    assert captured["stdio_params"] == {
        "command": "python",
        "args": ["-m", "demo_stdio"],
        "env": {"TOKEN": "demo"},
    }
    assert session.initialize_calls == 1
    assert [capability.kind for capability in capabilities] == [
        MCPCapabilityKind.TOOL,
        MCPCapabilityKind.RESOURCE,
        MCPCapabilityKind.RESOURCE_TEMPLATE,
        MCPCapabilityKind.PROMPT,
    ]
    assert capabilities[0].name == "scan"
    assert capabilities[0].input_schema == {"type": "object"}
    assert capabilities[1].uri == "https://example.test/resources/notes"
    assert capabilities[1].raw_payload["uri"] == "https://example.test/resources/notes"
    assert capabilities[2].uri == "workspace://{name}"
    assert capabilities[3].name == "report"

    await manager.shutdown(server.id)
    assert session.closed is True


@pytest.mark.anyio
async def test_discover_http_server_uses_streamable_http_transport_and_replaces_existing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_calls: list[dict[str, object]] = []
    first_session = _FakeSession(read_stream="http-read-1", write_stream="http-write-1")
    second_session = _FakeSession(read_stream="http-read-2", write_stream="http-write-2")
    sessions = [first_session, second_session]
    created_clients: list[_FakeAsyncClient] = []

    @asynccontextmanager
    async def fake_streamable_http_client(
        url: str,
        *,
        http_client: _FakeAsyncClient,
        terminate_on_close: bool,
    ) -> AsyncIterator[tuple[object, object, object]]:
        http_calls.append(
            {
                "url": url,
                "headers": http_client.headers,
                "timeout": http_client.timeout,
                "terminate_on_close": terminate_on_close,
            }
        )
        call_index = len(http_calls) - 1
        yield (
            f"http-read-{call_index + 1}",
            f"http-write-{call_index + 1}",
            lambda: f"session-{call_index + 1}",
        )

    def fake_async_client(*, headers: dict[str, str], timeout: float) -> _FakeAsyncClient:
        client = _FakeAsyncClient(headers=headers, timeout=timeout)
        created_clients.append(client)
        return client

    def fake_client_session(read_stream: object, write_stream: object) -> _FakeSession:
        session = sessions.pop(0)
        assert session.read_stream == read_stream
        assert session.write_stream == write_stream
        return session

    monkeypatch.setattr(
        "app.compat.mcp.client_manager.streamable_http_client", fake_streamable_http_client
    )
    monkeypatch.setattr("app.compat.mcp.client_manager.httpx.AsyncClient", fake_async_client)
    monkeypatch.setattr("app.compat.mcp.client_manager.ClientSession", fake_client_session)

    manager = MCPClientManager()
    server = ImportedMCPServer(
        id="http-server",
        name="http-server",
        source=CompatibilitySource.OPENCODE,
        scope=CompatibilityScope.PROJECT,
        transport=MCPTransport.HTTP,
        enabled=True,
        command=None,
        url="https://example.test/mcp",
        headers={"Authorization": "Bearer demo-token"},
        timeout_ms=7200,
        config_path="/tmp/opencode.json",
    )

    await manager.discover(server)
    assert first_session.initialize_calls == 1

    await manager.discover(server)
    assert first_session.closed is True
    assert second_session.initialize_calls == 1
    assert created_clients[0].headers == {"Authorization": "Bearer demo-token"}
    assert created_clients[0].timeout == 7.2
    assert http_calls == [
        {
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer demo-token"},
            "timeout": 7.2,
            "terminate_on_close": True,
        },
        {
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer demo-token"},
            "timeout": 7.2,
            "terminate_on_close": True,
        },
    ]

    await manager.shutdown(server.id)
    assert second_session.closed is True


@pytest.mark.anyio
async def test_call_tool_reuses_existing_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(read_stream="stdio-read", write_stream="stdio-write")

    @asynccontextmanager
    async def fake_stdio_client(server_params: object) -> AsyncIterator[tuple[object, object]]:
        del server_params
        yield ("stdio-read", "stdio-write")

    def fake_client_session(read_stream: object, write_stream: object) -> _FakeSession:
        assert read_stream == "stdio-read"
        assert write_stream == "stdio-write"
        return session

    monkeypatch.setattr("app.compat.mcp.client_manager.stdio_client", fake_stdio_client)
    monkeypatch.setattr("app.compat.mcp.client_manager.ClientSession", fake_client_session)

    manager = MCPClientManager()
    server = ImportedMCPServer(
        id="tool-server",
        name="tool-server",
        source=CompatibilitySource.CLAUDE,
        scope=CompatibilityScope.PROJECT,
        transport=MCPTransport.STDIO,
        enabled=True,
        command="python",
        args=["-m", "demo_stdio"],
        env={},
        timeout_ms=3000,
        config_path="/tmp/.mcp.json",
    )

    result = await manager.call_tool(server, tool_name="scan", arguments={"target": "demo"})
    content = cast(list[dict[str, object]], result["content"])
    first_item = content[0]

    assert result["isError"] is False
    assert first_item["text"] == "ok"
    assert session.call_tool_calls == [("scan", {"target": "demo"})]
    assert session.initialize_calls == 1
