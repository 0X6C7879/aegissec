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
        onSend={onSend}
        onInterrupt={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(onSend).toHaveBeenCalledWith("继续分析当前结果"));
    expect(useUiStore.getState().draftsBySession["session-send"]?.content ?? "").toBe("");
  });

  it("keeps the composer in interrupt mode while generation is active", async () => {
    const user = userEvent.setup();
    const onInterrupt = vi.fn().mockResolvedValue(undefined);

    render(
      <WorkbenchComposer
        sessionId="session-running"
        disabled={false}
        isGenerating={true}
        isInterrupting={false}
        onSend={vi.fn().mockResolvedValue(undefined)}
        onInterrupt={onInterrupt}
      />,
    );

    await user.type(screen.getByRole("textbox"), "生成结束后继续验证入口");

    expect(screen.getByRole("button", { name: "等待当前回复" })).toBeDisabled();
    expect(screen.getByText("助手正在回复；如需发送新问题，请先停止当前回复。")).toBeInTheDocument();
    expect(useUiStore.getState().draftsBySession["session-running"]?.content).toBe(
      "生成结束后继续验证入口",
    );

    await user.click(screen.getByRole("button", { name: "中断" }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
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
