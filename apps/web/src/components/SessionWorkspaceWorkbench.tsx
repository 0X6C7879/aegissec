import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  isApiError,
  advanceWorkflow,
  forkSessionMessage,
  getAttackGraph,
  getAttackGraphForRun,
  cancelGeneration,
  cancelSession,
  createSession,
  deleteSession,
  editSessionMessage,
  getRuntimeStatus,
  getSessionConversation,
  getSessionQueue,
  getWorkflow,
  getWorkflowExport,
  getWorkflowReplay,
  listSessions,
  listWorkflowTemplates,
  regenerateSessionMessage,
  rollbackSessionMessage,
  startWorkflow,
  updateSession,
  sendChatMessage,
} from "../lib/api";
import { useSessionEvents } from "../hooks/useSessionEvents";
import {
  mergeConversationGeneration,
  mergeSessionMessages,
  sortSessions,
  upsertSession,
} from "../lib/sessionUtils";
import { useUiStore } from "../store/uiStore";
import type { SessionGraphNode } from "../types/graphs";
import type {
  ChatGeneration,
  GenerationStep,
  SessionConversation,
  SessionMessage,
  SessionQueue,
  SessionSummary,
} from "../types/sessions";
import type {
  WorkflowRunDetail,
} from "../types/workflows";
import { AttackGraphWorkbench } from "./AttackGraphWorkbench";
import { ConversationFeed } from "./ConversationFeed";
import { ConversationSidebar } from "./ConversationSidebar";
import { WorkbenchComposer } from "./WorkbenchComposer";

type InvalidSessionState = {
  sessionId: string;
  message: string;
};

const EMPTY_SESSION_EVENTS: ReturnType<typeof useUiStore.getState>["eventsBySession"][string] = [];
const WORKSPACE_SIDEBAR_STORAGE_KEY = "aegissec.workspace.sidebar.collapsed.v1";

function getStoredWorkspaceSidebarState(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  return window.localStorage.getItem(WORKSPACE_SIDEBAR_STORAGE_KEY) === "true";
}

function getSessionDisplayTitle(title: string): string {
  return title === "New Session" ? "新对话" : title;
}

function buildInvalidSessionState(sessionId: string, message?: string): InvalidSessionState {
  return {
    sessionId,
    message:
      message && message.trim().length > 0
        ? message
        : `未找到 ID 为 ${sessionId} 的对话，已返回对话列表。`,
  };
}

function visibleSessionsForSidebar(
  sessions: SessionSummary[],
  activeSessionId: string | null,
): SessionSummary[] {
  return sessions.filter((session) => !session.deleted_at || session.id === activeSessionId);
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function formatWorkflowStatus(status: string | null): string {
  switch (status) {
    case "queued":
      return "排队中";
    case "running":
      return "运行中";
    case "needs_approval":
      return "待审批";
    case "paused":
      return "已暂停";
    case "done":
      return "已完成";
    case "error":
      return "异常";
    case "blocked":
      return "已阻塞";
    default:
      return status ?? "未开始";
  }
}

function getConnectionTone(state: string): string {
  return state === "open" ? "在线" : state === "connecting" ? "连接中" : "离线";
}

function buildOptimisticUserMessage(sessionId: string, content: string): SessionMessage {
  return {
    id: `optimistic-user-${crypto.randomUUID()}`,
    session_id: sessionId,
    role: "user" as const,
    content,
    assistant_transcript: [],
    attachments: [],
    created_at: new Date().toISOString(),
  };
}

function buildOptimisticAssistantMessage(
  sessionId: string,
  branchId: string | null,
  generationId: string,
  createdAt: string,
  queuePosition: number,
): SessionMessage {
  return {
    id: `optimistic-assistant-${crypto.randomUUID()}`,
    session_id: sessionId,
    branch_id: branchId,
    generation_id: generationId,
    role: "assistant",
    status: "queued",
    message_kind: "message",
    content: "",
    assistant_transcript: [
      {
        id: `optimistic-transcript-${crypto.randomUUID()}`,
        sequence: 1,
        kind: "status",
        status: "queued",
        title: queuePosition > 1 ? `排队 #${queuePosition}` : "等待开始",
        text:
          queuePosition > 1
            ? `已进入队列，前方还有 ${queuePosition - 1} 条等待。`
            : "已进入队列，等待开始。",
        recorded_at: createdAt,
        updated_at: createdAt,
      },
    ],
    attachments: [],
    created_at: createdAt,
  };
}

function buildOptimisticGeneration(
  sessionId: string,
  branchId: string | null,
  userMessageId: string,
  assistantMessageId: string,
  createdAt: string,
  queuePosition: number,
): ChatGeneration {
  const queuedStep: GenerationStep = {
    id: `optimistic-step-${crypto.randomUUID()}`,
    generation_id: `optimistic-generation-${crypto.randomUUID()}`,
    session_id: sessionId,
    message_id: assistantMessageId,
    sequence: 1,
    kind: "status",
    phase: "planning",
    status: "pending",
    state: "queued",
    label: "已加入队列",
    safe_summary:
      queuePosition > 1
        ? `已进入队列，前方还有 ${queuePosition - 1} 条等待。`
        : "已进入队列，等待开始。",
    delta_text: "",
    started_at: createdAt,
    ended_at: null,
  };

  const generationId = queuedStep.generation_id;

  return {
    id: generationId,
    session_id: sessionId,
    branch_id: branchId ?? "default-branch",
    action: "reply",
    user_message_id: userMessageId,
    assistant_message_id: assistantMessageId,
    status: "queued",
    steps: [{ ...queuedStep, generation_id: generationId }],
    created_at: createdAt,
    updated_at: createdAt,
    queue_position: queuePosition,
  };
}

function downloadJson(fileName: string, payload: unknown): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}


export function SessionWorkspaceWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { sessionId: routeSessionIdParam } = useParams<{ sessionId?: string }>();
  const routeSessionId = routeSessionIdParam ?? null;
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState<boolean>(() =>
    getStoredWorkspaceSidebarState(),
  );
  const [selectedTemplateName, setSelectedTemplateName] = useState("");
  const [pinnedWorkflowRunId, setPinnedWorkflowRunId] = useState<string | null>(null);
  const [isInsightsOpen, setIsInsightsOpen] = useState(true);
  const [selectedAttackNodeId, setSelectedAttackNodeId] = useState<string | null>(null);
  const [messageActionBusyId, setMessageActionBusyId] = useState<string | null>(null);
  const [invalidSessionState, setInvalidSessionState] = useState<InvalidSessionState | null>(null);
  const suppressRouteAutonavigateRef = useRef(false);

  const lastVisitedSessionId = useUiStore((state) => state.lastVisitedSessionId);
  const setLastVisitedSessionId = useUiStore((state) => state.setLastVisitedSessionId);
  const appendEvent = useUiStore((state) => state.appendEvent);
  const sessionEvents = useUiStore((state) =>
    routeSessionId
      ? (state.eventsBySession[routeSessionId] ?? EMPTY_SESSION_EVENTS)
      : EMPTY_SESSION_EVENTS,
  );

  const invalidateStaleSessionSelection = useCallback(
    (staleSessionId: string, message?: string): void => {
      suppressRouteAutonavigateRef.current = true;
      setInvalidSessionState(buildInvalidSessionState(staleSessionId, message));
      setLastVisitedSessionId(null);
      setPinnedWorkflowRunId(null);
      setIsInsightsOpen(false);
      setSelectedAttackNodeId(null);
      void queryClient.cancelQueries({ queryKey: ["conversation", staleSessionId] });
      void queryClient.cancelQueries({ queryKey: ["session-queue", staleSessionId] });
      void queryClient.cancelQueries({ queryKey: ["session", staleSessionId, "graph", "attack"] });
      navigate("/sessions", { replace: true });
    },
    [navigate, queryClient, setLastVisitedSessionId],
  );

  useEffect(() => {
    window.localStorage.setItem(WORKSPACE_SIDEBAR_STORAGE_KEY, String(isSidebarCollapsed));
  }, [isSidebarCollapsed]);

  const sessionsQuery = useQuery({
    queryKey: ["sessions", "workspace"],
    queryFn: ({ signal }) => listSessions(true, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const templatesQuery = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: ({ signal }) => listWorkflowTemplates(signal),
  });

  const runtimeStatusQuery = useQuery({
    queryKey: ["runtime-status"],
    queryFn: ({ signal }) => getRuntimeStatus(signal),
    placeholderData: (previousValue) => previousValue,
    refetchInterval: 15000,
  });

  const sortedSessions = useMemo(
    () => sortSessions(sessionsQuery.data ?? []),
    [sessionsQuery.data],
  );
  const routeSessionExists = useMemo(
    () =>
      routeSessionId !== null && sortedSessions.some((session) => session.id === routeSessionId),
    [routeSessionId, sortedSessions],
  );
  const routeSessionMissingFromList =
    routeSessionId !== null &&
    sessionsQuery.data !== undefined &&
    !sessionsQuery.isError &&
    !routeSessionExists;
  const activeSessionId = useMemo(() => {
    if (routeSessionId) {
      if (routeSessionMissingFromList || invalidSessionState?.sessionId === routeSessionId) {
        return null;
      }

      return routeSessionId;
    }

    if (invalidSessionState) {
      return null;
    }

    if (
      lastVisitedSessionId &&
      sortedSessions.some((session) => session.id === lastVisitedSessionId)
    ) {
      return lastVisitedSessionId;
    }

    return (
      sortedSessions.find((session) => !session.deleted_at)?.id ?? sortedSessions[0]?.id ?? null
    );
  }, [
    invalidSessionState,
    lastVisitedSessionId,
    routeSessionId,
    routeSessionMissingFromList,
    sortedSessions,
  ]);

  const activeSession = useMemo(
    () => sortedSessions.find((session) => session.id === activeSessionId) ?? null,
    [activeSessionId, sortedSessions],
  );

  const sidebarSessions = useMemo(
    () => visibleSessionsForSidebar(sortedSessions, activeSessionId),
    [activeSessionId, sortedSessions],
  );

  useEffect(() => {
    if (
      !routeSessionId &&
      activeSessionId &&
      !invalidSessionState &&
      !suppressRouteAutonavigateRef.current
    ) {
      navigate(`/sessions/${activeSessionId}/chat`, { replace: true });
    }
  }, [activeSessionId, invalidSessionState, navigate, routeSessionId]);

  useEffect(() => {
    if (activeSessionId) {
      setLastVisitedSessionId(activeSessionId);
    }
  }, [activeSessionId, setLastVisitedSessionId]);

  useEffect(() => {
    if (!routeSessionId || !routeSessionMissingFromList) {
      return;
    }

    invalidateStaleSessionSelection(
      routeSessionId,
      `未找到 ID 为 ${routeSessionId} 的对话，已停止当前会话同步。`,
    );
  }, [invalidateStaleSessionSelection, routeSessionId, routeSessionMissingFromList]);

  useEffect(() => {
    if (routeSessionId && invalidSessionState?.sessionId !== routeSessionId) {
      suppressRouteAutonavigateRef.current = false;
      setInvalidSessionState(null);
      return;
    }
  }, [invalidSessionState?.sessionId, routeSessionId]);

  const conversationQuery = useQuery({
    enabled: Boolean(activeSessionId),
    queryKey: ["conversation", activeSessionId],
    queryFn: ({ signal }) => getSessionConversation(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const sessionQueueQuery = useQuery({
    enabled: Boolean(activeSessionId),
    queryKey: ["session-queue", activeSessionId],
    queryFn: ({ signal }) => getSessionQueue(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
    refetchInterval: (query) => {
      const value = query.state.data;
      if (!value) {
        return false;
      }
      return value.active_generation || value.queued_generations.length > 0 ? 1500 : false;
    },
  });

  const sessionAttackGraphQuery = useQuery({
    enabled: Boolean(activeSessionId),
    queryKey: ["session", activeSessionId, "graph", "attack"],
    queryFn: ({ signal }) => getAttackGraph(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const staleSessionNotFoundMessage = useMemo(() => {
    const candidateErrors = [
      conversationQuery.error,
      sessionQueueQuery.error,
      sessionAttackGraphQuery.error,
    ];

    for (const error of candidateErrors) {
      if (isApiError(error) && error.status === 404) {
        return error.message;
      }
    }

    return null;
  }, [
    conversationQuery.error,
    sessionQueueQuery.error,
    sessionAttackGraphQuery.error,
  ]);

  useEffect(() => {
    if (!routeSessionId || !staleSessionNotFoundMessage) {
      return;
    }

    invalidateStaleSessionSelection(routeSessionId, staleSessionNotFoundMessage);
  }, [invalidateStaleSessionSelection, routeSessionId, staleSessionNotFoundMessage]);

  const inferredWorkflowRunId = sessionAttackGraphQuery.data?.workflow_run_id ?? null;

  useEffect(() => {
    if (inferredWorkflowRunId) {
      setPinnedWorkflowRunId(inferredWorkflowRunId);
    }
  }, [inferredWorkflowRunId]);

  const workflowRunId = pinnedWorkflowRunId ?? inferredWorkflowRunId;

  useEffect(() => {
    if (workflowRunId) {
      return;
    }

    setSelectedAttackNodeId(null);
  }, [workflowRunId]);

  useEffect(() => {
    if (!isInsightsOpen) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key !== "Escape") {
        return;
      }

      if (isInsightsOpen) {
        setIsInsightsOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isInsightsOpen]);

  const workflowQuery = useQuery({
    enabled: Boolean(workflowRunId),
    queryKey: ["workflow", workflowRunId],
    queryFn: ({ signal }) => getWorkflow(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const runAttackGraphQuery = useQuery({
    enabled: Boolean(workflowRunId),
    queryKey: ["workflow", workflowRunId, "graph", "attack"],
    queryFn: ({ signal }) => getAttackGraphForRun(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const workflowExportQuery = useQuery({
    enabled: Boolean(workflowRunId),
    queryKey: ["workflow", workflowRunId, "export"],
    queryFn: ({ signal }) => getWorkflowExport(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const workflowReplayQuery = useQuery({
    enabled: Boolean(workflowRunId),
    queryKey: ["workflow", workflowRunId, "replay"],
    queryFn: ({ signal }) => getWorkflowReplay(workflowRunId!, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const connectionState = useSessionEvents(activeSessionId);
  const sessionRuns = useMemo(
    () =>
      (runtimeStatusQuery.data?.recent_runs ?? []).filter(
        (run) => run.session_id === activeSessionId,
      ),
    [activeSessionId, runtimeStatusQuery.data?.recent_runs],
  );

  useEffect(() => {
    const templateNames = new Set((templatesQuery.data ?? []).map((template) => template.name));
    const activeTemplateName = workflowQuery.data?.template_name ?? null;

    if (!selectedTemplateName) {
      if (activeTemplateName && templateNames.has(activeTemplateName)) {
        setSelectedTemplateName(activeTemplateName);
        return;
      }

      const firstTemplateName = templatesQuery.data?.[0]?.name;
      if (firstTemplateName) {
        setSelectedTemplateName(firstTemplateName);
      }
      return;
    }

    if (templateNames.size > 0 && !templateNames.has(selectedTemplateName)) {
      setSelectedTemplateName(templatesQuery.data?.[0]?.name ?? "");
    }
  }, [selectedTemplateName, templatesQuery.data, workflowQuery.data?.template_name]);

  const createSessionMutation = useMutation({
    mutationFn: () => createSession(),
    onSuccess: async (createdSession) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setLastVisitedSessionId(createdSession.id);
      suppressRouteAutonavigateRef.current = false;
      setInvalidSessionState(null);
      navigate(`/sessions/${createdSession.id}/chat`);
    },
  });

  const renameSessionMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => updateSession(id, { title }),
    onSuccess: (updatedSession) => {
      queryClient.setQueryData<SessionConversation | undefined>(
        ["conversation", updatedSession.id],
        (currentValue) =>
          currentValue ? { ...currentValue, session: updatedSession } : currentValue,
      );
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
    },
  });

  const deleteSessionMutation = useMutation({
    mutationFn: (id: string) => deleteSession(id),
    onSuccess: async (_value, deletedId) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (routeSessionId === deletedId) {
        navigate("/sessions");
      }
    },
  });

  const restoreSessionMutation = useMutation({
    mutationFn: (id: string) => updateSession(id, {}),
    onSuccess: async (_value, restoredId) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      suppressRouteAutonavigateRef.current = false;
      setInvalidSessionState(null);
      navigate(`/sessions/${restoredId}/chat`);
    },
    onError: async (_error, restoredId) => {
      await restoreSessionMutation.reset();
      await queryClient.invalidateQueries({ queryKey: ["conversation", restoredId] });
    },
  });

  const restoreArchivedSessionMutation = useMutation({
    mutationFn: async (id: string) => {
      const { restoreSession } = await import("../lib/api");
      return restoreSession(id);
    },
    onSuccess: (restoredSession) => {
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, restoredSession),
      );
      suppressRouteAutonavigateRef.current = false;
      setInvalidSessionState(null);
      navigate(`/sessions/${restoredSession.id}/chat`);
    },
  });

  const pauseSessionMutation = useMutation({
    mutationFn: ({ id }: { id: string }) => updateSession(id, { status: "paused" }),
    onSuccess: (updatedSession) => {
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
    },
  });

  const cancelSessionMutation = useMutation({
    mutationFn: ({ id }: { id: string }) => cancelSession(id),
    onSuccess: (updatedSession) => {
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
      queryClient.setQueryData<SessionConversation | undefined>(
        ["conversation", updatedSession.id],
        (currentValue) =>
          currentValue ? { ...currentValue, session: updatedSession } : currentValue,
      );
      void queryClient.invalidateQueries({ queryKey: ["session-queue", updatedSession.id] });
    },
    onError: (error, variables) => {
      appendEvent(variables.id, {
        id: crypto.randomUUID(),
        sessionId: variables.id,
        type: "assistant.trace",
        createdAt: new Date().toISOString(),
        summary: "停止当前回复失败。",
        payload: { status: "error", error: error instanceof Error ? error.message : "未知错误" },
      });
    },
  });

  const sendChatMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      sendChatMessage(id, {
        content,
        attachments: [],
        branch_id: activeConversation?.active_branch?.id ?? null,
      }),
    onMutate: async ({ id, content }) => {
      await queryClient.cancelQueries({ queryKey: ["conversation", id] });
      const previousDetail = queryClient.getQueryData<SessionConversation | undefined>([
        "conversation",
        id,
      ]);
      const previousQueue = queryClient.getQueryData<SessionQueue | undefined>([
        "session-queue",
        id,
      ]);
      const optimisticMessage = buildOptimisticUserMessage(id, content);
      const createdAt = optimisticMessage.created_at;
      const branchId = previousDetail?.active_branch?.id ?? null;
      const activeGenerationId =
        previousQueue?.active_generation_id ?? previousQueue?.active_generation?.id ?? null;
      const queuedGenerationCount =
        previousQueue?.queued_generation_count ?? previousQueue?.queued_generations.length ?? 0;
      const optimisticGeneration = buildOptimisticGeneration(
        id,
        branchId,
        optimisticMessage.id,
        `optimistic-assistant-${crypto.randomUUID()}`,
        createdAt,
        queuedGenerationCount + 1,
      );
      const optimisticAssistantMessage = buildOptimisticAssistantMessage(
        id,
        branchId,
        optimisticGeneration.id,
        createdAt,
        queuedGenerationCount + 1,
      );
      optimisticGeneration.assistant_message_id = optimisticAssistantMessage.id;
      optimisticGeneration.steps = (optimisticGeneration.steps ?? []).map((step) => ({
        ...step,
        generation_id: optimisticGeneration.id,
        message_id: optimisticAssistantMessage.id,
      }));

      queryClient.setQueryData<SessionConversation | undefined>(
        ["conversation", id],
        (currentValue) => {
          const targetDetail = currentValue ?? previousDetail;
          if (!targetDetail) {
            return targetDetail;
          }

          const nextMessages =
            mergeSessionMessages({ ...targetDetail.session, messages: targetDetail.messages }, [
              optimisticMessage,
              optimisticAssistantMessage,
            ])?.messages ?? targetDetail.messages;

          return {
            ...targetDetail,
            session: {
              ...targetDetail.session,
              status: "running",
              updated_at: createdAt,
            },
            messages: nextMessages,
            generations: [...targetDetail.generations, optimisticGeneration],
            active_generation_id: targetDetail.active_generation_id ?? activeGenerationId,
            queued_generation_count:
              (targetDetail.queued_generation_count ?? queuedGenerationCount) + 1,
          };
        },
      );

      queryClient.setQueryData<SessionQueue | undefined>(["session-queue", id], (currentValue) => {
        const targetQueue = currentValue ?? previousQueue;
        if (!targetQueue) {
          return targetQueue;
        }

        return {
          ...targetQueue,
          session: {
            ...targetQueue.session,
            status: "running",
            updated_at: createdAt,
          },
          queued_generations: [...targetQueue.queued_generations, optimisticGeneration],
          active_generation_id: activeGenerationId,
          queued_generation_count: queuedGenerationCount + 1,
        };
      });

      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) => {
        const currentSession = currentValue?.find((session) => session.id === id);
        if (!currentSession) {
          return currentValue;
        }

        return upsertSession(currentValue, {
          ...currentSession,
          status: "running",
          updated_at: optimisticMessage.created_at,
        });
      });

      return {
        previousDetail,
        previousQueue,
        optimisticMessageId: optimisticMessage.id,
        optimisticAssistantMessageId: optimisticAssistantMessage.id,
        optimisticGenerationId: optimisticGeneration.id,
      };
    },
    onSuccess: async (response, _variables, context) => {
      queryClient.setQueryData<SessionConversation | undefined>(
        ["conversation", response.session.id],
        (currentValue) => {
          const baseMessages = (currentValue?.messages ?? []).filter(
            (message) =>
              message.id !== context?.optimisticMessageId &&
              message.id !== context?.optimisticAssistantMessageId,
          );
          const nextMessages = [response.user_message, response.assistant_message];
          const updatedDetail = currentValue
            ? {
                ...currentValue,
                session: response.session,
                messages: baseMessages,
                generations: currentValue.generations.filter(
                  (generation) => generation.id !== context?.optimisticGenerationId,
                ),
                active_generation_id:
                  response.active_generation_id ?? currentValue.active_generation_id,
                queued_generation_count:
                  response.queued_generation_count ?? currentValue.queued_generation_count,
              }
            : undefined;
          if (!updatedDetail) {
            return updatedDetail;
          }
          const nextConversation = {
            ...updatedDetail,
            active_branch: response.branch ?? updatedDetail.active_branch,
            messages:
              mergeSessionMessages(
                { ...updatedDetail.session, messages: updatedDetail.messages },
                nextMessages,
              )?.messages ?? updatedDetail.messages,
          };

          return response.generation
            ? (mergeConversationGeneration(nextConversation, response.generation) ??
                nextConversation)
            : nextConversation;
        },
      );
      queryClient.setQueryData<SessionQueue | undefined>(
        ["session-queue", response.session.id],
        (currentValue) => {
          const targetQueue = currentValue ?? context?.previousQueue;
          if (!targetQueue) {
            return targetQueue;
          }

          const filteredQueued = targetQueue.queued_generations.filter(
            (generation) => generation.id !== context?.optimisticGenerationId,
          );
          const activeGenerationId =
            response.active_generation_id ?? targetQueue.active_generation_id ?? null;
          const nextQueuedGenerations = response.generation
            ? activeGenerationId === response.generation.id ||
              response.generation.status !== "queued"
              ? filteredQueued.filter((generation) => generation.id !== response.generation?.id)
              : [
                  ...filteredQueued.filter(
                    (generation) => generation.id !== response.generation?.id,
                  ),
                  response.generation,
                ]
            : filteredQueued;
          const nextActiveGeneration =
            response.generation && activeGenerationId === response.generation.id
              ? response.generation
              : targetQueue.active_generation?.id === context?.optimisticGenerationId
                ? null
                : targetQueue.active_generation;

          return {
            ...targetQueue,
            session: response.session,
            active_generation: nextActiveGeneration,
            queued_generations: nextQueuedGenerations,
            active_generation_id: activeGenerationId,
            queued_generation_count:
              response.queued_generation_count ?? nextQueuedGenerations.length,
          };
        },
      );
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, response.session),
      );
      await queryClient.invalidateQueries({ queryKey: ["session-queue", response.session.id] });
      await queryClient.invalidateQueries({ queryKey: ["runtime-status"] });
    },
    onError: (error, variables, context) => {
      const isCancelledError =
        error instanceof Error && /cancelled|stopped current generation/i.test(error.message);
      const previousDetail = context?.previousDetail;
      const currentDetail = queryClient.getQueryData<SessionConversation | undefined>([
        "conversation",
        variables.id,
      ]);
      const hasPersistedUserMessage = (currentDetail?.messages ?? []).some(
        (message) =>
          message.role === "user" &&
          !message.id.startsWith("optimistic-user-") &&
          message.content.trim() === variables.content.trim(),
      );

      if (hasPersistedUserMessage && context?.optimisticMessageId) {
        queryClient.setQueryData<SessionConversation | undefined>(
          ["conversation", variables.id],
          (detail) =>
            detail
              ? {
                  ...detail,
                  messages: detail.messages.filter(
                    (message) =>
                      message.id !== context.optimisticMessageId &&
                      message.id !== context.optimisticAssistantMessageId,
                  ),
                  generations: detail.generations.filter(
                    (generation) => generation.id !== context.optimisticGenerationId,
                  ),
                }
              : detail,
        );
      } else if (previousDetail) {
        queryClient.setQueryData<SessionConversation | undefined>(
          ["conversation", variables.id],
          previousDetail,
        );
        queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
          upsertSession(currentValue, previousDetail.session),
        );
      }

      if (context?.previousQueue) {
        queryClient.setQueryData<SessionQueue | undefined>(
          ["session-queue", variables.id],
          context.previousQueue,
        );
      }

      if (!isCancelledError) {
        appendEvent(variables.id, {
          id: crypto.randomUUID(),
          sessionId: variables.id,
          type: "assistant.trace",
          createdAt: new Date().toISOString(),
          summary: "模型请求失败。",
          payload: { status: "error", error: error instanceof Error ? error.message : "未知错误" },
        });
      }

      void queryClient.invalidateQueries({ queryKey: ["conversation", variables.id] });
      void queryClient.invalidateQueries({ queryKey: ["session-queue", variables.id] });
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const cancelGenerationMutation = useMutation({
    mutationFn: ({
      sessionId: targetSessionId,
      generationId,
    }: {
      sessionId: string;
      generationId: string;
    }) => cancelGeneration(targetSessionId, generationId),
    onSuccess: async (_generation, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
      ]);
    },
  });

  const editMessageMutation = useMutation({
    mutationFn: ({
      sessionId: targetSessionId,
      messageId,
      content,
    }: {
      sessionId: string;
      messageId: string;
      content: string;
    }) =>
      editSessionMessage(targetSessionId, messageId, {
        content,
        attachments: [],
        branch_id: activeConversation?.active_branch?.id ?? null,
      }),
    onSuccess: async (_response, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", variables.sessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
      ]);
    },
  });

  const regenerateMessageMutation = useMutation({
    mutationFn: ({
      sessionId: targetSessionId,
      messageId,
      branchId,
    }: {
      sessionId: string;
      messageId: string;
      branchId: string | null;
    }) =>
      regenerateSessionMessage(targetSessionId, messageId, {
        branch_id: branchId,
      }),
    onSuccess: async (_response, variables) => {
      await invalidateWorkflowViews(variables.sessionId, workflowRunId);
    },
  });

  const forkMessageMutation = useMutation({
    mutationFn: ({
      sessionId: targetSessionId,
      messageId,
    }: {
      sessionId: string;
      messageId: string;
    }) => forkSessionMessage(targetSessionId, messageId),
    onSuccess: async (_response, variables) => {
      await invalidateWorkflowViews(variables.sessionId, workflowRunId);
    },
  });

  const rollbackMessageMutation = useMutation({
    mutationFn: ({
      sessionId: targetSessionId,
      messageId,
      branchId,
    }: {
      sessionId: string;
      messageId: string;
      branchId: string | null;
    }) =>
      rollbackSessionMessage(targetSessionId, messageId, {
        branch_id: branchId,
      }),
    onSuccess: async (_response, variables) => {
      await invalidateWorkflowViews(variables.sessionId, workflowRunId);
    },
  });

  const invalidateWorkflowViews = useCallback(
    async (targetSessionId: string, targetRunId: string | null): Promise<void> => {
      const invalidations = [
        queryClient.invalidateQueries({ queryKey: ["conversation", targetSessionId] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", targetSessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
        queryClient.invalidateQueries({ queryKey: ["session", targetSessionId, "graph", "attack"] }),
      ];

      if (targetRunId) {
        invalidations.push(
          queryClient.invalidateQueries({ queryKey: ["workflow", targetRunId] }),
          queryClient.invalidateQueries({ queryKey: ["workflow", targetRunId, "graph", "attack"] }),
          queryClient.invalidateQueries({ queryKey: ["workflow", targetRunId, "export"] }),
          queryClient.invalidateQueries({ queryKey: ["workflow", targetRunId, "replay"] }),
        );
      }

      await Promise.all(invalidations);
    },
    [queryClient],
  );

  const startWorkflowMutation = useMutation({
    mutationFn: ({
      activeSessionId: targetSessionId,
      templateName,
    }: {
      activeSessionId: string;
      templateName: string | null;
    }) => startWorkflow({ session_id: targetSessionId, template_name: templateName }),
    onSuccess: async (workflow) => {
      setPinnedWorkflowRunId(workflow.id);
      queryClient.setQueryData<WorkflowRunDetail>(["workflow", workflow.id], workflow);
      await invalidateWorkflowViews(workflow.session_id, workflow.id);
    },
  });

  const advanceWorkflowMutation = useMutation({
    mutationFn: ({ runId, approve }: { runId: string; approve?: boolean }) =>
      advanceWorkflow(runId, approve ? { approve: true } : {}),
    onSuccess: async (workflow) => {
      setPinnedWorkflowRunId(workflow.id);
      queryClient.setQueryData<WorkflowRunDetail>(["workflow", workflow.id], workflow);
      await invalidateWorkflowViews(workflow.session_id, workflow.id);
    },
  });

  const latestEvent = sessionEvents[sessionEvents.length - 1] ?? null;

  useEffect(() => {
    if (!activeSessionId || !latestEvent) {
      return;
    }

    const eventType = latestEvent.type;
    const shouldRefresh =
      eventType.startsWith("workflow.") ||
      eventType.startsWith("task.") ||
      eventType.startsWith("graph.") ||
      eventType === "message.created" ||
      eventType === "message.updated" ||
      eventType === "message.completed" ||
      eventType === "generation.started" ||
      eventType === "generation.cancelled" ||
      eventType === "generation.failed" ||
      eventType === "assistant.summary" ||
      eventType === "assistant.trace" ||
      eventType.startsWith("tool.call.") ||
      eventType === "session.updated";

    if (!shouldRefresh) {
      return;
    }

    const refreshTimer = window.setTimeout(() => {
      void invalidateWorkflowViews(activeSessionId, workflowRunId);
    }, 180);

    return () => {
      window.clearTimeout(refreshTimer);
    };
  }, [activeSessionId, invalidateWorkflowViews, latestEvent, workflowRunId]);

  const attackGraph = runAttackGraphQuery.data ?? sessionAttackGraphQuery.data;
  const activeConversation = conversationQuery.data ?? null;

  const workflowNeedsApproval =
    workflowQuery.data?.status === "needs_approval" ||
    workflowQuery.data?.state.approval?.required === true;

  const canAdvanceWorkflow =
    Boolean(workflowRunId) &&
    workflowQuery.data?.status !== "done" &&
    workflowQuery.data?.status !== "error";
  const activeGeneration = sessionQueueQuery.data?.active_generation ?? null;
  const queuedGenerationCount =
    sessionQueueQuery.data?.queued_generation_count ??
    sessionQueueQuery.data?.queued_generations.length ??
    activeConversation?.queued_generation_count ??
    0;
  const isGenerationActive =
    activeGeneration !== null ||
    Boolean(
      sessionQueueQuery.data?.active_generation_id ?? activeConversation?.active_generation_id,
    ) ||
    queuedGenerationCount > 0;

  function handleToggleSidebarCollapsed(): void {
    setIsSidebarCollapsed((currentValue) => !currentValue);
  }

  function handleSelectSession(nextSessionId: string): void {
    suppressRouteAutonavigateRef.current = false;
    setInvalidSessionState(null);
    navigate(`/sessions/${nextSessionId}/chat`);
  }

  async function handleRenameSession(targetSessionId: string): Promise<void> {
    const targetSession = sortedSessions.find((session) => session.id === targetSessionId);
    if (!targetSession) {
      return;
    }

    const nextTitle = window.prompt("修改对话标题", targetSession.title);
    if (nextTitle === null) {
      return;
    }

    const trimmed = nextTitle.trim();
    if (!trimmed || trimmed === targetSession.title) {
      return;
    }

    await renameSessionMutation.mutateAsync({ id: targetSessionId, title: trimmed });
  }

  function handleSelectNode(nodeId: string): void {
    setIsInsightsOpen(true);
    setSelectedAttackNodeId(nodeId);
  }

  async function handleEditAttackNode(node: SessionGraphNode): Promise<void> {
    if (!activeSession) {
      return;
    }

    const sourceMessageId = readString(node.data.source_message_id);
    if (!sourceMessageId) {
      return;
    }

    const initialContent =
      readString(node.data.message_content) ?? readString(node.data.summary) ?? node.label;
    const nextContent = window.prompt("编辑节点对应的对话内容", initialContent);
    if (nextContent === null) {
      return;
    }

    const trimmed = nextContent.trim();
    if (!trimmed) {
      return;
    }

    setMessageActionBusyId(sourceMessageId);
    try {
      await editMessageMutation.mutateAsync({
        sessionId: activeSession.id,
        messageId: sourceMessageId,
        content: trimmed,
      });
      await invalidateWorkflowViews(activeSession.id, workflowRunId);
    } finally {
      setMessageActionBusyId((currentValue) =>
        currentValue === sourceMessageId ? null : currentValue,
      );
    }
  }

  async function handleRegenerateAttackNode(node: SessionGraphNode): Promise<void> {
    if (!activeSession) {
      return;
    }

    const sourceMessageId = readString(node.data.source_message_id);
    if (!sourceMessageId) {
      return;
    }

    setMessageActionBusyId(sourceMessageId);
    try {
      await regenerateMessageMutation.mutateAsync({
        sessionId: activeSession.id,
        messageId: sourceMessageId,
        branchId: readString(node.data.branch_id) ?? activeConversation?.active_branch?.id ?? null,
      });
    } finally {
      setMessageActionBusyId((currentValue) =>
        currentValue === sourceMessageId ? null : currentValue,
      );
    }
  }

  async function handleForkAttackNode(node: SessionGraphNode): Promise<void> {
    if (!activeSession) {
      return;
    }

    const sourceMessageId = readString(node.data.source_message_id);
    if (!sourceMessageId) {
      return;
    }

    setMessageActionBusyId(sourceMessageId);
    try {
      await forkMessageMutation.mutateAsync({
        sessionId: activeSession.id,
        messageId: sourceMessageId,
      });
    } finally {
      setMessageActionBusyId((currentValue) =>
        currentValue === sourceMessageId ? null : currentValue,
      );
    }
  }

  async function handleRollbackAttackNode(node: SessionGraphNode): Promise<void> {
    if (!activeSession) {
      return;
    }

    const sourceMessageId = readString(node.data.source_message_id);
    if (!sourceMessageId) {
      return;
    }

    setMessageActionBusyId(sourceMessageId);
    try {
      await rollbackMessageMutation.mutateAsync({
        sessionId: activeSession.id,
        messageId: sourceMessageId,
        branchId: readString(node.data.branch_id) ?? activeConversation?.active_branch?.id ?? null,
      });
    } finally {
      setMessageActionBusyId((currentValue) =>
        currentValue === sourceMessageId ? null : currentValue,
      );
    }
  }

  if (sessionsQuery.isLoading && sessionsQuery.data === undefined && !activeSession) {
    return (
      <main className="conversation-workbench">
        <section className="conversation-main-shell">
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">正在加载 Workspace</p>
            <p className="conversation-empty-state-copy">稍后即可查看当前会话与聊天状态。</p>
          </section>
        </section>
      </main>
    );
  }

  return (
    <main
      className={`conversation-workbench${isSidebarCollapsed ? " conversation-workbench-sidebar-collapsed" : ""}`}
    >
      <ConversationSidebar
        sessions={sidebarSessions}
        activeSessionId={activeSessionId}
        collapsed={isSidebarCollapsed}
        isCreating={createSessionMutation.isPending}
        onCreate={async () => {
          await createSessionMutation.mutateAsync();
        }}
        onToggleCollapsed={handleToggleSidebarCollapsed}
        onSelect={handleSelectSession}
        onRename={handleRenameSession}
        onArchive={async (id) => {
          await deleteSessionMutation.mutateAsync(id);
        }}
        onRestore={async (id) => {
          await restoreArchivedSessionMutation.mutateAsync(id);
        }}
      />

      <section
        className={`conversation-main-shell workspace-session-shell${activeSession ? " workspace-session-shell-drawer-active" : ""}`}
      >
        {sessionsQuery.isError ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">对话列表暂不可用</p>
            <p className="conversation-empty-state-copy">{sessionsQuery.error.message}</p>
          </section>
        ) : !activeSession ? (
          invalidSessionState ? (
            <section className="conversation-empty-state workspace-empty-state-card">
              <p className="conversation-empty-state-title">对话不存在或已失效</p>
              <p className="conversation-empty-state-copy">{invalidSessionState.message}</p>
              <div className="management-action-row">
                <button
                  className="button button-secondary"
                  type="button"
                  onClick={() => {
                    suppressRouteAutonavigateRef.current = true;
                    setInvalidSessionState(null);
                    navigate("/sessions", { replace: true });
                  }}
                >
                  返回对话列表
                </button>
              </div>
            </section>
          ) : (
            <section className="conversation-empty-state workspace-empty-state-card">
              <p className="conversation-empty-state-title">还没有 Workspace</p>
              <p className="conversation-empty-state-copy">
                新建一个对话后，这里会进入聊天主视图，并按需展开执行进度。
              </p>
              <div className="management-action-row">
                <button
                  className="button button-primary"
                  type="button"
                  onClick={() => void createSessionMutation.mutateAsync()}
                >
                  新建对话
                </button>
              </div>
            </section>
          )
        ) : activeSession && conversationQuery.isLoading && !activeConversation ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">正在打开对话</p>
            <p className="conversation-empty-state-copy">消息与工作流状态正在同步。</p>
          </section>
        ) : activeSession && activeConversation ? (
          <>
            <header className="conversation-header workspace-session-header">
              <div className="conversation-header-copy">
                <h2 className="conversation-title">
                  {getSessionDisplayTitle(activeSession.title)}
                </h2>
              </div>

              <div className="conversation-header-actions workspace-session-header-actions">
                {(templatesQuery.data?.length ?? 0) > 0 ? (
                  <label className="workspace-inline-field" aria-label="选择工作流模板">
                    <select
                      className="field-input"
                      value={selectedTemplateName}
                      onChange={(event) => setSelectedTemplateName(event.target.value)}
                    >
                      {(templatesQuery.data ?? []).map((template) => (
                        <option key={template.name} value={template.name}>
                          {template.title}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={startWorkflowMutation.isPending}
                  onClick={() => {
                    void startWorkflowMutation.mutateAsync({
                      activeSessionId: activeSession.id,
                      templateName: selectedTemplateName || null,
                    });
                  }}
                >
                  {startWorkflowMutation.isPending ? "启动中" : workflowRunId ? "重新启动" : "启动工作流"}
                </button>
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={!canAdvanceWorkflow || workflowNeedsApproval || advanceWorkflowMutation.isPending}
                  onClick={() => {
                    if (!workflowRunId) {
                      return;
                    }
                    void advanceWorkflowMutation.mutateAsync({ runId: workflowRunId });
                  }}
                >
                  {advanceWorkflowMutation.isPending ? "推进中" : "推进下一步"}
                </button>
                {workflowNeedsApproval ? (
                  <>
                    <button
                      className="button button-secondary"
                      type="button"
                      disabled={!workflowRunId || advanceWorkflowMutation.isPending}
                      onClick={() => {
                        if (!workflowRunId) {
                          return;
                        }
                        void advanceWorkflowMutation.mutateAsync({ runId: workflowRunId, approve: true });
                      }}
                    >
                      批准继续
                    </button>
                    <button
                      className="button button-secondary"
                      type="button"
                      disabled={pauseSessionMutation.isPending}
                      onClick={() => {
                        void pauseSessionMutation.mutateAsync({ id: activeSession.id });
                      }}
                    >
                      暂停
                    </button>
                  </>
                ) : null}
                {workflowReplayQuery.data ? (
                  <button
                    className="text-button"
                    type="button"
                    onClick={() => {
                      downloadJson(`session-${activeSession.id}-replay.json`, workflowReplayQuery.data);
                    }}
                  >
                    回放
                  </button>
                ) : null}
                {workflowExportQuery.data ? (
                  <button
                    className="text-button"
                    type="button"
                    onClick={() => {
                      downloadJson(`session-${activeSession.id}-export.json`, workflowExportQuery.data);
                    }}
                  >
                    导出
                  </button>
                ) : null}
                <span className="management-status-badge tone-neutral">
                  {formatWorkflowStatus(workflowQuery.data?.status ?? activeSession.status)}
                </span>
                <span className={`connection-pill connection-${connectionState}`}>
                  {getConnectionTone(connectionState)}
                </span>
              </div>
            </header>

            {activeSession.deleted_at ? (
              <section className="conversation-inline-notice">
                对话已归档，仍可查看当前消息与执行摘要。
              </section>
            ) : null}

            <section className="workspace-session-grid workspace-session-grid-single">
              <section className="workspace-session-center-column">
                <section className="workspace-message-panel">
                  <ConversationFeed
                    messages={activeConversation.messages}
                    generations={activeConversation.generations}
                    events={sessionEvents}
                    runtimeRuns={sessionRuns}
                    activeGeneration={activeGeneration}
                    queuedGenerations={sessionQueueQuery.data?.queued_generations ?? []}
                    messageActionBusyId={messageActionBusyId}
                    cancelGenerationBusy={cancelGenerationMutation.isPending}
                    onCancelGeneration={(generationId) => {
                      void cancelGenerationMutation.mutateAsync({
                        sessionId: activeSession.id,
                        generationId,
                      });
                    }}
                    onEditMessage={async (message, content) => {
                      setMessageActionBusyId(message.id);
                      try {
                        await editMessageMutation.mutateAsync({
                          sessionId: activeSession.id,
                          messageId: message.id,
                          content,
                        });
                      } finally {
                        setMessageActionBusyId((currentValue) =>
                          currentValue === message.id ? null : currentValue,
                        );
                      }
                    }}
                  />
                  <WorkbenchComposer
                    sessionId={activeSession.id}
                    disabled={activeSession.deleted_at !== null}
                    isGenerating={isGenerationActive}
                    isInterrupting={
                      cancelGenerationMutation.isPending || cancelSessionMutation.isPending
                    }
                    queuedCount={queuedGenerationCount}
                    onSend={async (content) => {
                      await sendChatMutation.mutateAsync({ id: activeSession.id, content });
                    }}
                    onInterrupt={async () => {
                      if (activeGeneration) {
                        await cancelGenerationMutation.mutateAsync({
                          sessionId: activeSession.id,
                          generationId: activeGeneration.id,
                        });
                        return;
                      }
                      await cancelSessionMutation.mutateAsync({ id: activeSession.id });
                    }}
                  />
                </section>
              </section>

              <aside
                className={`workspace-graph-drawer${isInsightsOpen ? " workspace-graph-drawer-open" : ""}`}
              >
                <button
                  className="workspace-graph-drawer-handle"
                  type="button"
                  onClick={() => setIsInsightsOpen((currentValue) => !currentValue)}
                  aria-expanded={isInsightsOpen}
                  aria-label={isInsightsOpen ? "收起攻击图抽屉" : "展开攻击图抽屉"}
                >
                  <span className="workspace-graph-drawer-handle-indicator" aria-hidden="true" />
                </button>

                <section className="management-section-card workspace-graph-drawer-panel">
                  <div className="workspace-graph-drawer-panel-header">
                    <div>
                      <strong className="workspace-graph-drawer-title">攻击图工作台</strong>
                      <p className="workspace-graph-drawer-copy">
                        统一查看攻击路径、推进工作流，并直接回到对应会话消息。
                      </p>
                    </div>
                    <button
                      className="workspace-graph-drawer-close"
                      type="button"
                      onClick={() => setIsInsightsOpen(false)}
                      aria-label="关闭攻击图抽屉"
                    >
                      关闭
                    </button>
                  </div>

                  <div className="workspace-graph-drawer-body">
                    <AttackGraphWorkbench
                      graph={attackGraph}
                      tasks={workflowQuery.data?.tasks ?? []}
                      replay={workflowReplayQuery.data}
                      selectedNodeId={selectedAttackNodeId}
                      actionBusyId={messageActionBusyId}
                      onSelectNode={handleSelectNode}
                      onEditNode={handleEditAttackNode}
                      onRegenerateNode={handleRegenerateAttackNode}
                      onForkNode={handleForkAttackNode}
                      onRollbackNode={handleRollbackAttackNode}
                    />
                  </div>
                </section>
              </aside>
              
            </section>
          </>
        ) : conversationQuery.isError ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">对话详情暂不可用</p>
            <p className="conversation-empty-state-copy">{conversationQuery.error.message}</p>
          </section>
        ) : null}
      </section>
    </main>
  );
}
