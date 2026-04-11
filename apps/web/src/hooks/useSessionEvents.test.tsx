import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../store/uiStore";
import type {
  SessionConversation,
  SessionDetail,
  SessionQueue,
  SessionSummary,
} from "../types/sessions";
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
    useUiStore.setState({
      draftsBySession: {},
      eventsBySession: {},
      lastServerCursorBySession: {},
    });
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

  it("updates context-window cache and does not duplicate compaction markers on replay", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    const session = createSessionSummary();

    queryClient.setQueryData(["conversation", session.id], {
      session,
      active_branch: null,
      branches: [],
      messages: [],
      generations: [],
    } satisfies SessionConversation);

    const { unmount } = renderHook(() => useSessionEvents(session.id), {
      wrapper: createWrapper(queryClient),
    });

    const firstSocket = MockWebSocket.instances[0]!;
    act(() => {
      firstSocket.emitOpen();
      firstSocket.emitMessage({
        type: "session.compaction.completed",
        cursor: 31,
        created_at: "2026-04-11T18:21:02.000Z",
        data: {
          mode: "manual",
          summary: "已压缩对话",
        },
      });
      firstSocket.emitMessage({
        type: "session.context_window.updated",
        cursor: 32,
        created_at: "2026-04-11T18:21:03.000Z",
        data: {
          session_id: session.id,
          model: "gpt-5.4",
          context_window_tokens: 400000,
          used_tokens: 21408,
          reserved_response_tokens: 8192,
          usage_ratio: 0.0535,
          auto_compact_threshold_ratio: 0.8,
          last_compacted_at: "2026-04-11T18:21:02.000Z",
          last_compact_boundary: "compact-boundary:8",
          can_manual_compact: true,
          blocking_reason: null,
          breakdown: [],
        },
      });
    });

    expect(useUiStore.getState().eventsBySession[session.id]).toMatchObject([
      {
        cursor: 31,
        summary: "已压缩对话",
      },
    ]);
    expect(queryClient.getQueryData(["session-context-window", session.id])).toMatchObject({
      used_tokens: 21408,
      last_compact_boundary: "compact-boundary:8",
    });

    act(() => {
      firstSocket.emitClose();
      vi.advanceTimersByTime(1000);
    });

    const secondSocket = MockWebSocket.instances[1]!;
    expect(secondSocket.url).toBe("ws://127.0.0.1:8000/api/sessions/session-1/events?cursor=32");

    act(() => {
      secondSocket.emitOpen();
      secondSocket.emitMessage({
        type: "session.compaction.completed",
        cursor: 31,
        created_at: "2026-04-11T18:21:04.000Z",
        data: {
          mode: "manual",
          summary: "重复回放不应出现",
        },
      });
    });

    expect(useUiStore.getState().eventsBySession[session.id]).toHaveLength(1);
    expect(useUiStore.getState().eventsBySession[session.id]?.[0]?.summary).toBe("已压缩对话");

    unmount();
  });

  it("preserves visible think transcript blocks across reconnect replayed message updates", () => {
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
    act(() => {
      firstSocket.emitOpen();
      firstSocket.emitMessage({
        type: "message.updated",
        cursor: 21,
        created_at: "2026-04-01T10:00:05.000Z",
        data: {
          id: "assistant-message-think-1",
          session_id: session.id,
          role: "assistant",
          content: "<think>very secret</think>最终答复",
          attachments: [],
          assistant_transcript: [
            {
              id: "segment-reasoning-1",
              sequence: 1,
              kind: "reasoning",
              status: "running",
              text: "<think>private</think>分析中",
              recorded_at: "2026-04-01T10:00:04.000Z",
              updated_at: "2026-04-01T10:00:04.000Z",
            },
            {
              id: "segment-output-1",
              sequence: 2,
              kind: "output",
              status: "completed",
              text: "<think>very secret</think>最终答复",
              recorded_at: "2026-04-01T10:00:05.000Z",
              updated_at: "2026-04-01T10:00:05.000Z",
            },
          ],
        },
      });
    });

    act(() => {
      firstSocket.emitClose();
      vi.advanceTimersByTime(1000);
    });

    const secondSocket = MockWebSocket.instances[1]!;
    expect(secondSocket.url).toBe("ws://127.0.0.1:8000/api/sessions/session-1/events?cursor=21");

    act(() => {
      secondSocket.emitOpen();
      secondSocket.emitMessage({
        type: "message.updated",
        cursor: 21,
        created_at: "2026-04-01T10:00:06.000Z",
        data: {
          id: "assistant-message-think-1",
          session_id: session.id,
          role: "assistant",
          content: "replayed content should be ignored",
          attachments: [],
          assistant_transcript: [
            {
              id: "segment-output-replay",
              sequence: 1,
              kind: "output",
              status: "completed",
              text: "replayed content should be ignored",
              recorded_at: "2026-04-01T10:00:06.000Z",
              updated_at: "2026-04-01T10:00:06.000Z",
            },
          ],
        },
      });
    });

    expect(
      queryClient.getQueryData<SessionDetail>(["session", session.id])?.messages,
    ).toMatchObject([
      {
        id: "assistant-message-think-1",
        content: "<think>very secret</think>最终答复",
        assistant_transcript: [
          { kind: "reasoning", text: "<think>private</think>分析中" },
          { kind: "output", text: "<think>very secret</think>最终答复" },
        ],
      },
    ]);
    expect(
      queryClient.getQueryData<SessionConversation>(["conversation", session.id])?.messages,
    ).toMatchObject([
      {
        id: "assistant-message-think-1",
        content: "<think>very secret</think>最终答复",
        assistant_transcript: [
          { kind: "reasoning", text: "<think>private</think>分析中" },
          { kind: "output", text: "<think>very secret</think>最终答复" },
        ],
      },
    ]);
    expect(useUiStore.getState().lastServerCursorBySession[session.id]).toBe(21);

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
      queryClient.getQueryData<SessionConversation>(["conversation", session.id])?.generations[0]
        ?.steps,
    ).toHaveLength(205);

    unmount();
  });

  it("stops reconnecting after the session selection is cleared", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    const initialProps: { sessionId: string | null } = { sessionId: "session-1" };

    const { result, rerender, unmount } = renderHook(
      ({ sessionId }: { sessionId: string | null }) => useSessionEvents(sessionId),
      {
        initialProps,
        wrapper: createWrapper(queryClient),
      },
    );

    const socket = MockWebSocket.instances[0]!;

    act(() => {
      socket.emitOpen();
      socket.emitClose();
    });

    expect(result.current).toBe("closed");

    act(() => {
      rerender({ sessionId: null });
    });

    expect(result.current).toBe("closed");

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(MockWebSocket.instances).toHaveLength(1);

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
      queryClient.getQueryData<SessionConversation>(["conversation", session.id])?.generations[0]
        ?.steps,
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

  it("enriches conversation message transcript using existing generation steps on websocket updates", () => {
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
          status: "completed",
          reasoning_trace: [],
          steps: [
            {
              id: "generation-step-tool-rich",
              generation_id: "generation-1",
              session_id: session.id,
              message_id: "assistant-message-1",
              sequence: 1,
              kind: "tool",
              phase: "tool_result",
              status: "completed",
              state: "finished",
              label: "命令执行结果",
              safe_summary: "命令已完成。",
              delta_text: "",
              tool_name: "execute_kali_command",
              tool_call_id: "tool-rich-1",
              command: "curl -s http://target",
              metadata: {
                result: {
                  output: {
                    stdout: "rich stdout from generation",
                    stderr: "",
                    exit_code: 0,
                  },
                },
              },
              started_at: "2026-04-01T10:00:01.000Z",
              ended_at: "2026-04-01T10:00:02.000Z",
            },
          ],
          created_at: "2026-04-01T10:00:00.000Z",
          updated_at: "2026-04-01T10:00:02.000Z",
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
      socket.emitMessage({
        type: "message.updated",
        cursor: 31,
        created_at: "2026-04-01T10:00:03.000Z",
        data: {
          id: "assistant-message-1",
          session_id: session.id,
          generation_id: "generation-1",
          role: "assistant",
          content: "最终答复",
          attachments: [],
          assistant_transcript: [
            {
              id: "segment-thin-result",
              sequence: 1,
              kind: "tool_result",
              status: "completed",
              title: "命令执行结果",
              text: "命令已完成。",
              tool_name: "execute_kali_command",
              tool_call_id: "tool-rich-1",
              recorded_at: "2026-04-01T10:00:03.000Z",
              updated_at: "2026-04-01T10:00:03.000Z",
              metadata: {
                result: {
                  status: "completed",
                },
              },
            },
          ],
        },
      });
    });

    const mergedConversation = queryClient.getQueryData<SessionConversation>([
      "conversation",
      session.id,
    ]);
    const mergedMessage = mergedConversation?.messages[0];
    const mergedMetadata = mergedMessage?.assistant_transcript[0]?.metadata;
    const metadataResult =
      mergedMetadata && typeof mergedMetadata.result === "object" && mergedMetadata.result !== null
        ? (mergedMetadata.result as Record<string, unknown>)
        : null;
    const metadataOutput =
      mergedMetadata && typeof mergedMetadata.output === "object" && mergedMetadata.output !== null
        ? (mergedMetadata.output as Record<string, unknown>)
        : null;
    const resultOutput =
      metadataResult && typeof metadataResult.output === "object" && metadataResult.output !== null
        ? (metadataResult.output as Record<string, unknown>)
        : null;

    const enrichedStdout =
      (typeof resultOutput?.stdout === "string" ? resultOutput.stdout : null) ??
      (typeof metadataResult?.stdout === "string" ? metadataResult.stdout : null) ??
      (typeof metadataOutput?.stdout === "string" ? metadataOutput.stdout : null) ??
      (typeof mergedMetadata?.stdout === "string" ? mergedMetadata.stdout : null);

    expect(mergedMessage).toMatchObject({
      id: "assistant-message-1",
      generation_id: "generation-1",
      assistant_transcript: [{ tool_call_id: "tool-rich-1" }],
    });
    expect(enrichedStdout).toBe("rich stdout from generation");
    expect(mergedMessage?.assistant_transcript[0]?.metadata?.command).toBe("curl -s http://target");
    expect(mergedMessage?.assistant_transcript[0]?.metadata?.result).toMatchObject({
      status: "completed",
    });

    unmount();
  });

  it("keeps queued generations stable through completion and next-start websocket events", () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    const session = createSessionSummary();
    const queue: SessionQueue = {
      session,
      active_generation: {
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
      queued_generations: [
        {
          id: "optimistic-generation-2",
          session_id: session.id,
          branch_id: "branch-1",
          action: "reply",
          assistant_message_id: "assistant-message-2",
          status: "queued",
          reasoning_trace: [],
          queue_position: 1,
          created_at: "2026-04-01T10:00:01.000Z",
          updated_at: "2026-04-01T10:00:01.000Z",
        },
      ],
      active_generation_id: "generation-1",
      queued_generation_count: 1,
    };

    queryClient.setQueryData(["session-queue", session.id], queue);

    const { unmount } = renderHook(() => useSessionEvents(session.id), {
      wrapper: createWrapper(queryClient),
    });

    const socket = MockWebSocket.instances[0]!;
    act(() => {
      socket.emitOpen();
      socket.emitMessage({
        type: "tool.call.started",
        cursor: 1,
        created_at: "2026-04-01T10:00:01.500Z",
        data: {
          generation_id: "generation-1",
          tool_call_id: "tool-1",
          tool: "execute_kali_command",
          command: "nmap 127.0.0.1",
        },
      });
    });

    expect(queryClient.getQueryData<SessionQueue>(["session-queue", session.id])).toMatchObject({
      active_generation_id: "generation-1",
      queued_generations: [{ id: "optimistic-generation-2" }],
      queued_generation_count: 1,
    });

    act(() => {
      socket.emitMessage({
        type: "assistant.trace",
        cursor: 2,
        created_at: "2026-04-01T10:00:02.000Z",
        data: {
          generation_id: "generation-1",
          state: "generation.completed",
        },
      });
      socket.emitMessage({
        type: "generation.started",
        cursor: 3,
        created_at: "2026-04-01T10:00:03.000Z",
        data: {
          generation_id: "optimistic-generation-2",
          queued_prompt_count: 0,
        },
      });
    });

    expect(queryClient.getQueryData<SessionQueue>(["session-queue", session.id])).toMatchObject({
      active_generation_id: "optimistic-generation-2",
      active_generation: { id: "optimistic-generation-2", status: "running" },
      queued_generations: [],
      queued_generation_count: 0,
    });

    unmount();
  });
});
