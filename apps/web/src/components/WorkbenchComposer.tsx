import { useEffect, useMemo, useRef, useState } from "react";
import { useUiStore } from "../store/uiStore";

type WorkbenchComposerProps = {
  sessionId: string;
  disabled: boolean;
  isSending: boolean;
  onSend: (content: string) => Promise<void>;
};

export function WorkbenchComposer({
  sessionId,
  disabled,
  isSending,
  onSend,
}: WorkbenchComposerProps) {
  const draft = useUiStore((state) => state.draftsBySession[sessionId]);
  const setDraftContent = useUiStore((state) => state.setDraftContent);
  const clearDraft = useUiStore((state) => state.clearDraft);
  const sendLockRef = useRef(false);
  const [isLocallySending, setIsLocallySending] = useState(false);
  const [inputValue, setInputValue] = useState(draft?.content ?? "");

  const draftContent = draft?.content ?? "";
  const effectiveDraftContent = isLocallySending || isSending ? inputValue : draftContent;
  const isEmptyDraft = effectiveDraftContent.length === 0;
  const isSendDisabled = useMemo(
    () => disabled || isSending || isLocallySending || effectiveDraftContent.trim().length === 0,
    [disabled, effectiveDraftContent, isLocallySending, isSending],
  );

  useEffect(() => {
    if (isLocallySending || isSending) {
      return;
    }

    setInputValue(draftContent);
  }, [draftContent, isLocallySending, isSending]);

  async function handleSendMessage(): Promise<void> {
    const trimmed = effectiveDraftContent.trim();

    if (!trimmed || isSending || isLocallySending || disabled || sendLockRef.current) {
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
    if (event.key !== "Enter" || event.shiftKey || isSendDisabled) {
      return;
    }

    event.preventDefault();
    void handleSendMessage();
  }

  return (
    <section className="workbench-composer-shell">
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
          disabled={disabled || isSending || isLocallySending}
        />

        <div className="workbench-composer-footer">
          <button
            className={`workbench-primary-action${isSending || isLocallySending ? " workbench-primary-action-running" : ""}`}
            type="submit"
            disabled={isSendDisabled}
            aria-label={isSending || isLocallySending ? "运行中" : "发送"}
          >
            {isSending || isLocallySending ? (
              <span className="workbench-primary-action-indicator" aria-hidden="true">
                <span className="workbench-primary-action-indicator-core" />
              </span>
            ) : (
              "发送"
            )}
          </button>
        </div>
      </form>
    </section>
  );
}
