from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.compat.mcp.service import MCPService, get_mcp_service
from app.db.models import MCPServerRead

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class ToggleMCPServerRequest(BaseModel):
    enabled: bool


@router.post("/import", response_model=list[MCPServerRead])
async def import_mcp_servers(
    mcp_service: MCPService = Depends(get_mcp_service),
) -> list[MCPServerRead]:
    return await mcp_service.import_servers()


@router.get("/servers", response_model=list[MCPServerRead])
async def list_mcp_servers(
    mcp_service: MCPService = Depends(get_mcp_service),
) -> list[MCPServerRead]:
    return mcp_service.list_servers()


@router.get("/servers/{server_id}", response_model=MCPServerRead)
async def get_mcp_server(
    server_id: str,
    mcp_service: MCPService = Depends(get_mcp_service),
) -> MCPServerRead:
    server = mcp_service.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return server


@router.post("/servers/{server_id}/toggle", response_model=MCPServerRead)
async def toggle_mcp_server(
    server_id: str,
    request: ToggleMCPServerRequest,
    mcp_service: MCPService = Depends(get_mcp_service),
) -> MCPServerRead:
    server = await mcp_service.toggle_server(server_id, request.enabled)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return server


@router.post("/servers/{server_id}/refresh", response_model=MCPServerRead)
async def refresh_mcp_server(
    server_id: str,
    mcp_service: MCPService = Depends(get_mcp_service),
) -> MCPServerRead:
    server = await mcp_service.refresh_capabilities(server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return server
