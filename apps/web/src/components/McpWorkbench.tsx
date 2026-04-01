import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  checkMcpServerHealth,
  getMcpServer,
  importMcpServers,
  invokeMcpTool,
  listMcpServers,
  refreshMcpServer,
  registerManualMcpServer,
  setMcpServerEnabled,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import type { MCPCapability, MCPServer, MCPToolInvokeResponse, MCPTransport } from "../types/mcp";

const MCP_SERVERS_QUERY_KEY = ["mcp", "servers"] as const;

function buildServerSearchIndex(server: MCPServer): string {
  const capabilityContent = server.capabilities
    .flatMap((capability) => [capability.name, capability.title, capability.description])
    .join(" ");

  return [
    server.name,
    server.status,
    server.transport,
    server.config_path,
    server.command,
    server.url,
    capabilityContent,
  ]
    .join(" ")
    .toLowerCase();
}

function getServerTone(status: string): string {
  switch (status) {
    case "connected":
      return "tone-connected";
    case "error":
      return "tone-error";
    case "inactive":
      return "tone-inactive";
    default:
      return "tone-neutral";
  }
}

function getHealthTone(status: string | null): string {
  switch (status) {
    case "ok":
      return "tone-success";
    case "error":
      return "tone-error";
    case "degraded":
      return "tone-warning";
    default:
      return "tone-neutral";
  }
}

function getCapabilityTone(kind: string): string {
  switch (kind) {
    case "tool":
      return "tone-success";
    case "prompt":
      return "tone-warning";
    default:
      return "tone-neutral";
  }
}

function getCapabilityKey(capability: MCPCapability): string {
  return `${capability.kind}:${capability.name}`;
}

function groupCapabilities(capabilities: MCPCapability[]): Array<[string, MCPCapability[]]> {
  const grouped = new Map<string, MCPCapability[]>();

  for (const capability of capabilities) {
    const currentGroup = grouped.get(capability.kind) ?? [];
    currentGroup.push(capability);
    grouped.set(capability.kind, currentGroup);
  }

  return Array.from(grouped.entries());
}

function stringifyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function parseStringArray(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function parseJsonRecord(value: string, label: string): Record<string, string> {
  const trimmedValue = value.trim();

  if (!trimmedValue) {
    return {};
  }

  const parsedValue = JSON.parse(trimmedValue) as unknown;
  if (!parsedValue || typeof parsedValue !== "object" || Array.isArray(parsedValue)) {
    throw new Error(`${label} 需要是 JSON 对象。`);
  }

  const entries = Object.entries(parsedValue);
  if (entries.some(([, itemValue]) => typeof itemValue !== "string")) {
    throw new Error(`${label} 的值必须全部是字符串。`);
  }

  return Object.fromEntries(entries) as Record<string, string>;
}

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  const trimmedValue = value.trim();

  if (!trimmedValue) {
    return {};
  }

  const parsedValue = JSON.parse(trimmedValue) as unknown;
  if (!parsedValue || typeof parsedValue !== "object" || Array.isArray(parsedValue)) {
    throw new Error(`${label} 需要是 JSON 对象。`);
  }

  return parsedValue as Record<string, unknown>;
}

function resetFormState(setters: Array<() => void>) {
  setters.forEach((reset) => {
    reset();
  });
}

function upsertServerList(
  currentValue: MCPServer[] | undefined,
  updatedServer: MCPServer,
): MCPServer[] {
  if (!currentValue) {
    return [updatedServer];
  }

  const existingServer = currentValue.some((server) => server.id === updatedServer.id);
  if (!existingServer) {
    return [updatedServer, ...currentValue];
  }

  return currentValue.map((server) => (server.id === updatedServer.id ? updatedServer : server));
}

export function McpWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { serverId } = useParams<{ serverId?: string }>();
  const [searchValue, setSearchValue] = useState("");
  const [registerName, setRegisterName] = useState("");
  const [registerTransport, setRegisterTransport] = useState<MCPTransport>("stdio");
  const [registerEnabled, setRegisterEnabled] = useState(true);
  const [registerCommand, setRegisterCommand] = useState("");
  const [registerArgs, setRegisterArgs] = useState("");
  const [registerEnv, setRegisterEnv] = useState("{}");
  const [registerUrl, setRegisterUrl] = useState("");
  const [registerHeaders, setRegisterHeaders] = useState("{}");
  const [registerTimeoutMs, setRegisterTimeoutMs] = useState("5000");
  const [registerFormError, setRegisterFormError] = useState<string | null>(null);
  const [selectedCapabilityKey, setSelectedCapabilityKey] = useState<string | null>(null);
  const [toolArgumentsText, setToolArgumentsText] = useState("{}");
  const [toolArgumentsError, setToolArgumentsError] = useState<string | null>(null);
  const [invokeResult, setInvokeResult] = useState<MCPToolInvokeResponse | null>(null);

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

  const activeServerSummary = useMemo(
    () => (serversQuery.data ?? []).find((server) => server.id === selectedServerId) ?? null,
    [selectedServerId, serversQuery.data],
  );

  const serverDetailQuery = useQuery({
    enabled: Boolean(selectedServerId),
    queryKey: ["mcp", "server", selectedServerId],
    queryFn: ({ signal }) => getMcpServer(selectedServerId!, signal),
  });

  const activeServer = serverDetailQuery.data ?? activeServerSummary;
  const selectedCapability = useMemo(
    () =>
      activeServer?.capabilities.find(
        (capability) => getCapabilityKey(capability) === selectedCapabilityKey,
      ) ?? null,
    [activeServer, selectedCapabilityKey],
  );

  function handleSelectCapability(capabilityKey: string | null): void {
    setSelectedCapabilityKey(capabilityKey);
    setToolArgumentsError(null);
    setInvokeResult(null);
    setToolArgumentsText("{}");
  }

  useEffect(() => {
    if (!activeServer) {
      setSelectedCapabilityKey(null);
      setToolArgumentsError(null);
      setInvokeResult(null);
      setToolArgumentsText("{}");
      return;
    }

    const nextCapability =
      activeServer.capabilities.find((capability) => capability.kind === "tool") ??
      activeServer.capabilities[0] ??
      null;
    const currentCapabilityExists = activeServer.capabilities.some(
      (capability) => getCapabilityKey(capability) === selectedCapabilityKey,
    );

    if (!currentCapabilityExists) {
      setSelectedCapabilityKey(nextCapability ? getCapabilityKey(nextCapability) : null);
      setToolArgumentsError(null);
      setInvokeResult(null);
      setToolArgumentsText("{}");
    }
  }, [activeServer, selectedCapabilityKey]);

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

  const registerMutation = useMutation({
    mutationFn: () => {
      const trimmedName = registerName.trim();
      if (!trimmedName) {
        throw new Error("名称不能为空。");
      }

      const timeoutMs = Number(registerTimeoutMs);
      if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
        throw new Error("超时毫秒需要是大于 0 的数字。");
      }

      if (registerTransport === "stdio" && !registerCommand.trim()) {
        throw new Error("stdio 服务器需要填写命令。");
      }

      if (registerTransport === "http" && !registerUrl.trim()) {
        throw new Error("http 服务器需要填写 URL。");
      }

      return registerManualMcpServer({
        name: trimmedName,
        transport: registerTransport,
        enabled: registerEnabled,
        command: registerTransport === "stdio" ? registerCommand.trim() : null,
        args: registerTransport === "stdio" ? parseStringArray(registerArgs) : [],
        env: registerTransport === "stdio" ? parseJsonRecord(registerEnv, "环境变量") : {},
        url: registerTransport === "http" ? registerUrl.trim() : null,
        headers: registerTransport === "http" ? parseJsonRecord(registerHeaders, "请求头") : {},
        timeout_ms: timeoutMs,
      });
    },
    onSuccess: async (server) => {
      queryClient.setQueryData<MCPServer[] | undefined>(MCP_SERVERS_QUERY_KEY, (currentValue) =>
        upsertServerList(currentValue, server),
      );
      queryClient.setQueryData<MCPServer | undefined>(["mcp", "server", server.id], server);
      setRegisterFormError(null);
      resetFormState([
        () => setRegisterName(""),
        () => setRegisterTransport("stdio"),
        () => setRegisterEnabled(true),
        () => setRegisterCommand(""),
        () => setRegisterArgs(""),
        () => setRegisterEnv("{}"),
        () => setRegisterUrl(""),
        () => setRegisterHeaders("{}"),
        () => setRegisterTimeoutMs("5000"),
      ]);
      navigate(`/mcp/${server.id}`);
      await queryClient.invalidateQueries({ queryKey: ["mcp", "server"] });
    },
    onError: (error) => {
      setRegisterFormError(error.message);
    },
  });

  const enableMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      setMcpServerEnabled(id, enabled),
    onSuccess: async (updatedServer) => {
      queryClient.setQueryData<MCPServer[] | undefined>(MCP_SERVERS_QUERY_KEY, (currentValue) =>
        upsertServerList(currentValue, updatedServer),
      );
      queryClient.setQueryData<MCPServer | undefined>(
        ["mcp", "server", updatedServer.id],
        updatedServer,
      );
      await queryClient.invalidateQueries({ queryKey: ["mcp", "server", updatedServer.id] });
    },
  });

  const refreshMutation = useMutation({
    mutationFn: (id: string) => refreshMcpServer(id),
    onSuccess: async (updatedServer) => {
      queryClient.setQueryData<MCPServer[] | undefined>(MCP_SERVERS_QUERY_KEY, (currentValue) =>
        upsertServerList(currentValue, updatedServer),
      );
      queryClient.setQueryData<MCPServer | undefined>(
        ["mcp", "server", updatedServer.id],
        updatedServer,
      );
      await queryClient.invalidateQueries({ queryKey: ["mcp", "server", updatedServer.id] });
    },
  });

  const healthMutation = useMutation({
    mutationFn: (id: string) => checkMcpServerHealth(id),
    onSuccess: async (updatedServer) => {
      queryClient.setQueryData<MCPServer[] | undefined>(MCP_SERVERS_QUERY_KEY, (currentValue) =>
        upsertServerList(currentValue, updatedServer),
      );
      queryClient.setQueryData<MCPServer | undefined>(
        ["mcp", "server", updatedServer.id],
        updatedServer,
      );
      await queryClient.invalidateQueries({ queryKey: ["mcp", "server", updatedServer.id] });
    },
  });

  const invokeMutation = useMutation({
    mutationFn: ({ server, capability }: { server: MCPServer; capability: MCPCapability }) =>
      invokeMcpTool(server.id, capability.name, {
        arguments: parseJsonObject(toolArgumentsText, "工具参数"),
      }),
    onSuccess: (response) => {
      setToolArgumentsError(null);
      setInvokeResult(response);
    },
    onError: (error) => {
      setToolArgumentsError(error.message);
    },
  });

  const filteredCount = filteredServers.length;
  const mutationErrorMessage = enableMutation.isError
    ? enableMutation.error.message
    : refreshMutation.isError
      ? refreshMutation.error.message
      : healthMutation.isError
        ? healthMutation.error.message
        : importMutation.isError
          ? importMutation.error.message
          : null;

  return (
    <main className="management-workbench management-workbench-single">
      <section className="management-unified-panel panel" aria-label="MCP 管理">
        <header className="management-unified-header">
          <div className="management-detail-copy">
            <h2 className="panel-title">MCP</h2>
            <p className="management-unified-description">
              维护服务器连接，并在需要时完成一次工具级验证。
            </p>
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

        <div className="management-toolbar-row">
          <input
            className="management-search-input"
            type="search"
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder="搜索名称、传输方式、能力、命令或配置路径"
          />

          <span className="management-status-badge tone-neutral">{filteredCount} 项</span>
        </div>

        {mutationErrorMessage ? (
          <div className="management-error-banner">{mutationErrorMessage}</div>
        ) : null}

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
                    <p className="management-empty-copy">
                      可以先导入现有配置，或手动注册一台新服务器。
                    </p>
                  </div>
                ) : (
                  <ul className="management-card-grid mcp-card-grid">
                    {filteredServers.map((server) => {
                      const isActive = server.id === selectedServerId;
                      const primaryValue =
                        server.transport === "http"
                          ? (server.url ?? "未配置 URL")
                          : (server.command ?? "未配置命令");

                      return (
                        <li key={server.id}>
                          <article
                            className={`management-list-card mcp-card${isActive ? " management-list-card-active" : ""}`}
                          >
                            <div className="mcp-card-row">
                              <div className="management-detail-copy">
                                <strong className="management-list-title mcp-card-title">
                                  {server.name}
                                </strong>
                                <p className="management-list-subtitle">{primaryValue}</p>
                              </div>
                              <label
                                className="mcp-card-switch"
                                aria-label={`${server.name} 连接开关`}
                              >
                                <input
                                  type="checkbox"
                                  checked={server.enabled}
                                  onChange={() =>
                                    void enableMutation.mutateAsync({
                                      id: server.id,
                                      enabled: !server.enabled,
                                    })
                                  }
                                  disabled={enableMutation.isPending}
                                />
                                <span className="mcp-card-switch-track" />
                              </label>
                            </div>

                            <div className="action-row">
                              <span
                                className={`management-status-badge ${getServerTone(server.status)}`}
                              >
                                {server.status}
                              </span>
                              <span className="management-status-badge tone-neutral">
                                {server.transport}
                              </span>
                              <span
                                className={`management-status-badge ${getHealthTone(server.health_status)}`}
                              >
                                健康 {server.health_status ?? "未检测"}
                              </span>
                              <button
                                className="text-button"
                                type="button"
                                onClick={() => navigate(`/mcp/${server.id}`)}
                              >
                                查看详情
                              </button>
                            </div>
                          </article>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </section>

            <section className="management-section-card">
              <div className="management-section-header">
                <h3 className="management-section-title">手动注册</h3>
              </div>

              <form
                className="settings-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  setRegisterFormError(null);
                  void registerMutation.mutateAsync();
                }}
              >
                <div className="field-inline-group">
                  <label className="field-label">
                    名称
                    <input
                      className="field-inline-input"
                      type="text"
                      value={registerName}
                      onChange={(event) => setRegisterName(event.target.value)}
                      placeholder="例如：local-browser"
                    />
                  </label>

                  <label className="field-label">
                    传输方式
                    <select
                      className="field-inline-input"
                      value={registerTransport}
                      onChange={(event) => setRegisterTransport(event.target.value as MCPTransport)}
                    >
                      <option value="stdio">stdio</option>
                      <option value="http">http</option>
                    </select>
                  </label>

                  <label className="field-label">
                    超时毫秒
                    <input
                      className="field-inline-input"
                      type="number"
                      min="1"
                      value={registerTimeoutMs}
                      onChange={(event) => setRegisterTimeoutMs(event.target.value)}
                      placeholder="5000"
                    />
                  </label>
                </div>

                <label className="settings-inline-toggle">
                  <input
                    type="checkbox"
                    checked={registerEnabled}
                    onChange={(event) => setRegisterEnabled(event.target.checked)}
                  />
                  注册后立即启用
                </label>

                {registerTransport === "stdio" ? (
                  <>
                    <label className="field-label">
                      命令
                      <input
                        className="field-input"
                        type="text"
                        value={registerCommand}
                        onChange={(event) => setRegisterCommand(event.target.value)}
                        placeholder="例如：npx @modelcontextprotocol/server-filesystem"
                      />
                    </label>

                    <label className="field-label">
                      参数（每行一个）
                      <textarea
                        className="field-textarea"
                        value={registerArgs}
                        onChange={(event) => setRegisterArgs(event.target.value)}
                        placeholder={"--root\nD:/AI/aegissec"}
                      />
                    </label>

                    <label className="field-label">
                      环境变量 JSON
                      <textarea
                        className="field-textarea"
                        value={registerEnv}
                        onChange={(event) => setRegisterEnv(event.target.value)}
                        placeholder='{"NODE_ENV": "production"}'
                      />
                    </label>
                  </>
                ) : (
                  <>
                    <label className="field-label">
                      URL
                      <input
                        className="field-input"
                        type="url"
                        value={registerUrl}
                        onChange={(event) => setRegisterUrl(event.target.value)}
                        placeholder="例如：https://example.com/mcp"
                      />
                    </label>

                    <label className="field-label">
                      请求头 JSON
                      <textarea
                        className="field-textarea"
                        value={registerHeaders}
                        onChange={(event) => setRegisterHeaders(event.target.value)}
                        placeholder='{"Authorization": "Bearer <token>"}'
                      />
                    </label>
                  </>
                )}

                {registerFormError ? (
                  <div className="management-error-banner">{registerFormError}</div>
                ) : null}

                <div className="management-action-row">
                  <button
                    className="button button-primary"
                    type="submit"
                    disabled={registerMutation.isPending}
                  >
                    {registerMutation.isPending ? "注册中" : "注册服务器"}
                  </button>
                </div>
              </form>
            </section>
          </div>
        )}

        {selectedServerId && activeServer && typeof document !== "undefined"
          ? createPortal(
              <div className="management-modal-backdrop" role="presentation">
                <button
                  className="management-modal-dismiss"
                  type="button"
                  aria-label="关闭详情弹窗"
                  onClick={() => navigate("/mcp")}
                />
                <section
                  className="management-modal-card panel"
                  role="dialog"
                  aria-modal="true"
                  aria-label={`${activeServer.name} 详情`}
                >
                  <div className="management-modal-header">
                    <div className="management-detail-copy">
                      <h3 className="panel-title">{activeServer.name}</h3>
                    </div>

                    <div className="management-action-row">
                      <button
                        className="button button-secondary"
                        type="button"
                        disabled={healthMutation.isPending}
                        onClick={() => void healthMutation.mutateAsync(activeServer.id)}
                      >
                        {healthMutation.isPending ? "检测中" : "健康检查"}
                      </button>
                      <button
                        className="button button-secondary"
                        type="button"
                        disabled={refreshMutation.isPending || healthMutation.isPending}
                        onClick={() => void refreshMutation.mutateAsync(activeServer.id)}
                      >
                        {refreshMutation.isPending ? "刷新中" : "刷新能力"}
                      </button>
                      <button
                        className={
                          activeServer.enabled ? "button button-secondary" : "button button-primary"
                        }
                        type="button"
                        disabled={enableMutation.isPending || healthMutation.isPending}
                        onClick={() =>
                          void enableMutation.mutateAsync({
                            id: activeServer.id,
                            enabled: !activeServer.enabled,
                          })
                        }
                      >
                        {enableMutation.isPending
                          ? "提交中"
                          : activeServer.enabled
                            ? "禁用"
                            : "启用"}
                      </button>
                      <button
                        className="button button-secondary"
                        type="button"
                        onClick={() => navigate("/mcp")}
                      >
                        关闭
                      </button>
                    </div>
                  </div>

                  {serverDetailQuery.isLoading ? (
                    <div className="management-inline-notice">正在加载服务器详情。</div>
                  ) : null}
                  {serverDetailQuery.isError ? (
                    <div className="management-error-banner">{serverDetailQuery.error.message}</div>
                  ) : null}
                  {activeServer.last_error ? (
                    <div className="management-error-banner">{activeServer.last_error}</div>
                  ) : null}
                  {activeServer.health_error ? (
                    <div className="management-error-banner">{activeServer.health_error}</div>
                  ) : null}

                  <div className="management-info-grid">
                    <div className="management-info-card">
                      <span className="management-info-label">状态</span>
                      <strong
                        className={`management-status-badge ${getServerTone(activeServer.status)}`}
                      >
                        {activeServer.status}
                      </strong>
                    </div>
                    <div className="management-info-card">
                      <span className="management-info-label">启用状态</span>
                      <strong
                        className={`management-status-badge ${activeServer.enabled ? "tone-success" : "tone-neutral"}`}
                      >
                        {activeServer.enabled ? "已启用" : "已禁用"}
                      </strong>
                    </div>
                    <div className="management-info-card">
                      <span className="management-info-label">传输方式</span>
                      <strong className="management-info-value">{activeServer.transport}</strong>
                    </div>
                    <div className="management-info-card">
                      <span className="management-info-label">超时</span>
                      <strong className="management-info-value">
                        {activeServer.timeout_ms} ms
                      </strong>
                    </div>
                    <div className="management-info-card">
                      <span className="management-info-label">健康状态</span>
                      <strong
                        className={`management-status-badge ${getHealthTone(activeServer.health_status)}`}
                      >
                        {activeServer.health_status ?? "未检测"}
                      </strong>
                    </div>
                    <div className="management-info-card">
                      <span className="management-info-label">健康延迟</span>
                      <strong className="management-info-value">
                        {activeServer.health_latency_ms !== null
                          ? `${activeServer.health_latency_ms} ms`
                          : "未提供"}
                      </strong>
                    </div>
                    <div className="management-info-card">
                      <span className="management-info-label">来源</span>
                      <strong className="management-info-value">{activeServer.source}</strong>
                    </div>
                    <div className="management-info-card">
                      <span className="management-info-label">范围</span>
                      <strong className="management-info-value">{activeServer.scope}</strong>
                    </div>
                    <div className="management-info-card management-info-card-full">
                      <span className="management-info-label">配置路径</span>
                      <strong className="management-info-value management-info-code">
                        {activeServer.config_path}
                      </strong>
                    </div>
                    <div className="management-info-card management-info-card-full">
                      <span className="management-info-label">导入时间</span>
                      <strong className="management-info-value">
                        {formatDateTime(activeServer.imported_at)}
                      </strong>
                    </div>
                    <div className="management-info-card management-info-card-full">
                      <span className="management-info-label">最近健康检查</span>
                      <strong className="management-info-value">
                        {activeServer.health_checked_at
                          ? formatDateTime(activeServer.health_checked_at)
                          : "尚未执行"}
                      </strong>
                    </div>
                    {activeServer.command ? (
                      <div className="management-info-card management-info-card-full">
                        <span className="management-info-label">命令</span>
                        <strong className="management-info-value management-info-code">
                          {activeServer.command}
                        </strong>
                      </div>
                    ) : null}
                    {activeServer.url ? (
                      <div className="management-info-card management-info-card-full">
                        <span className="management-info-label">URL</span>
                        <strong className="management-info-value management-info-code">
                          {activeServer.url}
                        </strong>
                      </div>
                    ) : null}
                  </div>

                  {activeServer.args.length > 0 ? (
                    <div className="management-subcard">
                      <span className="management-info-label">启动参数</span>
                      <pre className="management-code-block">{activeServer.args.join("\n")}</pre>
                    </div>
                  ) : null}
                  {Object.keys(activeServer.env).length > 0 ? (
                    <div className="management-subcard">
                      <span className="management-info-label">环境变量</span>
                      <pre className="management-code-block">{stringifyJson(activeServer.env)}</pre>
                    </div>
                  ) : null}
                  {Object.keys(activeServer.headers).length > 0 ? (
                    <div className="management-subcard">
                      <span className="management-info-label">请求头</span>
                      <pre className="management-code-block">
                        {stringifyJson(activeServer.headers)}
                      </pre>
                    </div>
                  ) : null}

                  <section className="management-section-card management-section-card-compact">
                    <div className="management-section-header">
                      <h4 className="management-section-title">能力</h4>
                      <span className="management-status-badge tone-neutral">
                        {activeServer.capabilities.length} 项
                      </span>
                    </div>

                    {activeServer.capabilities.length === 0 ? (
                      <div className="management-inline-notice">
                        当前服务器还没有发现能力，刷新后再看一次。
                      </div>
                    ) : (
                      <div className="management-capability-groups">
                        {groupCapabilities(activeServer.capabilities).map(
                          ([groupName, capabilities]) => (
                            <div key={groupName} className="management-capability-group">
                              <div className="management-capability-group-header">
                                <h4>{groupName}</h4>
                                <span
                                  className={`management-status-badge ${getCapabilityTone(groupName)}`}
                                >
                                  {capabilities.length} 项
                                </span>
                              </div>

                              <ul className="management-capability-list">
                                {capabilities.map((capability) => {
                                  const capabilityKey = getCapabilityKey(capability);
                                  const isActive = capabilityKey === selectedCapabilityKey;

                                  return (
                                    <li key={capabilityKey}>
                                      <button
                                        className={`management-capability-card mcp-capability-button${isActive ? " mcp-capability-button-active" : ""}`}
                                        type="button"
                                        onClick={() => handleSelectCapability(capabilityKey)}
                                      >
                                        <div className="management-list-card-header">
                                          <strong className="management-list-title">
                                            {capability.title ?? capability.name}
                                          </strong>
                                          <span
                                            className={`management-status-badge ${getCapabilityTone(capability.kind)}`}
                                          >
                                            {capability.kind}
                                          </span>
                                        </div>
                                        <p className="management-list-copy">
                                          {capability.description ?? "暂无描述。"}
                                        </p>
                                      </button>
                                    </li>
                                  );
                                })}
                              </ul>
                            </div>
                          ),
                        )}
                      </div>
                    )}
                  </section>

                  {selectedCapability ? (
                    <section className="management-section-card management-section-card-compact">
                      <div className="management-section-header">
                        <h4 className="management-section-title">能力详情</h4>
                        <span
                          className={`management-status-badge ${getCapabilityTone(selectedCapability.kind)}`}
                        >
                          {selectedCapability.kind}
                        </span>
                      </div>

                      <div className="management-info-grid">
                        <div className="management-info-card">
                          <span className="management-info-label">名称</span>
                          <strong className="management-info-value">
                            {selectedCapability.name}
                          </strong>
                        </div>
                        <div className="management-info-card">
                          <span className="management-info-label">标题</span>
                          <strong className="management-info-value">
                            {selectedCapability.title ?? "未提供"}
                          </strong>
                        </div>
                        <div className="management-info-card management-info-card-full">
                          <span className="management-info-label">URI</span>
                          <strong className="management-info-value management-info-code">
                            {selectedCapability.uri ?? "未提供"}
                          </strong>
                        </div>
                      </div>

                      {selectedCapability.description ? (
                        <p className="management-unified-description">
                          {selectedCapability.description}
                        </p>
                      ) : null}
                      {Object.keys(selectedCapability.input_schema).length > 0 ? (
                        <div className="management-subcard">
                          <span className="management-info-label">输入 Schema</span>
                          <pre className="management-code-block">
                            {stringifyJson(selectedCapability.input_schema)}
                          </pre>
                        </div>
                      ) : null}
                      {Object.keys(selectedCapability.metadata).length > 0 ? (
                        <div className="management-subcard">
                          <span className="management-info-label">Metadata</span>
                          <pre className="management-code-block">
                            {stringifyJson(selectedCapability.metadata)}
                          </pre>
                        </div>
                      ) : null}
                      {Object.keys(selectedCapability.raw_payload).length > 0 ? (
                        <div className="management-subcard">
                          <span className="management-info-label">原始 Payload</span>
                          <pre className="management-code-block">
                            {stringifyJson(selectedCapability.raw_payload)}
                          </pre>
                        </div>
                      ) : null}

                      {selectedCapability.kind === "tool" ? (
                        <div className="management-subcard">
                          <div className="management-section-header">
                            <h4 className="management-section-title">调用工具</h4>
                          </div>

                          <label className="field-label">
                            参数 JSON
                            <textarea
                              className="field-textarea"
                              value={toolArgumentsText}
                              onChange={(event) => setToolArgumentsText(event.target.value)}
                              placeholder='{"path": "README.md"}'
                            />
                          </label>

                          {toolArgumentsError ? (
                            <div className="management-error-banner">{toolArgumentsError}</div>
                          ) : null}

                          <div className="management-action-row">
                            <button
                              className="button button-primary"
                              type="button"
                              disabled={invokeMutation.isPending}
                              onClick={() => {
                                if (!activeServer) {
                                  return;
                                }

                                setToolArgumentsError(null);
                                void invokeMutation.mutateAsync({
                                  server: activeServer,
                                  capability: selectedCapability,
                                });
                              }}
                            >
                              {invokeMutation.isPending ? "调用中" : "执行工具"}
                            </button>
                          </div>

                          {invokeResult ? (
                            <div className="management-subcard">
                              <span className="management-info-label">调用结果</span>
                              <pre className="management-code-block">
                                {stringifyJson(invokeResult.result)}
                              </pre>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </section>
                  ) : null}
                </section>
              </div>,
              document.body,
            )
          : null}
      </section>
    </main>
  );
}
