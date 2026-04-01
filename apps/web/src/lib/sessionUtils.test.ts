import { describe, expect, it } from "vitest";
import { extractSafeSessionSummary, shouldStoreRealtimeEvent } from "./sessionUtils";

describe("sessionUtils realtime summaries", () => {
  it("maps generation cancellation events to safe timeline text", () => {
    expect(
      extractSafeSessionSummary("generation.cancelled", {
        generation_id: "generation-1",
        message_id: "assistant-message-1",
      }),
    ).toEqual({
      label: "中断反馈",
      summary: "当前回复已停止，已保留到目前为止的可见输出。",
      tone: "warning",
    });
  });

  it("stores meaningful generation and summary events but ignores raw message updates", () => {
    expect(
      shouldStoreRealtimeEvent("generation.started", {
        generation_id: "generation-1",
        queued_prompt_count: 0,
      }),
    ).toBe(true);
    expect(
      shouldStoreRealtimeEvent("assistant.summary", {
        summary: "Assistant is analyzing the request and preparing a response.",
      }),
    ).toBe(true);
    expect(
      shouldStoreRealtimeEvent("message.updated", {
        message_id: "assistant-message-1",
        content: "partial",
      }),
    ).toBe(false);
  });
});
