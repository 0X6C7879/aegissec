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
    const onSend = vi.fn().mockResolvedValue(undefined);

    useUiStore.getState().setDraftContent("session-send", "继续分析当前结果");

    render(
      <WorkbenchComposer
        sessionId="session-send"
        disabled={false}
        isGenerating={false}
        isInterrupting={false}
        queuedCount={0}
        onSend={onSend}
        onInterrupt={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onSend).toHaveBeenCalledWith("继续分析当前结果"));
    expect(useUiStore.getState().draftsBySession["session-send"]?.content ?? "").toBe("");
  });

  it("allows queueing follow-up messages while generation is active", async () => {
    const user = userEvent.setup();
    const onInterrupt = vi.fn().mockResolvedValue(undefined);
    const onSend = vi.fn().mockResolvedValue(undefined);

    render(
      <WorkbenchComposer
        sessionId="session-running"
        disabled={false}
        isGenerating={true}
        isInterrupting={false}
        queuedCount={1}
        onSend={onSend}
        onInterrupt={onInterrupt}
      />,
    );

    await user.type(screen.getByRole("textbox"), "生成结束后继续验证入口");

    expect(screen.getByRole("button", { name: "加入队列" })).toBeEnabled();
    expect(
      screen.getByText("助手正在回复；新消息会排入队列，当前已有 1 条等待。"),
    ).toBeInTheDocument();
    expect(useUiStore.getState().draftsBySession["session-running"]?.content).toBe(
      "生成结束后继续验证入口",
    );

    await user.click(screen.getByRole("button", { name: "加入队列" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("生成结束后继续验证入口"));

    await user.click(screen.getByRole("button", { name: "中断" }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
  });

  it("keeps the textarea editable while the first send is still pending and preserves the follow-up draft", async () => {
    const user = userEvent.setup();
    let resolveFirstSend: () => void = () => {};
    const firstSend = new Promise<void>((resolve) => {
      resolveFirstSend = resolve;
    });
    const onSend = vi
      .fn<(content: string) => Promise<void>>()
      .mockImplementationOnce(() => firstSend)
      .mockResolvedValueOnce(undefined);
    const onInterrupt = vi.fn().mockResolvedValue(undefined);

    const { rerender } = render(
      <WorkbenchComposer
        sessionId="session-pending"
        disabled={false}
        isGenerating={false}
        isInterrupting={false}
        queuedCount={0}
        onSend={onSend}
        onInterrupt={onInterrupt}
      />,
    );

    await user.type(screen.getByRole("textbox"), "第一条消息");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onSend).toHaveBeenNthCalledWith(1, "第一条消息"));
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
        isGenerating={true}
        isInterrupting={false}
        queuedCount={1}
        onSend={onSend}
        onInterrupt={onInterrupt}
      />,
    );

    await user.click(screen.getByRole("button", { name: "加入队列" }));
    await waitFor(() => expect(onSend).toHaveBeenNthCalledWith(2, "第二条跟进"));
  });

  it("submits on Enter and preserves Shift + Enter for new lines", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(undefined);

    render(
      <WorkbenchComposer
        sessionId="session-enter"
        disabled={false}
        isGenerating={false}
        isInterrupting={false}
        queuedCount={0}
        onSend={onSend}
        onInterrupt={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.type(screen.getByRole("textbox"), "第一行");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(screen.getByRole("textbox"), "第二行");

    expect(screen.getByRole("textbox")).toHaveValue("第一行\n第二行");

    await user.keyboard("{Enter}");

    await waitFor(() => expect(onSend).toHaveBeenCalledWith("第一行\n第二行"));
    expect(useUiStore.getState().draftsBySession["session-enter"]?.content ?? "").toBe("");
  });
});
