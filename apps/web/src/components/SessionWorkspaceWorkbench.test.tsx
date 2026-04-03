import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { useUiStore } from "../store/uiStore";
import type { SessionGraph } from "../types/graphs";
import type { SessionConversation, SessionQueue, SessionSummary } from "../types/sessions";
import { SessionWorkspaceWorkbench } from "./SessionWorkspaceWorkbench";

const {
  mockAdvanceWorkflow,
  mockCancelGeneration,
  mockCancelSession,
  mockCreateSession,
  mockDeleteSession,
  mockEditSessionMessage,
  mockGetCausalGraph,
  mockGetCausalGraphForRun,
  mockGetEvidenceGraph,
  mockGetEvidenceGraphForRun,
  mockGetRuntimeStatus,
  mockGetSessionConversation,
  mockGetSessionQueue,
  mockGetTaskGraph,
  mockGetTaskGraphForRun,
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
  mockCancelGeneration: vi.fn(),
  mockCancelSession: vi.fn(),
  mockCreateSession: vi.fn(),
  mockDeleteSession: vi.fn(),
  mockEditSessionMessage: vi.fn(),
  mockGetCausalGraph: vi.fn(),
  mockGetCausalGraphForRun: vi.fn(),
  mockGetEvidenceGraph: vi.fn(),
  mockGetEvidenceGraphForRun: vi.fn(),
  mockGetRuntimeStatus: vi.fn(),
  mockGetSessionConversation: vi.fn(),
  mockGetSessionQueue: vi.fn(),
  mockGetTaskGraph: vi.fn(),
  mockGetTaskGraphForRun: vi.fn(),
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
    cancelGeneration: mockCancelGeneration,
    cancelSession: mockCancelSession,
    createSession: mockCreateSession,
    deleteSession: mockDeleteSession,
    editSessionMessage: mockEditSessionMessage,
    getCausalGraph: mockGetCausalGraph,
    getCausalGraphForRun: mockGetCausalGraphForRun,
    getEvidenceGraph: mockGetEvidenceGraph,
    getEvidenceGraphForRun: mockGetEvidenceGraphForRun,
    getRuntimeStatus: mockGetRuntimeStatus,
    getSessionConversation: mockGetSessionConversation,
    getSessionQueue: mockGetSessionQueue,
    getTaskGraph: mockGetTaskGraph,
    getTaskGraphForRun: mockGetTaskGraphForRun,
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

function createGraph(sessionId: string): SessionGraph {
  return {
    session_id: sessionId,
    workflow_run_id: "",
    graph_type: "task",
    current_stage: null,
    nodes: [],
    edges: [],
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
    mockGetTaskGraph.mockResolvedValue(createGraph("session-1"));
    mockGetEvidenceGraph.mockResolvedValue(createGraph("session-1"));
    mockGetCausalGraph.mockResolvedValue(createGraph("session-1"));
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
    expect(mockGetTaskGraph.mock.calls.length).toBeLessThanOrEqual(1);
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
    mockGetTaskGraph.mockResolvedValue(createGraph("stale-session"));

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
    expect(mockGetTaskGraph).toHaveBeenCalledTimes(1);
    expect(mockUseSessionEvents).toHaveBeenCalledWith("stale-session");
    expect(mockUseSessionEvents).toHaveBeenLastCalledWith(null);
  });

  it("keeps generic errors separate from the 404 recovery path", async () => {
    mockListSessions.mockResolvedValue([createSessionSummary("session-1")]);
    mockGetSessionConversation.mockRejectedValue(new Error("服务暂时异常"));
    mockGetSessionQueue.mockResolvedValue(createQueue("session-1"));
    mockGetTaskGraph.mockResolvedValue(createGraph("session-1"));

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
});
