import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  createSession,
  deleteSession,
  getRuntimeStatus,
  getSession,
  listSessions,
  restoreSession,
  sendChatMessage,
  updateSession,
} from "../lib/api";
import { useSessionEvents } from "../hooks/useSessionEvents";
import { mergeSessionMessages, sortSessions, upsertSession } from "../lib/sessionUtils";
import { useUiStore } from "../store/uiStore";
import type { SessionDetail, SessionEventEntry, SessionSummary } from "../types/sessions";
import { ConversationFeed } from "./ConversationFeed";
import { ConversationSidebar } from "./ConversationSidebar";
import { WorkbenchComposer } from "./WorkbenchComposer";

const EMPTY_SESSION_EVENTS: SessionEventEntry[] = [];
const CONVERSATION_SIDEBAR_STORAGE_KEY = "aegissec.conversation.sidebar.collapsed.v2";

function getStoredConversationSidebarState(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  return window.localStorage.getItem(CONVERSATION_SIDEBAR_STORAGE_KEY) === "true";
}

function buildOptimisticUserMessage(sessionId: string, content: string) {
  return {
    id: `optimistic-user-${crypto.randomUUID()}`,
    session_id: sessionId,
    role: "user" as const,
    content,
    attachments: [],
    created_at: new Date().toISOString(),
  };
}

function getSessionDisplayTitle(title: string): string {
  return title === "New Session" ? "新对话" : title;
}

function visibleSessionsForSidebar(sessions: SessionSummary[], activeSessionId: string | null): SessionSummary[] {
  return sessions.filter((session) => !session.deleted_at || session.id === activeSessionId);
}

function getConnectionTone(state: string): string {
  return state === "open" ? "在线" : state === "connecting" ? "连接中" : "离线";
}

export function ConversationWorkbench() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState<boolean>(() =>
    getStoredConversationSidebarState(),
  );
  const lastSidebarToggleAtRef = useRef(0);
  const lastVisitedSessionId = useUiStore((state) => state.lastVisitedSessionId);
  const setLastVisitedSessionId = useUiStore((state) => state.setLastVisitedSessionId);
  const appendEvent = useUiStore((state) => state.appendEvent);
  const sessionEvents = useUiStore((state) =>
    sessionId ? state.eventsBySession[sessionId] ?? EMPTY_SESSION_EVENTS : EMPTY_SESSION_EVENTS,
  );

  const sessionsQuery = useQuery({
    queryKey: ["sessions", "merged-workbench"],
    queryFn: ({ signal }) => listSessions(true, signal),
  });

  const sortedSessions = useMemo(() => sortSessions(sessionsQuery.data ?? []), [sessionsQuery.data]);
  const activeSessionId = useMemo(() => {
    if (sessionId) {
      return sessionId;
    }

    if (lastVisitedSessionId && sortedSessions.some((session) => session.id === lastVisitedSessionId)) {
      return lastVisitedSessionId;
    }

    return sortedSessions.find((session) => !session.deleted_at)?.id ?? sortedSessions[0]?.id ?? null;
  }, [lastVisitedSessionId, sessionId, sortedSessions]);

  const activeSession = useMemo(
    () => sortedSessions.find((session) => session.id === activeSessionId) ?? null,
    [activeSessionId, sortedSessions],
  );
  const sidebarSessions = useMemo(
    () => visibleSessionsForSidebar(sortedSessions, activeSessionId),
    [activeSessionId, sortedSessions],
  );

  useEffect(() => {
    window.localStorage.setItem(CONVERSATION_SIDEBAR_STORAGE_KEY, String(isSidebarCollapsed));
  }, [isSidebarCollapsed]);

  function handleToggleSidebarCollapsed(): void {
    const now = performance.now();
    if (now - lastSidebarToggleAtRef.current < 200) {
      return;
    }

    lastSidebarToggleAtRef.current = now;
    setIsSidebarCollapsed((currentValue) => {
      const nextValue = !currentValue;
      window.localStorage.setItem(CONVERSATION_SIDEBAR_STORAGE_KEY, String(nextValue));
      return nextValue;
    });
  }

  useEffect(() => {
    if (!sessionId && activeSessionId) {
      navigate(`/sessions/${activeSessionId}/chat`, { replace: true });
    }
  }, [activeSessionId, navigate, sessionId]);

  useEffect(() => {
    if (activeSessionId) {
      setLastVisitedSessionId(activeSessionId);
    }
  }, [activeSessionId, setLastVisitedSessionId]);

  const sessionDetailQuery = useQuery({
    enabled: Boolean(activeSessionId),
    queryKey: ["session", activeSessionId],
    queryFn: ({ signal }) => getSession(activeSessionId!, signal),
  });

  const runtimeStatusQuery = useQuery({
    queryKey: ["runtime-status"],
    queryFn: ({ signal }) => getRuntimeStatus(signal),
    placeholderData: (previousValue) => previousValue,
    refetchInterval: 15000,
  });

  const connectionState = useSessionEvents(activeSessionId);
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
      navigate(`/sessions/${createdSession.id}/chat`);
    },
  });

  const renameSessionMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => updateSession(id, { title }),
    onSuccess: (updatedSession) => {
      queryClient.setQueryData<SessionDetail | undefined>(["session", updatedSession.id], (currentValue) =>
        currentValue ? { ...currentValue, ...updatedSession } : currentValue,
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
      if (sessionId === deletedId) {
        navigate("/sessions");
      }
    },
  });

  const restoreSessionMutation = useMutation({
    mutationFn: (id: string) => restoreSession(id),
    onSuccess: (restoredSession) => {
      queryClient.setQueryData<SessionDetail | undefined>(["session", restoredSession.id], (currentValue) =>
        currentValue ? { ...currentValue, ...restoredSession } : currentValue,
      );
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, restoredSession),
      );
      navigate(`/sessions/${restoredSession.id}/chat`);
    },
  });

  const sendChatMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      sendChatMessage(id, { content, attachments: [] }),
    onMutate: async ({ id, content }) => {
      await queryClient.cancelQueries({ queryKey: ["session", id] });

      const previousDetail = queryClient.getQueryData<SessionDetail | undefined>(["session", id]);
      const optimisticMessage = buildOptimisticUserMessage(id, content);

      queryClient.setQueryData<SessionDetail | undefined>(["session", id], (currentValue) => {
        const targetDetail = currentValue ?? previousDetail;
        if (!targetDetail) {
          return targetDetail;
        }

        return mergeSessionMessages(
          {
            ...targetDetail,
            status: "running",
            updated_at: optimisticMessage.created_at,
          },
          [optimisticMessage],
        );
      });

      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) => {
        const targetSession = currentValue?.find((item) => item.id === id);
        if (!targetSession) {
          return currentValue;
        }

        return upsertSession(currentValue, {
          ...targetSession,
          status: "running",
          updated_at: optimisticMessage.created_at,
        });
      });

      return { previousDetail, optimisticMessageId: optimisticMessage.id };
    },
    onSuccess: async (response, _variables, context) => {
      queryClient.setQueryData<SessionDetail | undefined>(["session", response.session.id], (currentValue) => {
        const baseMessages = (currentValue?.messages ?? []).filter(
          (message) => message.id !== context?.optimisticMessageId,
        );
        const existingMessageIds = new Set(baseMessages.map((message) => message.id));
        const nextMessages = [response.user_message, response.assistant_message].filter(
          (message) => !existingMessageIds.has(message.id),
        );
        const updatedDetail = currentValue
          ? { ...currentValue, ...response.session, messages: baseMessages }
          : undefined;
        return mergeSessionMessages(updatedDetail, nextMessages);
      });
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, response.session),
      );
      await queryClient.invalidateQueries({ queryKey: ["runtime-status"] });
    },
    onError: (error, variables, context) => {
      if (context?.previousDetail) {
        queryClient.setQueryData<SessionDetail | undefined>(["session", variables.id], context.previousDetail);
      }

      appendEvent(variables.id, {
        id: crypto.randomUUID(),
        sessionId: variables.id,
        type: "assistant.trace",
        createdAt: new Date().toISOString(),
        summary: "模型请求失败。",
        payload: { status: "error", error: error instanceof Error ? error.message : "未知错误" },
      });
    },
  });

  async function handleCreate(): Promise<void> {
    await createSessionMutation.mutateAsync();
  }

  async function handleRename(): Promise<void> {
    if (!activeSession) {
      return;
    }

    const nextTitle = window.prompt("修改对话标题", activeSession.title);
    if (nextTitle === null) {
      return;
    }

    const trimmedTitle = nextTitle.trim();
    if (!trimmedTitle || trimmedTitle === activeSession.title) {
      return;
    }

    await renameSessionMutation.mutateAsync({ id: activeSession.id, title: trimmedTitle });
  }

  async function handleSend(content: string): Promise<void> {
    if (!activeSession) {
      return;
    }

    await sendChatMutation.mutateAsync({ id: activeSession.id, content });
  }

  function handleSelect(id: string): void {
    navigate(`/sessions/${id}/chat`);
  }

  const activeDetail = sessionDetailQuery.data ?? null;

  return (
    <main className={`conversation-workbench${isSidebarCollapsed ? " conversation-workbench-sidebar-collapsed" : ""}`}>
      <ConversationSidebar
        sessions={sidebarSessions}
        activeSessionId={activeSessionId}
        collapsed={isSidebarCollapsed}
        isCreating={createSessionMutation.isPending}
        onCreate={handleCreate}
        onToggleCollapsed={handleToggleSidebarCollapsed}
        onSelect={handleSelect}
        onRename={async (id) => {
          if (activeSession?.id === id) {
            await handleRename();
            return;
          }

          const targetSession = sortedSessions.find((session) => session.id === id);
          if (!targetSession) {
            return;
          }

          const nextTitle = window.prompt("修改对话标题", targetSession.title);
          if (nextTitle === null) {
            return;
          }

          const trimmedTitle = nextTitle.trim();
          if (!trimmedTitle || trimmedTitle === targetSession.title) {
            return;
          }

          await renameSessionMutation.mutateAsync({ id, title: trimmedTitle });
        }}
        onArchive={async (id) => {
          await deleteSessionMutation.mutateAsync(id);
        }}
        onRestore={async (id) => {
          await restoreSessionMutation.mutateAsync(id);
        }}
      />

      <section className="conversation-main-shell">
        {sessionsQuery.isLoading && !activeSession ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">正在加载对话</p>
            <p className="conversation-empty-state-copy">稍后即可继续。</p>
          </section>
        ) : sessionsQuery.isError ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">对话列表暂不可用</p>
            <p className="conversation-empty-state-copy">{sessionsQuery.error.message}</p>
          </section>
        ) : activeSession && sessionDetailQuery.isLoading ? (
          <section className="conversation-empty-state">
            <p className="conversation-empty-state-title">正在打开对话</p>
            <p className="conversation-empty-state-copy">消息与工具结果正在同步。</p>
          </section>
        ) : activeSession && activeDetail ? (
          <>
            <header className="conversation-header">
              <div className="conversation-header-copy">
                <h2 className="conversation-title">{getSessionDisplayTitle(activeSession.title)}</h2>
              </div>

              <div className="conversation-header-actions">
                <button
                  className="inline-button"
                  type="button"
                  onClick={() => navigate(`/sessions/${activeSession.id}/graph`)}
                >
                  图谱
                </button>
                <span className={`connection-pill connection-${connectionState}`}>{getConnectionTone(connectionState)}</span>
              </div>
            </header>

            {activeSession.deleted_at ? (
              <section className="conversation-inline-notice">对话已归档，恢复后可继续发送消息。</section>
            ) : null}

            <section className="conversation-body-shell">
              <ConversationFeed
                messages={activeDetail.messages}
                events={sessionEvents}
                runtimeRuns={sessionRuns}
              />

              <WorkbenchComposer
                sessionId={activeSession.id}
                disabled={activeSession.deleted_at !== null}
                isSending={sendChatMutation.isPending}
                onSend={handleSend}
              />
            </section>
          </>
        ) : (
          <section className="conversation-empty-state conversation-empty-state-centered">
            <p className="conversation-empty-state-title">开始新对话</p>
            <p className="conversation-empty-state-copy">在这里统一查看消息、推理和工具结果。</p>
            <button className="conversation-empty-action" type="button" onClick={() => void handleCreate()}>
              新建对话
            </button>
          </section>
        )}
      </section>
    </main>
  );
}
