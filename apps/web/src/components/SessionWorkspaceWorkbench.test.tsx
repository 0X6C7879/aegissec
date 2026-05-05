import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { useUiStore } from "../store/uiStore";
import type { SessionGraph } from "../types/graphs";
import type {
  ChatGeneration,
  SessionConversation,
  SessionContextWindowUsage,
  SessionQueue,
  SessionSummary,
} from "../types/sessions";
import type { SlashCatalogItem } from "../types/slash";
import { SessionWorkspaceWorkbench } from "./SessionWorkspaceWorkbench";

const {
  mockGetAttackGraph,
  mockCancelGeneration,
  mockCancelSession,
  mockCompactSessionContext,
  mockCreateSession,
  mockDeleteSession,
  mockEditSessionMessage,
  mockGetRuntimeStatus,
  mockGetSessionConversation,
  mockGetSessionContextWindowUsage,
  mockGetSessionQueue,
  mockGetSessionSlashCatalog,
  mockInjectActiveGenerationContext,
  mockForkSessionMessage,
  mockListSessions,
  mockRegenerateSessionMessage,
  mockRollbackSessionMessage,
  mockUpdateSession,
  mockSendChatMessage,
  mockUseSessionEvents,
} = vi.hoisted(() => ({
  mockGetAttackGraph: vi.fn(),
  mockCancelGeneration: vi.fn(),
  mockCancelSession: vi.fn(),
  mockCompactSessionContext: vi.fn(),
  mockCreateSession: vi.fn(),
  mockDeleteSession: vi.fn(),
  mockEditSessionMessage: vi.fn(),
  mockGetRuntimeStatus: vi.fn(),
  mockGetSessionConversation: vi.fn(),
  mockGetSessionContextWindowUsage: vi.fn(),
  mockGetSessionQueue: vi.fn(),
  mockGetSessionSlashCatalog: vi.fn(),
  mockInjectActiveGenerationContext: vi.fn(),
  mockForkSessionMessage: vi.fn(),
  mockListSessions: vi.fn(),
  mockRegenerateSessionMessage: vi.fn(),
  mockRollbackSessionMessage: vi.fn(),
  mockUpdateSession: vi.fn(),
  mockSendChatMessage: vi.fn(),
  mockUseSessionEvents: vi.fn(),
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");

  return {
    ...actual,
    getAttackGraph: mockGetAttackGraph,
    cancelGeneration: mockCancelGeneration,
    cancelSession: mockCancelSession,
    compactSessionContext: mockCompactSessionContext,
    createSession: mockCreateSession,
    deleteSession: mockDeleteSession,
    editSessionMessage: mockEditSessionMessage,
    getRuntimeStatus: mockGetRuntimeStatus,
    getSessionConversation: mockGetSessionConversation,
    getSessionContextWindowUsage: mockGetSessionContextWindowUsage,
    getSessionQueue: mockGetSessionQueue,
    getSessionSlashCatalog: mockGetSessionSlashCatalog,
    injectActiveGenerationContext: mockInjectActiveGenerationContext,
    forkSessionMessage: mockForkSessionMessage,
    listSessions: mockListSessions,
    regenerateSessionMessage: mockRegenerateSessionMessage,
    rollbackSessionMessage: mockRollbackSessionMessage,
    updateSession: mockUpdateSession,
    sendChatMessage: mockSendChatMessage,
  };
});

vi.mock("../hooks/useSessionEvents", () => ({
  useSessionEvents: mockUseSessionEvents,
}));

vi.mock("./ConversationSidebar", () => ({
  ConversationSidebar: ({
    sessions,
    onDelete,
  }: {
    sessions: SessionSummary[];
    onDelete: (sessionId: string) => Promise<void>;
  }) => (
    <div data-testid="conversation-sidebar">
      {sessions.map((session) => (
        <button
          key={session.id}
          type="button"
          onClick={() => {
            void onDelete(session.id);
          }}
        >
          delete-{session.id}
        </button>
      ))}
    </div>
  ),
}));

vi.mock("./ConversationFeed", () => ({
  ConversationFeed: ({
    onFocusShell,
  }: {
    onFocusShell?: (payload: {
      terminalId: string | null;
      command: string;
      toolCallId: string | null;
    }) => void;
  }) => (
    <div data-testid="conversation-feed">
      <button
        type="button"
        onClick={() =>
          onFocusShell?.({
            terminalId: "term-focus-1",
            command: "whoami",
            toolCallId: "tool-focus-1",
          })
        }
      >
        mock-focus-shell
      </button>
    </div>
  ),
}));

vi.mock("./WorkbenchComposer", () => ({
  WorkbenchComposer: ({
    isActiveGeneration,
    isPausedGeneration,
    queuedCount,
    slashCatalog,
    contextUsage,
    contextCompacting,
    onQueueSend,
    onInject,
    onManualCompact,
  }: {
    isActiveGeneration: boolean;
    isPausedGeneration: boolean;
    queuedCount: number;
    slashCatalog: Array<{ trigger: string; source: string }>;
    contextUsage: { used_tokens: number; context_window_tokens: number } | null;
    contextCompacting: boolean;
    onQueueSend: (payload: { content: string; slashAction?: unknown | null }) => Promise<void>;
    onInject: (content: string) => Promise<void>;
    onManualCompact?: () => Promise<void>;
  }) => (
    <div data-testid="workbench-composer">
      <span data-testid="composer-active-state">{String(isActiveGeneration)}</span>
      <span data-testid="composer-paused-state">{String(isPausedGeneration)}</span>
      <span data-testid="composer-queued-count">{queuedCount}</span>
      <span data-testid="composer-slash-catalog">
        {slashCatalog.map((item) => `${item.source}:${item.trigger}`).join("|")}
      </span>
      <span data-testid="composer-context-usage">
        {contextUsage ? `${contextUsage.used_tokens}/${contextUsage.context_window_tokens}` : "none"}
      </span>
      <span data-testid="composer-context-compacting">{String(contextCompacting)}</span>
      <button type="button" onClick={() => void onQueueSend({ content: "排队消息" })}>
        mock-queue-send
      </button>
      <button type="button" onClick={() => void onInject("注入消息")}>
        mock-inject-send
      </button>
      <button type="button" onClick={() => void onManualCompact?.()}>
        mock-manual-compact
      </button>
    </div>
  ),
}));

vi.mock("./runtime/ShellWorkbench", () => ({
  ShellWorkbench: ({
    variant,
    focusRequest,
  }: {
    variant?: string;
    focusRequest?: {
      terminalId: string | null;
      command: string;
      toolCallId: string | null;
    } | null;
  }) => (
    <div data-testid="shell-workbench" data-variant={variant ?? "default"}>
      <span data-testid="shell-workbench-focus-terminal">{focusRequest?.terminalId ?? "none"}</span>
    </div>
  ),
}));

vi.mock("./AttackGraphCanvas", () => ({
  AttackGraphCanvas: ({
    graph,
    onSelectNode,
  }: {
    graph: SessionGraph;
    onSelectNode: (nodeId: string | null) => void;
  }) => (
    <div data-testid="attack-graph-canvas-mock">
      {graph.nodes.map((node) => (
        <button key={node.id} type="button" onClick={() => onSelectNode(node.id)}>
          {node.label}
        </button>
      ))}
    </div>
  ),
}));

function createSessionSummary(id: string): SessionSummary {
  return {
    id,
    title: `对话 ${id}`,
    status: "running",
    project_id: null,
    active_branch_id: null,
    goal: null,
    scenario_type: null,
    current_phase: null,
    runtime_policy_json: null,
    created_at: "2026-04-01T10:00:00.000Z",
    updated_at: "2026-04-01T10:00:00.000Z",
    deleted_at: null,
  };
}

function createQueue(sessionId: string): SessionQueue {
  return {
    session: createSessionSummary(sessionId),
    active_generation: null,
    queued_generations: [],
    active_generation_id: null,
    queued_generation_count: 0,
  };
}

function createActiveGeneration(
  sessionId: string,
  overrides: Partial<ChatGeneration> = {},
): ChatGeneration {
  return {
    id: `${sessionId}-generation-1`,
    session_id: sessionId,
    branch_id: "branch-1",
    action: "reply",
    user_message_id: `${sessionId}-user-1`,
    assistant_message_id: `${sessionId}-assistant-1`,
    status: "running",
    steps: [],
    created_at: "2026-04-01T10:00:00.000Z",
    updated_at: "2026-04-01T10:00:01.000Z",
    queue_position: null,
    ...overrides,
  };
}

function createConversation(sessionId: string): SessionConversation {
  return {
    session: createSessionSummary(sessionId),
    active_branch: null,
    branches: [],
    messages: [],
    generations: [],
    active_generation_id: null,
    queued_generation_count: 0,
  };
}

function createGraph(sessionId: string, overrides: Partial<SessionGraph> = {}): SessionGraph {
  return {
    session_id: sessionId,
    workflow_run_id: "",
    graph_type: "attack",
    current_stage: null,
    nodes: [],
    edges: [],
    ...overrides,
  };
}

function createContextWindowUsage(sessionId: string): SessionContextWindowUsage {
  return {
    session_id: sessionId,
    model: "gpt-5.4",
    context_window_tokens: 400000,
    used_tokens: 64000,
    reserved_response_tokens: 8192,
    usage_ratio: 0.16,
    auto_compact_threshold_ratio: 0.8,
    last_compacted_at: null,
    last_compact_boundary: null,
    can_manual_compact: true,
    blocking_reason: null,
    breakdown: [],
  };
}

function createSlashCatalogItem(
  id: string,
  trigger: string,
  type: string,
  source: string,
): SlashCatalogItem {
  return {
    id,
    trigger,
    title: trigger,
    description: `${trigger} description`,
    type,
    source,
    badge: source,
    action: {
      id,
      trigger,
      type,
      source,
      display_text: `/${trigger}`,
      invocation: {
        tool_name: source === "skill" ? "execute_skill" : trigger,
        arguments: {},
        mcp_server_id: null,
        mcp_tool_name: null,
      },
    },
  };
}

function createAttackNode(overrides: Record<string, unknown> = {}) {
  return {
    id: "attack-node-1",
    graph_type: "attack" as const,
    node_type: "exploit",
    label: "可操作攻击节点",
    data: {
      status: "in_progress",
      summary: "攻击图节点摘要",
      source_message_id: "message-1",
      branch_id: "branch-1",
      generation_id: "generation-1",
      ...overrides,
    },
  };
}

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

function createMatchMedia(matches: boolean): (query: string) => MediaQueryList {
  return (query: string) =>
    ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }) as unknown as MediaQueryList;
}

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location-display">{location.pathname}</div>;
}

function renderWorkbench(initialPath: string) {
  const queryClient = createQueryClient();

  const renderResult = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route
            path="/sessions"
            element={
              <>
                <LocationDisplay />
                <SessionWorkspaceWorkbench />
              </>
            }
          />
          <Route
            path="/sessions/:sessionId/chat"
            element={
              <>
                <LocationDisplay />
                <SessionWorkspaceWorkbench />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  return { ...renderResult, queryClient };
}

describe("SessionWorkspaceWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("matchMedia", createMatchMedia(false));
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
      writable: true,
    });

    useUiStore.setState({
      draftsBySession: {},
      eventsBySession: {},
      lastServerCursorBySession: {},
      lastVisitedSessionId: null,
    });

    mockGetRuntimeStatus.mockResolvedValue({ recent_runs: [] });
    mockUseSessionEvents.mockReturnValue("closed");
    mockGetSessionConversation.mockImplementation(async (sessionId: string) =>
      createConversation(sessionId),
    );
    mockGetSessionContextWindowUsage.mockImplementation(async (sessionId: string) =>
      createContextWindowUsage(sessionId),
    );
    mockGetSessionQueue.mockResolvedValue(createQueue("session-1"));
    mockGetSessionSlashCatalog.mockResolvedValue([]);
    mockGetAttackGraph.mockResolvedValue(createGraph("session-1"));
    mockCompactSessionContext.mockResolvedValue({
      session_id: "session-1",
      mode: "manual",
      compacted: true,
      compact_boundary: "compact-boundary:1",
      before_tokens: 64000,
      after_tokens: 24000,
      reclaimed_tokens: 40000,
      summary: "已压缩对话",
      created_at: "2026-04-11T18:21:02.000Z",
    });
  });

  it("passes only backend slash catalog items to the composer", async () => {
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetSessionConversation.mockResolvedValue(createConversation("session-1"));
    mockGetSessionQueue.mockResolvedValue(createQueue("session-1"));
    mockGetSessionSlashCatalog.mockResolvedValue([
      createSlashCatalogItem(
        "builtin:list_available_skills",
        "list-available-skills",
        "builtin",
        "builtin",
      ),
      createSlashCatalogItem("skill:ctf-crypto", "ctf-crypto", "skill", "skill"),
    ]);

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("workbench-composer")).toBeInTheDocument();
    });

    expect(screen.getByTestId("composer-slash-catalog").textContent).toBe(
      "builtin:list-available-skills|skill:ctf-crypto",
    );
    expect(screen.getByTestId("composer-slash-catalog").textContent).not.toContain(
      "ui:goto-skills",
    );
    expect(screen.getByTestId("composer-slash-catalog").textContent).not.toContain("ui:goto-mcp");
    expect(screen.getByTestId("composer-slash-catalog").textContent).not.toContain(
      "ui:goto-runtime",
    );
  });

  it("passes context usage to the composer and wires manual compaction", async () => {
    const user = userEvent.setup();

    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("composer-context-usage").textContent).toBe("64000/400000");
    });

    await user.click(screen.getByRole("button", { name: "mock-manual-compact" }));

    await waitFor(() => {
      expect(mockCompactSessionContext).toHaveBeenCalledWith("session-1");
    });
  });

  it("deletes sessions permanently from local cache and UI state", async () => {
    const user = userEvent.setup();

    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockDeleteSession.mockResolvedValue(undefined);
    useUiStore.setState({
      lastVisitedSessionId: "session-1",
      draftsBySession: {
        "session-1": {
          content: "draft-content",
          queuedContent: "",
          queuedReady: false,
          attachmentForm: {
            name: "",
            contentType: "application/octet-stream",
            sizeBytes: "0",
          },
          attachments: [],
        },
      },
      eventsBySession: {
        "session-1": [
          {
            id: "event-1",
            sessionId: "session-1",
            type: "assistant.trace",
            createdAt: "2026-04-01T10:00:00.000Z",
            summary: "trace",
            payload: {},
          },
        ],
      },
      lastServerCursorBySession: {
        "session-1": 7,
      },
    });

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("workbench-composer")).toBeInTheDocument();
    });

    expect(mockListSessions).toHaveBeenCalledWith(false, expect.anything());

    await user.click(screen.getByRole("button", { name: "delete-session-1" }));

    await waitFor(() => {
      expect(mockDeleteSession).toHaveBeenCalledWith("session-1");
    });

    await waitFor(() => {
      expect(screen.getByTestId("location-display").textContent).toBe("/sessions");
    });

    expect(useUiStore.getState().draftsBySession["session-1"]).toBeUndefined();
    expect(useUiStore.getState().eventsBySession["session-1"]).toBeUndefined();
    expect(useUiStore.getState().lastServerCursorBySession["session-1"]).toBeUndefined();
  });

  it("recovers when the route session is missing from the current session list", async () => {
    useUiStore.setState({ lastVisitedSessionId: "stale-session" });
    mockListSessions.mockResolvedValue([createSessionSummary("session-2")]);

    renderWorkbench("/sessions/stale-session/chat");

    await waitFor(() => {
      expect(screen.getByTestId("location-display").textContent).toBe("/sessions");
    });

    await waitFor(() => {
      expect(screen.getByText("对话不存在或已失效")).toBeInTheDocument();
    });

    expect(
      screen.getByText("未找到 ID 为 stale-session 的对话，已停止当前会话同步。"),
    ).toBeInTheDocument();
    expect(useUiStore.getState().lastVisitedSessionId).toBeNull();
    expect(mockGetSessionConversation.mock.calls.length).toBeLessThanOrEqual(1);
    expect(mockGetSessionQueue.mock.calls.length).toBeLessThanOrEqual(1);
    expect(mockGetAttackGraph.mock.calls.length).toBeLessThanOrEqual(1);
    expect(mockUseSessionEvents).toHaveBeenLastCalledWith(null);
  });

  it("clears stale session selection after a 404 and stops session-specific activity", async () => {
    useUiStore.setState({ lastVisitedSessionId: "stale-session" });
    mockListSessions.mockResolvedValue([createSessionSummary("stale-session")]);
    mockGetSessionConversation.mockRejectedValue(
      new ApiError({
        message: "对话不存在",
        status: 404,
        statusText: "Not Found",
        path: "/api/sessions/stale-session/conversation",
      }),
    );
    mockGetSessionQueue.mockResolvedValue(createQueue("stale-session"));
    mockGetAttackGraph.mockResolvedValue(createGraph("stale-session"));

    renderWorkbench("/sessions/stale-session/chat");

    await waitFor(() => {
      expect(screen.getByTestId("location-display").textContent).toBe("/sessions");
    });

    await waitFor(() => {
      expect(screen.getByText("对话不存在或已失效")).toBeInTheDocument();
    });

    expect(screen.getByText("对话不存在")).toBeInTheDocument();
    expect(useUiStore.getState().lastVisitedSessionId).toBeNull();
    expect(mockGetSessionConversation).toHaveBeenCalledTimes(1);
    expect(mockGetSessionQueue).toHaveBeenCalledTimes(1);
    expect(mockGetAttackGraph).toHaveBeenCalledTimes(1);
    expect(mockUseSessionEvents).toHaveBeenCalledWith("stale-session");
    expect(mockUseSessionEvents).toHaveBeenLastCalledWith(null);
  });

  it("keeps generic errors separate from the 404 recovery path", async () => {
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetSessionConversation.mockRejectedValue(new Error("服务暂时异常"));
    mockGetSessionQueue.mockResolvedValue(createQueue("session-1"));
    mockGetAttackGraph.mockResolvedValue(createGraph("session-1"));

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByText("对话详情暂不可用")).toBeInTheDocument();
    });

    expect(screen.getByText("服务暂时异常")).toBeInTheDocument();
    expect(screen.queryByText("对话不存在或已失效")).not.toBeInTheDocument();
    expect(screen.getByTestId("location-display").textContent).toBe("/sessions/session-1/chat");
    expect(useUiStore.getState().lastVisitedSessionId).toBe("session-1");
    expect(mockUseSessionEvents).not.toHaveBeenLastCalledWith(null);
  });

  it("disables queue polling while websocket is open even with active generation", async () => {
    vi.useFakeTimers();
    try {
      const activeGeneration = createActiveGeneration("session-1");
      const queue = createQueue("session-1");
      queue.active_generation = activeGeneration;
      queue.active_generation_id = activeGeneration.id;

      mockUseSessionEvents.mockReturnValue("open");
      mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
      mockGetSessionQueue.mockResolvedValue(queue);

      renderWorkbench("/sessions/session-1/chat");

      await waitFor(() => {
        expect(mockGetSessionQueue).toHaveBeenCalledTimes(1);
      });

      await act(async () => {
        vi.advanceTimersByTime(1700);
        await Promise.resolve();
      });

      expect(mockGetSessionQueue).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(["closed", "error"] as const)(
    "enables queue fallback polling when websocket is %s and generation is active",
    async (connectionState) => {
      vi.useFakeTimers();
      try {
        const activeGeneration = createActiveGeneration("session-1");
        const queue = createQueue("session-1");
        queue.active_generation = activeGeneration;
        queue.active_generation_id = activeGeneration.id;

        mockUseSessionEvents.mockReturnValue(connectionState);
        mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
        mockGetSessionQueue.mockResolvedValue(queue);

        renderWorkbench("/sessions/session-1/chat");

        await waitFor(() => {
          expect(mockGetSessionQueue).toHaveBeenCalledTimes(1);
        });

        await act(async () => {
          vi.advanceTimersByTime(1700);
          await Promise.resolve();
        });

        expect(mockGetSessionQueue.mock.calls.length).toBeGreaterThanOrEqual(2);
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it("keeps explicit queue-send on the existing optimistic queue path while generation is active", async () => {
    const user = userEvent.setup();
    const activeGeneration = createActiveGeneration("session-1");
    const conversation = createConversation("session-1");
    const queue = createQueue("session-1");

    conversation.active_generation_id = activeGeneration.id;
    queue.active_generation = activeGeneration;
    queue.active_generation_id = activeGeneration.id;

    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetSessionConversation.mockResolvedValue(conversation);
    mockGetSessionQueue.mockResolvedValue(queue);
    mockSendChatMessage.mockImplementation(() => new Promise(() => {}));

    const { queryClient } = renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("workbench-composer")).toBeInTheDocument();
      expect(screen.getByTestId("composer-active-state").textContent).toBe("true");
    });

    await user.click(screen.getByRole("button", { name: "mock-queue-send" }));

    await waitFor(() => {
      expect(mockSendChatMessage).toHaveBeenCalledWith(
        "session-1",
        expect.objectContaining({
          content: "排队消息",
          attachments: [],
          branch_id: null,
        }),
      );
    });

    const queuedConversation = queryClient.getQueryData<SessionConversation>([
      "conversation",
      "session-1",
    ]);
    const queuedState = queryClient.getQueryData<SessionQueue>(["session-queue", "session-1"]);

    expect(mockInjectActiveGenerationContext).not.toHaveBeenCalled();
    expect(queuedConversation?.generations).toHaveLength(1);
    expect(queuedConversation?.messages.map((message) => message.role)).toEqual([
      "user",
      "assistant",
    ]);
    expect(queuedState?.queued_generations).toHaveLength(1);
    expect(queuedState?.queued_generation_count).toBe(1);
  });

  it("routes inject/continue through the dedicated endpoint without creating optimistic queued generations", async () => {
    const user = userEvent.setup();
    const activeGeneration = createActiveGeneration("session-1");
    const conversation = createConversation("session-1");
    const queue = createQueue("session-1");

    conversation.active_generation_id = activeGeneration.id;
    queue.active_generation = activeGeneration;
    queue.active_generation_id = activeGeneration.id;

    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetSessionConversation.mockResolvedValue(conversation);
    mockGetSessionQueue.mockResolvedValue(queue);
    mockInjectActiveGenerationContext.mockImplementation(() => new Promise(() => {}));

    const { queryClient } = renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("workbench-composer")).toBeInTheDocument();
      expect(screen.getByTestId("composer-active-state").textContent).toBe("true");
    });

    await user.click(screen.getByRole("button", { name: "mock-inject-send" }));

    await waitFor(() => {
      expect(mockInjectActiveGenerationContext).toHaveBeenCalledWith("session-1", {
        content: "注入消息",
      });
    });

    const injectedConversation = queryClient.getQueryData<SessionConversation>([
      "conversation",
      "session-1",
    ]);
    const injectedState = queryClient.getQueryData<SessionQueue>(["session-queue", "session-1"]);

    expect(mockSendChatMessage).not.toHaveBeenCalled();
    expect(injectedConversation?.messages).toHaveLength(0);
    expect(injectedConversation?.generations).toHaveLength(0);
    expect(injectedState?.queued_generations).toHaveLength(0);
    expect(injectedState?.queued_generation_count).toBe(0);
  });

  it("shows only the attack graph surface for active sessions", async () => {
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetAttackGraph.mockResolvedValue(
      createGraph("session-1", {
        current_stage: "safe_validation",
      }),
    );

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("attack-graph-workbench")).toBeInTheDocument();
    });

    expect(screen.queryByText("攻击路径主画布")).not.toBeInTheDocument();
    expect(screen.queryByText("新对话")).not.toBeInTheDocument();
    expect(screen.queryByText("攻击路径主视图，会话与图谱保持同步更新。")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "工作流控制" })).not.toBeInTheDocument();
    expect(screen.queryByText("任务图")).not.toBeInTheDocument();
    expect(screen.queryByText("证据图")).not.toBeInTheDocument();
    expect(screen.queryByText("因果图")).not.toBeInTheDocument();
    expect(screen.queryByText("任务树")).not.toBeInTheDocument();
    expect(screen.queryByText("任务与图谱")).not.toBeInTheDocument();
    expect(screen.queryByText("计划与推进")).not.toBeInTheDocument();
  });

  it("opens a docked shell panel under the attack graph when transcript shell focus is requested", async () => {
    const user = userEvent.setup();
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("conversation-feed")).toBeInTheDocument();
    });

    expect(screen.queryByTestId("workspace-shell-focus-panel")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "mock-focus-shell" }));

    await waitFor(() => {
      expect(screen.getByTestId("workspace-shell-focus-panel")).toBeInTheDocument();
      expect(screen.getByTestId("shell-workbench")).toHaveAttribute(
        "data-variant",
        "focus-docked",
      );
      expect(screen.getByTestId("shell-workbench-focus-terminal").textContent).toBe(
        "term-focus-1",
      );
    });
  });

  it("persists split pane sizing and supports keyboard resizing on desktop", async () => {
    const user = userEvent.setup();

    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);

    const { unmount } = renderWorkbench("/sessions/session-1/chat");

    const separator = await screen.findByRole("separator", { name: "调整图谱与聊天面板宽度" });
    const initialValueNow = Number(separator.getAttribute("aria-valuenow"));

    separator.focus();
    expect(separator).toHaveFocus();

    await user.keyboard("{ArrowRight}");

    await waitFor(() => {
      expect(Number(separator.getAttribute("aria-valuenow"))).toBeGreaterThan(initialValueNow);
    });

    const persistedRatio = window.localStorage.getItem("aegissec.workspace.chat-pane.ratio.v1");
    const resizedValueNow = separator.getAttribute("aria-valuenow");

    expect(persistedRatio).not.toBeNull();

    unmount();
    renderWorkbench("/sessions/session-1/chat");

    const restoredSeparator = await screen.findByRole("separator", {
      name: "调整图谱与聊天面板宽度",
    });

    await waitFor(() => {
      expect(restoredSeparator).toHaveAttribute("aria-valuenow", resizedValueNow ?? "");
    });
  });

  it("keeps the separator hidden when the workspace falls back to stacked layout", async () => {
    vi.stubGlobal("matchMedia", createMatchMedia(true));
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1024,
      writable: true,
    });
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("workbench-composer")).toBeInTheDocument();
    });

    expect(
      screen.queryByRole("separator", { name: "调整图谱与聊天面板宽度" }),
    ).not.toBeInTheDocument();
  });

  it("renders the session attack graph data directly", async () => {
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetAttackGraph.mockResolvedValue(
      createGraph("session-1", {
        nodes: [{ ...createAttackNode(), id: "session-node", label: "Session Graph Node" }],
      }),
    );

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Session Graph Node" })).toBeInTheDocument();
    });

    expect(mockGetAttackGraph).toHaveBeenCalled();
  });

  it("wires edit/regenerate/fork/rollback through the main workbench path", async () => {
    const user = userEvent.setup();
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("修订后的消息");
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetAttackGraph.mockResolvedValue(
      createGraph("session-1", {
        nodes: [createAttackNode()],
      }),
    );
    mockEditSessionMessage.mockResolvedValue({});
    mockRegenerateSessionMessage.mockResolvedValue({});
    mockForkSessionMessage.mockResolvedValue(createConversation("session-1"));
    mockRollbackSessionMessage.mockResolvedValue(createConversation("session-1"));

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "可操作攻击节点" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "可操作攻击节点" }));
    expect(screen.getByRole("dialog", { name: "可操作攻击节点 详情" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "重新生成" }));
    await user.click(screen.getByRole("button", { name: "分叉" }));
    await user.click(screen.getByRole("button", { name: "回滚" }));

    await waitFor(() => {
      expect(mockEditSessionMessage).toHaveBeenCalledWith(
        "session-1",
        "message-1",
        expect.objectContaining({ content: "修订后的消息" }),
      );
      expect(mockRegenerateSessionMessage).toHaveBeenCalledWith(
        "session-1",
        "message-1",
        expect.objectContaining({ branch_id: "branch-1" }),
      );
      expect(mockForkSessionMessage).toHaveBeenCalledWith("session-1", "message-1");
      expect(mockRollbackSessionMessage).toHaveBeenCalledWith(
        "session-1",
        "message-1",
        expect.objectContaining({ branch_id: "branch-1" }),
      );
    });

    expect(mockGetSessionConversation.mock.calls.length).toBeGreaterThan(1);
    expect(mockGetSessionQueue.mock.calls.length).toBeGreaterThan(1);
    expect(mockGetAttackGraph.mock.calls.length).toBeGreaterThan(1);

    promptSpy.mockRestore();
  });

  it("refreshes attack graphs when a message.delta event arrives", async () => {
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetAttackGraph.mockResolvedValue(
      createGraph("session-1", {
        nodes: [createAttackNode({ source_message_id: undefined })],
      }),
    );
    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("attack-graph-workbench")).toBeInTheDocument();
    });

    const sessionCallsBefore = mockGetAttackGraph.mock.calls.length;

    await act(async () => {
      useUiStore.setState({
        eventsBySession: {
          "session-1": [
            {
              id: "event-1",
              sessionId: "session-1",
              type: "message.delta",
              payload: {},
              createdAt: "2026-04-01T10:00:01.000Z",
              summary: "delta",
            },
          ],
        },
      });
    });

    await waitFor(
      () => {
        expect(mockGetAttackGraph.mock.calls.length).toBeGreaterThan(sessionCallsBefore);
      },
      { timeout: 3000 },
    );
  });
});
