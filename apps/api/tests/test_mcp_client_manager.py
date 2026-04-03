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


class _InitializeFailingSession(_FakeSession):
    async def initialize(self) -> None:
        raise RuntimeError("init boom\nTraceback: ignored noisy detail")


class _CallToolFailingSession(_FakeSession):
    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        self.call_tool_calls.append((tool_name, dict(arguments)))
        raise RuntimeError("upstream boom\nextra detail")


class _GroupedDiscoverFailingSession(_FakeSession):
    async def list_tools(self) -> object:
        raise BaseExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError("tool listing exploded"), GeneratorExit()],
        )


class _PartiallySupportedSession(_FakeSession):
    async def list_resources(self) -> object:
        raise RuntimeError("Method not found")

    async def list_resource_templates(self) -> object:
        raise RuntimeError("Method not found")

    async def list_prompts(self) -> object:
        raise RuntimeError("Method not found")


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

    assert session.closed is True


@pytest.mark.anyio
async def test_discover_http_server_uses_streamable_http_transport_per_operation(
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
    assert first_session.closed is True

    await manager.discover(server)
    assert second_session.initialize_calls == 1
    assert second_session.closed is True
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


@pytest.mark.anyio
async def test_call_tool_opens_and_closes_a_session_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_session = _FakeSession(read_stream="stdio-read", write_stream="stdio-write")
    second_session = _FakeSession(read_stream="stdio-read", write_stream="stdio-write")
    sessions = [first_session, second_session]

    @asynccontextmanager
    async def fake_stdio_client(server_params: object) -> AsyncIterator[tuple[object, object]]:
        del server_params
        yield ("stdio-read", "stdio-write")

    def fake_client_session(read_stream: object, write_stream: object) -> _FakeSession:
        assert read_stream == "stdio-read"
        assert write_stream == "stdio-write"
        return sessions.pop(0)

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

    first_result = await manager.call_tool(server, tool_name="scan", arguments={"target": "demo"})
    second_result = await manager.call_tool(
        server, tool_name="scan", arguments={"target": "demo-2"}
    )

    first_content = cast(list[dict[str, object]], first_result["content"])
    second_content = cast(list[dict[str, object]], second_result["content"])

    assert first_result["isError"] is False
    assert first_content[0]["text"] == "ok"
    assert second_result["isError"] is False
    assert second_content[0]["text"] == "ok"
    assert sessions == []
    assert first_session.call_tool_calls == [("scan", {"target": "demo"})]
    assert second_session.call_tool_calls == [("scan", {"target": "demo-2"})]
    assert first_session.initialize_calls == 1
    assert second_session.initialize_calls == 1
    assert first_session.closed is True
    assert second_session.closed is True


@pytest.mark.anyio
async def test_discover_normalizes_initialize_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _InitializeFailingSession(read_stream="stdio-read", write_stream="stdio-write")

    @asynccontextmanager
    async def fake_stdio_client(server_params: object) -> AsyncIterator[tuple[object, object]]:
        del server_params
        yield ("stdio-read", "stdio-write")

    def fake_client_session(read_stream: object, write_stream: object) -> _InitializeFailingSession:
        assert read_stream == "stdio-read"
        assert write_stream == "stdio-write"
        return session

    monkeypatch.setattr("app.compat.mcp.client_manager.stdio_client", fake_stdio_client)
    monkeypatch.setattr("app.compat.mcp.client_manager.ClientSession", fake_client_session)

    manager = MCPClientManager()
    server = ImportedMCPServer(
        id="discover-server",
        name="discover-server",
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

    with pytest.raises(RuntimeError) as exc_info:
        await manager.discover(server)

    assert str(exc_info.value) == (
        "MCP server 'discover-server' failed to discover capabilities: "
        "init boom Traceback: ignored noisy detail"
    )
    assert session.closed is True


@pytest.mark.anyio
async def test_call_tool_normalizes_upstream_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _CallToolFailingSession(read_stream="stdio-read", write_stream="stdio-write")

    @asynccontextmanager
    async def fake_stdio_client(server_params: object) -> AsyncIterator[tuple[object, object]]:
        del server_params
        yield ("stdio-read", "stdio-write")

    def fake_client_session(read_stream: object, write_stream: object) -> _CallToolFailingSession:
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

    with pytest.raises(RuntimeError) as exc_info:
        await manager.call_tool(server, tool_name="scan", arguments={"target": "demo"})

    assert str(exc_info.value) == (
        "MCP server 'tool-server' failed to call tool 'scan': upstream boom extra detail"
    )
    assert session.call_tool_calls == [("scan", {"target": "demo"})]
    assert session.closed is True


@pytest.mark.anyio
async def test_check_health_normalizes_transport_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_stdio_client(server_params: object) -> AsyncIterator[tuple[object, object]]:
        del server_params
        raise RuntimeError("launch boom\nstack trace detail")
        yield ("stdio-read", "stdio-write")

    monkeypatch.setattr("app.compat.mcp.client_manager.stdio_client", fake_stdio_client)

    manager = MCPClientManager()
    server = ImportedMCPServer(
        id="health-server",
        name="health-server",
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

    result = await manager.check_health(server)

    assert result.status == "error"
    assert (
        result.error
        == "MCP server 'health-server' failed health check: launch boom stack trace detail"
    )
    assert isinstance(result.latency_ms, int)


@pytest.mark.anyio
async def test_discover_strips_taskgroup_wrapper_text_from_base_exception_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _GroupedDiscoverFailingSession(read_stream="stdio-read", write_stream="stdio-write")

    @asynccontextmanager
    async def fake_stdio_client(server_params: object) -> AsyncIterator[tuple[object, object]]:
        del server_params
        yield ("stdio-read", "stdio-write")

    def fake_client_session(
        read_stream: object, write_stream: object
    ) -> _GroupedDiscoverFailingSession:
        assert read_stream == "stdio-read"
        assert write_stream == "stdio-write"
        return session

    monkeypatch.setattr("app.compat.mcp.client_manager.stdio_client", fake_stdio_client)
    monkeypatch.setattr("app.compat.mcp.client_manager.ClientSession", fake_client_session)

    manager = MCPClientManager()
    server = ImportedMCPServer(
        id="group-server",
        name="group-server",
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

    with pytest.raises(RuntimeError) as exc_info:
        await manager.discover(server)

    message = str(exc_info.value)
    assert message == (
        "MCP server 'group-server' failed to discover capabilities: tool listing exploded"
    )
    assert "unhandled errors in a TaskGroup" not in message
    assert "GeneratorExit" not in message


@pytest.mark.anyio
async def test_discover_uses_readable_fallback_for_cleanup_wrapper_only_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(read_stream="stdio-read", write_stream="stdio-write")

    @asynccontextmanager
    async def fake_stdio_client(server_params: object) -> AsyncIterator[tuple[object, object]]:
        del server_params
        yield ("stdio-read", "stdio-write")
        raise BaseExceptionGroup("unhandled errors in a TaskGroup", [GeneratorExit()])

    def fake_client_session(read_stream: object, write_stream: object) -> _FakeSession:
        assert read_stream == "stdio-read"
        assert write_stream == "stdio-write"
        return session

    monkeypatch.setattr("app.compat.mcp.client_manager.stdio_client", fake_stdio_client)
    monkeypatch.setattr("app.compat.mcp.client_manager.ClientSession", fake_client_session)

    manager = MCPClientManager()
    server = ImportedMCPServer(
        id="cleanup-server",
        name="cleanup-server",
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

    with pytest.raises(RuntimeError) as exc_info:
        await manager.discover(server)

    assert str(exc_info.value) == (
        "MCP server 'cleanup-server' failed to discover capabilities: "
        "MCP stdio server 'cleanup-server' exited unexpectedly during capability discovery."
    )


@pytest.mark.anyio
async def test_discover_ignores_unsupported_capability_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PartiallySupportedSession(read_stream="stdio-read", write_stream="stdio-write")

    @asynccontextmanager
    async def fake_stdio_client(server_params: object) -> AsyncIterator[tuple[object, object]]:
        del server_params
        yield ("stdio-read", "stdio-write")

    def fake_client_session(
        read_stream: object, write_stream: object
    ) -> _PartiallySupportedSession:
        assert read_stream == "stdio-read"
        assert write_stream == "stdio-write"
        return session

    monkeypatch.setattr("app.compat.mcp.client_manager.stdio_client", fake_stdio_client)
    monkeypatch.setattr("app.compat.mcp.client_manager.ClientSession", fake_client_session)

    manager = MCPClientManager()
    server = ImportedMCPServer(
        id="partial-server",
        name="partial-server",
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

    capabilities = await manager.discover(server)

    assert [capability.kind for capability in capabilities] == [MCPCapabilityKind.TOOL]
    assert capabilities[0].name == "scan"
    assert session.closed is True


def test_error_message_flattens_raw_taskgroup_wrappers() -> None:
    exc = BaseExceptionGroup(
        "unhandled errors in a TaskGroup",
        [RuntimeError("real child failure"), GeneratorExit()],
    )

    assert MCPClientManager.error_message(exc) == "real child failure"


@pytest.mark.anyio
async def test_shutdown_is_a_safe_noop_for_missing_or_closed_servers() -> None:
    manager = MCPClientManager()

    await manager.shutdown("missing-server")
    await manager.shutdown("missing-server")
