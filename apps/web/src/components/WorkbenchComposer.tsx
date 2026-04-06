import { useLayoutEffect, useRef, useState } from "react";
import { useUiStore } from "../store/uiStore";

type WorkbenchComposerProps = {
  sessionId: string;
  disabled: boolean;
  isGenerating: boolean;
  isInterrupting: boolean;
  queuedCount: number;
  onSend: (content: string) => Promise<void>;
  onInterrupt: () => Promise<void>;
};

export function WorkbenchComposer({
  sessionId,
  disabled,
  isGenerating,
  isInterrupting,
  queuedCount,
  onSend,
  onInterrupt,
}: WorkbenchComposerProps) {
  const draft = useUiStore((state) => state.draftsBySession[sessionId]);
  const setDraftContent = useUiStore((state) => state.setDraftContent);
  const sendLockRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [isLocallySending, setIsLocallySending] = useState(false);

  const draftContent = draft?.content ?? "";
  const isEmptyDraft = draftContent.length === 0;
  const trimmedDraftContent = draftContent.trim();
  const isPrimaryDisabled =
    disabled || (isLocallySending && !isGenerating) || trimmedDraftContent.length === 0;

  async function handleSendMessage(): Promise<void> {
    const trimmed = draftContent.trim();

    if (!trimmed || disabled || sendLockRef.current || (isLocallySending && !isGenerating)) {
      return;
    }

    sendLockRef.current = true;
    setIsLocallySending(true);
    setDraftContent(sessionId, "");

    try {
      await onSend(trimmed);
    } catch (error) {
      const latestDraftContent = useUiStore.getState().draftsBySession[sessionId]?.content ?? "";

      if (latestDraftContent.trim().length === 0) {
        setDraftContent(sessionId, trimmed);
      }

      throw error;
    } finally {
      sendLockRef.current = false;
      setIsLocallySending(false);
    }
  }

  async function handleSubmitMessage(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    await handleSendMessage();
  }

  function handleInputKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (
      event.key !== "Enter" ||
      event.shiftKey ||
      disabled ||
      trimmedDraftContent.length === 0 ||
      (isLocallySending && !isGenerating)
    ) {
      return;
    }

    event.preventDefault();
    void handleSendMessage();
  }

  const primaryActionLabel = isGenerating ? "加入队列" : isLocallySending ? "发送中" : "发送";
  const composerHint = isGenerating
    ? queuedCount > 0
      ? `助手正在回复；新消息会排入队列，当前已有 ${queuedCount} 条等待。`
      : "助手正在回复；现在发送的新消息会自动进入队列。"
    : "可直接发送，Shift + Enter 换行。";

  useLayoutEffect(() => {
    const textarea = textareaRef.current;

    if (!textarea) {
      return;
    }

    textarea.style.height = "auto";

    const computedMaxHeight = Number.parseFloat(window.getComputedStyle(textarea).maxHeight);
    const measuredScrollHeight = Math.max(textarea.scrollHeight, 44);
    const maxHeight = Number.isFinite(computedMaxHeight) ? computedMaxHeight : measuredScrollHeight;
    const nextHeight = Math.min(measuredScrollHeight, maxHeight);

    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = measuredScrollHeight > maxHeight ? "auto" : "hidden";

    if (draftContent.length === 0) {
      textarea.scrollTop = 0;
    }
  }, [draftContent]);

  return (
    <section className="workbench-composer-shell">
      <div className="workbench-composer-status-row">
        <div className="workbench-composer-status-copy">
          <p className="workbench-composer-hint">{composerHint}</p>
        </div>
      </div>

      <form className="workbench-chat-form" onSubmit={handleSubmitMessage}>
        <div className="workbench-chat-input-shell">
          <span className="workbench-chat-prompt" aria-hidden="true">
            operator $
          </span>
          <textarea
            ref={textareaRef}
            className={`workbench-chat-input${isEmptyDraft ? " workbench-chat-input-empty" : ""}`}
            rows={1}
            value={draftContent}
            onChange={(event) => {
              setDraftContent(sessionId, event.target.value);
            }}
            onKeyDown={handleInputKeyDown}
            placeholder="输入目标、上下文或要验证的问题"
            disabled={disabled}
          />
        </div>

        <div className="workbench-composer-footer">
          {(isGenerating || isInterrupting) && !disabled ? (
            <button
              className="workbench-ghost-action workbench-interrupt-action"
              type="button"
              onClick={() => void onInterrupt()}
              disabled={isInterrupting}
            >
              {isInterrupting ? "停止中" : "中断"}
            </button>
          ) : null}
          <button
            className={`workbench-primary-action${isLocallySending ? " workbench-primary-action-running" : ""}`}
            type="submit"
            disabled={isPrimaryDisabled}
            aria-label={primaryActionLabel}
          >
            {isLocallySending ? (
              <span className="workbench-primary-action-indicator" aria-hidden="true">
                <span className="workbench-primary-action-indicator-core" />
              </span>
            ) : (
              primaryActionLabel
            )}
          </button>
        </div>
      </form>
    </section>
  );
}
