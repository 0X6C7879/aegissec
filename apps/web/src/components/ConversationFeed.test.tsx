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

function buildMessages(
  overrides: {
    user?: Partial<SessionMessage>;
    assistant?: Partial<SessionMessage>;
  } = {},
): SessionMessage[] {
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
          id: "segment-reasoning-noise",
          kind: "reasoning",
          title: "思路进展",
          text: "Assistant is analyzing the request and preparing a response.",
        }),
        buildTranscriptSegment({
          id: "segment-reasoning",
          sequence: 2,
          kind: "reasoning",
          title: "思路进展",
          text: "<think>private</think>分析中",
        }),
        buildTranscriptSegment({
          id: "segment-status",
          sequence: 3,
          kind: "status",
          title: "运行状态",
          text: "Generation completed",
        }),
        buildTranscriptSegment({
          id: "segment-tool-call",
          sequence: 4,
          kind: "tool_call",
          title: "开始调用工具",
          tool_name: "bash",
          tool_call_id: "tool-call-1",
          text: "准备执行 nmap 127.0.0.1",
        }),
        buildTranscriptSegment({
          id: "segment-tool-result",
          sequence: 5,
          kind: "tool_result",
          title: "工具执行结果",
          tool_name: "bash",
          tool_call_id: "tool-call-1",
          text: "工具执行完成，状态：success。",
          metadata: {
            result: {
              status: "success",
              command: "nmap 127.0.0.1",
              exit_code: 0,
              stdout: "runtime command completed",
              stderr: "",
              artifacts: ["reports/auto.txt"],
            },
          },
        }),
        buildTranscriptSegment({
          id: "segment-output",
          sequence: 6,
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
  it("renders chronological reasoning, status, tool, and final output blocks together", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-reasoning-1",
                kind: "reasoning",
                title: "思路进展",
                text: "<think>private</think>正在检查 node5.buuoj.cn 的登录逻辑",
              }),
              buildTranscriptSegment({
                id: "segment-status",
                sequence: 2,
                kind: "status",
                title: "运行状态",
                text: "自动选择 ctf-web",
                metadata: { state: "skill.autoroute.selected", skill: "ctf-web" },
              }),
              buildTranscriptSegment({
                id: "segment-tool-call",
                sequence: 3,
                kind: "tool_call",
                title: "开始调用工具",
                tool_name: "bash",
                tool_call_id: "tool-call-1",
                text: "准备执行 nmap 127.0.0.1",
              }),
              buildTranscriptSegment({
                id: "segment-tool-result",
                sequence: 4,
                kind: "tool_result",
                title: "工具执行结果",
                tool_name: "bash",
                tool_call_id: "tool-call-1",
                text: "工具执行完成，状态：success。",
                metadata: {
                  result: {
                    status: "success",
                    command: "nmap 127.0.0.1",
                    exit_code: 0,
                    stdout: "runtime command completed",
                    stderr: "",
                    artifacts: ["reports/auto.txt"],
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-reasoning-2",
                sequence: 5,
                kind: "reasoning",
                title: "思路进展",
                text: "结合扫描结果继续确认过滤点",
              }),
              buildTranscriptSegment({
                id: "segment-output",
                sequence: 6,
                kind: "output",
                title: "正文输出",
                text: "<think>very secret</think>最终答复",
              }),
            ],
          },
        })}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    expect(screen.queryByText("本轮运行")).not.toBeInTheDocument();
    expect(screen.queryByText("运行时间线")).not.toBeInTheDocument();
    expect(screen.queryByText("生成队列")).not.toBeInTheDocument();
    expect(screen.queryByText("思路进展")).not.toBeInTheDocument();
    expect(screen.queryByText("工具调用")).not.toBeInTheDocument();
    expect(screen.queryByText("工具结果")).not.toBeInTheDocument();
    expect(screen.queryByText("正文输出")).not.toBeInTheDocument();
    expect(screen.queryByText("运行状态")).not.toBeInTheDocument();
    expect(screen.queryByText("思考过程")).not.toBeInTheDocument();
    expect(container.querySelector("details.assistant-reasoning-stream")).toBeNull();
    expect(container.querySelectorAll(".assistant-reasoning-block")).toHaveLength(2);
    expect(screen.getByText("自动选择")).toBeInTheDocument();
    expect(screen.getByText("ctf-web")).toBeInTheDocument();
    expect(container.querySelectorAll(".assistant-inline-cue")).toHaveLength(1);
    expect(container.querySelectorAll(".assistant-status-note")).toHaveLength(0);
    expect(screen.getAllByText("Shell").length).toBeGreaterThan(0);
    expect(screen.getAllByText("private").length).toBeGreaterThan(0);
    expect(screen.getByText("正在检查 node5.buuoj.cn 的登录逻辑")).toBeInTheDocument();
    expect(screen.getByText("结合扫描结果继续确认过滤点")).toBeInTheDocument();
    expect(screen.getAllByText("very secret").length).toBeGreaterThan(0);
    expect(screen.getByText("最终答复")).toBeInTheDocument();
    expect(
      container.querySelector(".assistant-output-block-final .assistant-inline-think"),
    ).not.toBeNull();
    const transcriptOrder = [...container.querySelectorAll(".assistant-transcript > *")].map(
      (element) => {
        if (element.classList.contains("assistant-reasoning-block")) {
          return "reasoning";
        }
        if (element.classList.contains("assistant-inline-cue")) {
          return "cue";
        }
        if (element.classList.contains("assistant-tool-block")) {
          return "tool";
        }
        if (element.classList.contains("assistant-output-block")) {
          return "output";
        }
        return "unknown";
      },
    );
    expect(transcriptOrder).toEqual(["reasoning", "cue", "tool", "reasoning", "output"]);
    expect(
      screen.queryByText("<think>private</think>正在检查 node5.buuoj.cn 的登录逻辑"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("<think>very secret</think>最终答复")).not.toBeInTheDocument();
    fireEvent.click(container.querySelector(".assistant-tool-summary")!);
    expect(screen.getByText("runtime command completed")).toBeInTheDocument();
    expect(screen.getByText("reports/auto.txt")).toBeInTheDocument();
    expect(screen.queryByText(/"status": "success"/)).not.toBeInTheDocument();
  });

  it("renders skill calls as lightweight inline names and keeps errors inline", () => {
    render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-skill-call",
                kind: "tool_call",
                sequence: 1,
                  tool_name: "execute_skill",
                  tool_call_id: "tool-skill-1",
                  text: "movement_tmux",
                metadata: {
                  arguments: {
                    skill_name_or_id: "movement_tmux",
                  },
                },
              }),
                buildTranscriptSegment({
                  id: "segment-skill-result",
                  kind: "tool_result",
                  sequence: 2,
                    tool_name: "execute_skill",
                    tool_call_id: "tool-skill-1",
                    text: "已准备 movement_tmux 技能上下文。",
                    metadata: {
                      result: {
                        execution: {
                          status: "prepared",
                        },
                        skill: {
                          title: "movement_tmux",
                      description: "Use tmux to move laterally.",
                      content: "# movement_tmux\nDetailed instructions",
                    },
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-error",
                kind: "error",
                sequence: 3,
                status: "failed",
                text: "连接目标失败。",
                metadata: {
                  detail: "socket timeout",
                },
              }),
              buildTranscriptSegment({
                id: "segment-output-final",
                kind: "output",
                sequence: 4,
                text: "最终建议在下一跳前重新确认权限。",
              }),
            ],
            content: "最终建议在下一跳前重新确认权限。",
          },
        })}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    expect(screen.getByText("Movement Tmux")).toBeInTheDocument();
    expect(screen.queryByText("Use tmux to move laterally.")).not.toBeInTheDocument();
    expect(screen.queryByText("# movement_tmux\nDetailed instructions")).not.toBeInTheDocument();
    expect(screen.queryByText("Read skill content for movement_tmux.")).not.toBeInTheDocument();
    expect(screen.getByText("连接目标失败。")).toBeInTheDocument();
    expect(screen.getByText("最终建议在下一跳前重新确认权限。")).toBeInTheDocument();
  });

  it("falls back to generation lifecycle text when status steps exist but carry no visible text", () => {
    render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            content: "",
            assistant_transcript: [],
          },
        })}
        generations={[
          buildGeneration({
            status: "running",
            steps: [
              buildStep({
                status: "running",
                phase: "planning",
                safe_summary: "",
                delta_text: "",
              }),
            ],
          }),
        ]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    expect(screen.getByText("正在持续更新当前回复。")).toBeInTheDocument();
    expect(document.querySelectorAll(".assistant-inline-cue")).toHaveLength(1);
  });

  it("preserves think blocks when rendering output-only generation fallback content", () => {
    render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            content: "",
            assistant_transcript: [],
          },
        })}
        generations={[
          buildGeneration({
            steps: [
              buildStep({
                id: "step-output-think",
                kind: "output",
                phase: "synthesis",
                status: "completed",
                delta_text: "<think>preserved</think>最终结论",
                safe_summary: "",
              }),
            ],
          }),
        ]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    expect(screen.getAllByText("preserved").length).toBeGreaterThan(0);
    expect(screen.getByText("最终结论")).toBeInTheDocument();
  });

  it("uses a single icon-only inline edit control for user messages", () => {
    const onEditMessage = vi.fn(async () => undefined);

    render(
      <ConversationFeed
        messages={buildMessages()}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
        onEditMessage={onEditMessage}
      />,
    );

    expect(screen.queryByRole("button", { name: "回溯到此" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开消息操作" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "分叉" })).not.toBeInTheDocument();
    expect(screen.queryByText("返回并编辑消息")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "返回并编辑消息" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "返回并编辑消息" }));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "暂存改写内容" },
    });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText("请继续追踪当前思路")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "返回并编辑消息" }));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "请继续追踪新的思路" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(onEditMessage).toHaveBeenCalledTimes(1);
    expect(onEditMessage).toHaveBeenCalledWith(
      expect.objectContaining({ id: "message-user" }),
      "请继续追踪新的思路",
    );
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
    expect(screen.getAllByText("已进入队列，前方还有 1 条等待。").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancelGeneration).toHaveBeenCalledWith("generation-queued");
  });
});
