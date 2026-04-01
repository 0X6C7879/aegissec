import { useEffect, useRef, useState } from "react";
import { useUiStore } from "../store/uiStore";

type WorkbenchComposerProps = {
  sessionId: string;
  disabled: boolean;
  isGenerating: boolean;
  isInterrupting: boolean;
  onSend: (content: string) => Promise<void>;
  onInterrupt: () => Promise<void>;
};

export function WorkbenchComposer({
  sessionId,
  disabled,
  isGenerating,
  isInterrupting,
  onSend,
  onInterrupt,
}: WorkbenchComposerProps) {
  const draft = useUiStore((state) => state.draftsBySession[sessionId]);
  const setDraftContent = useUiStore((state) => state.setDraftContent);
  const clearDraft = useUiStore((state) => state.clearDraft);
  const sendLockRef = useRef(false);
  const [isLocallySending, setIsLocallySending] = useState(false);
  const [inputValue, setInputValue] = useState(draft?.content ?? "");

  const draftContent = draft?.content ?? "";
  const effectiveDraftContent = isLocallySending ? inputValue : draftContent;
  const isEmptyDraft = effectiveDraftContent.length === 0;
  const trimmedDraftContent = effectiveDraftContent.trim();
  const isPrimaryDisabled =
    disabled || isGenerating || isLocallySending || trimmedDraftContent.length === 0;

  useEffect(() => {
    if (isLocallySending) {
      return;
    }

    setInputValue(draftContent);
  }, [draftContent, isLocallySending]);

  async function handleSendMessage(): Promise<void> {
    const trimmed = effectiveDraftContent.trim();

    if (!trimmed || isGenerating || isLocallySending || disabled || sendLockRef.current) {
      return;
    }

    sendLockRef.current = true;
    setIsLocallySending(true);
    setInputValue("");
    clearDraft(sessionId);

    try {
      await onSend(trimmed);
      setInputValue("");
      clearDraft(sessionId);
    } catch (error) {
      const latestDraftContent = useUiStore.getState().draftsBySession[sessionId]?.content ?? "";

      if (latestDraftContent.trim().length === 0) {
        setInputValue(trimmed);
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
    if (event.key !== "Enter" || event.shiftKey || isPrimaryDisabled) {
      return;
    }

    event.preventDefault();
    void handleSendMessage();
  }

  const primaryActionLabel = isGenerating ? "等待当前回复" : isLocallySending ? "发送中" : "发送";
  const composerHint = isGenerating
    ? "助手正在回复；如需发送新问题，请先停止当前回复。"
    : "可直接发送，Shift + Enter 换行。";

  return (
    <section className="workbench-composer-shell">
      <div className="workbench-composer-status-row">
        <div className="workbench-composer-status-copy">
          <span
            className={`workbench-composer-mode-pill${isGenerating ? " workbench-composer-mode-pill-active" : ""}`}
          >
            {isGenerating ? "回复中" : "准备发送"}
          </span>
          <p className="workbench-composer-hint">{composerHint}</p>
        </div>
      </div>

      <form className="workbench-chat-form" onSubmit={handleSubmitMessage}>
        <textarea
          className={`workbench-chat-input${isEmptyDraft ? " workbench-chat-input-empty" : ""}`}
          rows={1}
          value={effectiveDraftContent}
          onChange={(event) => {
            const nextValue = event.target.value;
            setInputValue(nextValue);
            setDraftContent(sessionId, nextValue);
          }}
          onKeyDown={handleInputKeyDown}
          placeholder="输入目标、上下文或要验证的问题"
          disabled={disabled || isLocallySending}
        />

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
