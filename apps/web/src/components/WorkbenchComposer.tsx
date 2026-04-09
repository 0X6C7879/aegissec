import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useUiStore } from "../store/uiStore";
import { SlashPopover } from "./SlashPopover";
import { isUiOnlySlashAction, type SlashAction, type SlashCatalogItem } from "../types/slash";

type ComposerSubmitAction = "send" | "inject" | "queue";

export type WorkbenchComposerQueuePayload = {
  content: string;
  slashAction?: SlashAction | null;
};

const WHOLE_INPUT_SLASH_PATTERN = /^\/[^\s]*$/;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function readWholeInputSlashQuery(value: string): string | null {
  if (!WHOLE_INPUT_SLASH_PATTERN.test(value)) {
    return null;
  }

  return value.slice(1).toLowerCase();
}

function matchesSelectedSlashDraft(value: string, action: SlashAction): boolean {
  const selectionPattern = new RegExp(`^/${escapeRegExp(action.trigger)}\\s*$`, "i");
  return selectionPattern.test(value);
}

function canSelectSlashCatalogItem(item: SlashCatalogItem | null): item is SlashCatalogItem {
  return item !== null && item.disabled !== true;
}

type WorkbenchComposerProps = {
  sessionId: string;
  slashCatalog: SlashCatalogItem[];
  disabled: boolean;
  isActiveGeneration: boolean;
  isPausedGeneration: boolean;
  isInterrupting: boolean;
  queuedCount: number;
  onQueueSend: (payload: WorkbenchComposerQueuePayload) => Promise<void>;
  onInject: (content: string) => Promise<void>;
  onInterrupt: () => Promise<void>;
  onLocalSlashAction?: (action: SlashAction) => Promise<boolean> | boolean;
};

export function WorkbenchComposer({
  sessionId,
  slashCatalog,
  disabled,
  isActiveGeneration,
  isPausedGeneration,
  isInterrupting,
  queuedCount,
  onQueueSend,
  onInject,
  onInterrupt,
  onLocalSlashAction,
}: WorkbenchComposerProps) {
  const draft = useUiStore((state) => state.draftsBySession[sessionId]);
  const setDraftContent = useUiStore((state) => state.setDraftContent);
  const sendLockRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [pendingAction, setPendingAction] = useState<ComposerSubmitAction | null>(null);
  const [selectedSlashAction, setSelectedSlashAction] = useState<SlashCatalogItem | null>(null);
  const [activeSlashIndex, setActiveSlashIndex] = useState(0);
  const [dismissedSlashValue, setDismissedSlashValue] = useState<string | null>(null);

  const draftContent = draft?.content ?? "";
  const isEmptyDraft = draftContent.length === 0;
  const trimmedDraftContent = draftContent.trim();
  const slashQuery = readWholeInputSlashQuery(draftContent);
  const filteredSlashCatalog = useMemo(() => {
    if (slashQuery === null) {
      return [];
    }

    const normalizedQuery = slashQuery.trim().toLowerCase();

    return slashCatalog.filter((item) => item.trigger.toLowerCase().startsWith(normalizedQuery));
  }, [slashCatalog, slashQuery]);
  const isSlashPickerOpen =
    slashQuery !== null && filteredSlashCatalog.length > 0 && dismissedSlashValue !== draftContent;
  const activeSlashItem =
    filteredSlashCatalog[Math.min(activeSlashIndex, filteredSlashCatalog.length - 1)] ?? null;
  const isPrimaryDisabled = disabled || pendingAction !== null || trimmedDraftContent.length === 0;
  const isPendingPrimaryAction = pendingAction !== null;
  const slashPopoverId = `slash-popover-${sessionId}`;

  useEffect(() => {
    if (!selectedSlashAction) {
      return;
    }

    if (!matchesSelectedSlashDraft(draftContent, selectedSlashAction.action)) {
      setSelectedSlashAction(null);
    }
  }, [draftContent, selectedSlashAction]);

  useEffect(() => {
    if (dismissedSlashValue !== null && dismissedSlashValue !== draftContent) {
      setDismissedSlashValue(null);
    }
  }, [dismissedSlashValue, draftContent]);

  useEffect(() => {
    if (!isSlashPickerOpen) {
      setActiveSlashIndex(0);
      return;
    }

    setActiveSlashIndex((currentValue) =>
      currentValue < filteredSlashCatalog.length ? currentValue : 0,
    );
  }, [filteredSlashCatalog.length, isSlashPickerOpen]);

  function applySlashSelection(action: SlashCatalogItem): void {
    if (action.disabled === true) {
      return;
    }

    const nextValue = `/${action.trigger} `;
    setSelectedSlashAction(action);
    setActiveSlashIndex(0);
    setDismissedSlashValue(null);
    setDraftContent(sessionId, nextValue);

    window.requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) {
        return;
      }

      textarea.focus();
      textarea.setSelectionRange(nextValue.length, nextValue.length);
    });
  }

  async function handleDispatch(action: ComposerSubmitAction): Promise<void> {
    const trimmed = draftContent.trim();
    const slashCatalogItem = action === "inject" ? null : selectedSlashAction;
    const slashAction =
      action === "inject" ||
      !slashCatalogItem ||
      !matchesSelectedSlashDraft(draftContent, slashCatalogItem.action)
        ? null
        : slashCatalogItem.action;

    if (!trimmed || disabled || sendLockRef.current || pendingAction !== null) {
      return;
    }

    if (slashAction && isUiOnlySlashAction(slashAction) && !onLocalSlashAction) {
      return;
    }

    sendLockRef.current = true;
    setPendingAction(action);
    setDraftContent(sessionId, "");
    setSelectedSlashAction(null);
    setDismissedSlashValue(null);

    try {
      if (action === "inject") {
        await onInject(trimmed);
        return;
      }

      if (slashAction && isUiOnlySlashAction(slashAction)) {
        const handled = (await onLocalSlashAction?.(slashAction)) ?? false;

        if (!handled) {
          setDraftContent(sessionId, draftContent);
          if (slashCatalogItem) {
            setSelectedSlashAction(slashCatalogItem);
          }
        }

        return;
      }

      await onQueueSend({
        content: trimmed,
        slashAction,
      });
    } catch (error) {
      const latestDraftContent = useUiStore.getState().draftsBySession[sessionId]?.content ?? "";

      if (latestDraftContent.trim().length === 0) {
        setDraftContent(sessionId, trimmed);

        if (slashCatalogItem) {
          setSelectedSlashAction(slashCatalogItem);
        }
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
    if (isSlashPickerOpen) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveSlashIndex((currentValue) =>
          filteredSlashCatalog.length === 0 ? 0 : (currentValue + 1) % filteredSlashCatalog.length,
        );
        return;
      }

      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveSlashIndex((currentValue) =>
          filteredSlashCatalog.length === 0
            ? 0
            : (currentValue - 1 + filteredSlashCatalog.length) % filteredSlashCatalog.length,
        );
        return;
      }

      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();

        if (canSelectSlashCatalogItem(activeSlashItem)) {
          applySlashSelection(activeSlashItem);
        }

        return;
      }

      if (event.key === "Escape") {
        event.preventDefault();
        setDismissedSlashValue(draftContent);
        return;
      }
    }

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
          <div className="workbench-chat-entry-shell">
            {isSlashPickerOpen ? (
              <SlashPopover
                id={slashPopoverId}
                items={filteredSlashCatalog}
                activeIndex={activeSlashIndex}
                onHoverItem={setActiveSlashIndex}
                onSelectItem={applySlashSelection}
              />
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
