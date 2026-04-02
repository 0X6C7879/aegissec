import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  AssistantTranscriptSegment,
  ChatGeneration,
  GenerationStep,
  SessionMessage,
} from "../types/sessions";
import { ConversationFeed } from "./ConversationFeed";

function buildTranscriptSegment(
  overrides: Partial<AssistantTranscriptSegment> = {},
): AssistantTranscriptSegment {
  return {
    id: "segment-1",
    sequence: 1,
    kind: "reasoning",
    status: "completed",
    title: "思路进展",
    text: "正在整理下一步。",
    recorded_at: "2026-04-01T10:00:01.000Z",
    updated_at: "2026-04-01T10:00:02.000Z",
    ...overrides,
  };
}

function buildStep(overrides: Partial<GenerationStep> = {}): GenerationStep {
  return {
    id: "step-1",
    generation_id: "generation-1",
    session_id: "session-1",
    message_id: "message-assistant",
    sequence: 1,
    kind: "status",
    phase: "planning",
    status: "completed",
    state: "completed",
    label: "运行状态",
    safe_summary: "当前生成已完成。",
    delta_text: "",
    started_at: "2026-04-01T10:00:01.000Z",
    ended_at: "2026-04-01T10:00:02.000Z",
    ...overrides,
  };
}

function buildGeneration(overrides: Partial<ChatGeneration> = {}): ChatGeneration {
  return {
    id: "generation-1",
    session_id: "session-1",
    branch_id: "branch-1",
    action: "reply",
    user_message_id: "message-user",
    assistant_message_id: "message-assistant",
    status: "completed",
    steps: [buildStep()],
    created_at: "2026-04-01T10:00:01.000Z",
    updated_at: "2026-04-01T10:00:04.000Z",
    ended_at: "2026-04-01T10:00:04.000Z",
    ...overrides,
  };
}

function buildMessages(overrides: {
  user?: Partial<SessionMessage>;
  assistant?: Partial<SessionMessage>;
} = {}): SessionMessage[] {
  return [
    {
      id: "message-user",
      session_id: "session-1",
      role: "user",
      content: "请继续追踪当前思路",
      assistant_transcript: [],
      attachments: [],
      created_at: "2026-04-01T10:00:00.000Z",
      ...overrides.user,
    },
    {
      id: "message-assistant",
      session_id: "session-1",
      generation_id: "generation-1",
      role: "assistant",
      content: "<think>very secret</think>最终答复",
      assistant_transcript: [
        buildTranscriptSegment({
          id: "segment-reasoning",
          kind: "reasoning",
          title: "思路进展",
          text: "<think>private</think>分析中",
        }),
        buildTranscriptSegment({
          id: "segment-tool-call",
          sequence: 2,
          kind: "tool_call",
          title: "开始调用工具",
          tool_name: "bash",
          tool_call_id: "tool-call-1",
          text: "准备执行 nmap 127.0.0.1",
        }),
        buildTranscriptSegment({
          id: "segment-tool-result",
          sequence: 3,
          kind: "tool_result",
          title: "工具执行结果",
          tool_name: "bash",
          tool_call_id: "tool-call-1",
          text: "工具执行完成，状态：success。",
          metadata: {
            stdout: "runtime command completed",
            stderr: "",
            artifacts: ["reports/auto.txt"],
            result: {
              status: "success",
              exit_code: 0,
            },
          },
        }),
        buildTranscriptSegment({
          id: "segment-output",
          sequence: 4,
          kind: "output",
          title: "正文输出",
          text: "<think>very secret</think>最终答复",
        }),
      ],
      attachments: [],
      created_at: "2026-04-01T10:00:04.000Z",
      ...overrides.assistant,
    },
  ];
}

describe("ConversationFeed", () => {
  it("renders the persisted assistant transcript inside one bubble and keeps think blocks visible", () => {
    render(
      <ConversationFeed
        messages={buildMessages()}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    expect(screen.queryByText("本轮运行")).not.toBeInTheDocument();
    expect(screen.queryByText("运行时间线")).not.toBeInTheDocument();
    expect(screen.queryByText("生成队列")).not.toBeInTheDocument();
    expect(screen.getByText("<think>private</think>分析中")).toBeInTheDocument();
    expect(screen.getByText("<think>very secret</think>最终答复")).toBeInTheDocument();
    expect(screen.getByText("runtime command completed")).toBeInTheDocument();
    expect(screen.getByText("reports/auto.txt")).toBeInTheDocument();
    expect(screen.getByText(/"status": "success"/)).toBeInTheDocument();
  });

  it("keeps rollback primary and moves edit or retry actions into the overflow menu", () => {
    const onEditMessage = vi.fn();
    const onRegenerateMessage = vi.fn();
    const onForkMessage = vi.fn();

    render(
      <ConversationFeed
        messages={buildMessages()}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
        onEditMessage={onEditMessage}
        onRegenerateMessage={onRegenerateMessage}
        onForkMessage={onForkMessage}
        onRollbackMessage={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("button", { name: "回溯到此" })).toHaveLength(2);

    const overflowButtons = screen.getAllByRole("button", { name: "打开消息操作" });
    fireEvent.click(overflowButtons[0]!);
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(onEditMessage).toHaveBeenCalledTimes(1);

    fireEvent.click(overflowButtons[1]!);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRegenerateMessage).toHaveBeenCalledTimes(1);

    fireEvent.click(overflowButtons[1]!);
    fireEvent.click(screen.getByRole("button", { name: "分叉" }));
    expect(onForkMessage).toHaveBeenCalledTimes(1);
  });

  it("renders queued generations inline as assistant bubbles and forwards cancel", () => {
    const onCancelGeneration = vi.fn();
    const messages: SessionMessage[] = [
      {
        id: "message-user",
        session_id: "session-1",
        role: "user",
        content: "排队中的下一条提示",
        assistant_transcript: [],
        attachments: [],
        created_at: "2026-04-01T10:01:00.000Z",
      },
    ];
    const queuedGeneration = buildGeneration({
      id: "generation-queued",
      user_message_id: "message-user",
      assistant_message_id: "message-assistant-queued",
      status: "queued",
      queue_position: 2,
      steps: [
        buildStep({
          id: "queued-step",
          generation_id: "generation-queued",
          message_id: "message-assistant-queued",
          status: "pending",
          state: "queued",
          safe_summary: "已进入队列，前方还有 1 条等待。",
          ended_at: null,
        }),
      ],
    });

    render(
      <ConversationFeed
        messages={messages}
        generations={[]}
        events={[]}
        runtimeRuns={[]}
        queuedGenerations={[queuedGeneration]}
        onCancelGeneration={onCancelGeneration}
      />,
    );

    expect(screen.queryByText("生成队列")).not.toBeInTheDocument();
    expect(screen.getByText("排队 #2")).toBeInTheDocument();
    expect(screen.getByText("已进入队列，前方还有 1 条等待。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancelGeneration).toHaveBeenCalledWith("generation-queued");
  });
});
