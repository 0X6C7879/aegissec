import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatGeneration, SessionEventEntry, SessionMessage } from "../types/sessions";
import { ConversationFeed } from "./ConversationFeed";

const messages: SessionMessage[] = [
  {
    id: "message-user",
    session_id: "session-1",
    role: "user",
    content: "请继续追踪当前思路",
    attachments: [],
    created_at: "2026-04-01T10:00:00.000Z",
  },
  {
    id: "message-assistant",
    session_id: "session-1",
    role: "assistant",
    content: "<think>private reasoning</think>\n\n已经整理出可验证的下一步。",
    attachments: [],
    created_at: "2026-04-01T10:00:04.000Z",
  },
];

describe("ConversationFeed", () => {
  it("renders safe assistant summaries without leaking hidden thinking text", () => {
    const events: SessionEventEntry[] = [
      {
        id: "event-generation-started",
        sessionId: "session-1",
        cursor: 1,
        type: "generation.started",
        createdAt: "2026-04-01T10:00:01.000Z",
        summary: "当前回复已开始。",
        payload: {
          generation_id: "generation-1",
          message_id: "message-assistant",
          queued_prompt_count: 1,
        },
      },
      {
        id: "event-summary",
        sessionId: "session-1",
        cursor: 2,
        type: "assistant.summary",
        createdAt: "2026-04-01T10:00:02.000Z",
        summary: "正在整理摘要。",
        payload: {
          summary: "<think>private reasoning</think> 正在整理可展示的高层摘要。",
          status: "running",
          reasoning: "private reasoning",
        },
      },
    ];

    render(<ConversationFeed messages={messages} generations={[]} events={events} runtimeRuns={[]} />);

    expect(screen.getByText("新一轮生成已开始，后面还有 1 条提示等待处理。")).toBeInTheDocument();
    expect(screen.getByText("思路摘要")).toBeInTheDocument();
    expect(screen.getByText("正在整理可展示的高层摘要。", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText("private reasoning")).not.toBeInTheDocument();
    expect(screen.queryByText("模型思路")).not.toBeInTheDocument();
    expect(screen.getByText("请继续追踪当前思路")).toBeInTheDocument();
    expect(screen.getByText("已经整理出可验证的下一步。")).toBeInTheDocument();
  });

  it("renders persisted reasoning history on cold load and dedupes overlapping live summaries", () => {
    const generations: ChatGeneration[] = [
      {
        id: "generation-1",
        session_id: "session-1",
        branch_id: "branch-1",
        action: "reply",
        assistant_message_id: "message-assistant",
        status: "completed",
        reasoning_trace: [
          {
            type: "assistant.trace",
            created_at: "2026-04-01T10:00:01.500Z",
            message_id: "message-assistant",
            status: "running",
            phase: "context_mapping",
            message: "<think>private reasoning</think> 正在把现有线索整理成可验证时间线。",
          },
          {
            type: "assistant.summary",
            created_at: "2026-04-01T10:00:02.000Z",
            message_id: "message-assistant",
            summary: "准备收敛到下一步验证。",
            status: "running",
          },
        ],
        created_at: "2026-04-01T10:00:01.000Z",
        updated_at: "2026-04-01T10:00:04.000Z",
        ended_at: "2026-04-01T10:00:04.000Z",
      },
    ];

    const events: SessionEventEntry[] = [
      {
        id: "event-summary-live",
        sessionId: "session-1",
        cursor: 3,
        type: "assistant.summary",
        createdAt: "2026-04-01T10:00:02.000Z",
        summary: "准备收敛到下一步验证。",
        payload: {
          message_id: "message-assistant",
          summary: "准备收敛到下一步验证。",
          status: "running",
        },
      },
    ];

    render(
      <ConversationFeed
        messages={messages}
        generations={generations}
        events={events}
        runtimeRuns={[]}
      />,
    );

    expect(screen.getByText("思路进展")).toBeInTheDocument();
    expect(
      screen.getByText("正在把现有线索整理成可验证时间线。", { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("准备收敛到下一步验证。")).toHaveLength(1);
    expect(screen.queryByText("private reasoning")).not.toBeInTheDocument();
  });

  it("keeps repeated identical reasoning steps when they have different stable identities", () => {
    const generations: ChatGeneration[] = [
      {
        id: "generation-1",
        session_id: "session-1",
        branch_id: "branch-1",
        action: "reply",
        assistant_message_id: "message-assistant",
        status: "completed",
        reasoning_trace: [
          {
            type: "assistant.trace",
            created_at: "2026-04-01T10:00:01.000Z",
            message_id: "message-assistant",
            sequence: 1,
            state: "tool.started",
            command: "nmap 127.0.0.1",
          },
          {
            type: "assistant.trace",
            created_at: "2026-04-01T10:00:02.000Z",
            message_id: "message-assistant",
            sequence: 2,
            state: "tool.started",
            command: "nmap 127.0.0.1",
          },
        ],
        created_at: "2026-04-01T10:00:00.000Z",
        updated_at: "2026-04-01T10:00:03.000Z",
      },
    ];

    render(
      <ConversationFeed
        messages={messages}
        generations={generations}
        events={[]}
        runtimeRuns={[]}
      />,
    );

    expect(screen.getAllByText("开始调用工具：nmap 127.0.0.1")).toHaveLength(2);
  });

  it("renders live observable assistant traces as thought entries", () => {
    const events: SessionEventEntry[] = [
      {
        id: "event-trace-live",
        sessionId: "session-1",
        cursor: 4,
        type: "assistant.trace",
        createdAt: "2026-04-01T10:00:03.000Z",
        summary: "开始调用工具：nmap 127.0.0.1",
        payload: {
          message_id: "message-assistant",
          state: "tool.started",
          command: "nmap 127.0.0.1",
        },
      },
    ];

    render(<ConversationFeed messages={messages} generations={[]} events={events} runtimeRuns={[]} />);

    expect(screen.getByText("思路进展 · tool started")).toBeInTheDocument();
    expect(screen.getByText("开始调用工具：nmap 127.0.0.1")).toBeInTheDocument();
  });

  it("renders skill tool calls without standalone skill chips in the body", () => {
    const skillEvents: SessionEventEntry[] = [
      {
        id: "skill-start",
        sessionId: "session-1",
        cursor: 3,
        type: "tool.call.started",
        createdAt: "2026-04-01T10:00:01.000Z",
        summary: "skill started",
        payload: {
          tool: "read_skill_content",
          tool_call_id: "tool-skill-1",
          arguments: {
            skill_name_or_id: "adscan",
          },
        },
      },
      {
        id: "skill-finished",
        sessionId: "session-1",
        cursor: 4,
        type: "tool.call.finished",
        createdAt: "2026-04-01T10:00:02.000Z",
        summary: "skill finished",
        payload: {
          tool: "read_skill_content",
          tool_call_id: "tool-skill-1",
          result: {
            skill: {
              directory_name: "adscan",
              content: "# adscan\nUse it for AD enumeration.",
            },
          },
        },
      },
    ];

    const { container } = render(
      <ConversationFeed
        messages={[
          {
            id: "message-user-skill",
            session_id: "session-1",
            role: "user",
            content: "use the adscan skill",
            attachments: [],
            created_at: "2026-04-01T10:00:00.000Z",
          },
        ]}
        generations={[]}
        events={skillEvents}
        runtimeRuns={[]}
      />,
    );

    expect(screen.getByText("Read skill adscan")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Read skill adscan/i }));
    expect(screen.getByText(/Use it for AD enumeration\./)).toBeInTheDocument();
    expect(container.querySelector(".assistant-tool-run-body .chat-artifact-chip")).toBeNull();
  });
});
