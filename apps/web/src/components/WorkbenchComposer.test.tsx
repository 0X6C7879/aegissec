import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../store/uiStore";
import { WorkbenchComposer } from "./WorkbenchComposer";

describe("WorkbenchComposer", () => {
  beforeEach(() => {
    useUiStore.setState({ draftsBySession: {}, eventsBySession: {} });
  });

  it("sends the active draft when generation is idle", async () => {
    const user = userEvent.setup();
    const onQueueSend = vi.fn().mockResolvedValue(undefined);
    const onInject = vi.fn().mockResolvedValue(undefined);

    useUiStore.getState().setDraftContent("session-send", "继续分析当前结果");

    render(
        <WorkbenchComposer
          sessionId="session-send"
          disabled={false}
          isActiveGeneration={false}
          isPausedGeneration={false}
          isInterrupting={false}
          queuedCount={0}
          onQueueSend={onQueueSend}
          onInject={onInject}
          onInterrupt={vi.fn().mockResolvedValue(undefined)}
        />,
      );

    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onQueueSend).toHaveBeenCalledWith("继续分析当前结果"));
    expect(onInject).not.toHaveBeenCalled();
    expect(useUiStore.getState().draftsBySession["session-send"]?.content ?? "").toBe("");
  });

  it("defaults to inject/continue while keeping queue-send explicit during active generation", async () => {
    const user = userEvent.setup();
    const onInterrupt = vi.fn().mockResolvedValue(undefined);
    const onQueueSend = vi.fn().mockResolvedValue(undefined);
    const onInject = vi.fn().mockResolvedValue(undefined);

    render(
      <WorkbenchComposer
        sessionId="session-running"
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

    await user.type(screen.getByRole("textbox"), "生成结束后继续验证入口");

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

    await user.type(screen.getByRole("textbox"), "生成结束后继续验证入口");
    await user.click(screen.getByRole("button", { name: "加入队列" }));
    await waitFor(() => expect(onQueueSend).toHaveBeenCalledWith("生成结束后继续验证入口"));

    await user.click(screen.getByRole("button", { name: "中断" }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
  });

  it("shows continue semantics for paused active generations", async () => {
    render(
      <WorkbenchComposer
        sessionId="session-paused"
        disabled={false}
        isActiveGeneration={true}
        isPausedGeneration={true}
        isInterrupting={false}
        queuedCount={0}
        onQueueSend={vi.fn().mockResolvedValue(undefined)}
        onInject={vi.fn().mockResolvedValue(undefined)}
        onInterrupt={vi.fn().mockResolvedValue(undefined)}
      />,
    );

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
      .fn<(content: string) => Promise<void>>()
      .mockImplementationOnce(() => firstSend)
      .mockResolvedValueOnce(undefined);
    const onInject = vi.fn().mockResolvedValue(undefined);
    const onInterrupt = vi.fn().mockResolvedValue(undefined);

    const { rerender } = render(
      <WorkbenchComposer
        sessionId="session-pending"
        disabled={false}
        isActiveGeneration={false}
        isPausedGeneration={false}
        isInterrupting={false}
        queuedCount={0}
        onQueueSend={onQueueSend}
        onInject={onInject}
        onInterrupt={onInterrupt}
      />,
    );

    await user.type(screen.getByRole("textbox"), "第一条消息");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onQueueSend).toHaveBeenNthCalledWith(1, "第一条消息"));
    expect(screen.getByRole("textbox")).toBeEnabled();

    await user.type(screen.getByRole("textbox"), "第二条跟进");

    expect(screen.getByRole("textbox")).toHaveValue("第二条跟进");
    expect(useUiStore.getState().draftsBySession["session-pending"]?.content).toBe("第二条跟进");

    resolveFirstSend();
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());

    rerender(
      <WorkbenchComposer
        sessionId="session-pending"
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

    render(
      <WorkbenchComposer
        sessionId="session-enter"
        disabled={false}
        isActiveGeneration={false}
        isPausedGeneration={false}
        isInterrupting={false}
        queuedCount={0}
        onQueueSend={onQueueSend}
        onInject={vi.fn().mockResolvedValue(undefined)}
        onInterrupt={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.type(screen.getByRole("textbox"), "第一行");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(screen.getByRole("textbox"), "第二行");

    expect(screen.getByRole("textbox")).toHaveValue("第一行\n第二行");

    await user.keyboard("{Enter}");

    await waitFor(() => expect(onQueueSend).toHaveBeenCalledWith("第一行\n第二行"));
    expect(useUiStore.getState().draftsBySession["session-enter"]?.content ?? "").toBe("");
  });

  it("uses Enter as inject/continue while active generation is in progress", async () => {
    const user = userEvent.setup();
    const onInject = vi.fn().mockResolvedValue(undefined);

    render(
      <WorkbenchComposer
        sessionId="session-enter-inject"
        disabled={false}
        isActiveGeneration={true}
        isPausedGeneration={false}
        isInterrupting={false}
        queuedCount={0}
        onQueueSend={vi.fn().mockResolvedValue(undefined)}
        onInject={onInject}
        onInterrupt={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.type(screen.getByRole("textbox"), "补充新的判断依据");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(onInject).toHaveBeenCalledWith("补充新的判断依据"));
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
      render(
        <WorkbenchComposer
          sessionId="session-autosize"
          disabled={false}
          isActiveGeneration={false}
          isPausedGeneration={false}
          isInterrupting={false}
          queuedCount={0}
          onQueueSend={onQueueSend}
          onInject={vi.fn().mockResolvedValue(undefined)}
          onInterrupt={vi.fn().mockResolvedValue(undefined)}
        />,
      );

      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;

      await waitFor(() => expect(textarea.style.height).toBe("132px"));
      expect(textarea.style.overflowY).toBe("hidden");

      await user.click(screen.getByRole("button", { name: "发送" }));

      await waitFor(() => expect(onQueueSend).toHaveBeenCalledWith("恢复后的草稿内容"));
      await waitFor(() => expect(textarea.style.height).toBe("44px"));
      expect(textarea.style.overflowY).toBe("hidden");
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
      render(
        <WorkbenchComposer
          sessionId="session-autosize-cap"
          disabled={false}
          isActiveGeneration={false}
          isPausedGeneration={false}
          isInterrupting={false}
          queuedCount={0}
          onQueueSend={onQueueSend}
          onInject={vi.fn().mockResolvedValue(undefined)}
          onInterrupt={vi.fn().mockResolvedValue(undefined)}
        />,
      );

      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;

      await waitFor(() => expect(textarea.style.height).toBe("180px"));
      expect(textarea.style.overflowY).toBe("auto");
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
