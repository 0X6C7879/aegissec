import { useLayoutEffect, useRef, useState } from "react";
import { useUiStore } from "../store/uiStore";

type ComposerSubmitAction = "send" | "inject" | "queue";

type WorkbenchComposerProps = {
  sessionId: string;
  disabled: boolean;
  isActiveGeneration: boolean;
  isPausedGeneration: boolean;
  isInterrupting: boolean;
  queuedCount: number;
  onQueueSend: (content: string) => Promise<void>;
  onInject: (content: string) => Promise<void>;
  onInterrupt: () => Promise<void>;
};

export function WorkbenchComposer({
  sessionId,
  disabled,
  isActiveGeneration,
  isPausedGeneration,
  isInterrupting,
  queuedCount,
  onQueueSend,
  onInject,
  onInterrupt,
}: WorkbenchComposerProps) {
  const draft = useUiStore((state) => state.draftsBySession[sessionId]);
  const setDraftContent = useUiStore((state) => state.setDraftContent);
  const sendLockRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [pendingAction, setPendingAction] = useState<ComposerSubmitAction | null>(null);

  const draftContent = draft?.content ?? "";
  const isEmptyDraft = draftContent.length === 0;
  const trimmedDraftContent = draftContent.trim();
  const isPrimaryDisabled = disabled || pendingAction !== null || trimmedDraftContent.length === 0;
  const isPendingPrimaryAction = pendingAction !== null;

  async function handleDispatch(action: ComposerSubmitAction): Promise<void> {
    const trimmed = draftContent.trim();

    if (!trimmed || disabled || sendLockRef.current || pendingAction !== null) {
      return;
    }

    const submit = action === "inject" ? onInject : onQueueSend;

    sendLockRef.current = true;
    setPendingAction(action);
    setDraftContent(sessionId, "");

    try {
      await submit(trimmed);
    } catch (error) {
      const latestDraftContent = useUiStore.getState().draftsBySession[sessionId]?.content ?? "";

      if (latestDraftContent.trim().length === 0) {
        setDraftContent(sessionId, trimmed);
      }

      throw error;
    } finally {
      sendLockRef.current = false;
      setPendingAction(null);
    }
  }

  async function handlePrimaryAction(): Promise<void> {
    await handleDispatch(isActiveGeneration ? "inject" : "send");
  }

  async function handleQueueAction(): Promise<void> {
    await handleDispatch("queue");
  }

  async function handleSubmitMessage(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    await handlePrimaryAction();
  }

  function handleInputKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (
      event.key !== "Enter" ||
      event.shiftKey ||
      disabled ||
      trimmedDraftContent.length === 0 ||
      pendingAction !== null
    ) {
      return;
    }

    event.preventDefault();
    void handlePrimaryAction();
  }

  const inlineInjectLabel = isPausedGeneration ? "继续" : "注入";
  const primaryActionLabel = isActiveGeneration ? inlineInjectLabel : "发送";
  const composerHint = isActiveGeneration
    ? isPausedGeneration
      ? "当前回复已暂停；补充说明后可继续，也可改为加入队列。"
      : queuedCount > 0
        ? `助手正在回复；可直接注入补充上下文，当前还有 ${queuedCount} 条排队消息。`
        : "助手正在回复；可直接注入补充上下文，或单独加入队列。"
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
          {isActiveGeneration ? (
            <button
              className="workbench-inline-inject-affordance"
              type="button"
              onClick={() => {
                if (trimmedDraftContent.length === 0) {
                  textareaRef.current?.focus();
                  return;
                }

                void handlePrimaryAction();
              }}
              disabled={disabled || pendingAction !== null}
              aria-label={`${inlineInjectLabel}当前回复`}
            >
              <span className="workbench-inline-inject-plus" aria-hidden="true">
                +
              </span>
              <span>{inlineInjectLabel}</span>
            </button>
          ) : null}
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
          {(isActiveGeneration || isInterrupting) && !disabled ? (
            <button
              className="workbench-ghost-action workbench-interrupt-action"
              type="button"
              onClick={() => void onInterrupt()}
              disabled={isInterrupting}
            >
              {isInterrupting ? "停止中" : "中断"}
            </button>
          ) : null}
          {isActiveGeneration ? (
            <button
              className="workbench-ghost-action workbench-queue-action"
              type="button"
              onClick={() => void handleQueueAction()}
              disabled={disabled || pendingAction !== null || trimmedDraftContent.length === 0}
            >
              加入队列
            </button>
          ) : null}
          <button
            className={`workbench-primary-action${isPendingPrimaryAction ? " workbench-primary-action-running" : ""}`}
            type="submit"
            disabled={isPrimaryDisabled}
            aria-label={primaryActionLabel}
          >
            {isPendingPrimaryAction ? (
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
