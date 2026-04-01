import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SessionEventEntry, SessionMessage } from "../types/sessions";
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
    content: "已经整理出可验证的下一步。",
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

    render(<ConversationFeed messages={messages} events={events} runtimeRuns={[]} />);

    expect(screen.getByText("新一轮生成已开始，后面还有 1 条提示等待处理。")).toBeInTheDocument();
    expect(screen.getByText("思路摘要")).toBeInTheDocument();
    expect(screen.getByText("正在整理可展示的高层摘要。", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText("private reasoning")).not.toBeInTheDocument();
    expect(screen.getByText("请继续追踪当前思路")).toBeInTheDocument();
    expect(screen.getByText("已经整理出可验证的下一步。")).toBeInTheDocument();
  });
  it("renders skill tool calls without standalone skill chips in the body", () => {
    const skillEvents: SessionEventEntry[] = [
      {
        id: "skill-start",
        sessionId: "session-1",
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
