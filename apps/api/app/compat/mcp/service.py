from __future__ import annotations

import json

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
    MCPCapability,
    MCPServer,
    MCPServerRead,
    MCPServerStatus,
    to_mcp_capability_read,
    to_mcp_server_read,
)
from app.db.repositories import MCPRepository
from app.db.session import get_db_session


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
        capabilities_by_server_id: dict[str, list[MCPCapability]] = {}
        for imported_server in imported_servers:
            server_record = self._to_server_record(imported_server)
            if imported_server.enabled:
                discovered = await self._safe_discover(imported_server, server_record)
                capabilities_by_server_id[server_record.id] = self._to_capability_records(
                    server_record.id,
                    discovered,
                )
            else:
                capabilities_by_server_id[server_record.id] = []
            server_records.append(server_record)

        self._repository.replace_all(server_records, capabilities_by_server_id)
        return self.list_servers()

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
            capabilities = self._to_capability_records(server.id, discovered)
            self._repository.save_server(server, capabilities)
        else:
            await self._client_manager.shutdown(server.id)
            server.status = MCPServerStatus.INACTIVE
            self._repository.save_server(server, [])

        return self.get_server(server_id)

    async def refresh_capabilities(self, server_id: str) -> MCPServerRead | None:
        server = self._repository.get_server(server_id)
        if server is None:
            return None

        discovered = await self._safe_discover(self._to_imported_server(server), server)
        capabilities = self._to_capability_records(server.id, discovered)
        self._repository.save_server(server, capabilities)
        return self.get_server(server_id)

    async def _safe_discover(
        self,
        imported_server: ImportedMCPServer,
        server_record: MCPServer,
    ) -> list[DiscoveredMCPCapability]:
        try:
            discovered = await self._client_manager.discover(imported_server)
        except Exception as exc:
            server_record.status = MCPServerStatus.ERROR
            server_record.last_error = str(exc)
            return []

        server_record.status = MCPServerStatus.CONNECTED
        server_record.last_error = None
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
            config_path=server.config_path,
        )


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
