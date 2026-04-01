import { useEffect, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  createSession,
  deleteSession,
  getSession,
  listSessions,
  restoreSession,
  sendChatMessage,
  updateSession,
} from "../lib/api";
import { mergeSessionMessages, sortSessions, upsertSession } from "../lib/sessionUtils";
import { useSessionEvents } from "../hooks/useSessionEvents";
import { useUiStore } from "../store/uiStore";
import type {
  AttachmentMetadata,
  SessionDetail,
  SessionEventEntry,
  SessionSummary,
} from "../types/sessions";
import { SessionChatPane } from "./SessionChatPane";
import { SessionOverviewPane } from "./SessionOverviewPane";
import { SessionSidebar } from "./SessionSidebar";
import { WorkspaceNavigation } from "./WorkspaceNavigation";

const EMPTY_SESSION_EVENTS: SessionEventEntry[] = [];

function filterActiveSessions(
  sessions: SessionSummary[],
  includeDeleted: boolean,
): SessionSummary[] {
  return includeDeleted ? sessions : sessions.filter((session) => session.deleted_at === null);
}

export function SessionWorkspace() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const includeDeleted = useUiStore((state) => state.includeDeleted);
  const lastVisitedSessionId = useUiStore((state) => state.lastVisitedSessionId);
  const setIncludeDeleted = useUiStore((state) => state.setIncludeDeleted);
  const setLastVisitedSessionId = useUiStore((state) => state.setLastVisitedSessionId);
  const sessionEvents = useUiStore((state) =>
    sessionId ? (state.eventsBySession[sessionId] ?? EMPTY_SESSION_EVENTS) : EMPTY_SESSION_EVENTS,
  );

  const sessionsQuery = useQuery({
    queryKey: ["sessions", includeDeleted],
    queryFn: ({ signal }) => listSessions(includeDeleted, signal),
  });

  const sortedSessions = useMemo(
    () => sortSessions(sessionsQuery.data ?? []),
    [sessionsQuery.data],
  );
  const visibleSessions = useMemo(
    () => filterActiveSessions(sortedSessions, includeDeleted),
    [includeDeleted, sortedSessions],
  );

  const activeSessionId = useMemo(() => {
    if (sessionId) {
      return sessionId;
    }

    if (
      lastVisitedSessionId &&
      visibleSessions.some((session) => session.id === lastVisitedSessionId)
    ) {
      return lastVisitedSessionId;
    }

    return visibleSessions[0]?.id ?? sortedSessions[0]?.id ?? null;
  }, [lastVisitedSessionId, sessionId, sortedSessions, visibleSessions]);

  const activeSession = useMemo(
    () => sortedSessions.find((session) => session.id === activeSessionId) ?? null,
    [activeSessionId, sortedSessions],
  );

  useEffect(() => {
    if (sessionId) {
      setLastVisitedSessionId(sessionId);
    }
  }, [sessionId, setLastVisitedSessionId]);

  const sessionDetailQuery = useQuery({
    enabled: Boolean(activeSessionId),
    queryKey: ["session", activeSessionId],
    queryFn: ({ signal }) => getSession(activeSessionId!, signal),
  });

  const connectionState = useSessionEvents(sessionId ?? null);

  const createSessionMutation = useMutation({
    mutationFn: (title: string) => createSession(title.trim() || undefined),
    onSuccess: async (createdSession) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setLastVisitedSessionId(createdSession.id);
      navigate(`/sessions/${createdSession.id}/chat`);
    },
  });

  const renameSessionMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => updateSession(id, { title }),
    onSuccess: (updatedSession) => {
      queryClient.setQueryData<SessionDetail | undefined>(
        ["session", updatedSession.id],
        (currentValue) =>
          currentValue
            ? {
                ...currentValue,
                ...updatedSession,
              }
            : currentValue,
      );
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, updatedSession),
      );
    },
  });

  const deleteSessionMutation = useMutation({
    mutationFn: (id: string) => deleteSession(id),
    onSuccess: async (_result, deletedId) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });

      if (sessionId === deletedId) {
        navigate("/sessions");
      }
    },
  });

  const restoreSessionMutation = useMutation({
    mutationFn: (id: string) => restoreSession(id),
    onSuccess: (restoredSession) => {
      queryClient.setQueryData<SessionDetail | undefined>(
        ["session", restoredSession.id],
        (currentValue) =>
          currentValue
            ? {
                ...currentValue,
                ...restoredSession,
              }
            : currentValue,
      );
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, restoredSession),
      );
      navigate(`/sessions/${restoredSession.id}/chat`);
    },
  });

  const sendChatMutation = useMutation({
    mutationFn: ({
      id,
      content,
      attachments,
    }: {
      id: string;
      content: string;
      attachments: AttachmentMetadata[];
    }) => sendChatMessage(id, { content, attachments }),
    onSuccess: (response) => {
      queryClient.setQueryData<SessionDetail | undefined>(
        ["session", response.session.id],
        (currentValue) => {
          const updatedDetail = currentValue
            ? {
                ...currentValue,
                ...response.session,
              }
            : undefined;

          return mergeSessionMessages(updatedDetail, [
            response.user_message,
            response.assistant_message,
          ]);
        },
      );
      queryClient.setQueriesData<SessionSummary[]>({ queryKey: ["sessions"] }, (currentValue) =>
        upsertSession(currentValue, response.session),
      );
    },
  });

  async function handleCreate(title: string): Promise<void> {
    await createSessionMutation.mutateAsync(title);
  }

  async function handleRename(title: string): Promise<void> {
    if (!activeSession) {
      return;
    }

    await renameSessionMutation.mutateAsync({ id: activeSession.id, title });
  }

  async function handleDelete(): Promise<void> {
    if (!activeSession) {
      return;
    }

    await deleteSessionMutation.mutateAsync(activeSession.id);
  }

  async function handleRestore(): Promise<void> {
    if (!activeSession) {
      return;
    }

    await restoreSessionMutation.mutateAsync(activeSession.id);
  }

  async function handleSend(content: string, attachments: AttachmentMetadata[]): Promise<void> {
    if (!activeSession) {
      return;
    }

    await sendChatMutation.mutateAsync({
      id: activeSession.id,
      content,
      attachments,
    });
  }

  function handleSelect(sessionToOpenId: string): void {
    setLastVisitedSessionId(sessionToOpenId);
    navigate(`/sessions/${sessionToOpenId}/chat`);
  }

  const activeDetail = sessionDetailQuery.data ?? null;

  return (
    <main className="workspace-shell">
      <WorkspaceNavigation />

      <div className="workspace-layout">
        <div className="sidebar-column">
          <SessionSidebar
            sessions={sortedSessions}
            activeSessionId={activeSessionId}
            includeDeleted={includeDeleted}
            isCreating={createSessionMutation.isPending}
            onCreate={handleCreate}
            onIncludeDeletedChange={setIncludeDeleted}
            onSelect={handleSelect}
          />
        </div>

        <div className="main-column">
          {sessionsQuery.isLoading && !activeSession ? (
            <section className="panel workspace-pane">
              <div className="empty-state">
                <h1 className="panel-title">Loading sessions…</h1>
                <p className="empty-copy">
                  Fetching the retained session workspace from the backend.
                </p>
              </div>
            </section>
          ) : sessionsQuery.isError ? (
            <section className="panel workspace-pane">
              <div className="empty-state">
                <h1 className="panel-title">Session list unavailable</h1>
                <p className="empty-copy">{sessionsQuery.error.message}</p>
              </div>
            </section>
          ) : sessionId && activeSession && activeDetail ? (
            <SessionChatPane
              session={activeSession}
              detail={activeDetail}
              events={sessionEvents}
              connectionState={connectionState}
              isRenaming={renameSessionMutation.isPending}
              isDeleting={deleteSessionMutation.isPending}
              isRestoring={restoreSessionMutation.isPending}
              isSending={sendChatMutation.isPending}
              onRename={handleRename}
              onDelete={handleDelete}
              onRestore={handleRestore}
              onSend={handleSend}
            />
          ) : sessionId && sessionDetailQuery.isError ? (
            <section className="panel workspace-pane">
              <div className="empty-state">
                <h1 className="panel-title">Session detail unavailable</h1>
                <p className="empty-copy">{sessionDetailQuery.error.message}</p>
              </div>
            </section>
          ) : (
            <SessionOverviewPane
              session={activeSession}
              detail={activeDetail}
              isRenaming={renameSessionMutation.isPending}
              isDeleting={deleteSessionMutation.isPending}
              isRestoring={restoreSessionMutation.isPending}
              onRename={handleRename}
              onDelete={handleDelete}
              onRestore={handleRestore}
              onOpenChat={handleSelect}
            />
          )}
        </div>
      </div>
    </main>
  );
}
