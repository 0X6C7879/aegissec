import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  compactSessionContext,
  isApiError,
  forkSessionMessage,
  getAttackGraph,
  cancelGeneration,
  cancelSession,
  createSession,
  deleteSession,
  editSessionMessage,
  getRuntimeStatus,
  getSessionConversation,
  getSessionContextWindowUsage,
  getSessionQueue,
  getSessionSlashCatalog,
  injectActiveGenerationContext,
  listSessions,
  regenerateSessionMessage,
  rollbackSessionMessage,
  updateSession,
  sendChatMessage,
} from "../lib/api";
import { useSessionEvents } from "../hooks/useSessionEvents";
import { useWorkspaceSplitPane } from "../hooks/useWorkspaceSplitPane";
import {
  mergeConversationGeneration,
  mergeSessionMessages,
  sortSessions,
  upsertSession,
} from "../lib/sessionUtils";
import { generateClientId } from "../lib/uuid";
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
import type { SlashAction } from "../types/slash";
import { AttackGraphWorkbench } from "./AttackGraphWorkbench";
import { ConversationFeed, type ShellFocusPayload } from "./ConversationFeed";
import { ConversationSidebar } from "./ConversationSidebar";
import { ShellWorkbench } from "./runtime/ShellWorkbench";
import { WorkbenchComposer } from "./WorkbenchComposer";

type InvalidSessionState = {
  sessionId: string;
  message: string;
};

type WorkspaceShellFocusRequest = ShellFocusPayload & {
  requestId: number;
};

const EMPTY_SESSION_EVENTS: ReturnType<typeof useUiStore.getState>["eventsBySession"][string] = [];
const WORKSPACE_SIDEBAR_STORAGE_KEY = "aegissec.workspace.sidebar.collapsed.v1";

function getStoredWorkspaceSidebarState(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  return window.localStorage.getItem(WORKSPACE_SIDEBAR_STORAGE_KEY) === "true";
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

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function buildOptimisticUserMessage(
  sessionId: string,
  content: string,
  slashAction: SlashAction | null = null,
): SessionMessage {
  return {
    id: `optimistic-user-${generateClientId()}`,
    session_id: sessionId,
    role: "user" as const,
    content,
    metadata: slashAction
      ? {
          slash_action: slashAction,
        }
      : undefined,
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
    id: `optimistic-assistant-${generateClientId()}`,
    session_id: sessionId,
    branch_id: branchId,
    generation_id: generationId,
    role: "assistant",
    status: "queued",
    message_kind: "message",
    content: "",
    assistant_transcript: [
      {
        id: `optimistic-transcript-${generateClientId()}`,
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
    id: `optimistic-step-${generateClientId()}`,
    generation_id: `optimistic-generation-${generateClientId()}`,
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

export function SessionWorkspaceWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { sessionId: routeSessionIdParam } = useParams<{ sessionId?: string }>();
  const routeSessionId = routeSessionIdParam ?? null;
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState<boolean>(() =>
    getStoredWorkspaceSidebarState(),
  );
  const [selectedAttackNodeId, setSelectedAttackNodeId] = useState<string | null>(null);
  const [messageActionBusyId, setMessageActionBusyId] = useState<string | null>(null);
  const [isShellFocusPanelOpen, setIsShellFocusPanelOpen] = useState(false);
  const [shellFocusRequest, setShellFocusRequest] = useState<WorkspaceShellFocusRequest | null>(
    null,
  );
  const [invalidSessionState, setInvalidSessionState] = useState<InvalidSessionState | null>(null);
  const suppressRouteAutonavigateRef = useRef(false);
  const workspaceSplitPane = useWorkspaceSplitPane({
    controlledPaneId: "workspace-chat-panel",
  });

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
    queryFn: ({ signal }) => listSessions(false, signal),
    placeholderData: (previousValue) => previousValue,
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

    return sortedSessions[0]?.id ?? null;
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

  const slashCatalogQuery = useQuery({
    enabled: activeSessionId !== null,
    queryKey: ["session", activeSessionId, "slash-catalog"],
    queryFn: ({ signal }) => getSessionSlashCatalog(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
  });
  const slashCatalog = useMemo(() => {
    const seenIds = new Set<string>();
    const seenTriggers = new Set<string>();

    return (slashCatalogQuery.data ?? []).filter((item) => {
      const normalizedTrigger = item.trigger.trim().toLowerCase();
      if (seenIds.has(item.id) || seenTriggers.has(normalizedTrigger)) {
        return false;
      }

      seenIds.add(item.id);
      seenTriggers.add(normalizedTrigger);
      return true;
    });
  }, [slashCatalogQuery.data]);

  const sidebarSessions = useMemo(() => sortedSessions, [sortedSessions]);

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

  const contextWindowUsageQuery = useQuery({
    enabled: Boolean(activeSessionId),
    queryKey: ["session-context-window", activeSessionId],
    queryFn: ({ signal }) => getSessionContextWindowUsage(activeSessionId!, signal),
    placeholderData: (previousValue) => previousValue,
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
  }, [conversationQuery.error, sessionQueueQuery.error, sessionAttackGraphQuery.error]);

  useEffect(() => {
    if (!routeSessionId || !staleSessionNotFoundMessage) {
      return;
    }

    invalidateStaleSessionSelection(routeSessionId, staleSessionNotFoundMessage);
  }, [invalidateStaleSessionSelection, routeSessionId, staleSessionNotFoundMessage]);

  useEffect(() => {
    if (sessionAttackGraphQuery.data?.nodes.length) {
      return;
    }

    setSelectedAttackNodeId(null);
  }, [sessionAttackGraphQuery.data?.nodes]);

  useSessionEvents(activeSessionId);
  const sessionRuns = useMemo(
    () =>
      (runtimeStatusQuery.data?.recent_runs ?? []).filter(
        (run) => run.session_id === activeSessionId,
      ),
    [activeSessionId, runtimeStatusQuery.data?.recent_runs],
  );

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
      const queryKeysToClear: Array<readonly unknown[]> = [
        ["conversation", deletedId],
        ["session-queue", deletedId],
        ["session-context-window", deletedId],
        ["session", deletedId],
        ["session", deletedId, "graph", "attack"],
        ["session", deletedId, "slash-catalog"],
      ];
      const clearDeletedSessionQueries = (): void => {
        for (const queryKey of queryKeysToClear) {
          queryClient.setQueryData(queryKey, undefined);
          queryClient.removeQueries({ queryKey, exact: true });
        }
      };

      await Promise.all(
        queryKeysToClear.map((queryKey) =>
          queryClient.cancelQueries({ queryKey, exact: true }),
        ),
      );
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        currentValue?.filter((session) => session.id !== deletedId),
      );
      clearDeletedSessionQueries();
      useUiStore.setState((state) => {
        const nextDraftsBySession = { ...state.draftsBySession };
        const nextEventsBySession = { ...state.eventsBySession };
        const nextLastCursorBySession = { ...state.lastServerCursorBySession };
        delete nextDraftsBySession[deletedId];
        delete nextEventsBySession[deletedId];
        delete nextLastCursorBySession[deletedId];
        return {
          draftsBySession: nextDraftsBySession,
          eventsBySession: nextEventsBySession,
          lastServerCursorBySession: nextLastCursorBySession,
          lastVisitedSessionId:
            state.lastVisitedSessionId === deletedId ? null : state.lastVisitedSessionId,
        };
      });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (routeSessionId === deletedId) {
        setSelectedAttackNodeId(null);
        setInvalidSessionState(null);
        setLastVisitedSessionId(null);
        suppressRouteAutonavigateRef.current = true;
        navigate("/sessions");
      }
      clearDeletedSessionQueries();
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
        id: generateClientId(),
        sessionId: variables.id,
        type: "assistant.trace",
        createdAt: new Date().toISOString(),
        summary: "停止当前回复失败。",
        payload: { status: "error", error: error instanceof Error ? error.message : "未知错误" },
      });
    },
  });

  const compactSessionContextMutation = useMutation({
    mutationFn: ({ id }: { id: string }) => compactSessionContext(id),
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["session-context-window", variables.id] }),
        queryClient.invalidateQueries({ queryKey: ["conversation", variables.id] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", variables.id] }),
      ]);
    },
  });

  const sendChatMutation = useMutation({
    mutationFn: ({
      id,
      content,
      slashAction,
    }: {
      id: string;
      content: string;
      slashAction?: SlashAction | null;
    }) =>
      sendChatMessage(id, {
        content,
        slash_action: slashAction ?? undefined,
        attachments: [],
        branch_id: activeConversation?.active_branch?.id ?? null,
      }),
    onMutate: async ({ id, content, slashAction }) => {
      await queryClient.cancelQueries({ queryKey: ["conversation", id] });
      const previousDetail = queryClient.getQueryData<SessionConversation | undefined>([
        "conversation",
        id,
      ]);
      const previousQueue = queryClient.getQueryData<SessionQueue | undefined>([
        "session-queue",
        id,
      ]);
      const optimisticMessage = buildOptimisticUserMessage(id, content, slashAction ?? null);
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
        `optimistic-assistant-${generateClientId()}`,
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
          id: generateClientId(),
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

  const injectActiveGenerationMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      injectActiveGenerationContext(id, { content }),
    onSuccess: async (_response, variables) => {
      await Promise.all([
        invalidatePrimaryViews(variables.id),
        queryClient.invalidateQueries({ queryKey: ["runtime-status"] }),
      ]);
    },
    onError: (error, variables) => {
      appendEvent(variables.id, {
        id: generateClientId(),
        sessionId: variables.id,
        type: "assistant.trace",
        createdAt: new Date().toISOString(),
        summary: "补充上下文失败。",
        payload: { status: "error", error: error instanceof Error ? error.message : "未知错误" },
      });

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
      await invalidatePrimaryViews(variables.sessionId);
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
      await invalidatePrimaryViews(variables.sessionId);
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
      await invalidatePrimaryViews(variables.sessionId);
    },
  });

  const invalidatePrimaryViews = useCallback(
    async (targetSessionId: string): Promise<void> => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", targetSessionId] }),
        queryClient.invalidateQueries({ queryKey: ["session-queue", targetSessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
        queryClient.invalidateQueries({
          queryKey: ["session", targetSessionId, "graph", "attack"],
        }),
      ]);
    },
    [queryClient],
  );

  const latestEvent = sessionEvents[sessionEvents.length - 1] ?? null;

  useEffect(() => {
    if (!activeSessionId || !latestEvent) {
      return;
    }

    const eventType = latestEvent.type;
    const shouldRefresh =
      eventType.startsWith("graph.") ||
      eventType === "message.created" ||
      eventType === "message.updated" ||
      eventType === "message.delta" ||
      eventType === "message.completed" ||
      eventType === "generation.started" ||
      eventType === "generation.cancelled" ||
      eventType === "generation.failed" ||
      eventType === "assistant.summary" ||
      eventType === "assistant.trace" ||
      eventType === "session.compaction.completed" ||
      eventType.startsWith("tool.call.") ||
      eventType === "session.updated";

    if (!shouldRefresh) {
      return;
    }

    const refreshTimer = window.setTimeout(() => {
      void invalidatePrimaryViews(activeSessionId);
    }, 180);

    return () => {
      window.clearTimeout(refreshTimer);
    };
  }, [activeSessionId, invalidatePrimaryViews, latestEvent]);

  const attackGraph = sessionAttackGraphQuery.data;
  const activeConversation = conversationQuery.data ?? null;
  const activeGeneration = sessionQueueQuery.data?.active_generation ?? null;
  const activeGenerationId =
    sessionQueueQuery.data?.active_generation_id ??
    activeConversation?.active_generation_id ??
    null;
  const queuedGenerationCount =
    sessionQueueQuery.data?.queued_generation_count ??
    sessionQueueQuery.data?.queued_generations.length ??
    activeConversation?.queued_generation_count ??
    0;
  const isPausedGeneration =
    activeConversation?.session.status === "paused" ||
    sessionQueueQuery.data?.session.status === "paused";
  const isInjectableGenerationActive = activeGeneration !== null || Boolean(activeGenerationId);

  function handleToggleSidebarCollapsed(): void {
    setIsSidebarCollapsed((currentValue) => !currentValue);
  }

  function handleSelectSession(nextSessionId: string): void {
    suppressRouteAutonavigateRef.current = false;
    setInvalidSessionState(null);
    navigate(`/sessions/${nextSessionId}/chat`);
  }

  const handleLocalSlashAction = useCallback(
    async (action: SlashAction): Promise<boolean> => {
      switch (action.id) {
        case "builtin:goto-skills":
          navigate("/skills");
          return true;
        case "builtin:goto-mcp":
          navigate("/mcp");
          return true;
        case "builtin:goto-runtime":
          navigate("/runtime");
          return true;
        default:
          return false;
      }
    },
    [navigate],
  );

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

  function handleSelectNode(nodeId: string | null): void {
    setSelectedAttackNodeId(nodeId);
  }

  const handleFocusShellFromConversation = useCallback((payload: ShellFocusPayload): void => {
    setIsShellFocusPanelOpen(true);
    setShellFocusRequest((currentValue) => ({
      ...payload,
      requestId: (currentValue?.requestId ?? 0) + 1,
    }));
  }, []);

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
      await invalidatePrimaryViews(activeSession.id);
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

  useEffect(() => {
    setIsShellFocusPanelOpen(false);
    setShellFocusRequest(null);
  }, [activeSessionId]);

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
        onDelete={async (id) => {
          await deleteSessionMutation.mutateAsync(id);
        }}
      />

      <section className="conversation-main-shell workspace-session-shell workspace-session-shell-terminal">
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
            <p className="conversation-empty-state-copy">消息与攻击路径数据正在同步。</p>
          </section>
        ) : activeSession && activeConversation ? (
          <>
            <section
              ref={workspaceSplitPane.containerRef}
              className={`workspace-session-grid workspace-session-grid-attack-main${workspaceSplitPane.isEnabled ? " workspace-session-grid-resizable" : ""}${workspaceSplitPane.isDragging ? " workspace-session-grid-resizing" : ""}`}
              style={workspaceSplitPane.gridStyle}
            >
              <section className="workspace-graph-main-column workspace-stage-panel">
                <section className="workspace-graph-main-shell">
                  <AttackGraphWorkbench
                    graph={attackGraph}
                    selectedNodeId={selectedAttackNodeId}
                    actionBusyId={messageActionBusyId}
                    onSelectNode={handleSelectNode}
                    onEditNode={handleEditAttackNode}
                    onRegenerateNode={handleRegenerateAttackNode}
                    onForkNode={handleForkAttackNode}
                    onRollbackNode={handleRollbackAttackNode}
                  />
                </section>
                {isShellFocusPanelOpen ? (
                  <section
                    className="workspace-shell-focus-panel-shell"
                    data-testid="workspace-shell-focus-panel"
                  >
                    <ShellWorkbench
                      sessionId={activeSession.id}
                      disabled={false}
                      variant="focus-docked"
                      focusRequest={shellFocusRequest}
                      onDismiss={() => setIsShellFocusPanelOpen(false)}
                    />
                  </section>
                ) : null}
              </section>

              {workspaceSplitPane.isEnabled ? (
                <div
                  className={`workspace-split-pane-separator${workspaceSplitPane.isDragging ? " workspace-split-pane-separator-active" : ""}`}
                  data-testid="workspace-split-pane-separator"
                  {...workspaceSplitPane.separatorProps}
                />
              ) : null}

              <section
                className="workspace-session-side-column workspace-session-side-column-transcript"
                id="workspace-chat-panel"
              >
                <section className="workspace-message-panel workspace-terminal-panel">
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
                    onFocusShell={handleFocusShellFromConversation}
                  />
                  <WorkbenchComposer
                    sessionId={activeSession.id}
                    slashCatalog={slashCatalog}
                    disabled={false}
                    isActiveGeneration={isInjectableGenerationActive || isPausedGeneration}
                    isPausedGeneration={isPausedGeneration}
                    isInterrupting={
                      cancelGenerationMutation.isPending || cancelSessionMutation.isPending
                    }
                    queuedCount={queuedGenerationCount}
                    contextUsage={contextWindowUsageQuery.data ?? null}
                    contextUsageLoading={contextWindowUsageQuery.isLoading}
                    contextCompacting={compactSessionContextMutation.isPending}
                    onQueueSend={async ({ content, slashAction }) => {
                      await sendChatMutation.mutateAsync({
                        id: activeSession.id,
                        content,
                        slashAction,
                      });
                    }}
                    onInject={async (content) => {
                      await injectActiveGenerationMutation.mutateAsync({
                        id: activeSession.id,
                        content,
                        });
                    }}
                    onLocalSlashAction={handleLocalSlashAction}
                    onManualCompact={async () => {
                      await compactSessionContextMutation.mutateAsync({ id: activeSession.id });
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
