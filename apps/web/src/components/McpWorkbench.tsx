import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  importMcpServers,
  listMcpServers,
  refreshMcpServer,
  toggleMcpServer,
} from "../lib/api";
import type { MCPServer } from "../types/mcp";

const MCP_SERVERS_QUERY_KEY = ["mcp", "servers"] as const;

function buildServerSearchIndex(server: MCPServer): string {
  return [
    server.name,
    server.status,
  ]
    .join(" ")
    .toLowerCase();
}

function countServers(servers: MCPServer[], predicate: (server: MCPServer) => boolean): number {
  return servers.filter(predicate).length;
}

export function McpWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { serverId } = useParams<{ serverId?: string }>();
  const [searchValue, setSearchValue] = useState("");

  const serversQuery = useQuery({
    queryKey: MCP_SERVERS_QUERY_KEY,
    queryFn: ({ signal }) => listMcpServers(signal),
  });

  const filteredServers = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();
    const servers = serversQuery.data ?? [];

    if (!keyword) {
      return servers;
    }

    return servers.filter((server) => buildServerSearchIndex(server).includes(keyword));
  }, [searchValue, serversQuery.data]);

  const selectedServerId = useMemo(() => {
    const servers = serversQuery.data ?? [];

    if (serverId && servers.some((server) => server.id === serverId)) {
      return serverId;
    }

    return null;
  }, [serverId, serversQuery.data]);

  const importMutation = useMutation({
    mutationFn: () => importMcpServers(),
    onSuccess: async (servers) => {
      queryClient.setQueryData<MCPServer[]>(MCP_SERVERS_QUERY_KEY, servers);
      await queryClient.invalidateQueries({ queryKey: ["mcp", "server"] });

      if (!selectedServerId && servers[0]) {
        navigate(`/mcp/${servers[0].id}`, { replace: true });
      }
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => toggleMcpServer(id, enabled),
    onSuccess: (updatedServer) => {
      queryClient.setQueryData<MCPServer[] | undefined>(MCP_SERVERS_QUERY_KEY, (currentValue) =>
        currentValue?.map((server) => (server.id === updatedServer.id ? updatedServer : server)),
      );
    },
  });

  const refreshMutation = useMutation({
    mutationFn: (id: string) => refreshMcpServer(id),
    onSuccess: (updatedServer) => {
      queryClient.setQueryData<MCPServer[] | undefined>(MCP_SERVERS_QUERY_KEY, (currentValue) =>
        currentValue?.map((server) => (server.id === updatedServer.id ? updatedServer : server)),
      );
    },
  });

  const connectedCount = countServers(serversQuery.data ?? [], (server) => server.status === "connected");
  const enabledCount = countServers(serversQuery.data ?? [], (server) => server.enabled);
  const errorCount = countServers(serversQuery.data ?? [], (server) => server.status === "error");
  const totalCount = serversQuery.data?.length ?? 0;
  const filteredCount = filteredServers.length;
  const mutationErrorMessage = toggleMutation.isError
    ? toggleMutation.error.message
    : refreshMutation.isError
      ? refreshMutation.error.message
      : importMutation.isError
        ? importMutation.error.message
        : null;

  return (
    <main className="management-workbench management-workbench-single">
      <section className="management-unified-panel panel" aria-label="MCP 管理">
        <header className="management-unified-header">
          <div className="management-detail-copy">
            <h2 className="panel-title">MCP</h2>
          </div>

          <button
            className="button button-secondary"
            type="button"
            onClick={() => void importMutation.mutateAsync()}
            disabled={importMutation.isPending}
          >
            {importMutation.isPending ? "导入中" : "导入服务器"}
          </button>
        </header>

        <div className="management-metric-row management-metric-row-wide">
          <div className="management-metric-card">
            <span className="management-metric-label">总数</span>
            <strong className="management-metric-value">{totalCount}</strong>
          </div>
          <div className="management-metric-card">
            <span className="management-metric-label">启用</span>
            <strong className="management-metric-value">{enabledCount}</strong>
          </div>
          <div className="management-metric-card">
            <span className="management-metric-label">已连接</span>
            <strong className="management-metric-value">{connectedCount}</strong>
          </div>
          <div className="management-metric-card">
            <span className="management-metric-label">异常</span>
            <strong className="management-metric-value">{errorCount}</strong>
          </div>
        </div>

        <div className="management-toolbar-row">
          <input
            className="management-search-input"
            type="search"
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder="搜索名称、传输方式、能力或配置路径"
          />

          <span className="management-status-badge tone-neutral">{filteredCount}/{totalCount} 个结果</span>
        </div>

        {mutationErrorMessage ? <div className="management-error-banner">{mutationErrorMessage}</div> : null}

        {serversQuery.isLoading ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">准备 MCP 管理台</p>
            <p className="management-empty-copy">正在获取服务器目录。</p>
          </div>
        ) : serversQuery.isError ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">当前无法展示 MCP</p>
            <p className="management-empty-copy">{serversQuery.error.message}</p>
          </div>
        ) : totalCount === 0 ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">还没有 MCP 服务器</p>
            <p className="management-empty-copy">点击“导入服务器”，把现有后端配置拉入工作台。</p>
          </div>
        ) : (
          <div className="management-unified-body management-unified-stack">
            <section className="management-section-card management-section-card-compact">
              <div className="management-section-header">
                <h3 className="management-section-title">MCP 列表</h3>
                <span className="management-status-badge tone-neutral">{filteredCount} 项</span>
              </div>

              <div className="management-list-shell">
                {filteredServers.length === 0 ? (
                  <div className="management-empty-state">
                    <p className="management-empty-title">没有匹配的服务器</p>
                    <p className="management-empty-copy">你可以先导入，或换一个关键词。</p>
                  </div>
                ) : (
                  <ul className="management-card-grid mcp-card-grid">
                    {filteredServers.map((server) => {
                      const isActive = server.id === selectedServerId;

                      return (
                        <li key={server.id}>
                          <article className={`management-list-card mcp-card${isActive ? " management-list-card-active" : ""}`}>
                            <div className="mcp-card-row">
                              <strong className="management-list-title mcp-card-title">{server.name}</strong>
                              <label className="mcp-card-switch" aria-label={`${server.name} 连接开关`}>
                                <input
                                  type="checkbox"
                                  checked={server.enabled}
                                  onChange={() =>
                                    void toggleMutation.mutateAsync({
                                      id: server.id,
                                      enabled: !server.enabled,
                                    })
                                  }
                                  disabled={toggleMutation.isPending}
                                />
                                <span className="mcp-card-switch-track" />
                              </label>
                            </div>
                          </article>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </section>
          </div>
        )}
      </section>
    </main>
  );
}
