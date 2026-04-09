import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../store/uiStore";
import type { SlashAction, SlashCatalogItem } from "../types/slash";
import { WorkbenchComposer } from "./WorkbenchComposer";

const slashCatalog: SlashCatalogItem[] = [
  {
    id: "slash-skill-recon",
    trigger: "recon",
    title: "Recon",
    type: "skill",
    source: "skill",
    description: "运行侦察技能。",
    badge: "Skill",
    action: {
      id: "skill:recon",
      trigger: "recon",
      type: "skill",
      source: "skill",
      display_text: "/recon",
      invocation: {
        tool_name: "execute_skill",
        arguments: { skill_name_or_id: "recon" },
        mcp_server_id: null,
        mcp_tool_name: null,
      },
    },
  },
  {
    id: "slash-mcp-pivot",
    trigger: "pivot",
    title: "Pivot Tool",
    type: "mcp",
    source: "mcp",
    description: "调用 MCP pivot 工具。",
    badge: "Pivot MCP",
    keybind: "Tab",
    action: {
      id: "mcp:server-1:pivot",
      trigger: "pivot",
      type: "mcp",
      source: "mcp",
      display_text: "/pivot",
      invocation: {
        tool_name: "call_mcp_tool",
        arguments: { target: "pivot" },
        mcp_server_id: "server-1",
        mcp_tool_name: "pivot",
      },
    },
  },
  {
    id: "slash-builtin-note",
    trigger: "note",
    title: "Quick Note",
    type: "builtin",
    source: "ui",
    description: "写入笔记。",
    badge: "Builtin",
    action: {
      id: "builtin:note",
      trigger: "note",
      type: "builtin",
      source: "ui",
      display_text: "/note",
      invocation: {
        tool_name: "session_note",
        arguments: {},
        mcp_server_id: null,
        mcp_tool_name: null,
      },
    },
  },
  {
    id: "slash-mcp-locked",
    trigger: "locked",
    title: "Locked MCP Tool",
    type: "mcp",
    source: "mcp",
    description: "需要额外参数，v1 不允许直接执行。",
    badge: "Locked MCP",
    disabled: true,
    action: {
      id: "mcp:server-2:locked",
      trigger: "locked",
      type: "mcp",
      source: "mcp",
      display_text: "/locked",
      invocation: {
        tool_name: "call_mcp_tool",
        arguments: {},
        mcp_server_id: "server-2",
        mcp_tool_name: "locked",
      },
    },
  },
];

type RenderComposerOptions = {
  sessionId?: string;
  slashItems?: SlashCatalogItem[];
  disabled?: boolean;
  isActiveGeneration?: boolean;
  isPausedGeneration?: boolean;
  isInterrupting?: boolean;
  queuedCount?: number;
  onQueueSend?: ReturnType<typeof vi.fn>;
  onInject?: ReturnType<typeof vi.fn>;
  onInterrupt?: ReturnType<typeof vi.fn>;
  onLocalSlashAction?: ((action: SlashAction) => Promise<boolean> | boolean) | undefined;
};

function renderComposer({
  sessionId = "session-default",
  slashItems = [],
  disabled = false,
  isActiveGeneration = false,
  isPausedGeneration = false,
  isInterrupting = false,
  queuedCount = 0,
  onQueueSend = vi.fn().mockResolvedValue(undefined),
  onInject = vi.fn().mockResolvedValue(undefined),
  onInterrupt = vi.fn().mockResolvedValue(undefined),
  onLocalSlashAction,
}: RenderComposerOptions = {}) {
  const renderResult = render(
    <WorkbenchComposer
      sessionId={sessionId}
      slashCatalog={slashItems}
      disabled={disabled}
      isActiveGeneration={isActiveGeneration}
      isPausedGeneration={isPausedGeneration}
      isInterrupting={isInterrupting}
      queuedCount={queuedCount}
      onQueueSend={onQueueSend}
      onInject={onInject}
      onInterrupt={onInterrupt}
      onLocalSlashAction={onLocalSlashAction}
    />,
  );

  return {
    ...renderResult,
    onQueueSend,
    onInject,
    onInterrupt,
    textbox: screen.getByRole("textbox") as HTMLTextAreaElement,
  };
}

describe("WorkbenchComposer", () => {
  beforeEach(() => {
    useUiStore.setState({ draftsBySession: {}, eventsBySession: {} });
  });

  it("sends the active draft when generation is idle", async () => {
    const user = userEvent.setup();
    const onQueueSend = vi.fn().mockResolvedValue(undefined);
    const onInject = vi.fn().mockResolvedValue(undefined);

    useUiStore.getState().setDraftContent("session-send", "继续分析当前结果");

    renderComposer({
      sessionId: "session-send",
      onQueueSend,
      onInject,
    });

    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() =>
      expect(onQueueSend).toHaveBeenCalledWith({
        content: "继续分析当前结果",
        slashAction: null,
      }),
    );
    expect(onInject).not.toHaveBeenCalled();
    expect(useUiStore.getState().draftsBySession["session-send"]?.content ?? "").toBe("");
  });

  it("defaults to inject/continue while keeping queue-send explicit during active generation", async () => {
    const user = userEvent.setup();
    const onInterrupt = vi.fn().mockResolvedValue(undefined);
    const onQueueSend = vi.fn().mockResolvedValue(undefined);
    const onInject = vi.fn().mockResolvedValue(undefined);

    const { textbox } = renderComposer({
      sessionId: "session-running",
      isActiveGeneration: true,
      queuedCount: 1,
      onQueueSend,
      onInject,
      onInterrupt,
    });

    await user.type(textbox, "生成结束后继续验证入口");

    expect(screen.getByRole("button", { name: "注入当前回复" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "注入" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "加入队列" })).toBeEnabled();
    expect(
      screen.getByText("助手正在回复；可直接注入补充上下文，当前还有 1 条排队消息。"),
    ).toBeInTheDocument();
    expect(useUiStore.getState().draftsBySession["session-running"]?.content).toBe(
      "生成结束后继续验证入口",
    );

    await user.click(screen.getByRole("button", { name: "注入" }));
    await waitFor(() => expect(onInject).toHaveBeenCalledWith("生成结束后继续验证入口"));

    await user.type(textbox, "生成结束后继续验证入口");
    await user.click(screen.getByRole("button", { name: "加入队列" }));
    await waitFor(() =>
      expect(onQueueSend).toHaveBeenCalledWith({
        content: "生成结束后继续验证入口",
        slashAction: null,
      }),
    );

    await user.click(screen.getByRole("button", { name: "中断" }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
  });

  it("shows continue semantics for paused active generations", () => {
    renderComposer({
      sessionId: "session-paused",
      isActiveGeneration: true,
      isPausedGeneration: true,
    });

    expect(screen.getByRole("button", { name: "继续当前回复" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续" })).toBeInTheDocument();
    expect(
      screen.getByText("当前回复已暂停；补充说明后可继续，也可改为加入队列。"),
    ).toBeInTheDocument();
  });

  it("keeps the textarea editable while the first send is still pending and preserves the follow-up draft", async () => {
    const user = userEvent.setup();
    let resolveFirstSend: () => void = () => {};
    const firstSend = new Promise<void>((resolve) => {
      resolveFirstSend = resolve;
    });
    const onQueueSend = vi
      .fn<({ content }: { content: string }) => Promise<void>>()
      .mockImplementationOnce(() => firstSend)
      .mockResolvedValueOnce(undefined);
    const onInject = vi.fn().mockResolvedValue(undefined);
    const onInterrupt = vi.fn().mockResolvedValue(undefined);

    const renderResult = renderComposer({
      sessionId: "session-pending",
      onQueueSend,
      onInject,
      onInterrupt,
    });
    const { textbox, rerender } = renderResult;

    await user.type(textbox, "第一条消息");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() =>
      expect(onQueueSend).toHaveBeenNthCalledWith(1, {
        content: "第一条消息",
        slashAction: null,
      }),
    );
    expect(textbox).toBeEnabled();

    await user.type(textbox, "第二条跟进");

    expect(textbox).toHaveValue("第二条跟进");
    expect(useUiStore.getState().draftsBySession["session-pending"]?.content).toBe("第二条跟进");

    resolveFirstSend();
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());

    rerender(
      <WorkbenchComposer
        sessionId="session-pending"
        slashCatalog={[]}
        disabled={false}
        isActiveGeneration={true}
        isPausedGeneration={false}
        isInterrupting={false}
        queuedCount={1}
        onQueueSend={onQueueSend}
        onInject={onInject}
        onInterrupt={onInterrupt}
      />,
    );

    await user.click(screen.getByRole("button", { name: "注入" }));
    await waitFor(() => expect(onInject).toHaveBeenNthCalledWith(1, "第二条跟进"));
  });

  it("submits on Enter and preserves Shift + Enter for new lines", async () => {
    const user = userEvent.setup();
    const onQueueSend = vi.fn().mockResolvedValue(undefined);

    const { textbox } = renderComposer({
      sessionId: "session-enter",
      onQueueSend,
    });

    await user.type(textbox, "第一行");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(textbox, "第二行");

    expect(textbox).toHaveValue("第一行\n第二行");

    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(onQueueSend).toHaveBeenCalledWith({
        content: "第一行\n第二行",
        slashAction: null,
      }),
    );
    expect(useUiStore.getState().draftsBySession["session-enter"]?.content ?? "").toBe("");
  });

  it("uses Enter as inject/continue while active generation is in progress", async () => {
    const user = userEvent.setup();
    const onInject = vi.fn().mockResolvedValue(undefined);

    const { textbox } = renderComposer({
      sessionId: "session-enter-inject",
      isActiveGeneration: true,
      onInject,
    });

    await user.type(textbox, "补充新的判断依据");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(onInject).toHaveBeenCalledWith("补充新的判断依据"));
  });

  it("opens the slash picker for slash-only input and closes after deleting the slash", async () => {
    const user = userEvent.setup();
    const { textbox } = renderComposer({
      sessionId: "session-slash-open",
      slashItems: slashCatalog,
    });

    await user.type(textbox, "/");

    expect(screen.getByRole("listbox", { name: "斜杠指令" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/recon/i })).toBeInTheDocument();

    await user.keyboard("{Backspace}");

    expect(textbox).toHaveValue("");
    expect(screen.queryByRole("listbox", { name: "斜杠指令" })).not.toBeInTheDocument();
  });

  it("renders stable data attributes for slash options", async () => {
    const user = userEvent.setup();
    const { textbox } = renderComposer({
      sessionId: "session-slash-data-id",
      slashItems: slashCatalog,
    });

    await user.type(textbox, "/re");

    const reconOption = screen.getByRole("option", { name: /\/recon/i });
    expect(reconOption).toHaveAttribute("data-slash-id", "slash-skill-recon");
    expect(reconOption).toHaveAttribute("data-slash-trigger", "recon");
    expect(reconOption).toHaveAttribute("data-slash-type", "skill");
  });

  it("filters slash candidates for whole-input /prefix only", async () => {
    const user = userEvent.setup();
    const { textbox } = renderComposer({
      sessionId: "session-slash-filter",
      slashItems: slashCatalog,
    });

    await user.type(textbox, "/pi");

    expect(screen.getByRole("option", { name: /\/pivot/i })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /\/recon/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /\/note/i })).not.toBeInTheDocument();
  });

  it("supports arrow navigation and Enter selection for slash items", async () => {
    const user = userEvent.setup();
    const { textbox } = renderComposer({
      sessionId: "session-slash-enter",
      slashItems: slashCatalog,
    });

    await user.type(textbox, "/");
    await user.keyboard("{ArrowDown}{Enter}");

    expect(textbox).toHaveValue("/pivot ");
    expect(screen.queryByRole("listbox", { name: "斜杠指令" })).not.toBeInTheDocument();
  });

  it("keeps the active slash item scrolled into view for keyboard and hover changes", async () => {
    const user = userEvent.setup();
    const scrollIntoViewMock = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;

    HTMLElement.prototype.scrollIntoView = scrollIntoViewMock;

    try {
      const { textbox } = renderComposer({
        sessionId: "session-slash-scroll",
        slashItems: slashCatalog,
      });

      await user.type(textbox, "/");
      scrollIntoViewMock.mockClear();

      await user.keyboard("{ArrowDown}");
      await waitFor(() => expect(scrollIntoViewMock).toHaveBeenCalledWith({ block: "nearest" }));

      scrollIntoViewMock.mockClear();
      await user.hover(screen.getByRole("option", { name: /\/recon/i }));
      await waitFor(() => expect(scrollIntoViewMock).toHaveBeenCalledWith({ block: "nearest" }));
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("supports Tab selection for slash items", async () => {
    const user = userEvent.setup();
    const { textbox } = renderComposer({
      sessionId: "session-slash-tab",
      slashItems: slashCatalog,
    });

    await user.type(textbox, "/re");
    await user.keyboard("{Tab}");

    expect(textbox).toHaveValue("/recon ");
    expect(screen.queryByRole("listbox", { name: "斜杠指令" })).not.toBeInTheDocument();
  });

  it("closes the slash picker with Escape without clearing the draft", async () => {
    const user = userEvent.setup();
    const { textbox } = renderComposer({
      sessionId: "session-slash-escape",
      slashItems: slashCatalog,
    });

    await user.type(textbox, "/");
    expect(screen.getByRole("listbox", { name: "斜杠指令" })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(textbox).toHaveValue("/");
    expect(screen.queryByRole("listbox", { name: "斜杠指令" })).not.toBeInTheDocument();
  });

  it("updates the active slash item on hover and selects with mouse click", async () => {
    const user = userEvent.setup();
    const { textbox } = renderComposer({
      sessionId: "session-slash-mouse",
      slashItems: slashCatalog,
    });

    await user.type(textbox, "/");

    const pivotOption = screen.getByRole("option", { name: /\/pivot/i });
    await user.hover(pivotOption);

    expect(pivotOption).toHaveAttribute("aria-selected", "true");

    await user.click(pivotOption);

    expect(textbox).toHaveValue("/pivot ");
    expect(screen.queryByRole("listbox", { name: "斜杠指令" })).not.toBeInTheDocument();
  });

  it("submits a selected governed slash as structured payload while preserving visible slash text", async () => {
    const user = userEvent.setup();
    const governedSlash = slashCatalog[0];
    const onQueueSend = vi.fn().mockResolvedValue(undefined);

    const { textbox } = renderComposer({
      sessionId: "session-slash-submit",
      slashItems: slashCatalog,
      onQueueSend,
    });

    await user.type(textbox, "/re");
    await user.keyboard("{Tab}");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() =>
      expect(onQueueSend).toHaveBeenCalledWith({
        content: "/recon",
        slashAction: governedSlash.action,
      }),
    );
  });

  it("routes ui-only builtin slash actions through the local handler without queueing chat", async () => {
    const user = userEvent.setup();
    const uiOnlySlash = slashCatalog[2];
    const onQueueSend = vi.fn().mockResolvedValue(undefined);
    const onLocalSlashAction = vi.fn().mockResolvedValue(true);

    const { textbox } = renderComposer({
      sessionId: "session-slash-ui-only",
      slashItems: slashCatalog,
      onQueueSend,
      onLocalSlashAction,
    });

    await user.type(textbox, "/no");
    await user.keyboard("{Tab}");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onLocalSlashAction).toHaveBeenCalledWith(uiOnlySlash.action));
    expect(onQueueSend).not.toHaveBeenCalled();
    expect(textbox).toHaveValue("");
  });

  it("restores input and slash selection state when the local slash handler reports failure", async () => {
    const user = userEvent.setup();
    const onQueueSend = vi.fn().mockResolvedValue(undefined);
    const onLocalSlashAction = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);

    const { textbox } = renderComposer({
      sessionId: "session-slash-ui-failure",
      slashItems: slashCatalog,
      onQueueSend,
      onLocalSlashAction,
    });

    await user.type(textbox, "/no");
    await user.keyboard("{Tab}");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onLocalSlashAction).toHaveBeenCalledTimes(1));
    expect(onQueueSend).not.toHaveBeenCalled();
    expect(textbox).toHaveValue("/note ");

    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onLocalSlashAction).toHaveBeenCalledTimes(2));
    expect(onQueueSend).not.toHaveBeenCalled();
    expect(textbox).toHaveValue("");
  });

  it("does not select disabled slash items with keyboard", async () => {
    const user = userEvent.setup();
    const { textbox } = renderComposer({
      sessionId: "session-slash-disabled",
      slashItems: slashCatalog,
    });

    await user.type(textbox, "/locked");

    const lockedOption = screen.getByRole("option", { name: /\/locked/i });
    expect(lockedOption).toBeDisabled();

    await user.keyboard("{Enter}");

    expect(textbox).toHaveValue("/locked");
    expect(screen.getByRole("listbox", { name: "斜杠指令" })).toBeInTheDocument();
  });

  it("auto-resizes with draft content and resets after send clears the draft", async () => {
    const user = userEvent.setup();
    const onQueueSend = vi.fn().mockResolvedValue(undefined);
    const originalScrollHeightDescriptor = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "scrollHeight",
    );

    Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
      configurable: true,
      get() {
        return this.value.length === 0 ? 44 : 132;
      },
    });

    useUiStore.getState().setDraftContent("session-autosize", "恢复后的草稿内容");

    try {
      const { textbox } = renderComposer({
        sessionId: "session-autosize",
        onQueueSend,
      });

      await waitFor(() => expect(textbox.style.height).toBe("132px"));
      expect(textbox.style.overflowY).toBe("hidden");

      await user.click(screen.getByRole("button", { name: "发送" }));

      await waitFor(() =>
        expect(onQueueSend).toHaveBeenCalledWith({
          content: "恢复后的草稿内容",
          slashAction: null,
        }),
      );
      await waitFor(() => expect(textbox.style.height).toBe("44px"));
      expect(textbox.style.overflowY).toBe("hidden");
    } finally {
      if (originalScrollHeightDescriptor) {
        Object.defineProperty(
          HTMLTextAreaElement.prototype,
          "scrollHeight",
          originalScrollHeightDescriptor,
        );
      }
    }
  });

  it("caps textarea height and enables internal scrolling when content exceeds max height", async () => {
    const onQueueSend = vi.fn().mockResolvedValue(undefined);
    const originalScrollHeightDescriptor = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "scrollHeight",
    );
    const originalGetComputedStyle = window.getComputedStyle;

    Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
      configurable: true,
      get() {
        return this.value.length === 0 ? 44 : 260;
      },
    });

    vi.spyOn(window, "getComputedStyle").mockImplementation((element) => {
      const styles = originalGetComputedStyle(element);
      return Object.assign({}, styles, { maxHeight: "180px" }) as CSSStyleDeclaration;
    });

    useUiStore.getState().setDraftContent("session-autosize-cap", "超长草稿内容");

    try {
      const { textbox } = renderComposer({
        sessionId: "session-autosize-cap",
        onQueueSend,
      });

      await waitFor(() => expect(textbox.style.height).toBe("180px"));
      expect(textbox.style.overflowY).toBe("auto");
    } finally {
      vi.restoreAllMocks();

      if (originalScrollHeightDescriptor) {
        Object.defineProperty(
          HTMLTextAreaElement.prototype,
          "scrollHeight",
          originalScrollHeightDescriptor,
        );
      }
    }
  });
});
