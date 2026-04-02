import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../store/uiStore";
import type { SessionConversation, SessionDetail, SessionSummary } from "../types/sessions";
import { useSessionEvents } from "./useSessionEvents";

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  constructor(url: string | URL) {
    this.url = String(url);
    MockWebSocket.instances.push(this);
  }

  close(): void {
    this.onclose?.({} as CloseEvent);
  }

  emitOpen(): void {
    this.onopen?.({} as Event);
  }

  emitMessage(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
  }

  emitClose(): void {
    this.onclose?.({} as CloseEvent);
  }
}

function createSessionSummary(): SessionSummary {
  return {
    id: "session-1",
    title: "当前对话",
    status: "running",
    project_id: null,
    goal: null,
    scenario_type: null,
    current_phase: null,
    runtime_policy_json: null,
    created_at: "2026-04-01T10:00:00.000Z",
    updated_at: "2026-04-01T10:00:00.000Z",
    deleted_at: null,
  };
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("useSessionEvents", () => {
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
  });

  it("reconnects from the last seen cursor and ignores replayed events", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    const session = createSessionSummary();
    const sessionDetail: SessionDetail = { ...session, messages: [] };
    const sessionConversation: SessionConversation = {
      session,
      active_branch: null,
      branches: [],
      messages: [],
      generations: [],
    };

    queryClient.setQueryData(["session", session.id], sessionDetail);
    queryClient.setQueryData(["conversation", session.id], sessionConversation);
    queryClient.setQueryData(["sessions", false], [session]);

    const { unmount } = renderHook(() => useSessionEvents(session.id), {
      wrapper: createWrapper(queryClient),
    });

    const firstSocket = MockWebSocket.instances[0]!;
    expect(firstSocket.url).toBe("ws://127.0.0.1:8000/api/sessions/session-1/events");

    act(() => {
      firstSocket.emitOpen();
      firstSocket.emitMessage({
        type: "message.created",
        cursor: 12,
        created_at: "2026-04-01T10:00:02.000Z",
        data: {
          id: "assistant-message-1",
          session_id: "session-1",
          role: "assistant",
          content: "第一次回复",
          attachments: [],
        },
      });
    });

    expect(
      queryClient.getQueryData<SessionConversation>(["conversation", session.id])?.messages,
    ).toMatchObject([{ id: "assistant-message-1", content: "第一次回复" }]);
    expect(useUiStore.getState().lastServerCursorBySession[session.id]).toBe(12);

    act(() => {
      firstSocket.emitClose();
      vi.advanceTimersByTime(1000);
    });

    const secondSocket = MockWebSocket.instances[1]!;
    expect(secondSocket.url).toBe("ws://127.0.0.1:8000/api/sessions/session-1/events?cursor=12");

    act(() => {
      secondSocket.emitOpen();
      secondSocket.emitMessage({
        type: "message.created",
        cursor: 12,
        created_at: "2026-04-01T10:00:03.000Z",
        data: {
          id: "assistant-message-1",
          session_id: "session-1",
          role: "assistant",
          content: "重复回放不应覆盖",
          attachments: [],
        },
      });
      secondSocket.emitMessage({
        type: "assistant.summary",
        cursor: 13,
        created_at: "2026-04-01T10:00:04.000Z",
        data: {
          summary: "新的安全摘要",
          status: "running",
        },
      });
      secondSocket.emitMessage({
        type: "assistant.trace",
        cursor: 14,
        created_at: "2026-04-01T10:00:04.500Z",
        data: {
          message_id: "assistant-message-1",
          state: "tool.started",
          command: "nmap 127.0.0.1",
        },
      });
    });

    expect(
      queryClient.getQueryData<SessionConversation>(["conversation", session.id])?.messages,
    ).toMatchObject([{ id: "assistant-message-1", content: "第一次回复" }]);
    expect(useUiStore.getState().eventsBySession[session.id]).toMatchObject([
      {
        cursor: 13,
        summary: "新的安全摘要",
      },
      {
        cursor: 14,
        summary: "开始调用工具：nmap 127.0.0.1",
      },
    ]);
    expect(useUiStore.getState().lastServerCursorBySession[session.id]).toBe(14);

    unmount();
  });

  it("keeps long live reasoning history in conversation generations", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    const session = createSessionSummary();
    const sessionConversation: SessionConversation = {
      session,
      active_branch: null,
      branches: [],
      messages: [],
      generations: [
        {
          id: "generation-1",
          session_id: session.id,
          branch_id: "branch-1",
          action: "reply",
          assistant_message_id: "assistant-message-1",
          status: "running",
          reasoning_trace: [],
          created_at: "2026-04-01T10:00:00.000Z",
          updated_at: "2026-04-01T10:00:00.000Z",
        },
      ],
    };

    queryClient.setQueryData(["conversation", session.id], sessionConversation);

    const { unmount } = renderHook(() => useSessionEvents(session.id), {
      wrapper: createWrapper(queryClient),
    });

    const socket = MockWebSocket.instances[0]!;
    act(() => {
      socket.emitOpen();
      for (let cursor = 1; cursor <= 205; cursor += 1) {
        socket.emitMessage({
          type: "assistant.trace",
          cursor,
          created_at: `2026-04-01T10:00:${String(cursor % 60).padStart(2, "0")}.000Z`,
          data: {
            message_id: "assistant-message-1",
            state: "tool.started",
            command: `cmd-${cursor}`,
          },
        });
      }
    });

    expect(useUiStore.getState().eventsBySession[session.id]).toHaveLength(200);
    expect(
      queryClient.getQueryData<SessionConversation>(["conversation", session.id])?.generations[0]
        ?.reasoning_trace,
    ).toHaveLength(205);
    expect(
      queryClient.getQueryData<SessionConversation>(["conversation", session.id])?.generations[0]?.steps,
    ).toHaveLength(205);

    unmount();
  });

  it("stores assistant transcripts on message payloads and keeps generation steps secondary", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    const session = createSessionSummary();
    const sessionConversation: SessionConversation = {
      session,
      active_branch: null,
      branches: [],
      messages: [],
      generations: [
        {
          id: "generation-1",
          session_id: session.id,
          branch_id: "branch-1",
          action: "reply",
          assistant_message_id: "assistant-message-1",
          status: "running",
          reasoning_trace: [],
          steps: [],
          created_at: "2026-04-01T10:00:00.000Z",
          updated_at: "2026-04-01T10:00:00.000Z",
        },
      ],
      active_generation_id: "generation-1",
      queued_generation_count: 0,
    };

    queryClient.setQueryData(["conversation", session.id], sessionConversation);

    const { unmount } = renderHook(() => useSessionEvents(session.id), {
      wrapper: createWrapper(queryClient),
    });

    const socket = MockWebSocket.instances[0]!;
    act(() => {
      socket.emitOpen();
      socket.emitMessage({
        type: "tool.call.started",
        cursor: 1,
        created_at: "2026-04-01T10:00:01.000Z",
        data: {
          generation_id: "generation-1",
          message_id: "assistant-message-1",
          tool: "execute_kali_command",
          tool_call_id: "tool-1",
          command: "nmap 127.0.0.1",
        },
      });
      socket.emitMessage({
        type: "message.updated",
        cursor: 2,
        created_at: "2026-04-01T10:00:02.000Z",
        data: {
          id: "assistant-message-1",
          session_id: session.id,
          generation_id: "generation-1",
          role: "assistant",
          content: "阶段性输出",
          attachments: [],
          assistant_transcript: [
            {
              id: "segment-1",
              sequence: 1,
              kind: "tool_call",
              status: "running",
              title: "开始调用工具",
              text: "准备执行 nmap 127.0.0.1",
              tool_name: "execute_kali_command",
              tool_call_id: "tool-1",
              recorded_at: "2026-04-01T10:00:01.000Z",
              updated_at: "2026-04-01T10:00:01.000Z",
            },
            {
              id: "segment-2",
              sequence: 2,
              kind: "output",
              status: "running",
              title: "正文输出",
              text: "阶段性输出",
              recorded_at: "2026-04-01T10:00:02.000Z",
              updated_at: "2026-04-01T10:00:02.000Z",
            },
          ],
        },
      });
      socket.emitMessage({
        type: "assistant.trace",
        cursor: 3,
        created_at: "2026-04-01T10:00:03.000Z",
        data: {
          generation_id: "generation-1",
          message_id: "assistant-message-1",
          state: "generation.completed",
        },
      });
    });

    expect(
      queryClient.getQueryData<SessionConversation>(["conversation", session.id])?.messages,
    ).toMatchObject([
      {
        id: "assistant-message-1",
        content: "阶段性输出",
        assistant_transcript: [
          {
            kind: "tool_call",
            tool_call_id: "tool-1",
          },
          {
            kind: "output",
            text: "阶段性输出",
          },
        ],
      },
    ]);
    expect(
      queryClient.getQueryData<SessionConversation>(["conversation", session.id])?.generations[0]?.steps,
    ).toMatchObject([
      {
        kind: "tool",
        tool_call_id: "tool-1",
        status: "running",
      },
      {
        kind: "status",
        state: "generation.completed",
        status: "completed",
      },
    ]);
    expect(
      queryClient
        .getQueryData<SessionConversation>(["conversation", session.id])
        ?.generations[0]?.steps?.some((step) => step.kind === "output"),
    ).toBe(false);

    unmount();
  });
});
