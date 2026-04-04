import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { useUiStore } from "../store/uiStore";
import type { SessionGraph } from "../types/graphs";
import type { SessionConversation, SessionQueue, SessionSummary } from "../types/sessions";
import type { WorkflowRunExport, WorkflowRunReplay } from "../types/workflows";
import { SessionWorkspaceWorkbench } from "./SessionWorkspaceWorkbench";

const {
  mockAdvanceWorkflow,
  mockGetAttackGraph,
  mockGetAttackGraphForRun,
  mockCancelGeneration,
  mockCancelSession,
  mockCreateSession,
  mockDeleteSession,
  mockEditSessionMessage,
  mockGetRuntimeStatus,
  mockGetSessionConversation,
  mockGetSessionQueue,
  mockGetWorkflow,
  mockGetWorkflowExport,
  mockGetWorkflowReplay,
  mockForkSessionMessage,
  mockListSessions,
  mockListWorkflowTemplates,
  mockRegenerateSessionMessage,
  mockRollbackSessionMessage,
  mockStartWorkflow,
  mockUpdateSession,
  mockSendChatMessage,
  mockUseSessionEvents,
} = vi.hoisted(() => ({
  mockAdvanceWorkflow: vi.fn(),
  mockGetAttackGraph: vi.fn(),
  mockGetAttackGraphForRun: vi.fn(),
  mockCancelGeneration: vi.fn(),
  mockCancelSession: vi.fn(),
  mockCreateSession: vi.fn(),
  mockDeleteSession: vi.fn(),
  mockEditSessionMessage: vi.fn(),
  mockGetRuntimeStatus: vi.fn(),
  mockGetSessionConversation: vi.fn(),
  mockGetSessionQueue: vi.fn(),
  mockGetWorkflow: vi.fn(),
  mockGetWorkflowExport: vi.fn(),
  mockGetWorkflowReplay: vi.fn(),
  mockForkSessionMessage: vi.fn(),
  mockListSessions: vi.fn(),
  mockListWorkflowTemplates: vi.fn(),
  mockRegenerateSessionMessage: vi.fn(),
  mockRollbackSessionMessage: vi.fn(),
  mockStartWorkflow: vi.fn(),
  mockUpdateSession: vi.fn(),
  mockSendChatMessage: vi.fn(),
  mockUseSessionEvents: vi.fn(),
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");

  return {
    ...actual,
    advanceWorkflow: mockAdvanceWorkflow,
    getAttackGraph: mockGetAttackGraph,
    getAttackGraphForRun: mockGetAttackGraphForRun,
    cancelGeneration: mockCancelGeneration,
    cancelSession: mockCancelSession,
    createSession: mockCreateSession,
    deleteSession: mockDeleteSession,
    editSessionMessage: mockEditSessionMessage,
    getRuntimeStatus: mockGetRuntimeStatus,
    getSessionConversation: mockGetSessionConversation,
    getSessionQueue: mockGetSessionQueue,
    getWorkflow: mockGetWorkflow,
    getWorkflowExport: mockGetWorkflowExport,
    getWorkflowReplay: mockGetWorkflowReplay,
    forkSessionMessage: mockForkSessionMessage,
    listSessions: mockListSessions,
    listWorkflowTemplates: mockListWorkflowTemplates,
    regenerateSessionMessage: mockRegenerateSessionMessage,
    rollbackSessionMessage: mockRollbackSessionMessage,
    startWorkflow: mockStartWorkflow,
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
  WorkbenchComposer: () => <div data-testid="workbench-composer" />,
}));

vi.mock("./AttackGraphCanvas", () => ({
  AttackGraphCanvas: ({
    graph,
    onSelectNode,
  }: {
    graph: SessionGraph;
    onSelectNode: (nodeId: string) => void;
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

function createGraph(
  sessionId: string,
  overrides: Partial<SessionGraph> = {},
): SessionGraph {
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

function createWorkflow(sessionId: string, runId: string) {
  return {
    id: runId,
    session_id: sessionId,
    template_name: "authorized-assessment",
    status: "running",
    current_stage: "safe_validation",
    state: {},
    last_error: null,
    created_at: "2026-04-01T10:00:00.000Z",
    updated_at: "2026-04-01T10:00:00.000Z",
    started_at: "2026-04-01T10:00:00.000Z",
    ended_at: null,
    tasks: [],
  };
}

function createWorkflowReplay(sessionId: string, runId: string): WorkflowRunReplay {
  return {
    run_id: runId,
    session_id: sessionId,
    template_name: "authorized-assessment",
    status: "running",
    current_stage: "safe_validation",
    replay_steps: [],
    replan_records: [],
    batch_state: {
      contract_version: "v1",
      cycle: 0,
      status: "idle",
      max_nodes_per_cycle: 1,
      selected_task_ids: [],
      executed_task_ids: [],
      started_at: null,
      ended_at: null,
    },
  };
}

function createWorkflowExport(sessionId: string, runId: string): WorkflowRunExport {
  const run = createWorkflow(sessionId, runId);
  const attackGraph = createGraph(sessionId, { workflow_run_id: runId });

  return {
    run,
    task_graph: createGraph(sessionId, { graph_type: "task", workflow_run_id: runId }),
    evidence_graph: createGraph(sessionId, { graph_type: "evidence", workflow_run_id: runId }),
    causal_graph: createGraph(sessionId, { graph_type: "causal", workflow_run_id: runId }),
    attack_graph: attackGraph,
    execution_records: [],
    replan_records: [],
    batch_state: {
      contract_version: "v1",
      cycle: 0,
      status: "idle",
      max_nodes_per_cycle: 1,
      selected_task_ids: [],
      executed_task_ids: [],
      started_at: null,
      ended_at: null,
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

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location-display">{location.pathname}</div>;
}

function renderWorkbench(initialPath: string) {
  const queryClient = createQueryClient();

  return render(
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
}

describe("SessionWorkspaceWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    useUiStore.setState({
      draftsBySession: {},
      eventsBySession: {},
      lastServerCursorBySession: {},
      lastVisitedSessionId: null,
    });

    mockListWorkflowTemplates.mockResolvedValue([]);
    mockGetRuntimeStatus.mockResolvedValue({ recent_runs: [] });
    mockUseSessionEvents.mockReturnValue("closed");
    mockGetSessionConversation.mockImplementation(async (sessionId: string) =>
      createConversation(sessionId),
    );
    mockGetSessionQueue.mockResolvedValue(createQueue("session-1"));
    mockGetAttackGraph.mockResolvedValue(createGraph("session-1"));
    mockGetAttackGraphForRun.mockResolvedValue(createGraph("session-1"));
    mockGetWorkflow.mockResolvedValue(createWorkflow("session-1", "run-1"));
    mockGetWorkflowExport.mockResolvedValue(createWorkflowExport("session-1", "run-1"));
    mockGetWorkflowReplay.mockResolvedValue(createWorkflowReplay("session-1", "run-1"));
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

  it("shows only the attack graph surface for active workflow sessions", async () => {
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetAttackGraph.mockResolvedValue(
      createGraph("session-1", {
        workflow_run_id: "run-1",
        current_stage: "safe_validation",
      }),
    );
    mockGetAttackGraphForRun.mockResolvedValue(
      createGraph("session-1", {
        workflow_run_id: "run-1",
        current_stage: "safe_validation",
      }),
    );

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("attack-graph-workbench")).toBeInTheDocument();
    });

    expect(screen.getByText("攻击图")).toBeInTheDocument();
    expect(screen.getByText("节点详情")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "工作流控制" })).not.toBeInTheDocument();
    expect(screen.queryByText("任务图")).not.toBeInTheDocument();
    expect(screen.queryByText("证据图")).not.toBeInTheDocument();
    expect(screen.queryByText("因果图")).not.toBeInTheDocument();
    expect(screen.queryByText("任务树")).not.toBeInTheDocument();
    expect(screen.queryByText("任务与图谱")).not.toBeInTheDocument();
    expect(screen.queryByText("计划与推进")).not.toBeInTheDocument();
  });

  it("prefers run attack graph data once workflow_run_id is available", async () => {
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetAttackGraph.mockResolvedValue(
      createGraph("session-1", {
        workflow_run_id: "run-1",
        nodes: [{ ...createAttackNode(), id: "session-node", label: "Session Graph Node" }],
      }),
    );
    mockGetAttackGraphForRun.mockResolvedValue(
      createGraph("session-1", {
        workflow_run_id: "run-1",
        nodes: [{ ...createAttackNode(), id: "run-node", label: "Run Graph Node" }],
      }),
    );

    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Run Graph Node" })).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: "Session Graph Node" })).not.toBeInTheDocument();
    expect(mockGetAttackGraphForRun).toHaveBeenCalled();
  });

  it("wires edit/regenerate/fork/rollback through the main workbench path", async () => {
    const user = userEvent.setup();
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("修订后的消息");
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetAttackGraph.mockResolvedValue(
      createGraph("session-1", {
        workflow_run_id: "run-1",
        nodes: [createAttackNode()],
      }),
    );
    mockGetAttackGraphForRun.mockResolvedValue(
      createGraph("session-1", {
        workflow_run_id: "run-1",
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
    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "重生成" }));
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
    expect(mockGetAttackGraphForRun.mock.calls.length).toBeGreaterThan(1);

    promptSpy.mockRestore();
  });

  it("refreshes attack graphs when a message.delta event arrives", async () => {
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetAttackGraph.mockResolvedValue(
      createGraph("session-1", {
        workflow_run_id: "run-1",
        nodes: [createAttackNode({ source_message_id: undefined })],
      }),
    );
    mockGetAttackGraphForRun.mockResolvedValue(
      createGraph("session-1", {
        workflow_run_id: "run-1",
        nodes: [createAttackNode({ id: "run-node-1", source_message_id: undefined })],
      }),
    );
    renderWorkbench("/sessions/session-1/chat");

    await waitFor(() => {
      expect(screen.getByTestId("attack-graph-workbench")).toBeInTheDocument();
    });

    const sessionCallsBefore = mockGetAttackGraph.mock.calls.length;
    const runCallsBefore = mockGetAttackGraphForRun.mock.calls.length;

    await act(async () => {
      useUiStore.setState({
        eventsBySession: {
          "session-1": [
            {
              id: "event-1",
              session_id: "session-1",
              type: "message.delta",
              payload: {},
              created_at: "2026-04-01T10:00:01.000Z",
            },
          ],
        },
      });
    });

    await waitFor(() => {
      expect(mockGetAttackGraph.mock.calls.length).toBeGreaterThan(sessionCallsBefore);
      expect(mockGetAttackGraphForRun.mock.calls.length).toBeGreaterThan(runCallsBefore);
    }, { timeout: 3000 });
  });
});
