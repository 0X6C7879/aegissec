import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getSessionEventsUrl } from "../lib/api";
import {
  buildEventSummary,
  isRecord,
  mergeConversationReasoningEvent,
  mergeSessionMessage,
  shouldStoreRealtimeEvent,
  toSessionMessageEvent,
  toSessionSummaryUpdate,
  upsertSession,
} from "../lib/sessionUtils";
import { useUiStore } from "../store/uiStore";
import type {
  SessionConversation,
  SessionDetail,
  SessionEventEnvelope,
  SessionSummary,
} from "../types/sessions";

type ConnectionState = "connecting" | "open" | "closed" | "error";

function normalizeEventEnvelope(value: unknown): SessionEventEnvelope {
  if (!isRecord(value)) {
    return {
      type: "unknown",
      data: value,
      created_at: new Date().toISOString(),
    };
  }

  const type =
    typeof value.type === "string"
      ? value.type
      : typeof value.event === "string"
        ? value.event
        : "unknown";

  const data =
    "data" in value
      ? value.data
      : "payload" in value
        ? value.payload
        : "message" in value
          ? value.message
          : "session" in value
            ? value.session
            : value;

  const createdAt =
    typeof value.created_at === "string"
      ? value.created_at
      : typeof value.timestamp === "string"
        ? value.timestamp
        : new Date().toISOString();

  const cursor = typeof value.cursor === "number" && Number.isFinite(value.cursor) ? value.cursor : null;

  return {
    type,
    cursor,
    data,
    created_at: createdAt,
  };
}

function createRealtimeEventId(
  sessionId: string,
  type: string,
  createdAt: string,
  cursor?: number | null,
): string {
  if (typeof cursor === "number" && Number.isFinite(cursor)) {
    return `${sessionId}:${cursor}`;
  }

  return `${sessionId}:${type}:${createdAt}:${crypto.randomUUID()}`;
}

function updateSessionLists(
  currentValue: SessionSummary[] | undefined,
  session: SessionSummary,
): SessionSummary[] {
  return upsertSession(currentValue, session);
}

export function useSessionEvents(sessionId: string | null): ConnectionState {
  const queryClient = useQueryClient();
  const appendEvent = useUiStore((state) => state.appendEvent);
  const markEventCursorSeen = useUiStore((state) => state.markEventCursorSeen);
  const [connectionState, setConnectionState] = useState<ConnectionState>("closed");

  useEffect(() => {
    if (!sessionId) {
      setConnectionState("closed");
      return undefined;
    }

    const targetSessionId = sessionId;

    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let currentSocket: WebSocket | null = null;
    let isDisposed = false;

    function clearReconnectTimer(): void {
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    }

    function connect(): void {
      clearReconnectTimer();
      setConnectionState("connecting");

      const cursor = useUiStore.getState().lastServerCursorBySession[targetSessionId] ?? null;
      const websocket = new WebSocket(getSessionEventsUrl(targetSessionId, cursor));
      currentSocket = websocket;

      websocket.onopen = () => {
        if (isDisposed) {
          return;
        }

        setConnectionState("open");
      };

      websocket.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data) as unknown;
          const envelope = normalizeEventEnvelope(parsed);
          const createdAt = envelope.created_at ?? new Date().toISOString();

          if (
            typeof envelope.cursor === "number" &&
            Number.isFinite(envelope.cursor) &&
            !markEventCursorSeen(targetSessionId, envelope.cursor)
          ) {
            return;
          }

          if (shouldStoreRealtimeEvent(envelope.type, envelope.data)) {
            appendEvent(targetSessionId, {
              id: createRealtimeEventId(targetSessionId, envelope.type, createdAt, envelope.cursor),
              sessionId: targetSessionId,
              cursor: envelope.cursor ?? null,
              type: envelope.type,
              createdAt,
              summary: buildEventSummary(envelope.type, envelope.data),
              payload: envelope.data,
            });
          }

          if (envelope.type.startsWith("session.")) {
            queryClient.setQueryData<SessionDetail | undefined>(
              ["session", targetSessionId],
              (currentValue) => {
                if (!currentValue) {
                  return currentValue;
                }

                const updatedSession = toSessionSummaryUpdate(currentValue, envelope.data, createdAt);
                return updatedSession ? { ...currentValue, ...updatedSession } : currentValue;
              },
            );

            queryClient.setQueryData<SessionConversation | undefined>(
              ["conversation", targetSessionId],
              (currentValue) => {
                if (!currentValue) {
                  return currentValue;
                }

                const updatedSession = toSessionSummaryUpdate(currentValue.session, envelope.data, createdAt);
                return updatedSession ? { ...currentValue, session: updatedSession } : currentValue;
              },
            );

            queryClient.setQueriesData<SessionSummary[]>(
              { queryKey: ["sessions"] },
              (currentValue) => {
                const currentSession = currentValue?.find((item) => item.id === targetSessionId);

                if (!currentSession) {
                  return currentValue;
                }

                const updatedSession = toSessionSummaryUpdate(currentSession, envelope.data, createdAt);
                return updatedSession ? updateSessionLists(currentValue, updatedSession) : currentValue;
              },
            );
          }

          if (
            envelope.type === "message.created" ||
            envelope.type === "message.updated" ||
            envelope.type === "message.delta" ||
            envelope.type === "message.completed"
          ) {
            const createdMessage = toSessionMessageEvent(envelope.data, targetSessionId, createdAt);

            if (!createdMessage) {
              return;
            }

            queryClient.setQueryData<SessionDetail | undefined>(
              ["session", targetSessionId],
              (currentValue) => mergeSessionMessage(currentValue, createdMessage),
            );

            queryClient.setQueryData<SessionConversation | undefined>(
              ["conversation", targetSessionId],
              (currentValue) => {
                if (!currentValue) {
                  return currentValue;
                }

                return {
                  ...currentValue,
                  messages:
                    mergeSessionMessage(
                      { ...currentValue.session, messages: currentValue.messages },
                      createdMessage,
                    )?.messages ?? currentValue.messages,
                };
              },
            );
          }

          if (envelope.type === "assistant.summary" || envelope.type === "assistant.trace") {
            queryClient.setQueryData<SessionConversation | undefined>(
              ["conversation", targetSessionId],
              (currentValue) =>
                mergeConversationReasoningEvent(
                  currentValue,
                  envelope.type,
                  envelope.data,
                  createdAt,
                  envelope.cursor ?? null,
                ),
            );
          }

          if (
            envelope.type.startsWith("generation.") ||
            envelope.type === "session.updated" ||
            envelope.type.startsWith("tool.call.")
          ) {
            void queryClient.invalidateQueries({ queryKey: ["session-queue", targetSessionId] });
          }
        } catch {
          appendEvent(targetSessionId, {
            id: crypto.randomUUID(),
            sessionId: targetSessionId,
            cursor: null,
            type: "event.parse_error",
            createdAt: new Date().toISOString(),
            summary: "Received an unreadable websocket event.",
            payload: null,
          });
        }
      };

      websocket.onerror = () => {
        if (isDisposed) {
          return;
        }

        setConnectionState("error");
      };

      websocket.onclose = () => {
        if (isDisposed) {
          return;
        }

        setConnectionState((currentValue) => (currentValue === "error" ? "error" : "closed"));
        clearReconnectTimer();
        reconnectTimer = setTimeout(() => {
          if (!isDisposed) {
            connect();
          }
        }, 1000);
      };
    }

    connect();

    return () => {
      isDisposed = true;
      clearReconnectTimer();
      currentSocket?.close();
    };
  }, [appendEvent, markEventCursorSeen, queryClient, sessionId]);

  return connectionState;
}
