from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.db.models import (
    CompatibilitySource,
    MCPCapability,
    MCPServer,
    MCPServerStatus,
)

STALE_IMPORTED_SERVER_ERROR = "Server not found in the latest MCP import."


class MCPRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def list_servers(self) -> list[MCPServer]:
        statement = select(MCPServer).order_by(
            col(MCPServer.source).asc(),
            col(MCPServer.scope).asc(),
            col(MCPServer.name).asc(),
            col(MCPServer.config_path).asc(),
        )
        return list(self.db_session.exec(statement).all())

    def get_server(self, server_id: str) -> MCPServer | None:
        return self.db_session.get(MCPServer, server_id)

    def list_capabilities(self, server_id: str) -> list[MCPCapability]:
        statement = (
            select(MCPCapability)
            .where(MCPCapability.server_id == server_id)
            .order_by(
                col(MCPCapability.kind).asc(),
                col(MCPCapability.name).asc(),
                col(MCPCapability.uri).asc(),
            )
        )
        return list(self.db_session.exec(statement).all())

    def replace_all(
        self,
        servers: list[MCPServer],
        capabilities_by_server_id: dict[str, list[MCPCapability] | None],
    ) -> None:
        imported_by_id = {server.id: server for server in servers}
        imported_ids = set(imported_by_id)

        existing_servers = self.list_servers()
        for existing_server in existing_servers:
            if existing_server.id not in imported_ids:
                if existing_server.source == CompatibilitySource.LOCAL:
                    continue

                existing_server.enabled = False
                existing_server.status = MCPServerStatus.INACTIVE
                existing_server.last_error = STALE_IMPORTED_SERVER_ERROR
                existing_server.health_status = "error"
                existing_server.health_latency_ms = None
                existing_server.health_error = STALE_IMPORTED_SERVER_ERROR
                self.db_session.add(existing_server)
                continue

            imported = imported_by_id[existing_server.id]
            existing_server.name = imported.name
            existing_server.source = imported.source
            existing_server.scope = imported.scope
            existing_server.transport = imported.transport
            existing_server.enabled = imported.enabled
            existing_server.command = imported.command
            existing_server.args_json = list(imported.args_json)
            existing_server.env_json = dict(imported.env_json)
            existing_server.url = imported.url
            existing_server.headers_json = dict(imported.headers_json)
            existing_server.timeout_ms = imported.timeout_ms
            existing_server.status = imported.status
            existing_server.last_error = imported.last_error
            existing_server.health_status = imported.health_status
            existing_server.health_latency_ms = imported.health_latency_ms
            existing_server.health_error = imported.health_error
            existing_server.health_checked_at = imported.health_checked_at
            existing_server.config_path = imported.config_path
            self.db_session.add(existing_server)

            capabilities = capabilities_by_server_id.get(existing_server.id)
            if capabilities is not None:
                for capability in self.list_capabilities(existing_server.id):
                    self.db_session.delete(capability)
                for capability in capabilities:
                    self.db_session.add(capability)

            imported_ids.remove(existing_server.id)

        for imported_id in imported_ids:
            imported = imported_by_id[imported_id]
            self.db_session.add(imported)
            capabilities = capabilities_by_server_id.get(imported_id)
            if capabilities is not None:
                for capability in capabilities:
                    self.db_session.add(capability)
        self.db_session.commit()

    def save_server(self, server: MCPServer, capabilities: list[MCPCapability] | None) -> None:
        self.db_session.add(server)
        if capabilities is not None:
            for capability in self.list_capabilities(server.id):
                self.db_session.delete(capability)
            self.db_session.flush()
            for capability in capabilities:
                self.db_session.add(capability)
        self.db_session.commit()

    def update_server(self, server: MCPServer) -> MCPServer:
        self.db_session.add(server)
        self.db_session.commit()
        self.db_session.refresh(server)
        return server

    def delete_server(self, server_id: str) -> bool:
        server = self.get_server(server_id)
        if server is None:
            return False

        for capability in self.list_capabilities(server_id):
            self.db_session.delete(capability)
        self.db_session.delete(server)
        self.db_session.commit()
        return True
