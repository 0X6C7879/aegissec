from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Depends
from sqlmodel import Session as DBSession

from app.compat.mcp.client_manager import MCPClientManager
from app.compat.mcp.importer import (
    MCPImportTarget,
    configured_mcp_import_targets,
    default_mcp_import_targets,
    import_mcp_servers,
)
from app.compat.mcp.models import DiscoveredMCPCapability, ImportedMCPServer
from app.core.settings import Settings, get_settings
from app.db.models import (
    CompatibilityScope,
    CompatibilitySource,
    MCPCapability,
    MCPCapabilityKind,
    MCPServer,
    MCPServerRead,
    MCPServerStatus,
    MCPTransport,
    to_mcp_capability_read,
    to_mcp_server_read,
)
from app.db.repositories import MCPRepository
from app.db.session import get_db_session


class MCPDisabledServerError(Exception):
    pass


class MCPInvalidToolError(Exception):
    pass


class MCPService:
    def __init__(
        self,
        db_session: DBSession,
        settings: Settings,
        client_manager: MCPClientManager,
    ) -> None:
        self._repository = MCPRepository(db_session)
        self._settings = settings
        self._client_manager = client_manager

    async def import_servers(self) -> list[MCPServerRead]:
        imported_servers = import_mcp_servers(resolve_mcp_import_targets(self._settings))
        server_records: list[MCPServer] = []
        capabilities_by_server_id: dict[str, list[MCPCapability] | None] = {}
        for imported_server in imported_servers:
            server_record = self._to_server_record(imported_server)
            if imported_server.enabled:
                discovered = await self._safe_discover(imported_server, server_record)
                capabilities_by_server_id[server_record.id] = (
                    self._to_capability_records(server_record.id, discovered)
                    if discovered is not None
                    else None
                )
            else:
                capabilities_by_server_id[server_record.id] = None
            server_records.append(server_record)

        self._repository.replace_all(server_records, capabilities_by_server_id)
        return self.list_servers()

    async def register_manual_server(
        self,
        *,
        name: str,
        transport: str,
        enabled: bool,
        command: str | None,
        args: list[str],
        env: dict[str, str],
        url: str | None,
        headers: dict[str, str],
        timeout_ms: int,
    ) -> MCPServerRead:
        imported_server = ImportedMCPServer(
            id=str(uuid4()),
            name=name,
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            transport=MCPTransport(transport),
            enabled=enabled,
            command=command,
            args=list(args),
            env=dict(env),
            url=url,
            headers=dict(headers),
            timeout_ms=timeout_ms,
            config_path=f"manual://{name}",
        )
        server_record = self._to_server_record(imported_server)
        capabilities: list[MCPCapability] | None = None
        if imported_server.enabled:
            discovered = await self._safe_discover(imported_server, server_record)
            if discovered is not None:
                capabilities = self._to_capability_records(server_record.id, discovered)
        self._repository.save_server(server_record, capabilities)
        return self._read_server(server_record)

    def list_servers(self) -> list[MCPServerRead]:
        return [self._read_server(server) for server in self._repository.list_servers()]

    def get_server(self, server_id: str) -> MCPServerRead | None:
        server = self._repository.get_server(server_id)
        if server is None:
            return None
        return self._read_server(server)

    async def toggle_server(self, server_id: str, enabled: bool) -> MCPServerRead | None:
        server = self._repository.get_server(server_id)
        if server is None:
            return None

        server.enabled = enabled
        server.last_error = None
        if enabled:
            discovered = await self._safe_discover(self._to_imported_server(server), server)
            capabilities = (
                self._to_capability_records(server.id, discovered)
                if discovered is not None
                else None
            )
            self._repository.save_server(server, capabilities)
        else:
            await self._client_manager.shutdown(server.id)
            server.status = MCPServerStatus.INACTIVE
            self._repository.save_server(server, None)

        return self.get_server(server_id)

    async def refresh_capabilities(self, server_id: str) -> MCPServerRead | None:
        server = self._repository.get_server(server_id)
        if server is None:
            return None
        if not server.enabled:
            return self.get_server(server_id)

        discovered = await self._safe_discover(self._to_imported_server(server), server)
        capabilities = (
            self._to_capability_records(server.id, discovered) if discovered is not None else None
        )
        self._repository.save_server(server, capabilities)
        return self.get_server(server_id)

    async def check_server_health(self, server_id: str) -> MCPServerRead | None:
        server = self._repository.get_server(server_id)
        if server is None:
            return None

        result = await self._client_manager.check_health(self._to_imported_server(server))
        self._apply_health_result(server, result.status, result.latency_ms, result.error)
        self._repository.update_server(server)
        return self.get_server(server_id)

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object] | None:
        server = self._repository.get_server(server_id)
        if server is None:
            return None
        if not server.enabled:
            raise MCPDisabledServerError(f"MCP server '{server.name}' is disabled.")

        matching_capabilities = [
            capability
            for capability in self._repository.list_capabilities(server.id)
            if capability.name == tool_name
        ]
        if not matching_capabilities:
            raise MCPInvalidToolError(
                f"MCP tool '{tool_name}' is not registered for server '{server.name}'."
            )
        if not any(
            capability.kind == MCPCapabilityKind.TOOL for capability in matching_capabilities
        ):
            raise MCPInvalidToolError(
                f"MCP capability '{tool_name}' on server '{server.name}' is not an invokable tool."
            )
        imported_server = self._to_imported_server(server)
        try:
            result = await self._client_manager.call_tool(
                imported_server,
                tool_name=tool_name,
                arguments=arguments,
            )
        except Exception as exc:
            server.status = MCPServerStatus.ERROR
            server.last_error = str(exc)
            self._apply_health_result(server, "error", None, str(exc))
            self._repository.update_server(server)
            raise

        server.status = MCPServerStatus.CONNECTED
        server.last_error = None
        self._apply_health_result(server, "ok", None, None)
        self._repository.update_server(server)
        return result

    async def _safe_discover(
        self,
        imported_server: ImportedMCPServer,
        server_record: MCPServer,
    ) -> list[DiscoveredMCPCapability] | None:
        try:
            discovered = await self._client_manager.discover(imported_server)
        except Exception as exc:
            server_record.status = MCPServerStatus.ERROR
            server_record.last_error = str(exc)
            self._apply_health_result(server_record, "error", None, str(exc))
            return None

        server_record.status = MCPServerStatus.CONNECTED
        server_record.last_error = None
        self._apply_health_result(server_record, "ok", None, None)
        return discovered

    def _read_server(self, server: MCPServer) -> MCPServerRead:
        capabilities = [
            to_mcp_capability_read(capability)
            for capability in self._repository.list_capabilities(server.id)
        ]
        return to_mcp_server_read(server, capabilities)

    @staticmethod
    def _to_server_record(imported_server: ImportedMCPServer) -> MCPServer:
        return MCPServer(
            id=imported_server.id,
            name=imported_server.name,
            source=imported_server.source,
            scope=imported_server.scope,
            transport=imported_server.transport,
            enabled=imported_server.enabled,
            command=imported_server.command,
            args_json=list(imported_server.args),
            env_json=dict(imported_server.env),
            url=imported_server.url,
            headers_json=dict(imported_server.headers),
            timeout_ms=imported_server.timeout_ms,
            status=MCPServerStatus.INACTIVE,
            last_error=None,
            health_status=imported_server.health_status,
            health_latency_ms=imported_server.health_latency_ms,
            health_error=imported_server.health_error,
            health_checked_at=imported_server.health_checked_at,
            config_path=imported_server.config_path,
        )

    @staticmethod
    def _to_capability_records(
        server_id: str,
        discovered: list[DiscoveredMCPCapability],
    ) -> list[MCPCapability]:
        return [
            MCPCapability(
                id=f"{server_id}:{capability.kind}:{capability.uri or capability.name}",
                server_id=server_id,
                kind=capability.kind,
                name=capability.name,
                title=capability.title,
                description=capability.description,
                uri=capability.uri,
                metadata_json=MCPService._json_compatible_mapping(capability.metadata),
                input_schema_json=MCPService._json_compatible_mapping(capability.input_schema),
                raw_payload_json=MCPService._json_compatible_mapping(capability.raw_payload),
            )
            for capability in discovered
        ]

    @staticmethod
    def _json_compatible_mapping(payload: dict[str, object]) -> dict[str, object]:
        normalized = json.loads(json.dumps(payload, default=str))
        if not isinstance(normalized, dict):
            return {}
        return dict(normalized)

    @staticmethod
    def _to_imported_server(server: MCPServer) -> ImportedMCPServer:
        return ImportedMCPServer(
            id=server.id,
            name=server.name,
            source=server.source,
            scope=server.scope,
            transport=server.transport,
            enabled=server.enabled,
            command=server.command,
            args=list(server.args_json),
            env=dict(server.env_json),
            url=server.url,
            headers=dict(server.headers_json),
            timeout_ms=server.timeout_ms,
            health_status=server.health_status,
            health_latency_ms=server.health_latency_ms,
            health_error=server.health_error,
            health_checked_at=server.health_checked_at,
            config_path=server.config_path,
        )

    @staticmethod
    def _apply_health_result(
        server: MCPServer,
        status: str,
        latency_ms: int | None,
        error: str | None,
    ) -> None:
        server.health_status = status
        server.health_latency_ms = latency_ms
        server.health_error = error
        server.health_checked_at = datetime.now(UTC)


def resolve_mcp_import_targets(settings: Settings) -> list[MCPImportTarget]:
    configured_targets = configured_mcp_import_targets(settings.mcp_import_paths)
    all_targets = default_mcp_import_targets() + configured_targets
    deduplicated: dict[str, MCPImportTarget] = {}
    for target in all_targets:
        deduplicated[target.file_path] = target
    return list(deduplicated.values())


def get_mcp_client_manager() -> MCPClientManager:
    return MCPClientManager()


def get_mcp_service(
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    client_manager: MCPClientManager = Depends(get_mcp_client_manager),
) -> MCPService:
    return MCPService(db_session, settings, client_manager)
