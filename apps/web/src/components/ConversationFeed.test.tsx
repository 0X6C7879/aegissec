import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatGeneration, GenerationStep, SessionMessage } from "../types/sessions";
import { ConversationFeed } from "./ConversationFeed";

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
      attachments: [],
      created_at: "2026-04-01T10:00:00.000Z",
      ...overrides.user,
    },
    {
      id: "message-assistant",
      session_id: "session-1",
      generation_id: "generation-1",
      role: "assistant",
      content: "<think>private reasoning</think>\n\n已经整理出可验证的下一步。",
      attachments: [],
      created_at: "2026-04-01T10:00:04.000Z",
      ...overrides.assistant,
    },
  ];
}

describe("ConversationFeed", () => {
  it("renders a bound generation timeline without leaking hidden thinking text", () => {
    const messages = buildMessages();
    const generations = [
      buildGeneration({
        steps: [
          buildStep({
            id: "step-status",
            safe_summary: "新一轮生成已开始，后面还有 1 条提示等待处理。",
            status: "running",
            state: "started",
            ended_at: null,
          }),
          buildStep({
            id: "step-reasoning",
            sequence: 2,
            kind: "reasoning",
            label: "思路摘要",
            safe_summary: "正在整理可展示的高层摘要。",
          }),
        ],
      }),
    ];

    render(<ConversationFeed messages={messages} generations={generations} events={[]} runtimeRuns={[]} />);

    expect(screen.getByText("本轮运行")).toBeInTheDocument();
    expect(screen.getByText("运行时间线")).toBeInTheDocument();
    expect(screen.getByText("新一轮生成已开始，后面还有 1 条提示等待处理。")).toBeInTheDocument();
    expect(screen.getByText("思路摘要")).toBeInTheDocument();
    expect(screen.getByText("正在整理可展示的高层摘要。")).toBeInTheDocument();
    expect(screen.getByText("已经整理出可验证的下一步。")).toBeInTheDocument();
    expect(screen.queryByText("private reasoning")).not.toBeInTheDocument();
    expect(screen.queryByText("执行过程")).not.toBeInTheDocument();
  });

  it("renders persisted generation steps on cold load", () => {
    const messages = buildMessages();
    const generations = [
      buildGeneration({
        steps: [
          buildStep({
            id: "step-reasoning",
            kind: "reasoning",
            label: "思路进展",
            safe_summary: "正在把现有线索整理成可验证时间线。",
          }),
          buildStep({
            id: "step-summary",
            sequence: 2,
            kind: "reasoning",
            label: "思路摘要",
            safe_summary: "准备收敛到下一步验证。",
          }),
        ],
      }),
    ];

    render(<ConversationFeed messages={messages} generations={generations} events={[]} runtimeRuns={[]} />);

    expect(screen.getByText("思路进展")).toBeInTheDocument();
    expect(screen.getByText("正在把现有线索整理成可验证时间线。")).toBeInTheDocument();
    expect(screen.getByText("思路摘要")).toBeInTheDocument();
    expect(screen.getAllByText("准备收敛到下一步验证。")).toHaveLength(1);
  });

  it("keeps repeated identical tool summaries when steps have different stable identities", () => {
    const messages = buildMessages();
    const generations = [
      buildGeneration({
        steps: [
          buildStep({
            id: "tool-step-1",
            kind: "tool",
            phase: "tool_running",
            status: "running",
            state: "started",
            safe_summary: "开始调用工具：nmap 127.0.0.1",
            tool_name: "bash",
            tool_call_id: "tool-call-1",
            command: "nmap 127.0.0.1",
            ended_at: null,
          }),
          buildStep({
            id: "tool-step-2",
            sequence: 2,
            kind: "tool",
            phase: "tool_running",
            status: "running",
            state: "started",
            safe_summary: "开始调用工具：nmap 127.0.0.1",
            tool_name: "bash",
            tool_call_id: "tool-call-2",
            command: "nmap 127.0.0.1",
            ended_at: null,
          }),
        ],
      }),
    ];

    render(<ConversationFeed messages={messages} generations={generations} events={[]} runtimeRuns={[]} />);

    expect(screen.getAllByText("开始调用工具：nmap 127.0.0.1")).toHaveLength(2);
  });

  it("renders queue panel state and forwards cancel actions", () => {
    const onCancelGeneration = vi.fn();
    const queuedUserMessage: SessionMessage = {
      id: "message-user-queued",
      session_id: "session-1",
      role: "user",
      content: "排队中的下一条提示",
      attachments: [],
      created_at: "2026-04-01T10:01:00.000Z",
    };
    const messages = [
      ...buildMessages(),
      queuedUserMessage,
    ];
    const activeGeneration = buildGeneration({
      id: "generation-active",
      user_message_id: "message-user",
      assistant_message_id: "message-assistant",
      status: "running",
      steps: [
        buildStep({
          id: "active-step",
          generation_id: "generation-active",
          status: "running",
          state: "started",
          safe_summary: "当前生成正在进行中。",
          ended_at: null,
        }),
      ],
    });
    const queuedGeneration = buildGeneration({
      id: "generation-queued",
      user_message_id: queuedUserMessage.id,
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
        activeGeneration={activeGeneration}
        queuedGenerations={[queuedGeneration]}
        onCancelGeneration={onCancelGeneration}
      />,
    );

    expect(screen.getByText("生成队列")).toBeInTheDocument();
    expect(screen.getByText("当前执行")).toBeInTheDocument();
    expect(screen.getByText("排队 #2")).toBeInTheDocument();
    expect(screen.getAllByText("排队中的下一条提示")).toHaveLength(2);

    const cancelButtons = screen.getAllByRole("button", { name: "取消" });
    fireEvent.click(cancelButtons[0]);
    fireEvent.click(cancelButtons[1]);

    expect(onCancelGeneration).toHaveBeenNthCalledWith(1, "generation-active");
    expect(onCancelGeneration).toHaveBeenNthCalledWith(2, "generation-queued");
  });

  it("renders skill-oriented tool steps inside the timeline without legacy standalone drawers", () => {
    const messages = [
      {
        id: "message-user-skill",
        session_id: "session-1",
        role: "user",
        content: "use the adscan skill",
        attachments: [],
        created_at: "2026-04-01T10:00:00.000Z",
      },
      {
        id: "message-assistant-skill",
        session_id: "session-1",
        generation_id: "generation-skill",
        role: "assistant",
        content: "我已经加载技能并继续处理。",
        attachments: [],
        created_at: "2026-04-01T10:00:04.000Z",
      },
    ] satisfies SessionMessage[];
    const generations = [
      buildGeneration({
        id: "generation-skill",
        user_message_id: "message-user-skill",
        assistant_message_id: "message-assistant-skill",
        steps: [
          buildStep({
            id: "skill-step",
            generation_id: "generation-skill",
            message_id: "message-assistant-skill",
            kind: "tool",
            phase: "tool_result",
            status: "completed",
            state: "finished",
            tool_name: "read_skill_content",
            tool_call_id: "tool-skill-1",
            safe_summary: "Read skill adscan",
            metadata: {
              skill: {
                directory_name: "adscan",
                content: "Use it for AD enumeration.",
              },
            },
          }),
        ],
      }),
    ];

    const { container } = render(
      <ConversationFeed messages={messages} generations={generations} events={[]} runtimeRuns={[]} />,
    );

    expect(screen.getByText("Read skill adscan")).toBeInTheDocument();
    expect(screen.queryByText("执行过程")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Read skill adscan/i })).toBeNull();
    expect(container.querySelector(".assistant-tool-run-body .chat-artifact-chip")).toBeNull();
  });
});
