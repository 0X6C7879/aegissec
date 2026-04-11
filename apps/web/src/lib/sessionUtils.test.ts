import { describe, expect, it } from "vitest";
import {
  extractSafeSessionSummary,
  mergeConversationGenerationEvent,
  mergeConversationReasoningEvent,
  mergeQueueState,
  mergeSessionEventEntries,
  mergeSessionMessage,
  shouldStoreRealtimeEvent,
  toSessionMessageEvent,
} from "./sessionUtils";
import type { SessionConversation, SessionDetail } from "../types/sessions";

function createBaseConversation(): SessionConversation {
  return {
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
}

function createBaseSessionDetail(messages: SessionDetail["messages"] = []): SessionDetail {
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
    messages,
  };
}

function createRichShellMetadata() {
  return {
    result: {
      output: {
        stdout: "result stdout",
        stderr: "result stderr",
        exit_code: 0,
        artifact_paths: ["artifacts/result.log"],
      },
      execution: {
        payload: {
          data: {
            stdout: "result execution stdout",
          },
        },
      },
    },
    output: {
      stdout: "output stdout",
      stderr: "output stderr",
      exit_code: 0,
      artifact_paths: ["artifacts/output.log"],
    },
    execution: {
      payload: {
        data: {
          stdout: "execution payload stdout",
          stderr: "execution payload stderr",
          exit_code: 0,
          artifact_paths: ["artifacts/execution.log"],
        },
      },
    },
    payload: {
      data: {
        stdout: "payload data stdout",
        stderr: "payload data stderr",
        exit_code: 0,
        artifact_paths: ["artifacts/payload.log"],
      },
    },
    data: {
      stdout: "data stdout",
      stderr: "data stderr",
      exit_code: 0,
      artifact_paths: ["artifacts/data.log"],
    },
    stdout: "top-level stdout",
    stderr: "top-level stderr",
    exit_code: 0,
    artifact_paths: ["artifacts/top.log"],
  };
}

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
    expect(
      shouldStoreRealtimeEvent("session.compaction.completed", {
        summary: "已压缩对话",
      }),
    ).toBe(true);
    expect(
      shouldStoreRealtimeEvent("session.context_window.updated", {
        used_tokens: 1200,
      }),
    ).toBe(false);
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

  it("maps compaction traces and session events to safe visible summaries", () => {
    expect(
      extractSafeSessionSummary("session.compaction.completed", {
        summary: "已压缩对话",
      }),
    ).toEqual({
      label: "上下文压缩",
      summary: "已压缩对话",
      tone: "success",
    });

    expect(
      extractSafeSessionSummary("session.compaction.failed", {
        error: "active generation is running",
      }),
    ).toEqual({
      label: "上下文压缩",
      summary: "上下文压缩失败：active generation is running",
      tone: "error",
    });

    expect(
      extractSafeSessionSummary("assistant.trace", {
        state: "context.compacted",
      }),
    ).toEqual({
      label: "上下文压缩",
      summary: "已压缩对话",
      tone: "success",
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

  it("preserves shell result fields from transcript segment roots when metadata is absent", () => {
    expect(
      toSessionMessageEvent(
        {
          id: "assistant-message-2",
          session_id: "session-1",
          role: "assistant",
          content: "最终答复",
          attachments: [],
          assistant_transcript: [
            {
              id: "segment-root-shell",
              sequence: 1,
              kind: "tool_result",
              status: "completed",
              title: "工具执行结果",
              text: null,
              tool_name: "execute_kali_command",
              tool_call_id: "tool-root-1",
              recorded_at: "2026-04-01T10:00:03.000Z",
              updated_at: "2026-04-01T10:00:04.000Z",
              command: "curl -s http://target",
              stdout: "root stdout",
              stderr: "",
              result: {
                stdout: "root stdout",
              },
              output: {
                text: "root output text",
              },
            },
          ],
        },
        "session-1",
        "2026-04-01T10:00:04.000Z",
      ),
    ).toMatchObject({
      id: "assistant-message-2",
      assistant_transcript: [
        {
          kind: "tool_result",
          tool_name: "execute_kali_command",
          metadata: {
            command: "curl -s http://target",
            stdout: "root stdout",
            stderr: "",
            result: {
              stdout: "root stdout",
            },
            output: {
              text: "root output text",
            },
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
        stdout: "scan done",
        stderr: "",
        exit_code: 0,
        artifact_paths: ["reports/scan.txt"],
        result: {
          command: "nmap 127.0.0.1",
          status: "completed",
          stdout: "scan done",
          stderr: "",
          exit_code: 0,
          artifacts: ["reports/scan.txt"],
        },
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
        metadata: {
          stdout: "scan done",
          stderr: "",
          exit_code: 0,
          artifact_paths: ["reports/scan.txt"],
          result: {
            command: "nmap 127.0.0.1",
            status: "completed",
            stdout: "scan done",
            stderr: "",
            exit_code: 0,
            artifacts: ["reports/scan.txt"],
          },
        },
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

  it("preserves top-level output containers from live tool completion events", () => {
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

    const merged = mergeConversationGenerationEvent(
      baseConversation,
      "tool.call.finished",
      {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
        tool: "execute_kali_command",
        tool_call_id: "tool-output-1",
        command: "nmap 127.0.0.1",
        status: "completed",
        output: {
          stdout: "scan done",
          stderr: "",
        },
      },
      "2026-04-01T10:00:02.000Z",
      15,
    );

    expect(merged?.generations[0]?.steps).toMatchObject([
      {
        kind: "tool",
        tool_call_id: "tool-output-1",
        status: "completed",
        phase: "tool_result",
        metadata: {
          output: {
            stdout: "scan done",
            stderr: "",
          },
        },
      },
    ]);
  });

  it("preserves sibling live tool completion containers used by shell rendering", () => {
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

    const merged = mergeConversationGenerationEvent(
      baseConversation,
      "tool.call.finished",
      {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
        tool: "execute_kali_command",
        tool_call_id: "tool-output-2",
        command: "dirb http://target",
        status: "completed",
        execution: {
          stdout: "execution stdout",
          stderr: "",
        },
        payload: {
          text: "payload output",
        },
        data: {
          stdout: "data stdout",
        },
      },
      "2026-04-01T10:00:03.000Z",
      16,
    );

    expect(merged?.generations[0]?.steps).toMatchObject([
      {
        kind: "tool",
        tool_call_id: "tool-output-2",
        status: "completed",
        phase: "tool_result",
        metadata: {
          execution: {
            stdout: "execution stdout",
            stderr: "",
          },
          payload: {
            text: "payload output",
          },
          data: {
            stdout: "data stdout",
          },
        },
      },
    ]);
  });

  it("does not downgrade rich live tool step metadata when a later thin replay arrives", () => {
    const withRichResult = mergeConversationGenerationEvent(
      createBaseConversation(),
      "tool.call.finished",
      {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
        tool: "execute_kali_command",
        tool_call_id: "tool-rich-1",
        command: "bash -lc whoami",
        status: "completed",
        ...createRichShellMetadata(),
      },
      "2026-04-01T10:00:05.000Z",
      17,
    );

    const withThinReplay = mergeConversationGenerationEvent(
      withRichResult,
      "tool.call.finished",
      {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
        tool: "execute_kali_command",
        tool_call_id: "tool-rich-1",
        command: "bash -lc whoami",
        status: "completed",
        result: {
          status: "completed",
        },
      },
      "2026-04-01T10:00:06.000Z",
      18,
    );

    expect(withThinReplay?.generations[0]?.steps).toMatchObject([
      {
        kind: "tool",
        tool_call_id: "tool-rich-1",
        status: "completed",
        metadata: {
          result: {
            status: "completed",
            output: {
              stdout: "result stdout",
              stderr: "result stderr",
              exit_code: 0,
              artifact_paths: ["artifacts/result.log"],
            },
            execution: {
              payload: {
                data: {
                  stdout: "result execution stdout",
                },
              },
            },
          },
          output: {
            stdout: "output stdout",
            stderr: "output stderr",
            exit_code: 0,
            artifact_paths: ["artifacts/output.log"],
          },
          execution: {
            payload: {
              data: {
                stdout: "execution payload stdout",
                stderr: "execution payload stderr",
                exit_code: 0,
                artifact_paths: ["artifacts/execution.log"],
              },
            },
          },
          payload: {
            data: {
              stdout: "payload data stdout",
              stderr: "payload data stderr",
              exit_code: 0,
              artifact_paths: ["artifacts/payload.log"],
            },
          },
          data: {
            stdout: "data stdout",
            stderr: "data stderr",
            exit_code: 0,
            artifact_paths: ["artifacts/data.log"],
          },
          stdout: "top-level stdout",
          stderr: "top-level stderr",
          exit_code: 0,
          artifact_paths: ["artifacts/top.log"],
        },
      },
    ]);
  });

  it("keeps the stronger assistant body when a later update only carries partial content", () => {
    const merged = mergeSessionMessage(
      {
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
        messages: [
          {
            id: "assistant-message-1",
            session_id: "session-1",
            role: "assistant",
            content: "最终答复包含完整结论",
            assistant_transcript: [],
            attachments: [],
            created_at: "2026-04-01T10:00:01.000Z",
          },
        ],
      },
      {
        id: "assistant-message-1",
        session_id: "session-1",
        role: "assistant",
        content: "最终答复",
        assistant_transcript: [],
        attachments: [],
        created_at: "2026-04-01T10:00:02.000Z",
      },
    );

    expect(merged?.messages).toMatchObject([
      {
        id: "assistant-message-1",
        content: "最终答复包含完整结论",
      },
    ]);
  });

  it("preserves an existing rich transcript when the incoming tool segment is thin", () => {
    const merged = mergeSessionMessage(
      createBaseSessionDetail([
        {
          id: "assistant-message-1",
          session_id: "session-1",
          role: "assistant",
          content: "最终答复",
          assistant_transcript: [
            {
              id: "segment-rich",
              sequence: 1,
              kind: "tool_result",
              status: "completed",
              title: "命令执行结果",
              text: "工具执行完成。",
              tool_name: "execute_kali_command",
              tool_call_id: "tool-transcript-1",
              recorded_at: "2026-04-01T10:00:01.000Z",
              updated_at: "2026-04-01T10:00:02.000Z",
              metadata: createRichShellMetadata(),
            },
          ],
          attachments: [],
          created_at: "2026-04-01T10:00:01.000Z",
        },
      ]),
      {
        id: "assistant-message-1",
        session_id: "session-1",
        role: "assistant",
        content: "最终答复",
        assistant_transcript: [
          {
            id: "segment-rich",
            sequence: 1,
            kind: "tool_result",
            status: "completed",
            title: "命令执行结果",
            text: "命令已完成。",
            tool_name: "execute_kali_command",
            tool_call_id: "tool-transcript-1",
            recorded_at: "2026-04-01T10:00:03.000Z",
            updated_at: "2026-04-01T10:00:04.000Z",
            metadata: {
              result: {
                status: "completed",
              },
            },
          },
        ],
        attachments: [],
        created_at: "2026-04-01T10:00:04.000Z",
      },
    );

    expect(merged?.messages[0]?.assistant_transcript).toMatchObject([
      {
        id: "segment-rich",
        tool_call_id: "tool-transcript-1",
        metadata: {
          result: {
            status: "completed",
            output: {
              stdout: "result stdout",
              stderr: "result stderr",
              exit_code: 0,
              artifact_paths: ["artifacts/result.log"],
            },
          },
          execution: {
            payload: {
              data: {
                stdout: "execution payload stdout",
                stderr: "execution payload stderr",
              },
            },
          },
          payload: {
            data: {
              stdout: "payload data stdout",
              stderr: "payload data stderr",
            },
          },
          data: {
            stdout: "data stdout",
            stderr: "data stderr",
          },
          stdout: "top-level stdout",
          stderr: "top-level stderr",
          exit_code: 0,
          artifact_paths: ["artifacts/top.log"],
        },
      },
    ]);
  });

  it("enriches a thin incoming transcript from richer generation tool step context", () => {
    const detailWithGeneration = {
      ...createBaseSessionDetail([
        {
          id: "assistant-message-1",
          session_id: "session-1",
          generation_id: "generation-1",
          role: "assistant",
          content: "占位答复",
          assistant_transcript: [],
          attachments: [],
          created_at: "2026-04-01T10:00:01.000Z",
        },
      ]),
      generations: [
        {
          id: "generation-1",
          session_id: "session-1",
          branch_id: "branch-1",
          action: "reply",
          assistant_message_id: "assistant-message-1",
          status: "completed",
          reasoning_trace: [],
          steps: [
            {
              id: "generation-step-rich",
              generation_id: "generation-1",
              session_id: "session-1",
              message_id: "assistant-message-1",
              sequence: 1,
              kind: "tool",
              phase: "tool_result",
              status: "completed",
              state: "finished",
              label: "命令执行结果",
              safe_summary: "工具执行完成。",
              delta_text: "",
              tool_name: "execute_kali_command",
              tool_call_id: "tool-generation-1",
              command: "bash -lc whoami",
              metadata: createRichShellMetadata(),
              started_at: "2026-04-01T10:00:01.000Z",
              ended_at: "2026-04-01T10:00:02.000Z",
            },
          ],
          created_at: "2026-04-01T10:00:00.000Z",
          updated_at: "2026-04-01T10:00:02.000Z",
        },
      ],
    };

    const merged = mergeSessionMessage(detailWithGeneration, {
      id: "assistant-message-1",
      session_id: "session-1",
      generation_id: "generation-1",
      role: "assistant",
      content: "最终答复",
      assistant_transcript: [
        {
          id: "segment-thin-incoming",
          sequence: 1,
          kind: "tool_result",
          status: "completed",
          title: "命令执行结果",
          text: "命令已完成。",
          tool_name: "execute_kali_command",
          tool_call_id: "tool-generation-1",
          recorded_at: "2026-04-01T10:00:03.000Z",
          updated_at: "2026-04-01T10:00:04.000Z",
          metadata: {
            result: {
              status: "completed",
            },
          },
        },
      ],
      attachments: [],
      created_at: "2026-04-01T10:00:04.000Z",
    });

    const mergedSegment = merged?.messages[0]?.assistant_transcript[0];
    expect(merged?.messages[0]?.assistant_transcript).toHaveLength(1);
    expect(mergedSegment?.id).toBe("segment-thin-incoming");
    expect(mergedSegment?.tool_call_id).toBe("tool-generation-1");
    expect(mergedSegment?.metadata).toMatchObject({
      result: {
        status: "completed",
        output: {
          stdout: "result stdout",
          stderr: "result stderr",
          exit_code: 0,
        },
      },
      output: {
        stdout: "output stdout",
        stderr: "output stderr",
      },
      execution: {
        payload: {
          data: {
            stdout: "execution payload stdout",
            stderr: "execution payload stderr",
          },
        },
      },
      payload: {
        data: {
          stdout: "payload data stdout",
        },
      },
      data: {
        stdout: "data stdout",
      },
      stdout: "top-level stdout",
      stderr: "top-level stderr",
      exit_code: 0,
    });
    expect(mergedSegment?.metadata?.["artifact_paths"]).toEqual(["artifacts/top.log"]);
  });

  it("merges tool transcript segments semantically by kind and tool_call_id even when ids differ", () => {
    const merged = mergeSessionMessage(
      createBaseSessionDetail([
        {
          id: "assistant-message-1",
          session_id: "session-1",
          role: "assistant",
          content: "最终答复",
          assistant_transcript: [
            {
              id: "segment-original",
              sequence: 1,
              kind: "tool_result",
              status: "completed",
              title: "命令执行结果",
              text: "第一次回放。",
              tool_name: "execute_kali_command",
              tool_call_id: "tool-semantic-1",
              recorded_at: "2026-04-01T10:00:01.000Z",
              updated_at: "2026-04-01T10:00:02.000Z",
              metadata: {
                output: {
                  stdout: "existing stdout",
                },
                artifact_paths: ["artifacts/existing.log"],
              },
            },
          ],
          attachments: [],
          created_at: "2026-04-01T10:00:01.000Z",
        },
      ]),
      {
        id: "assistant-message-1",
        session_id: "session-1",
        role: "assistant",
        content: "最终答复",
        assistant_transcript: [
          {
            id: "segment-replayed-with-new-id",
            sequence: 1,
            kind: "tool_result",
            status: "completed",
            title: "命令执行结果",
            text: "第二次回放。",
            tool_name: "execute_kali_command",
            tool_call_id: "tool-semantic-1",
            recorded_at: "2026-04-01T10:00:03.000Z",
            updated_at: "2026-04-01T10:00:04.000Z",
            metadata: {
              output: {
                stderr: "incoming stderr",
              },
              artifact_paths: ["artifacts/incoming.log"],
            },
          },
        ],
        attachments: [],
        created_at: "2026-04-01T10:00:04.000Z",
      },
    );

    const mergedSegment = merged?.messages[0]?.assistant_transcript[0];

    expect(merged?.messages[0]?.assistant_transcript).toHaveLength(1);
    expect(mergedSegment?.id).toBe("segment-original");
    expect(mergedSegment?.tool_call_id).toBe("tool-semantic-1");
    expect(mergedSegment?.metadata).toMatchObject({
      output: {
        stdout: "existing stdout",
        stderr: "incoming stderr",
      },
    });
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

  it("keeps queued generations stable when the active generation completes and the next one starts", () => {
    const baseQueue = {
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
      active_generation: {
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
      queued_generations: [
        {
          id: "optimistic-generation-2",
          session_id: "session-1",
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

    const afterCompleted = mergeQueueState(baseQueue, "assistant.trace", {
      generation_id: "generation-1",
      state: "generation.completed",
    });

    expect(afterCompleted).toMatchObject({
      active_generation: null,
      active_generation_id: null,
      queued_generation_count: 1,
      queued_generations: [{ id: "optimistic-generation-2" }],
    });

    const afterNextStarted = mergeQueueState(afterCompleted, "generation.started", {
      generation_id: "optimistic-generation-2",
      queued_prompt_count: 0,
    });

    expect(afterNextStarted).toMatchObject({
      active_generation: { id: "optimistic-generation-2", status: "running" },
      active_generation_id: "optimistic-generation-2",
      queued_generations: [],
      queued_generation_count: 0,
    });
  });
});
