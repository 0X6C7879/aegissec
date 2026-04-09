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

function getSlashCatalogMatchScore(item: SlashCatalogItem, normalizedQuery: string): number | null {
  if (normalizedQuery.length === 0) {
    return 0;
  }

  const normalizedTrigger = item.trigger.trim().toLowerCase();
  const normalizedTitle = item.title.trim().toLowerCase();

  if (normalizedTrigger === normalizedQuery) {
    return 0;
  }
  if (normalizedTrigger.startsWith(normalizedQuery)) {
    return 1;
  }
  if (normalizedTitle.startsWith(normalizedQuery)) {
    return 2;
  }
  if (normalizedTitle === normalizedQuery) {
    return 3;
  }

  return null;
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

    return slashCatalog
      .map((item, index) => ({
        item,
        index,
        score: getSlashCatalogMatchScore(item, normalizedQuery),
      }))
      .filter((entry) => entry.score !== null)
      .sort((left, right) => {
        if (left.score !== right.score) {
          return (left.score ?? Number.MAX_SAFE_INTEGER) - (right.score ?? Number.MAX_SAFE_INTEGER);
        }

        return left.index - right.index;
      })
      .map((entry) => entry.item);
  }, [slashCatalog, slashQuery]);
  const isSlashPickerOpen =
    slashQuery !== null && filteredSlashCatalog.length > 0 && dismissedSlashValue !== draftContent;
  const activeSlashItem =
    filteredSlashCatalog[Math.min(activeSlashIndex, filteredSlashCatalog.length - 1)] ?? null;
  const isPrimaryInterruptAction = isActiveGeneration && trimmedDraftContent.length === 0;
  const isPrimaryDisabled =
    disabled ||
    pendingAction !== null ||
    (!isPrimaryInterruptAction && trimmedDraftContent.length === 0) ||
    (isPrimaryInterruptAction && isInterrupting);
  const isPendingPrimaryAction = pendingAction !== null;
  const isPrimaryActionRunning = isPendingPrimaryAction || (isPrimaryInterruptAction && isInterrupting);
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
    if (isPrimaryInterruptAction) {
      if (disabled || isInterrupting || pendingAction !== null) {
        return;
      }

      await onInterrupt();
      return;
    }

    await handleDispatch(isActiveGeneration ? "inject" : "send");
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

  const primaryActionLabel = isPrimaryInterruptAction
    ? isInterrupting
      ? "正在中断对话"
      : "中断当前对话"
    : isActiveGeneration
      ? "发送并注入当前任务"
      : "发送";
  const composerHint = isActiveGeneration
    ? isPausedGeneration
      ? "当前回复已暂停，输入内容后会直接注入当前任务。"
      : queuedCount > 0
        ? `助手正在回复，输入后会直接注入当前任务；当前还有 ${queuedCount} 条消息排队。`
        : "助手正在回复，输入后会直接注入当前任务。"
    : "可直接发送，Shift + Enter 换行。";
  const showWaitingIndicator = isPendingPrimaryAction || isPrimaryInterruptAction;

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
          <button
            className={`workbench-primary-action${isPrimaryActionRunning ? " workbench-primary-action-running" : ""}`}
            type="submit"
            disabled={isPrimaryDisabled}
            aria-label={primaryActionLabel}
          >
            {showWaitingIndicator ? (
              <span className="workbench-primary-action-indicator" aria-hidden="true">
                <span className="workbench-primary-action-indicator-core" />
                <span className="workbench-primary-action-indicator-core" />
                <span className="workbench-primary-action-indicator-core" />
              </span>
            ) : (
              <span className="workbench-primary-action-send-icon" aria-hidden="true">
                <svg viewBox="0 0 20 20" focusable="false">
                  <path
                    d="M4.25 10h11.5M10 4.25l5.75 5.75L10 15.75"
                    fill="none"
                    stroke="currentColor"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="1.8"
                  />
                </svg>
              </span>
            )}
          </button>
        </div>
      </form>
    </section>
  );
}
