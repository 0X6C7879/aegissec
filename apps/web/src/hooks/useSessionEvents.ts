import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getSessionEventsUrl } from "../lib/api";
import {
  buildEventSummary,
  isRecord,
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

  return {
    type,
    data,
    created_at: createdAt,
  };
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
  const [connectionState, setConnectionState] = useState<ConnectionState>("closed");

  useEffect(() => {
    if (!sessionId) {
      setConnectionState("closed");
      return undefined;
    }

    setConnectionState("connecting");
    const websocket = new WebSocket(getSessionEventsUrl(sessionId));

    websocket.onopen = () => {
      setConnectionState("open");
    };

    websocket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as unknown;
        const envelope = normalizeEventEnvelope(parsed);
        const createdAt = envelope.created_at ?? new Date().toISOString();

        if (shouldStoreRealtimeEvent(envelope.type, envelope.data)) {
          appendEvent(sessionId, {
            id: crypto.randomUUID(),
            sessionId,
            type: envelope.type,
            createdAt,
            summary: buildEventSummary(envelope.type, envelope.data),
            payload: envelope.data,
          });
        }

        if (envelope.type.startsWith("session.")) {
          queryClient.setQueryData<SessionDetail | undefined>(
            ["session", sessionId],
            (currentValue) => {
              if (!currentValue) {
                return currentValue;
              }

              const updatedSession = toSessionSummaryUpdate(currentValue, envelope.data, createdAt);
              return updatedSession ? { ...currentValue, ...updatedSession } : currentValue;
            },
          );

          queryClient.setQueryData<SessionConversation | undefined>(
            ["conversation", sessionId],
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
              const currentSession = currentValue?.find((item) => item.id === sessionId);

              if (!currentSession) {
                return currentValue;
              }

              const updatedSession = toSessionSummaryUpdate(
                currentSession,
                envelope.data,
                createdAt,
              );
              return updatedSession
                ? updateSessionLists(currentValue, updatedSession)
                : currentValue;
            },
          );
        }

        if (
          envelope.type === "message.created" ||
          envelope.type === "message.updated" ||
          envelope.type === "message.delta" ||
          envelope.type === "message.completed"
        ) {
          const createdMessage = toSessionMessageEvent(envelope.data, sessionId, createdAt);

          if (!createdMessage) {
            return;
          }

          queryClient.setQueryData<SessionDetail | undefined>(
            ["session", sessionId],
            (currentValue) => mergeSessionMessage(currentValue, createdMessage),
          );

          queryClient.setQueryData<SessionConversation | undefined>(
            ["conversation", sessionId],
            (currentValue) => {
              if (!currentValue) {
                return currentValue;
              }
              return {
                ...currentValue,
                messages: mergeSessionMessage(
                  { ...currentValue.session, messages: currentValue.messages },
                  createdMessage,
                )?.messages ?? currentValue.messages,
              };
            },
          );
        }

        if (
          envelope.type.startsWith("generation.") ||
          envelope.type === "session.updated" ||
          envelope.type.startsWith("tool.call.")
        ) {
          void queryClient.invalidateQueries({ queryKey: ["session-queue", sessionId] });
        }
      } catch {
        appendEvent(sessionId, {
          id: crypto.randomUUID(),
          sessionId,
          type: "event.parse_error",
          createdAt: new Date().toISOString(),
          summary: "Received an unreadable websocket event.",
          payload: null,
        });
      }
    };

    websocket.onerror = () => {
      setConnectionState("error");
    };

    websocket.onclose = () => {
      setConnectionState((currentValue) => (currentValue === "error" ? "error" : "closed"));
    };

    return () => {
      websocket.close();
    };
  }, [appendEvent, queryClient, sessionId]);

  return connectionState;
}
