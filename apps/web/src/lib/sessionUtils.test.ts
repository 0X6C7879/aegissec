import { describe, expect, it } from "vitest";
import {
  extractSafeSessionSummary,
  mergeConversationGenerationEvent,
  mergeConversationReasoningEvent,
  mergeSessionEventEntries,
  shouldStoreRealtimeEvent,
  toSessionMessageEvent,
} from "./sessionUtils";

describe("sessionUtils realtime summaries", () => {
  it("maps generation cancellation events to safe timeline text", () => {
    expect(
      extractSafeSessionSummary("generation.cancelled", {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
      }),
    ).toEqual({
      label: "中断反馈",
      summary: "当前回复已停止，已保留到目前为止的可见输出。",
      tone: "warning",
    });
  });

  it("stores meaningful generation and summary events but ignores raw message updates", () => {
    expect(
      shouldStoreRealtimeEvent("generation.started", {
        generation_id: "generation-1",
        queued_prompt_count: 0,
      }),
    ).toBe(true);
    expect(
      shouldStoreRealtimeEvent("assistant.summary", {
        summary: "Assistant is analyzing the request and preparing a response.",
      }),
    ).toBe(true);
    expect(
      shouldStoreRealtimeEvent("message.updated", {
        message_id: "assistant-message-1",
        content: "partial",
      }),
    ).toBe(false);
    expect(
      shouldStoreRealtimeEvent("assistant.trace", {
        state: "generation.failed",
        error: "runtime crashed",
      }),
    ).toBe(true);
    expect(
      shouldStoreRealtimeEvent("assistant.trace", {
        state: "tool.started",
        command: "nmap 127.0.0.1",
      }),
    ).toBe(true);
  });

  it("maps observable assistant traces to safe visible summaries", () => {
    expect(
      extractSafeSessionSummary("assistant.trace", {
        state: "tool.started",
        command: "nmap 127.0.0.1",
      }),
    ).toEqual({
      label: "思路进展 · tool started",
      summary: "开始调用工具：nmap 127.0.0.1",
      tone: "connected",
    });
  });

  it("preserves think tags in visible summaries", () => {
    expect(
      extractSafeSessionSummary("assistant.summary", {
        summary: "<think>private reasoning 正在整理可展示摘要。",
        status: "running",
      }),
    ).toEqual({
      label: "思路摘要",
      summary: "<think>private reasoning 正在整理可展示摘要。",
      tone: "connected",
    });
  });

  it("parses assistant transcripts from message payloads", () => {
    expect(
      toSessionMessageEvent(
        {
          id: "assistant-message-1",
          session_id: "session-1",
          role: "assistant",
          content: "最终答复",
          attachments: [],
          assistant_transcript: [
            {
              id: "segment-1",
              sequence: 1,
              kind: "tool_result",
              status: "completed",
              title: "工具执行结果",
              text: "工具执行完成，状态：success。",
              tool_name: "bash",
              tool_call_id: "tool-1",
              recorded_at: "2026-04-01T10:00:01.000Z",
              updated_at: "2026-04-01T10:00:02.000Z",
              metadata: {
                stdout: "runtime command completed",
                artifacts: ["reports/auto.txt"],
              },
            },
          ],
        },
        "session-1",
        "2026-04-01T10:00:02.000Z",
      ),
    ).toMatchObject({
      id: "assistant-message-1",
      assistant_transcript: [
        {
          kind: "tool_result",
          tool_name: "bash",
          metadata: {
            stdout: "runtime command completed",
            artifacts: ["reports/auto.txt"],
          },
        },
      ],
    });
  });

  it("merges live reasoning events into conversation generations", () => {
    const merged = mergeConversationReasoningEvent(
      {
        session: {
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
        },
        active_branch: null,
        branches: [],
        messages: [],
        generations: [
          {
            id: "generation-1",
            session_id: "session-1",
            branch_id: "branch-1",
            action: "reply",
            assistant_message_id: "assistant-message-1",
            status: "running",
            reasoning_trace: [],
            created_at: "2026-04-01T10:00:00.000Z",
            updated_at: "2026-04-01T10:00:00.000Z",
          },
        ],
      },
      "assistant.summary",
      {
        message_id: "assistant-message-1",
        summary: "新的可见摘要",
      },
      "2026-04-01T10:00:01.000Z",
      21,
    );

    expect(merged?.generations[0]?.reasoning_summary).toBe("新的可见摘要");
    expect(merged?.generations[0]?.reasoning_trace).toMatchObject([
      {
        type: "assistant.summary",
        cursor: 21,
        sequence: 1,
        summary: "新的可见摘要",
      },
    ]);
  });

  it("aggregates tool and output events into one generation timeline", () => {
    const baseConversation = {
      session: {
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
      },
      active_branch: null,
      branches: [],
      messages: [],
      generations: [
        {
          id: "generation-1",
          session_id: "session-1",
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

    const withToolStart = mergeConversationGenerationEvent(
      baseConversation,
      "tool.call.started",
      {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
        tool: "execute_kali_command",
        tool_call_id: "tool-1",
        command: "nmap 127.0.0.1",
      },
      "2026-04-01T10:00:01.000Z",
      11,
    );
    const withToolFinished = mergeConversationGenerationEvent(
      withToolStart,
      "tool.call.finished",
      {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
        tool: "execute_kali_command",
        tool_call_id: "tool-1",
        command: "nmap 127.0.0.1",
        status: "completed",
      },
      "2026-04-01T10:00:02.000Z",
      12,
    );
    const withDelta = mergeConversationGenerationEvent(
      withToolFinished,
      "message.delta",
      {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
        role: "assistant",
        content: "partial reply",
        delta: "partial reply",
      },
      "2026-04-01T10:00:03.000Z",
      13,
    );
    const withCompleted = mergeConversationGenerationEvent(
      withDelta,
      "assistant.trace",
      {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
        state: "generation.completed",
      },
      "2026-04-01T10:00:04.000Z",
      14,
    );

    expect(withCompleted?.generations[0]?.status).toBe("completed");
    expect(withCompleted?.generations[0]?.steps).toMatchObject([
      {
        kind: "tool",
        tool_call_id: "tool-1",
        status: "completed",
        phase: "tool_result",
      },
      {
        kind: "output",
        status: "running",
        phase: "synthesis",
        delta_text: "partial reply",
      },
      {
        kind: "status",
        status: "completed",
        state: "generation.completed",
      },
    ]);
  });

  it("dedupes replayed timeline entries by server cursor", () => {
    const initialEvents = mergeSessionEventEntries([], {
      id: "session-1:12",
      sessionId: "session-1",
      cursor: 12,
      type: "assistant.summary",
      createdAt: "2026-04-01T10:00:02.000Z",
      summary: "第一次摘要",
      payload: { summary: "第一次摘要" },
    });

    const replayedEvents = mergeSessionEventEntries(initialEvents, {
      id: "session-1:12",
      sessionId: "session-1",
      cursor: 12,
      type: "assistant.summary",
      createdAt: "2026-04-01T10:00:03.000Z",
      summary: "重复回放",
      payload: { summary: "重复回放" },
    });

    expect(replayedEvents).toHaveLength(1);
    expect(replayedEvents[0]).toMatchObject({
      cursor: 12,
      summary: "重复回放",
    });
  });
});
