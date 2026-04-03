from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from time import perf_counter
from typing import Any, TypeVar, cast

import httpx

from app.compat.mcp.models import DiscoveredMCPCapability, ImportedMCPServer, MCPHealthCheckResult
from app.db.models import MCPCapabilityKind, MCPTransport
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

_T = TypeVar("_T")


class MCPClientManager:
    async def discover(self, server: ImportedMCPServer) -> list[DiscoveredMCPCapability]:
        return await self._with_session(server, self._discover_capabilities)

    async def call_tool(
        self,
        server: ImportedMCPServer,
        *,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        async def invoke(session: Any) -> dict[str, object]:
            call_tool = getattr(session, "call_tool", None)
            if not callable(call_tool):
                raise RuntimeError("MCP client session does not support call_tool().")
            call_tool_fn = cast(Callable[[str, dict[str, object]], Awaitable[object]], call_tool)
            result = await call_tool_fn(tool_name, arguments)
            return self._payload_to_json_dict(result)

        return await self._with_session(server, invoke)

    async def shutdown(self, server_id: str) -> None:
        del server_id

    async def check_health(self, server: ImportedMCPServer) -> MCPHealthCheckResult:
        started = perf_counter()
        try:
            await self._with_session(server, self._noop)
        except Exception as exc:
            latency_ms = int((perf_counter() - started) * 1000)
            return MCPHealthCheckResult(status="error", latency_ms=latency_ms, error=str(exc))

        latency_ms = int((perf_counter() - started) * 1000)
        return MCPHealthCheckResult(status="ok", latency_ms=latency_ms, error=None)

    async def _with_session(
        self,
        server: ImportedMCPServer,
        operation: Callable[[Any], Awaitable[_T]],
    ) -> _T:
        async with AsyncExitStack() as exit_stack:
            session = await self._open_session(server, exit_stack)
            await self._initialize(session)
            return await operation(session)

    async def _open_session(self, server: ImportedMCPServer, exit_stack: AsyncExitStack) -> Any:
        if server.transport == MCPTransport.STDIO:
            return await self._open_stdio_session(server, exit_stack)
        return await self._open_http_session(server, exit_stack)

    async def _open_stdio_session(
        self, server: ImportedMCPServer, exit_stack: AsyncExitStack
    ) -> Any:
        if server.command is None:
            raise RuntimeError(f"MCP stdio server '{server.name}' is missing a command.")

        server_params = StdioServerParameters(
            command=server.command,
            args=list(server.args),
            env=dict(server.env),
        )
        read_stream, write_stream = await exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        session = ClientSession(read_stream, write_stream)
        return await self._maybe_enter_async_context(exit_stack, session)

    async def _open_http_session(
        self, server: ImportedMCPServer, exit_stack: AsyncExitStack
    ) -> Any:
        if server.url is None:
            raise RuntimeError(f"MCP HTTP server '{server.name}' is missing a URL.")

        http_client = httpx.AsyncClient(
            headers=dict(server.headers),
            timeout=server.timeout_ms / 1000,
        )
        managed_http_client = await self._maybe_enter_async_context(exit_stack, http_client)
        read_stream, write_stream, _ = await exit_stack.enter_async_context(
            streamable_http_client(
                server.url,
                http_client=managed_http_client,
                terminate_on_close=True,
            )
        )
        session = ClientSession(read_stream, write_stream)
        return await self._maybe_enter_async_context(exit_stack, session)

    @staticmethod
    async def _noop(session: Any) -> None:
        del session

    @staticmethod
    async def _maybe_enter_async_context(exit_stack: AsyncExitStack, resource: Any) -> Any:
        if hasattr(resource, "__aenter__") and hasattr(resource, "__aexit__"):
            return await exit_stack.enter_async_context(cast(Any, resource))
        if hasattr(resource, "close"):
            close_method = cast(Any, resource).close
            exit_stack.push_async_callback(close_method)
        return resource

    @staticmethod
    async def _initialize(session: Any) -> None:
        initialize = getattr(session, "initialize", None)
        if not callable(initialize):
            raise RuntimeError("MCP client session does not support initialize().")
        initialize_fn = cast(Callable[[], Awaitable[object]], initialize)
        await initialize_fn()

    async def _discover_capabilities(self, session: Any) -> list[DiscoveredMCPCapability]:
        capabilities: list[DiscoveredMCPCapability] = []
        capabilities.extend(await self._discover_tools(session))
        capabilities.extend(await self._discover_resources(session))
        capabilities.extend(await self._discover_resource_templates(session))
        capabilities.extend(await self._discover_prompts(session))
        return capabilities

    async def _discover_tools(self, session: Any) -> list[DiscoveredMCPCapability]:
        result = await session.list_tools()
        return [
            DiscoveredMCPCapability(
                kind=MCPCapabilityKind.TOOL,
                name=self._string_attr(tool, "name"),
                title=self._optional_string_attr(tool, "title"),
                description=self._optional_string_attr(tool, "description"),
                uri=None,
                metadata=self._metadata(tool),
                input_schema=self._input_schema(tool),
                raw_payload=self._raw_payload(tool),
            )
            for tool in getattr(result, "tools", [])
        ]

    async def _discover_resources(self, session: Any) -> list[DiscoveredMCPCapability]:
        result = await session.list_resources()
        return [
            DiscoveredMCPCapability(
                kind=MCPCapabilityKind.RESOURCE,
                name=self._string_attr(resource, "name", fallback_attr="uri"),
                title=self._optional_string_attr(resource, "title"),
                description=self._optional_string_attr(resource, "description"),
                uri=self._optional_string_attr(resource, "uri"),
                metadata=self._metadata(resource),
                input_schema={},
                raw_payload=self._raw_payload(resource),
            )
            for resource in getattr(result, "resources", [])
        ]

    async def _discover_resource_templates(self, session: Any) -> list[DiscoveredMCPCapability]:
        result = await session.list_resource_templates()
        return [
            DiscoveredMCPCapability(
                kind=MCPCapabilityKind.RESOURCE_TEMPLATE,
                name=self._string_attr(template, "name", fallback_attr="uriTemplate"),
                title=self._optional_string_attr(template, "title"),
                description=self._optional_string_attr(template, "description"),
                uri=self._optional_string_attr(template, "uriTemplate"),
                metadata=self._metadata(template),
                input_schema={},
                raw_payload=self._raw_payload(template),
            )
            for template in getattr(result, "resourceTemplates", [])
        ]

    async def _discover_prompts(self, session: Any) -> list[DiscoveredMCPCapability]:
        result = await session.list_prompts()
        return [
            DiscoveredMCPCapability(
                kind=MCPCapabilityKind.PROMPT,
                name=self._string_attr(prompt, "name"),
                title=self._optional_string_attr(prompt, "title"),
                description=self._optional_string_attr(prompt, "description"),
                uri=None,
                metadata=self._metadata(prompt),
                input_schema={},
                raw_payload=self._raw_payload(prompt),
            )
            for prompt in getattr(result, "prompts", [])
        ]

    @staticmethod
    def _raw_payload(item: Any) -> dict[str, object]:
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            try:
                payload = model_dump(mode="json")
            except TypeError:
                payload = model_dump()
            if isinstance(payload, dict):
                return MCPClientManager._make_json_safe(payload)
        return {}

    @staticmethod
    def _make_json_safe(payload: dict[str, object]) -> dict[str, object]:
        return cast(dict[str, object], json.loads(json.dumps(payload, default=str)))

    @staticmethod
    def _payload_to_json_dict(payload: object) -> dict[str, object]:
        model_dump = getattr(payload, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump(mode="json")
            except TypeError:
                dumped = model_dump()
            if isinstance(dumped, dict):
                return MCPClientManager._make_json_safe(dumped)
            return {"result": dumped}
        if isinstance(payload, dict):
            return MCPClientManager._make_json_safe(payload)
        return {"result": payload}

    def _metadata(self, item: Any) -> dict[str, object]:
        payload = self._raw_payload(item)
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            return dict(metadata)
        return {}

    def _input_schema(self, item: Any) -> dict[str, object]:
        for key in ("inputSchema", "input_schema"):
            payload = self._raw_payload(item)
            value = payload.get(key)
            if isinstance(value, dict):
                return dict(value)
            attribute = getattr(item, key, None)
            if isinstance(attribute, dict):
                return dict(attribute)
        return {}

    @staticmethod
    def _string_attr(item: Any, attribute: str, fallback_attr: str | None = None) -> str:
        value = getattr(item, attribute, None)
        if isinstance(value, str):
            return value
        if fallback_attr is not None:
            fallback = getattr(item, fallback_attr, None)
            if isinstance(fallback, str):
                return fallback
        raise RuntimeError(f"MCP capability is missing required attribute '{attribute}'.")

    @staticmethod
    def _optional_string_attr(item: Any, attribute: str) -> str | None:
        value = getattr(item, attribute, None)
        if value is None:
            return None
        return value if isinstance(value, str) else str(value)
