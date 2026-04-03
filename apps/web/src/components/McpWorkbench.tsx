import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  checkMcpServerHealth,
  deleteMcpServer,
  getMcpServer,
  importMcpServers,
  invokeMcpTool,
  listMcpServers,
  refreshMcpServer,
  registerManualMcpServer,
  setMcpServerEnabled,
} from "../lib/api";
import type {
  MCPCapability,
  MCPCapabilityKind,
  MCPServer,
  MCPToolInvokeResponse,
  MCPTransport,
} from "../types/mcp";

const MCP_SERVERS_QUERY_KEY = ["mcp", "servers"] as const;
const EMPTY_MCP_SERVERS: MCPServer[] = [];
const MCP_IMPORT_MISSING_ERROR = "Server not found in the latest MCP import.";
const MCP_STALE_SERVER_COPY = "该服务器未在最近一次导入配置中出现，当前仅保留历史能力快照。";

const MCP_VIEW_OPTIONS = [
  { key: "servers", label: "服务器", emptyTitle: "没有匹配的服务器" },
  { key: "tool", label: "工具", emptyTitle: "没有匹配的工具" },
  { key: "resource", label: "资源", emptyTitle: "没有匹配的资源" },
  { key: "prompt", label: "Prompts", emptyTitle: "没有匹配的 Prompts" },
] as const;

type McpViewKey = (typeof MCP_VIEW_OPTIONS)[number]["key"];

type CapabilityCounts = {
  tool: number;
  resource: number;
  resource_template: number;
  prompt: number;
};

type FlattenedCapability = {
  server: MCPServer;
  capability: MCPCapability;
  capabilityKey: string;
  searchIndex: string;
};

function buildServerSearchIndex(server: MCPServer): string {
  const capabilityContent = server.capabilities
    .flatMap((capability) => [
      capability.kind,
      capability.name,
      capability.title,
      capability.description,
      capability.uri,
    ])
    .join(" ");

  return [
    server.name,
    server.status,
    server.transport,
    server.config_path,
    server.command,
    server.url,
    server.source,
    server.scope,
    capabilityContent,
  ]
    .join(" ")
    .toLowerCase();
}

function buildCapabilitySearchIndex(server: MCPServer, capability: MCPCapability): string {
  return [
    server.name,
    server.status,
    server.transport,
    server.config_path,
    server.source,
    server.scope,
    capability.kind,
    capability.name,
    capability.title,
    capability.description,
    capability.uri,
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

function getCapabilityKey(capability: MCPCapability): string {
  return `${capability.kind}:${capability.name}`;
}

function isStaleImportedServer(server: MCPServer): boolean {
  return (
    !server.config_path.startsWith("manual://") &&
    server.enabled === false &&
    server.last_error === MCP_IMPORT_MISSING_ERROR
  );
}

function isImportMissingMessage(message: string | null): boolean {
  return message === MCP_IMPORT_MISSING_ERROR;
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

function removeServerFromList(
  currentValue: MCPServer[] | undefined,
  serverId: string,
): MCPServer[] {
  if (!currentValue) {
    return [];
  }

  return currentValue.filter((server) => server.id !== serverId);
}

function getCapabilityCounts(capabilities: MCPCapability[]): CapabilityCounts {
  return capabilities.reduce<CapabilityCounts>(
    (counts, capability) => {
      if (capability.kind === "tool") {
        counts.tool += 1;
      }
      if (capability.kind === "resource") {
        counts.resource += 1;
      }
      if (capability.kind === "resource_template") {
        counts.resource_template += 1;
      }
      if (capability.kind === "prompt") {
        counts.prompt += 1;
      }

      return counts;
    },
    {
      tool: 0,
      resource: 0,
      resource_template: 0,
      prompt: 0,
    },
  );
}

function getPreferredCapabilities(capabilities: MCPCapability[]): MCPCapability[] {
  const tools = capabilities.filter((capability) => capability.kind === "tool");
  return tools.length > 0 ? tools : capabilities;
}

function flattenCapabilities(servers: MCPServer[]): FlattenedCapability[] {
  return servers.flatMap((server) =>
    server.capabilities.map((capability) => ({
      server,
      capability,
      capabilityKey: getCapabilityKey(capability),
      searchIndex: buildCapabilitySearchIndex(server, capability),
    })),
  );
}

function getPrimaryServerValue(server: MCPServer): string {
  return server.transport === "http"
    ? (server.url ?? "未配置 URL")
    : (server.command ?? "未配置命令");
}

function getFlatViewTitle(view: McpViewKey): string {
  switch (view) {
    case "servers":
      return "服务器总览";
    case "tool":
      return "全部工具";
    case "resource":
      return "全部资源";
    case "prompt":
      return "全部 Prompts";
  }
}

function matchesFlatView(view: McpViewKey, kind: MCPCapabilityKind): boolean {
  if (view === "tool") {
    return kind === "tool";
  }

  if (view === "resource") {
    return kind === "resource" || kind === "resource_template";
  }

  if (view === "prompt") {
    return kind === "prompt";
  }

  return false;
}

export function McpWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { serverId } = useParams<{ serverId?: string }>();
  const [searchValue, setSearchValue] = useState("");
  const [activeView, setActiveView] = useState<McpViewKey>("servers");
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
  const [routeSelectedCapabilityKey, setRouteSelectedCapabilityKey] = useState<string | null>(null);
  const [toolArgumentsText, setToolArgumentsText] = useState("{}");
  const [toolArgumentsError, setToolArgumentsError] = useState<string | null>(null);
  const [invokeResult, setInvokeResult] = useState<MCPToolInvokeResponse | null>(null);

  const serversQuery = useQuery({
    queryKey: MCP_SERVERS_QUERY_KEY,
    queryFn: ({ signal }) => listMcpServers(signal),
  });

  const allServers = serversQuery.data ?? EMPTY_MCP_SERVERS;

  const filteredServers = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();

    if (!keyword) {
      return allServers;
    }

    return allServers.filter((server) => buildServerSearchIndex(server).includes(keyword));
  }, [allServers, searchValue]);

  const flattenedCapabilities = useMemo(() => flattenCapabilities(allServers), [allServers]);

  const filteredTools = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();

    return flattenedCapabilities.filter(
      (entry) =>
        matchesFlatView("tool", entry.capability.kind) &&
        (!keyword || entry.searchIndex.includes(keyword)),
    );
  }, [flattenedCapabilities, searchValue]);

  const filteredResources = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();

    return flattenedCapabilities.filter(
      (entry) =>
        matchesFlatView("resource", entry.capability.kind) &&
        (!keyword || entry.searchIndex.includes(keyword)),
    );
  }, [flattenedCapabilities, searchValue]);

  const filteredPrompts = useMemo(() => {
    const keyword = searchValue.trim().toLowerCase();

    return flattenedCapabilities.filter(
      (entry) =>
        matchesFlatView("prompt", entry.capability.kind) &&
        (!keyword || entry.searchIndex.includes(keyword)),
    );
  }, [flattenedCapabilities, searchValue]);

  const selectedServerId = useMemo(() => {
    if (serverId && allServers.some((server) => server.id === serverId)) {
      return serverId;
    }

    return null;
  }, [allServers, serverId]);

  const activeServerSummary = useMemo(
    () => allServers.find((server) => server.id === selectedServerId) ?? null,
    [allServers, selectedServerId],
  );

  const serverDetailQuery = useQuery({
    enabled: Boolean(selectedServerId),
    queryKey: ["mcp", "server", selectedServerId],
    queryFn: ({ signal }) => getMcpServer(selectedServerId!, signal),
  });

  const activeServer = serverDetailQuery.data ?? activeServerSummary;
  const activeServerIsStale = activeServer ? isStaleImportedServer(activeServer) : false;
  const preferredCapabilities = useMemo(
    () => (activeServer ? getPreferredCapabilities(activeServer.capabilities) : []),
    [activeServer],
  );

  const selectedCapability = useMemo(
    () =>
      activeServer?.capabilities.find(
        (capability) => getCapabilityKey(capability) === selectedCapabilityKey,
      ) ?? null,
    [activeServer, selectedCapabilityKey],
  );

  const currentViewCount =
    activeView === "servers"
      ? filteredServers.length
      : activeView === "tool"
        ? filteredTools.length
        : activeView === "resource"
          ? filteredResources.length
          : filteredPrompts.length;

  const currentFlatItems =
    activeView === "tool"
      ? filteredTools
      : activeView === "resource"
        ? filteredResources
        : filteredPrompts;

  function handleSelectCapability(capabilityKey: string | null): void {
    setSelectedCapabilityKey(capabilityKey);
    setToolArgumentsError(null);
    setInvokeResult(null);
    setToolArgumentsText("{}");
  }

  function handleOpenCapability(server: MCPServer, capabilityKey: string): void {
    setRouteSelectedCapabilityKey(capabilityKey);

    if (server.id === selectedServerId) {
      handleSelectCapability(capabilityKey);
    }

    navigate(`/mcp/${server.id}`);
  }

  useEffect(() => {
    if (!activeServer) {
      setSelectedCapabilityKey(null);
      setRouteSelectedCapabilityKey(null);
      setToolArgumentsError(null);
      setInvokeResult(null);
      setToolArgumentsText("{}");
      return;
    }

    if (routeSelectedCapabilityKey) {
      const requestedCapability = activeServer.capabilities.find(
        (capability) => getCapabilityKey(capability) === routeSelectedCapabilityKey,
      );

      setRouteSelectedCapabilityKey(null);

      if (requestedCapability) {
        setSelectedCapabilityKey(getCapabilityKey(requestedCapability));
        setToolArgumentsError(null);
        setInvokeResult(null);
        setToolArgumentsText("{}");
        return;
      }
    }

    const nextCapability = getPreferredCapabilities(activeServer.capabilities)[0] ?? null;
    const currentCapabilityExists = activeServer.capabilities.some(
      (capability) => getCapabilityKey(capability) === selectedCapabilityKey,
    );

    if (!currentCapabilityExists) {
      setSelectedCapabilityKey(nextCapability ? getCapabilityKey(nextCapability) : null);
      setToolArgumentsError(null);
      setInvokeResult(null);
      setToolArgumentsText("{}");
    }
  }, [activeServer, routeSelectedCapabilityKey, selectedCapabilityKey]);

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

  const deleteMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) => {
      const confirmed = window.confirm(`确认删除 MCP 服务器“${name}”吗？`);
      if (!confirmed) {
        return false;
      }

      await deleteMcpServer(id);
      return true;
    },
    onSuccess: async (deleted, variables) => {
      if (!deleted) {
        return;
      }

      queryClient.setQueryData<MCPServer[] | undefined>(MCP_SERVERS_QUERY_KEY, (currentValue) =>
        removeServerFromList(currentValue, variables.id),
      );
      queryClient.removeQueries({ queryKey: ["mcp", "server", variables.id] });

      if (selectedServerId === variables.id) {
        navigate("/mcp", { replace: true });
      }

      await queryClient.invalidateQueries({ queryKey: ["mcp", "server"] });
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

  const mutationErrorMessage = enableMutation.isError
    ? enableMutation.error.message
    : refreshMutation.isError
      ? refreshMutation.error.message
      : healthMutation.isError
        ? healthMutation.error.message
        : deleteMutation.isError
          ? deleteMutation.error.message
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
              同时保留服务器视角与能力平铺视角，便于快速查找工具、资源与 Prompts。
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
            placeholder="搜索服务器、工具、标题、描述、传输方式或配置路径"
          />

          <span className="management-status-badge tone-neutral">{currentViewCount} 项</span>
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
                <h3 className="management-section-title">视图</h3>
                <span className="management-status-badge tone-neutral">{currentViewCount} 项</span>
              </div>

              <div className="mcp-view-tabs" role="tablist" aria-label="MCP 视图切换">
                {MCP_VIEW_OPTIONS.map((view) => {
                  const count =
                    view.key === "servers"
                      ? filteredServers.length
                      : view.key === "tool"
                        ? filteredTools.length
                        : view.key === "resource"
                          ? filteredResources.length
                          : filteredPrompts.length;

                  return (
                    <button
                      key={view.key}
                      className={`mcp-view-tab${activeView === view.key ? " mcp-view-tab-active" : ""}`}
                      type="button"
                      role="tab"
                      aria-selected={activeView === view.key}
                      onClick={() => setActiveView(view.key)}
                    >
                      <span>{view.label}</span>
                      <span className="management-status-badge tone-neutral">{count}</span>
                    </button>
                  );
                })}
              </div>
            </section>

            <section className="management-section-card management-section-card-compact">
              <div className="management-section-header">
                <h3 className="management-section-title">{getFlatViewTitle(activeView)}</h3>
                <span className="management-status-badge tone-neutral">{currentViewCount} 项</span>
              </div>

              <div className="management-list-shell">
                {activeView === "servers" ? (
                  filteredServers.length === 0 ? (
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
                        const counts = getCapabilityCounts(server.capabilities);
                        const isStale = isStaleImportedServer(server);

                        return (
                          <li key={server.id}>
                            <article
                              className={`management-list-card mcp-server-card${isActive ? " management-list-card-active" : ""}`}
                            >
                              <div className="mcp-card-row">
                                <div className="management-detail-copy">
                                  <strong className="management-list-title mcp-card-title">
                                    {server.name}
                                  </strong>
                                  <p className="management-list-subtitle">
                                    {getPrimaryServerValue(server)}
                                  </p>
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

                              <p className="management-list-copy mcp-server-meta-copy">
                                配置路径：{server.config_path}
                              </p>

                              <div className="mcp-server-count-row">
                                <span className="management-status-badge tone-success">
                                  工具 {counts.tool}
                                </span>
                                <span className="management-status-badge tone-neutral">
                                  资源 {counts.resource}
                                </span>
                                <span className="management-status-badge tone-neutral">
                                  模板 {counts.resource_template}
                                </span>
                                <span className="management-status-badge tone-warning">
                                  Prompts {counts.prompt}
                                </span>
                              </div>

                              <div className="action-row">
                                {isStale ? (
                                  <span className="management-status-badge tone-warning">
                                    导入缺失
                                  </span>
                                ) : null}
                                <span
                                  className={`management-status-badge ${server.enabled ? "tone-success" : "tone-neutral"}`}
                                >
                                  {server.enabled ? "已启用" : "已禁用"}
                                </span>
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
                                <span className="management-status-badge tone-neutral">
                                  {server.source}/{server.scope}
                                </span>
                                <button
                                  className="text-button"
                                  type="button"
                                  onClick={() => navigate(`/mcp/${server.id}`)}
                                >
                                  查看详情
                                </button>
                                <button
                                  className="text-button"
                                  type="button"
                                  disabled={deleteMutation.isPending}
                                  onClick={() =>
                                    deleteMutation.mutate({
                                      id: server.id,
                                      name: server.name,
                                    })
                                  }
                                >
                                  {deleteMutation.isPending ? "删除中" : "删除"}
                                </button>
                              </div>
                            </article>
                          </li>
                        );
                      })}
                    </ul>
                  )
                ) : currentFlatItems.length === 0 ? (
                  <div className="management-empty-state">
                    <p className="management-empty-title">
                      {MCP_VIEW_OPTIONS.find((view) => view.key === activeView)?.emptyTitle ??
                        "没有匹配项"}
                    </p>
                    <p className="management-empty-copy">
                      可以调整关键词，或切回服务器视图查看完整连接信息。
                    </p>
                  </div>
                ) : (
                  <ul className="management-card-grid">
                    {currentFlatItems.map((entry) => (
                      <li key={`${entry.server.id}:${entry.capabilityKey}`}>
                        <article className="management-list-card mcp-flat-card">
                          <strong className="management-list-title">{entry.capability.name}</strong>
                          <p className="management-list-subtitle">{entry.server.name}</p>

                          <div className="action-row">
                            {isStaleImportedServer(entry.server) ? (
                              <span className="management-status-badge tone-warning">导入缺失</span>
                            ) : null}
                            <button
                              className="text-button"
                              type="button"
                              onClick={() =>
                                handleOpenCapability(entry.server, entry.capabilityKey)
                              }
                            >
                              {entry.capability.kind === "tool" ? "查看并调用" : "查看详情"}
                            </button>
                          </div>
                        </article>
                      </li>
                    ))}
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
                        disabled={deleteMutation.isPending}
                        onClick={() =>
                          deleteMutation.mutate({
                            id: activeServer.id,
                            name: activeServer.name,
                          })
                        }
                      >
                        {deleteMutation.isPending ? "删除中" : "删除"}
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

                  {serverDetailQuery.isError ? (
                    <div className="management-error-banner">{serverDetailQuery.error.message}</div>
                  ) : null}
                  {activeServerIsStale ? (
                    <div className="management-inline-notice">{MCP_STALE_SERVER_COPY}</div>
                  ) : null}
                  {activeServer.last_error && !isImportMissingMessage(activeServer.last_error) ? (
                    <div className="management-error-banner">{activeServer.last_error}</div>
                  ) : null}
                  {activeServer.health_error &&
                  !isImportMissingMessage(activeServer.health_error) ? (
                    <div className="management-error-banner">{activeServer.health_error}</div>
                  ) : null}

                  <section className="management-section-card management-section-card-compact">
                    <div className="management-section-header">
                      <h4 className="management-section-title">
                        {preferredCapabilities.some((capability) => capability.kind === "tool")
                          ? "工具"
                          : "能力"}
                      </h4>
                      <span className="management-status-badge tone-neutral">
                        {preferredCapabilities.length} 项
                      </span>
                    </div>

                    {preferredCapabilities.length === 0 ? (
                      <div className="management-inline-notice">
                        当前服务器还没有发现能力，刷新后再看一次。
                      </div>
                    ) : (
                      <ul className="management-capability-list">
                        {preferredCapabilities.map((capability) => {
                          const capabilityKey = getCapabilityKey(capability);
                          const isActive = capabilityKey === selectedCapabilityKey;

                          return (
                            <li key={capabilityKey}>
                              <button
                                className={`management-capability-card mcp-capability-button${isActive ? " mcp-capability-button-active" : ""}`}
                                type="button"
                                onClick={() => handleSelectCapability(capabilityKey)}
                              >
                                <strong className="management-list-title">{capability.name}</strong>
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </section>

                  {selectedCapability?.kind === "tool" ? (
                    <section className="management-section-card management-section-card-compact">
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
