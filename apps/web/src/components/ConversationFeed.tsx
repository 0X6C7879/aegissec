import { useEffect, useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatBytes } from "../lib/format";
import { stripHiddenThinkingBlocks } from "../lib/sessionUtils";
import type { RuntimeExecutionRun } from "../types/runtime";
import type { ChatGeneration, SessionEventEntry, SessionMessage } from "../types/sessions";
import { GenerationQueuePanel, GenerationRunTimeline } from "./GenerationRunTimeline";
import { StatusBadge } from "./StatusBadge";

type ConversationFeedProps = {
  messages: SessionMessage[];
  generations: ChatGeneration[];
  events: SessionEventEntry[];
  runtimeRuns: RuntimeExecutionRun[];
  activeGeneration?: ChatGeneration | null;
  queuedGenerations?: ChatGeneration[];
  activeBranchId?: string | null;
  messageActionBusyId?: string | null;
  cancelGenerationBusy?: boolean;
  onCancelGeneration?: (generationId: string) => void;
  onEditMessage?: (message: SessionMessage) => void;
  onRegenerateMessage?: (message: SessionMessage) => void;
  onForkMessage?: (message: SessionMessage) => void;
  onRollbackMessage?: (message: SessionMessage) => void;
};

type GenerationRun = {
  id: string;
  generation: ChatGeneration;
  assistantMessage: SessionMessage | null;
};

type ConversationTurn = {
  id: string;
  userMessage: SessionMessage;
  generationRuns: GenerationRun[];
  supplementalMessages: SessionMessage[];
};

function toTimestamp(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }

  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function compareMessages(left: SessionMessage, right: SessionMessage): number {
  const leftSequence = typeof left.sequence === "number" ? left.sequence : Number.MAX_SAFE_INTEGER;
  const rightSequence = typeof right.sequence === "number" ? right.sequence : Number.MAX_SAFE_INTEGER;

  if (leftSequence !== rightSequence) {
    return leftSequence - rightSequence;
  }

  const timestampDifference = toTimestamp(left.created_at) - toTimestamp(right.created_at);
  if (timestampDifference !== 0) {
    return timestampDifference;
  }

  return left.id.localeCompare(right.id);
}

function compareGenerations(left: ChatGeneration, right: ChatGeneration): number {
  const timestampDifference =
    toTimestamp(left.started_at ?? left.created_at) - toTimestamp(right.started_at ?? right.created_at);

  if (timestampDifference !== 0) {
    return timestampDifference;
  }

  return left.id.localeCompare(right.id);
}

function renderUserMessage(content: string) {
  return <div className="chat-bubble-plain">{content}</div>;
}

function renderAssistantMessage(content: string) {
  if (!content.trim()) {
    return (
      <span className="chat-bubble-streaming-indicator" role="status" aria-live="polite">
        <span />
        <span />
        <span />
      </span>
    );
  }

  return (
    <div className="chat-bubble-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function readGenerationAssistantMessageId(generation: ChatGeneration): string | null {
  return generation.assistant_message_id ?? null;
}

function readGenerationUserMessageId(generation: ChatGeneration): string | null {
  return generation.user_message_id ?? null;
}

function buildConversationRows(
  messages: SessionMessage[],
  generations: ChatGeneration[],
): {
  turns: ConversationTurn[];
  orphanMessages: SessionMessage[];
  orphanGenerationRuns: GenerationRun[];
} {
  const sortedMessages = [...messages].sort(compareMessages);
  const sortedGenerations = [...generations].sort(compareGenerations);
  const userMessages = sortedMessages.filter((message) => message.role === "user");
  const nonUserMessages = sortedMessages.filter((message) => message.role !== "user");
  const messageById = new Map(sortedMessages.map((message) => [message.id, message] as const));
  const matchedMessageIds = new Set<string>();
  const matchedGenerationIds = new Set<string>();

  if (userMessages.length === 0) {
    return {
      turns: [],
      orphanMessages: nonUserMessages,
      orphanGenerationRuns: sortedGenerations.map((generation) => ({
        id: generation.id,
        generation,
        assistantMessage:
          (readGenerationAssistantMessageId(generation)
            ? messageById.get(readGenerationAssistantMessageId(generation)!)
            : null) ?? null,
      })),
    };
  }

  const turns = userMessages.map((userMessage, index) => {
    const nextUserMessage = userMessages[index + 1] ?? null;
    const turnStart = toTimestamp(userMessage.created_at);
    const turnEnd = nextUserMessage ? toTimestamp(nextUserMessage.created_at) : Number.POSITIVE_INFINITY;
    const turnMessages = nonUserMessages.filter((message) => {
      const timestamp = toTimestamp(message.created_at);
      return timestamp >= turnStart && timestamp < turnEnd;
    });
    const turnMessageIds = new Set(turnMessages.map((message) => message.id));

    const generationRuns = sortedGenerations
      .filter((generation) => {
        if (matchedGenerationIds.has(generation.id)) {
          return false;
        }

        const generationUserMessageId = readGenerationUserMessageId(generation);
        if (generationUserMessageId && generationUserMessageId === userMessage.id) {
          return true;
        }

        const assistantMessageId = readGenerationAssistantMessageId(generation);
        if (assistantMessageId && turnMessageIds.has(assistantMessageId)) {
          return true;
        }

        const timestamp = toTimestamp(generation.started_at ?? generation.created_at);
        return !generationUserMessageId && timestamp >= turnStart && timestamp < turnEnd;
      })
      .map((generation) => {
        matchedGenerationIds.add(generation.id);
        const assistantMessage =
          (readGenerationAssistantMessageId(generation)
            ? messageById.get(readGenerationAssistantMessageId(generation)!)
            : turnMessages.find((message) => message.generation_id === generation.id)) ?? null;

        if (assistantMessage) {
          matchedMessageIds.add(assistantMessage.id);
        }

        return {
          id: generation.id,
          generation,
          assistantMessage,
        } satisfies GenerationRun;
      });

    const supplementalMessages = turnMessages.filter((message) => !matchedMessageIds.has(message.id));
    supplementalMessages.forEach((message) => {
      matchedMessageIds.add(message.id);
    });

    return {
      id: userMessage.id,
      userMessage,
      generationRuns,
      supplementalMessages,
    } satisfies ConversationTurn;
  });

  const orphanMessages = nonUserMessages.filter((message) => !matchedMessageIds.has(message.id));
  const orphanGenerationRuns = sortedGenerations
    .filter((generation) => !matchedGenerationIds.has(generation.id))
    .map((generation) => ({
      id: generation.id,
      generation,
      assistantMessage:
        (readGenerationAssistantMessageId(generation)
          ? messageById.get(readGenerationAssistantMessageId(generation)!)
          : null) ?? null,
    }));

  return { turns, orphanMessages, orphanGenerationRuns };
}

function getGenerationActionLabel(action: string): string {
  switch (action) {
    case "reply":
      return "回复";
    case "edit":
      return "编辑后重答";
    case "regenerate":
      return "重新生成";
    case "fork":
      return "分叉生成";
    case "rollback":
      return "回溯生成";
    default:
      return "生成";
  }
}

function mergeGenerations(
  generations: ChatGeneration[],
  activeGeneration: ChatGeneration | null,
  queuedGenerations: ChatGeneration[],
): ChatGeneration[] {
  const mergedById = new Map<string, ChatGeneration>();

  generations.forEach((generation) => {
    mergedById.set(generation.id, generation);
  });

  if (activeGeneration) {
    mergedById.set(activeGeneration.id, activeGeneration);
  }

  queuedGenerations.forEach((generation) => {
    mergedById.set(generation.id, generation);
  });

  return [...mergedById.values()].sort(compareGenerations);
}

export function ConversationFeed({
  messages,
  generations,
  activeGeneration = null,
  queuedGenerations = [],
  activeBranchId,
  messageActionBusyId,
  cancelGenerationBusy = false,
  onCancelGeneration,
  onEditMessage,
  onRegenerateMessage,
  onForkMessage,
  onRollbackMessage,
}: ConversationFeedProps) {
  const feedRef = useRef<HTMLElement | null>(null);
  const previousLastItemSignature = useRef<string | null>(null);

  const mergedGenerations = useMemo(
    () => mergeGenerations(generations, activeGeneration, queuedGenerations),
    [activeGeneration, generations, queuedGenerations],
  );

  const { turns, orphanMessages, orphanGenerationRuns } = useMemo(
    () => buildConversationRows(messages, mergedGenerations),
    [messages, mergedGenerations],
  );

  const lastItemSignature = useMemo(() => {
    const lastMessage = messages[messages.length - 1] ?? null;
    const lastGeneration = mergedGenerations[mergedGenerations.length - 1] ?? null;

    return [
      messages.length,
      lastMessage?.id ?? "none",
      lastMessage?.content.length ?? 0,
      mergedGenerations.length,
      lastGeneration?.id ?? "none",
      lastGeneration?.status ?? "none",
      lastGeneration?.updated_at ?? "none",
      queuedGenerations.length,
    ].join(":");
  }, [mergedGenerations, messages, queuedGenerations.length]);

  useEffect(() => {
    const feedElement = feedRef.current;
    if (!feedElement || !lastItemSignature) {
      return;
    }

    if (lastItemSignature === previousLastItemSignature.current) {
      return;
    }

    const distanceToBottom =
      feedElement.scrollHeight - feedElement.clientHeight - feedElement.scrollTop;
    const shouldStickToBottom =
      previousLastItemSignature.current === null || distanceToBottom <= 72;

    if (!shouldStickToBottom) {
      previousLastItemSignature.current = lastItemSignature;
      return;
    }

    feedElement.scrollTo({
      top: feedElement.scrollHeight,
      behavior: previousLastItemSignature.current === null ? "auto" : "smooth",
    });
    previousLastItemSignature.current = lastItemSignature;
  }, [lastItemSignature]);

  function renderMessageBubble(message: SessionMessage) {
    const assistantContent =
      message.role === "assistant" || message.role === "system"
        ? stripHiddenThinkingBlocks(message.content)
        : null;
    const isBusy = messageActionBusyId === message.id;
    const canEdit = message.role === "user" && typeof onEditMessage === "function";
    const canRegenerate = message.role === "assistant" && typeof onRegenerateMessage === "function";
    const canFork = typeof onForkMessage === "function";
    const canRollback = typeof onRollbackMessage === "function";

    return (
      <article key={message.id} className={`chat-bubble chat-bubble-${message.role}`}>
        <div className="assistant-tool-run-heading">
          <strong className="assistant-tool-run-label">{message.role}</strong>
          <div className="assistant-tool-run-side">
            {message.branch_id && activeBranchId && message.branch_id !== activeBranchId ? (
              <span className="management-token-chip">分支消息</span>
            ) : null}
            {message.status && message.status !== "completed" ? (
              <StatusBadge status={message.status} />
            ) : null}
          </div>
        </div>
        {message.role === "user"
          ? renderUserMessage(message.content)
          : renderAssistantMessage(assistantContent ?? message.content)}
        {message.attachments.length > 0 ? (
          <div className="chat-bubble-artifacts">
            {message.attachments.map((attachment) => (
              <span key={attachment.id} className="chat-artifact-chip">
                {attachment.name} · {attachment.content_type} · {formatBytes(attachment.size_bytes)}
              </span>
            ))}
          </div>
        ) : null}
        {canEdit || canRegenerate || canFork || canRollback ? (
          <div className="management-action-row">
            {canEdit ? (
              <button
                className="inline-button"
                type="button"
                disabled={isBusy}
                onClick={() => onEditMessage?.(message)}
              >
                编辑
              </button>
            ) : null}
            {canRegenerate ? (
              <button
                className="inline-button"
                type="button"
                disabled={isBusy}
                onClick={() => onRegenerateMessage?.(message)}
              >
                重试
              </button>
            ) : null}
            {canFork ? (
              <button
                className="inline-button"
                type="button"
                disabled={isBusy}
                onClick={() => onForkMessage?.(message)}
              >
                分叉
              </button>
            ) : null}
            {canRollback ? (
              <button
                className="inline-button"
                type="button"
                disabled={isBusy}
                onClick={() => onRollbackMessage?.(message)}
              >
                回溯到此
              </button>
            ) : null}
          </div>
        ) : null}
      </article>
    );
  }

  function renderGenerationRun(run: GenerationRun) {
    const { generation, assistantMessage } = run;

    return (
      <section key={run.id} className="assistant-turn-card">
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-[22px] border border-[rgba(57,84,72,0.14)] bg-[var(--surface-subtle)]/85 px-4 py-4 shadow-[0_18px_42px_rgba(41,43,38,0.06)]">
          <div className="grid gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="assistant-section-kicker">本轮运行</span>
              <span className="management-token-chip">{getGenerationActionLabel(generation.action)}</span>
            </div>
            <p className="m-0 text-sm leading-6 text-[var(--text-secondary)]">
              过程与最终回复绑定展示，隐藏推理不会直接暴露在时间线中。
            </p>
          </div>
          <StatusBadge status={generation.status} />
        </div>

        <GenerationRunTimeline generation={generation} assistantMessage={assistantMessage} />

        {assistantMessage ? (
          renderMessageBubble(assistantMessage)
        ) : (
          <article className="chat-bubble chat-bubble-assistant">{renderAssistantMessage("")}</article>
        )}
      </section>
    );
  }

  const hasContent =
    turns.length > 0 ||
    orphanMessages.length > 0 ||
    orphanGenerationRuns.length > 0 ||
    activeGeneration !== null ||
    queuedGenerations.length > 0;

  if (!hasContent) {
    return (
      <section ref={feedRef} className="conversation-feed conversation-feed-empty">
        <div className="conversation-feed-empty-card">
          <p className="conversation-feed-empty-title">从这里开始新的对话</p>
          <p className="conversation-feed-empty-copy">
            发送第一条提示后，这里会按对话顺序展示消息，并把每次生成绑定到对应回复上。
          </p>
        </div>
      </section>
    );
  }

  return (
    <section ref={feedRef} className="conversation-feed conversation-feed-threaded">
      <GenerationQueuePanel
        activeGeneration={activeGeneration}
        queuedGenerations={queuedGenerations}
        messages={messages}
        cancelDisabled={cancelGenerationBusy}
        onCancelGeneration={onCancelGeneration}
      />

      {orphanGenerationRuns.map((run) => renderGenerationRun(run))}
      {orphanMessages.map((message) => renderMessageBubble(message))}

      {turns.map((turn) => (
        <article key={turn.id} className="chat-turn">
          {renderMessageBubble(turn.userMessage)}
          {turn.generationRuns.map((run) => renderGenerationRun(run))}
          {turn.supplementalMessages.map((message) => renderMessageBubble(message))}
        </article>
      ))}
    </section>
  );
}
