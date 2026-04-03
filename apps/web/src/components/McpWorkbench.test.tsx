import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MCPCapability, MCPServer } from "../types/mcp";
import { McpWorkbench } from "./McpWorkbench";

const IMPORT_MISSING_ERROR = "Server not found in the latest MCP import.";
const STALE_SERVER_COPY = "该服务器未在最近一次导入配置中出现，当前仅保留历史能力快照。";

const {
  mockCheckMcpServerHealth,
  mockDeleteMcpServer,
  mockGetMcpServer,
  mockImportMcpServers,
  mockInvokeMcpTool,
  mockListMcpServers,
  mockRefreshMcpServer,
  mockRegisterManualMcpServer,
  mockSetMcpServerEnabled,
} = vi.hoisted(() => ({
  mockCheckMcpServerHealth: vi.fn(),
  mockDeleteMcpServer: vi.fn(),
  mockGetMcpServer: vi.fn(),
  mockImportMcpServers: vi.fn(),
  mockInvokeMcpTool: vi.fn(),
  mockListMcpServers: vi.fn(),
  mockRefreshMcpServer: vi.fn(),
  mockRegisterManualMcpServer: vi.fn(),
  mockSetMcpServerEnabled: vi.fn(),
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");

  return {
    ...actual,
    checkMcpServerHealth: mockCheckMcpServerHealth,
    deleteMcpServer: mockDeleteMcpServer,
    getMcpServer: mockGetMcpServer,
    importMcpServers: mockImportMcpServers,
    invokeMcpTool: mockInvokeMcpTool,
    listMcpServers: mockListMcpServers,
    refreshMcpServer: mockRefreshMcpServer,
    registerManualMcpServer: mockRegisterManualMcpServer,
    setMcpServerEnabled: mockSetMcpServerEnabled,
  };
});

function createCapability(
  kind: MCPCapability["kind"],
  name: string,
  overrides: Partial<MCPCapability> = {},
): MCPCapability {
  return {
    kind,
    name,
    title: overrides.title ?? `${name} 标题`,
    description: overrides.description ?? `${name} 描述`,
    uri: overrides.uri ?? null,
    metadata: overrides.metadata ?? {},
    input_schema: overrides.input_schema ?? {},
    raw_payload: overrides.raw_payload ?? {},
  };
}

function createServer(overrides: Partial<MCPServer> & Pick<MCPServer, "id" | "name">): MCPServer {
  return {
    id: overrides.id,
    name: overrides.name,
    source: overrides.source ?? "local",
    scope: overrides.scope ?? "project",
    transport: overrides.transport ?? "stdio",
    enabled: overrides.enabled ?? true,
    command: overrides.command ?? "npx mock-mcp-server",
    args: overrides.args ?? [],
    env: overrides.env ?? {},
    url: overrides.url ?? null,
    headers: overrides.headers ?? {},
    timeout_ms: overrides.timeout_ms ?? 5000,
    status: overrides.status ?? "connected",
    last_error: overrides.last_error ?? null,
    health_status: overrides.health_status ?? "ok",
    health_latency_ms: overrides.health_latency_ms ?? 42,
    health_error: overrides.health_error ?? null,
    health_checked_at: overrides.health_checked_at ?? "2026-04-02T10:00:00.000Z",
    config_path: overrides.config_path ?? `D:/mcp/${overrides.id}.json`,
    imported_at: overrides.imported_at ?? "2026-04-02T09:00:00.000Z",
    capabilities: overrides.capabilities ?? [],
  };
}

const alphaServer = createServer({
  id: "server-alpha",
  name: "alpha-server",
  transport: "stdio",
  command: "npx alpha-mcp",
  config_path: "D:/configs/alpha.json",
  enabled: true,
  status: "connected",
  health_status: "ok",
  capabilities: [
    createCapability("tool", "scan_hosts", {
      title: "主机扫描",
      description: "扫描目标主机",
      input_schema: { type: "object", properties: { host: { type: "string" } } },
    }),
    createCapability("tool", "collect_logs", {
      title: "日志采集",
      description: "收集审计日志",
    }),
    createCapability("resource", "asset_inventory", {
      title: "资产库存",
      description: "读取资产资源",
      uri: "resource://asset_inventory",
    }),
    createCapability("prompt", "triage_prompt", {
      title: "研判 Prompt",
      description: "辅助研判",
    }),
  ],
});

const betaServer = createServer({
  id: "server-beta",
  name: "beta-server",
  transport: "http",
  command: null,
  url: "https://beta.example/mcp",
  config_path: "D:/configs/second-config.json",
  enabled: false,
  status: "error",
  last_error: IMPORT_MISSING_ERROR,
  health_status: "degraded",
  health_error: IMPORT_MISSING_ERROR,
  source: "opencode",
  scope: "user",
  capabilities: [
    createCapability("tool", "deploy_report", {
      title: "报告下发",
      description: "发送报告到目标系统",
      input_schema: { type: "object" },
    }),
    createCapability("resource_template", "report_template", {
      title: "报告模板",
      description: "渲染资源模板",
      uri: "template://report",
    }),
  ],
});

const servers = [alphaServer, betaServer];

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location-display">{location.pathname}</div>;
}

function renderWorkbench(initialPath = "/mcp") {
  const queryClient = createQueryClient();

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route
            path="/mcp"
            element={
              <>
                <LocationDisplay />
                <McpWorkbench />
              </>
            }
          />
          <Route
            path="/mcp/:serverId"
            element={
              <>
                <LocationDisplay />
                <McpWorkbench />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("McpWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mockListMcpServers.mockResolvedValue(servers);
    mockGetMcpServer.mockImplementation(async (serverId: string) => {
      const server = servers.find((item) => item.id === serverId);
      if (!server) {
        throw new Error("missing server");
      }
      return server;
    });
    mockImportMcpServers.mockResolvedValue(servers);
    mockRegisterManualMcpServer.mockResolvedValue(alphaServer);
    mockSetMcpServerEnabled.mockResolvedValue(alphaServer);
    mockRefreshMcpServer.mockResolvedValue(alphaServer);
    mockCheckMcpServerHealth.mockResolvedValue(alphaServer);
    mockDeleteMcpServer.mockResolvedValue(undefined);
    mockInvokeMcpTool.mockResolvedValue({
      server_id: alphaServer.id,
      tool_name: "scan_hosts",
      result: { ok: true },
    });
  });

  it("renders richer server cards with per-kind counts and top summary totals", async () => {
    renderWorkbench();

    await screen.findByText("服务器总览");

    expect(screen.getByText("已启用 1 · 已连接 1")).toBeInTheDocument();
    expect(screen.getAllByText("模板 1").length).toBeGreaterThan(0);

    const alphaCard = screen.getByText("alpha-server").closest("article");
    expect(alphaCard).not.toBeNull();

    const alphaWithin = within(alphaCard!);
    expect(alphaWithin.getByText("工具 2")).toBeInTheDocument();
    expect(alphaWithin.getByText("资源 1")).toBeInTheDocument();
    expect(alphaWithin.getByText("Prompts 1")).toBeInTheDocument();
    expect(alphaWithin.getByText("健康 ok")).toBeInTheDocument();

    const betaCard = screen.getByText("beta-server").closest("article");
    expect(betaCard).not.toBeNull();

    expect(within(betaCard!).getByText("导入缺失")).toBeInTheDocument();
  });

  it("shows a flattened all-tools view and filters by server fields", async () => {
    const user = userEvent.setup();

    renderWorkbench();
    await screen.findByText("服务器总览");

    await user.click(screen.getByRole("tab", { name: /工具/i }));

    expect(screen.getByText("全部工具")).toBeInTheDocument();
    expect(screen.getByText("scan_hosts")).toBeInTheDocument();
    expect(screen.getByText("collect_logs")).toBeInTheDocument();
    expect(screen.getByText("deploy_report")).toBeInTheDocument();
    expect(screen.getByText("beta-server")).toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox"));
    await user.type(screen.getByRole("searchbox"), "second-config.json");

    await waitFor(() => {
      expect(screen.queryByText("scan_hosts")).not.toBeInTheDocument();
    });

    expect(screen.getByText("deploy_report")).toBeInTheDocument();
    expect(screen.getByText("beta-server")).toBeInTheDocument();
    expect(screen.getByText("D:/configs/second-config.json")).toBeInTheDocument();
  });

  it("jumps from the flat tool list into the existing route-driven detail modal", async () => {
    const user = userEvent.setup();

    renderWorkbench();
    await screen.findByText("服务器总览");

    await user.click(screen.getByRole("tab", { name: /工具/i }));

    const deployCard = screen.getByText("deploy_report").closest("article");
    expect(deployCard).not.toBeNull();

    await user.click(within(deployCard!).getByRole("button", { name: "查看并调用" }));

    await waitFor(() => {
      expect(screen.getByTestId("location-display").textContent).toBe("/mcp/server-beta");
    });

    const dialog = await screen.findByRole("dialog", { name: "beta-server 详情" });

    expect(within(dialog).getByText("导入缺失")).toBeInTheDocument();
    expect(within(dialog).getByText(STALE_SERVER_COPY)).toBeInTheDocument();
    expect(within(dialog).queryByText(IMPORT_MISSING_ERROR)).not.toBeInTheDocument();
    expect(within(dialog).getByText("deploy_report")).toBeInTheDocument();
    expect(within(dialog).getByText("报告模板")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "执行工具" })).toBeInTheDocument();
  });

  it("shows delete buttons and removes a server from the list after delete", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWorkbench();
    await screen.findByText("服务器总览");

    const alphaCard = screen.getByText("alpha-server").closest("article");
    expect(alphaCard).not.toBeNull();

    await user.click(within(alphaCard!).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(screen.queryByText("alpha-server")).not.toBeInTheDocument();
    });

    expect(mockDeleteMcpServer).toHaveBeenCalledWith("server-alpha");
    expect(confirmSpy).toHaveBeenCalledWith("确认删除 MCP 服务器“alpha-server”吗？");
    confirmSpy.mockRestore();
  });

  it("deletes the active server from detail view and closes the modal route", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWorkbench("/mcp/server-alpha");

    const dialog = await screen.findByRole("dialog", { name: "alpha-server 详情" });
    await user.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(screen.getByTestId("location-display").textContent).toBe("/mcp");
    });

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "alpha-server 详情" })).not.toBeInTheDocument();
    });

    confirmSpy.mockRestore();
  });

  it("shows an error banner when delete fails", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    mockDeleteMcpServer.mockRejectedValueOnce(new Error("delete boom"));

    renderWorkbench();
    await screen.findByText("服务器总览");

    const betaCard = screen.getByText("beta-server").closest("article");
    expect(betaCard).not.toBeNull();

    await user.click(within(betaCard!).getByRole("button", { name: "删除" }));

    expect(await screen.findByText("delete boom")).toBeInTheDocument();
    expect(screen.getByText("beta-server")).toBeInTheDocument();

    confirmSpy.mockRestore();
  });
});
