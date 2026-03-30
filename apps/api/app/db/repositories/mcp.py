from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.db.models import MCPCapability, MCPServer


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
        capabilities_by_server_id: dict[str, list[MCPCapability]],
    ) -> None:
        for capability in list(self.db_session.exec(select(MCPCapability)).all()):
            self.db_session.delete(capability)
        for server in self.list_servers():
            self.db_session.delete(server)

        for server in servers:
            self.db_session.add(server)
        self.db_session.flush()
        for capabilities in capabilities_by_server_id.values():
            for capability in capabilities:
                self.db_session.add(capability)
        self.db_session.commit()

    def save_server(self, server: MCPServer, capabilities: list[MCPCapability]) -> None:
        self.db_session.add(server)
        for capability in self.list_capabilities(server.id):
            self.db_session.delete(capability)
        self.db_session.flush()
        for capability in capabilities:
            self.db_session.add(capability)
        self.db_session.commit()
