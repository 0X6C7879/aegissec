import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  AssistantTranscriptSegment,
  ChatGeneration,
  GenerationStep,
  SessionMessage,
} from "../types/sessions";
import "../styles.css";
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

  it("reads shell stdout and stderr from metadata.result without surfacing raw JSON", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-result-call",
                kind: "tool_call",
                sequence: 1,
                tool_name: "bash",
                tool_call_id: "tool-result-direct",
                text: "准备执行 python verify.py",
                metadata: {
                  command: "python verify.py",
                },
              }),
              buildTranscriptSegment({
                id: "segment-result-direct",
                kind: "tool_result",
                sequence: 2,
                tool_name: "bash",
                tool_call_id: "tool-result-direct",
                text: "命令执行结束。",
                metadata: {
                  result: {
                    command: "python verify.py",
                    stdout: "verification complete",
                    stderr: "minor warning",
                    exit_code: 2,
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-result-output",
                kind: "output",
                sequence: 3,
                text: "结果已经整理完成。",
              }),
            ],
            content: "结果已经整理完成。",
          },
        })}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    fireEvent.click(container.querySelector(".assistant-tool-summary")!);

    expect(screen.getByText("verification complete")).toBeInTheDocument();
    expect(screen.getByText("minor warning")).toBeInTheDocument();
    expect(screen.getByText("退出码：2")).toBeInTheDocument();
    expect(screen.queryByText(/"stdout":/)).not.toBeInTheDocument();
  });

  it("reads metadata.output stdout and stderr while preserving present-empty stdout", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-output-call",
                kind: "tool_call",
                sequence: 1,
                tool_name: "bash",
                tool_call_id: "tool-output-paths",
                text: "准备执行 whoami",
                metadata: {
                  command: "whoami",
                },
              }),
              buildTranscriptSegment({
                id: "segment-output-result",
                kind: "tool_result",
                sequence: 2,
                tool_name: "bash",
                tool_call_id: "tool-output-paths",
                text: "命令执行结束。",
                metadata: {
                  output: {
                    stdout: "",
                    stderr: ["warn one", "warn two"],
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-output-final",
                kind: "output",
                sequence: 3,
                text: "输出路径校验完成。",
              }),
            ],
            content: "输出路径校验完成。",
          },
        })}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    fireEvent.click(container.querySelector(".assistant-tool-summary")!);

    expect(screen.getByText("(empty)")).toBeInTheDocument();
    expect(screen.getByText(/warn one\s+warn two/)).toBeInTheDocument();
  });

  it("renders result-only shell blocks and reads metadata.result.output.text", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-result-only",
                kind: "tool_result",
                sequence: 1,
                tool_name: "bash",
                tool_call_id: "tool-result-only",
                text: "结果已返回。",
                metadata: {
                  result: {
                    command: "ls -la",
                    exit_code: 0,
                    output: {
                      text: "directory listing ready",
                    },
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-result-only-final",
                kind: "output",
                sequence: 2,
                text: "孤立结果也已展示。",
              }),
            ],
            content: "孤立结果也已展示。",
          },
        })}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    expect(container.querySelectorAll(".assistant-tool-block")).toHaveLength(1);

    fireEvent.click(container.querySelector(".assistant-tool-summary")!);

    expect(screen.getAllByText("ls -la")).toHaveLength(2);
    expect(screen.getByText("directory listing ready")).toBeInTheDocument();
    expect(screen.getByText("退出码：0")).toBeInTheDocument();
  });

  it("renders result-only shell output even when the backend omits command metadata", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-result-only-no-command",
                kind: "tool_result",
                sequence: 1,
                tool_name: "bash",
                tool_call_id: "tool-result-only-no-command",
                text: "命令执行结束。",
                metadata: {
                  result: {
                    stdout: "fallback stdout still visible",
                    stderr: "",
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-result-only-no-command-output",
                kind: "output",
                sequence: 2,
                text: "无命令元数据的结果也已展示。",
              }),
            ],
            content: "无命令元数据的结果也已展示。",
          },
        })}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    expect(container.querySelectorAll(".assistant-tool-block")).toHaveLength(1);

    fireEvent.click(container.querySelector(".assistant-tool-summary")!);

    expect(screen.getByText("fallback stdout still visible")).toBeInTheDocument();
    expect(screen.getAllByText("Shell").length).toBeGreaterThan(0);
  });

  it("globally pairs non-adjacent tool segments by tool_call_id and falls back to message and summary text", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-pair-call-1",
                kind: "tool_call",
                sequence: 1,
                tool_name: "bash",
                tool_call_id: "tool-pair-1",
                text: "准备执行 echo first",
                metadata: {
                  command: "echo first",
                },
              }),
              buildTranscriptSegment({
                id: "segment-pair-reasoning",
                kind: "reasoning",
                sequence: 2,
                text: "中间插入的思考不应打断配对。",
              }),
              buildTranscriptSegment({
                id: "segment-pair-result-1",
                kind: "tool_result",
                sequence: 3,
                tool_name: "bash",
                tool_call_id: "tool-pair-1",
                text: "首个结果已完成。",
                metadata: {
                  result: {
                    message: "message fallback rendered",
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-pair-call-2",
                kind: "tool_call",
                sequence: 4,
                tool_name: "bash",
                tool_call_id: "tool-pair-2",
                text: "准备执行 echo second",
                metadata: {
                  command: "echo second",
                },
              }),
              buildTranscriptSegment({
                id: "segment-pair-result-2",
                kind: "tool_result",
                sequence: 5,
                tool_name: "bash",
                tool_call_id: "tool-pair-2",
                text: "第二个结果已完成。",
                metadata: {
                  result: {
                    summary: "summary fallback rendered",
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-pair-output",
                kind: "output",
                sequence: 6,
                text: "最终输出保持不变。",
              }),
            ],
            content: "最终输出保持不变。",
          },
        })}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    const transcriptOrder = [...container.querySelectorAll(".assistant-transcript > *")].map(
      (element) => {
        if (element.classList.contains("assistant-tool-block")) {
          return "tool";
        }
        if (element.classList.contains("assistant-reasoning-block")) {
          return "reasoning";
        }
        if (element.classList.contains("assistant-output-block")) {
          return "output";
        }
        return "unknown";
      },
    );

    expect(container.querySelectorAll(".assistant-tool-block")).toHaveLength(2);
    expect(transcriptOrder).toEqual(["tool", "reasoning", "tool", "output"]);

    const summaries = container.querySelectorAll(".assistant-tool-summary");
    fireEvent.click(summaries[0]!);
    fireEvent.click(summaries[1]!);

    expect(screen.getByText("message fallback rendered")).toBeInTheDocument();
    expect(screen.getByText("summary fallback rendered")).toBeInTheDocument();
    expect(screen.getByText("中间插入的思考不应打断配对。")) .toBeInTheDocument();
  });

  it("prefers the latest tool result for a repeated tool_call_id", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-repeat-call",
                kind: "tool_call",
                sequence: 1,
                tool_name: "bash",
                tool_call_id: "tool-repeat-1",
                text: "准备执行 echo refresh",
                metadata: {
                  command: "echo refresh",
                },
              }),
              buildTranscriptSegment({
                id: "segment-repeat-result-1",
                kind: "tool_result",
                sequence: 2,
                tool_name: "bash",
                tool_call_id: "tool-repeat-1",
                text: "早期结果。",
                metadata: {
                  result: {
                    stdout: "stale output",
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-repeat-result-2",
                kind: "tool_result",
                sequence: 3,
                tool_name: "bash",
                tool_call_id: "tool-repeat-1",
                text: "最终结果。",
                metadata: {
                  result: {
                    stdout: "latest output",
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-repeat-output",
                kind: "output",
                sequence: 4,
                text: "重复结果校验完成。",
              }),
            ],
            content: "重复结果校验完成。",
          },
        })}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    fireEvent.click(container.querySelector(".assistant-tool-summary")!);

    expect(screen.getByText("latest output")).toBeInTheDocument();
    expect(screen.queryByText("stale output")).not.toBeInTheDocument();
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

  it("keeps the final assistant answer visible when live output only contains a partial transcript", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            content: "最终答复已经完整返回。",
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-tool-call-partial",
                kind: "tool_call",
                sequence: 1,
                tool_name: "bash",
                tool_call_id: "tool-call-partial",
                text: "printf 'partial output'",
                metadata: {
                  command: "printf 'partial output'",
                },
              }),
              buildTranscriptSegment({
                id: "segment-tool-result-partial",
                kind: "tool_result",
                sequence: 2,
                tool_name: "bash",
                tool_call_id: "tool-call-partial",
                text: "partial output",
                metadata: {
                  result: {
                    command: "printf 'partial output'",
                    status: "completed",
                  },
                },
              }),
              buildTranscriptSegment({
                id: "segment-output-partial",
                kind: "output",
                sequence: 3,
                text: "最终答复",
              }),
            ],
          },
        })}
        generations={[buildGeneration()]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    fireEvent.click(container.querySelector(".assistant-tool-summary")!);

    expect(screen.getByText("partial output")).toBeInTheDocument();
    expect(screen.getByText("最终答复已经完整返回。")).toBeInTheDocument();
    expect(container.querySelectorAll(".assistant-output-block")).toHaveLength(2);
    expect(container.querySelectorAll(".assistant-output-block-final")).toHaveLength(1);
  });

  it("fills in missing shell results from live generation steps when the message transcript only has the tool call", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            content: "",
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-tool-call-live",
                kind: "tool_call",
                sequence: 1,
                status: "running",
                tool_name: "execute_kali_command",
                tool_call_id: "tool-live-1",
                text: "pytest -q",
                metadata: {
                  command: "pytest -q",
                },
              }),
            ],
          },
        })}
        generations={[
          buildGeneration({
            status: "running",
            steps: [
              buildStep({
                id: "step-tool-result-live",
                kind: "tool",
                phase: "tool_result",
                status: "completed",
                sequence: 2,
                tool_name: "execute_kali_command",
                tool_call_id: "tool-live-1",
                command: "pytest -q",
                safe_summary: "命令已完成，状态：success。",
                metadata: {
                  result: {
                    command: "pytest -q",
                    stdout: "test session starts\ncollected 16 items",
                    stderr: "",
                    status: "success",
                  },
                },
              }),
            ],
          }),
        ]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    fireEvent.click(container.querySelector(".assistant-tool-summary")!);

    expect(screen.getByText(/test session starts\s+collected 16 items/)).toBeInTheDocument();
  });

  it("renders live shell output when the generation step stores results under metadata.output", () => {
    const { container } = render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            content: "",
            assistant_transcript: [
              buildTranscriptSegment({
                id: "segment-tool-call-live-output",
                kind: "tool_call",
                sequence: 1,
                status: "running",
                tool_name: "execute_kali_command",
                tool_call_id: "tool-live-output-1",
                text: "dirb http://target",
                metadata: {
                  command: "dirb http://target",
                },
              }),
            ],
          },
        })}
        generations={[
          buildGeneration({
            status: "running",
            steps: [
              buildStep({
                id: "step-tool-result-live-output",
                kind: "tool",
                phase: "tool_result",
                status: "completed",
                sequence: 2,
                tool_name: "execute_kali_command",
                tool_call_id: "tool-live-output-1",
                command: "dirb http://target",
                safe_summary: "命令已完成，状态：success。",
                metadata: {
                  output: {
                    stdout: "==> /admin",
                    stderr: "",
                  },
                },
              }),
            ],
          }),
        ]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    fireEvent.click(container.querySelector(".assistant-tool-summary")!);

    expect(screen.getByText("==> /admin")).toBeInTheDocument();
  });

  it.each([
    ["execution", { execution: { stdout: "execution stdout", stderr: "" } }, "execution stdout"],
    ["payload", { payload: { text: "payload output" } }, "payload output"],
    ["data", { data: { stdout: "data stdout", stderr: "" } }, "data stdout"],
  ])(
    "renders live shell output when the generation step stores results under metadata.%s",
    (_label, metadata, expectedOutput) => {
      const { container } = render(
        <ConversationFeed
          messages={buildMessages({
            assistant: {
              content: "",
              assistant_transcript: [
                buildTranscriptSegment({
                  id: "segment-tool-call-live-container",
                  kind: "tool_call",
                  sequence: 1,
                  status: "running",
                  tool_name: "execute_kali_command",
                  tool_call_id: "tool-live-container-1",
                  text: "gobuster dir -u http://target",
                  metadata: {
                    command: "gobuster dir -u http://target",
                  },
                }),
              ],
            },
          })}
          generations={[
            buildGeneration({
              status: "running",
              steps: [
                buildStep({
                  id: "step-tool-result-live-container",
                  kind: "tool",
                  phase: "tool_result",
                  status: "completed",
                  sequence: 2,
                  tool_name: "execute_kali_command",
                  tool_call_id: "tool-live-container-1",
                  command: "gobuster dir -u http://target",
                  safe_summary: "命令已完成，状态：success。",
                  metadata,
                }),
              ],
            }),
          ]}
          events={[]}
          runtimeRuns={[]}
        />,
      );

      fireEvent.click(container.querySelector(".assistant-tool-summary")!);

      expect(screen.getByText(expectedOutput)).toBeInTheDocument();
    },
  );

  it("keeps long inline code and autolink content wrap-enabled inside assistant markdown bubbles", () => {
    const longCode =
      "php://filter/convert.base64-encode/resource=/var/www/html/storage/logs/very-long-example-trace-file.php";
    const longUrl =
      "https://target.example/internal/really/long/path/with/no-natural-breakpoints/and/a/payload/that/should/stay/inside/the/bubble";

    render(
      <ConversationFeed
        messages={buildMessages({
          assistant: {
            assistant_transcript: [],
            content: `- 利用链入口：\`${longCode}\`\n- 参考地址：${longUrl}`,
          },
        })}
        generations={[buildGeneration({ steps: [] })]}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    const codeElement = screen.getByText(longCode);
    const linkElement = screen.getByRole("link", { name: longUrl });
    const markdownContainer = codeElement.closest(".chat-bubble-markdown");

    expect(codeElement.tagName).toBe("CODE");
    expect(markdownContainer).not.toBeNull();
    expect(getComputedStyle(markdownContainer!).overflowWrap).toBe("anywhere");
    expect(getComputedStyle(codeElement).overflowWrap).toBe("anywhere");
    expect(getComputedStyle(linkElement).overflowWrap).toBe("anywhere");
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
