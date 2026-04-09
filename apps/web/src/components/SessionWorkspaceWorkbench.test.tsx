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
  SessionQueue,
  SessionSummary,
} from "../types/sessions";
import { SessionWorkspaceWorkbench } from "./SessionWorkspaceWorkbench";

const {
  mockGetAttackGraph,
  mockCancelGeneration,
  mockCancelSession,
  mockCreateSession,
  mockDeleteSession,
  mockEditSessionMessage,
  mockGetRuntimeStatus,
  mockGetSessionConversation,
  mockGetSessionQueue,
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
  mockCreateSession: vi.fn(),
  mockDeleteSession: vi.fn(),
  mockEditSessionMessage: vi.fn(),
  mockGetRuntimeStatus: vi.fn(),
  mockGetSessionConversation: vi.fn(),
  mockGetSessionQueue: vi.fn(),
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
    createSession: mockCreateSession,
    deleteSession: mockDeleteSession,
    editSessionMessage: mockEditSessionMessage,
    getRuntimeStatus: mockGetRuntimeStatus,
    getSessionConversation: mockGetSessionConversation,
    getSessionQueue: mockGetSessionQueue,
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
  ConversationSidebar: () => <div data-testid="conversation-sidebar" />,
}));

vi.mock("./ConversationFeed", () => ({
  ConversationFeed: () => <div data-testid="conversation-feed" />,
}));

vi.mock("./WorkbenchComposer", () => ({
  WorkbenchComposer: ({
    isActiveGeneration,
    isPausedGeneration,
    queuedCount,
    onQueueSend,
    onInject,
  }: {
    isActiveGeneration: boolean;
    isPausedGeneration: boolean;
    queuedCount: number;
    onQueueSend: (payload: { content: string; slashAction?: unknown | null }) => Promise<void>;
    onInject: (content: string) => Promise<void>;
  }) => (
    <div data-testid="workbench-composer">
      <span data-testid="composer-active-state">{String(isActiveGeneration)}</span>
      <span data-testid="composer-paused-state">{String(isPausedGeneration)}</span>
      <span data-testid="composer-queued-count">{queuedCount}</span>
      <button type="button" onClick={() => void onQueueSend({ content: "排队消息" })}>
        mock-queue-send
      </button>
      <button type="button" onClick={() => void onInject("注入消息")}>
        mock-inject-send
      </button>
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
    mockGetSessionQueue.mockResolvedValue(createQueue("session-1"));
    mockGetAttackGraph.mockResolvedValue(createGraph("session-1"));
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
