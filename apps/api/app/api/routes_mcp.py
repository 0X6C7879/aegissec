from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.compat.mcp.service import (
    MCPDisabledServerError,
    MCPInvalidToolError,
    MCPService,
    get_mcp_service,
)
from app.db.models import MCPServerRead, MCPTransport

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class ToggleMCPServerRequest(BaseModel):
    enabled: bool


class ManualMCPServerRegisterRequest(BaseModel):
    name: str
    transport: MCPTransport
    enabled: bool = True
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = 5000


class MCPToolInvokeRequest(BaseModel):
    arguments: dict[str, object] = Field(default_factory=dict)


class MCPToolInvokeResponse(BaseModel):
    server_id: str
    tool_name: str
    result: dict[str, object]


@router.post("/import", response_model=list[MCPServerRead])
async def import_mcp_servers(
    mcp_service: MCPService = Depends(get_mcp_service),
) -> list[MCPServerRead]:
    return await mcp_service.import_servers()


@router.post("/register", response_model=MCPServerRead)
async def register_manual_mcp_server(
    payload: ManualMCPServerRegisterRequest,
    mcp_service: MCPService = Depends(get_mcp_service),
) -> MCPServerRead:
    if payload.transport is MCPTransport.STDIO and not payload.command:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual stdio MCP server requires a command.",
        )
    if payload.transport is MCPTransport.HTTP and not payload.url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual HTTP MCP server requires a url.",
        )

    return await mcp_service.register_manual_server(
        name=payload.name,
        transport=payload.transport.value,
        enabled=payload.enabled,
        command=payload.command,
        args=list(payload.args),
        env=dict(payload.env),
        url=payload.url,
        headers=dict(payload.headers),
        timeout_ms=payload.timeout_ms,
    )


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


@router.post("/servers/{server_id}/enable", response_model=MCPServerRead)
async def enable_mcp_server(
    server_id: str,
    mcp_service: MCPService = Depends(get_mcp_service),
) -> MCPServerRead:
    server = await mcp_service.toggle_server(server_id, True)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return server


@router.post("/servers/{server_id}/disable", response_model=MCPServerRead)
async def disable_mcp_server(
    server_id: str,
    mcp_service: MCPService = Depends(get_mcp_service),
) -> MCPServerRead:
    server = await mcp_service.toggle_server(server_id, False)
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


@router.post("/servers/{server_id}/health", response_model=MCPServerRead)
async def check_mcp_server_health(
    server_id: str,
    mcp_service: MCPService = Depends(get_mcp_service),
) -> MCPServerRead:
    server = await mcp_service.check_server_health(server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return server


@router.post("/servers/{server_id}/tools/{tool_name}/invoke", response_model=MCPToolInvokeResponse)
async def invoke_mcp_tool(
    server_id: str,
    tool_name: str,
    payload: MCPToolInvokeRequest,
    mcp_service: MCPService = Depends(get_mcp_service),
) -> MCPToolInvokeResponse:
    server = mcp_service.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")

    try:
        result = await mcp_service.call_tool(server_id, tool_name, dict(payload.arguments))
    except MCPDisabledServerError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MCPInvalidToolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return MCPToolInvokeResponse(server_id=server_id, tool_name=tool_name, result=result)
