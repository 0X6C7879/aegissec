import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatBytes } from "../lib/format";
import type { RuntimeExecutionRun } from "../types/runtime";
import type {
  AssistantTranscriptSegment,
  ChatGeneration,
  SessionEventEntry,
  SessionMessage,
} from "../types/sessions";
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

function compareTranscriptSegments(
  left: AssistantTranscriptSegment,
  right: AssistantTranscriptSegment,
): number {
  if (left.sequence !== right.sequence) {
    return left.sequence - right.sequence;
  }

  const timestampDifference = toTimestamp(left.recorded_at) - toTimestamp(right.recorded_at);
  if (timestampDifference !== 0) {
    return timestampDifference;
  }

  return left.id.localeCompare(right.id);
}

function renderUserMessage(content: string) {
  return <div className="chat-bubble-plain">{content}</div>;
}

function renderMarkdownMessage(content: string) {
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

function formatMessageRole(role: string): string {
  switch (role) {
    case "user":
      return "你";
    case "assistant":
      return "助手";
    case "system":
      return "系统";
    case "tool":
      return "工具";
    default:
      return role;
  }
}

function getTranscriptKindLabel(kind: AssistantTranscriptSegment["kind"]): string {
  switch (kind) {
    case "reasoning":
      return "思路";
    case "tool_call":
      return "工具调用";
    case "tool_result":
      return "工具结果";
    case "output":
      return "回复";
    case "error":
      return "异常";
    case "status":
      return "状态";
    default:
      return kind.replace(/[_-]+/g, " ");
  }
}

function getTranscriptSegmentTitle(segment: AssistantTranscriptSegment): string {
  if (segment.title?.trim()) {
    return segment.title;
  }

  if (segment.kind === "tool_call") {
    return segment.tool_name?.trim() || "开始调用工具";
  }

  if (segment.kind === "tool_result") {
    return segment.tool_name?.trim() || "工具执行结果";
  }

  if (segment.kind === "reasoning") {
    return "思路进展";
  }

  if (segment.kind === "output") {
    return "正文输出";
  }

  if (segment.kind === "error") {
    return "执行异常";
  }

  return "运行状态";
}

function buildGenerationStatusText(generation: ChatGeneration): string {
  if (generation.status === "queued") {
    return generation.queue_position && generation.queue_position > 1
      ? `已进入队列，前方还有 ${generation.queue_position - 1} 条等待。`
      : "已进入队列，等待开始。";
  }

  if (generation.status === "running") {
    return "正在持续更新当前回复。";
  }

  if (generation.status === "failed") {
    return generation.error_message?.trim() || "当前生成失败。";
  }

  if (generation.status === "cancelled") {
    return "当前生成已停止。";
  }

  return "当前生成已完成。";
}

function mapGenerationStepToTranscriptSegment(
  step: NonNullable<ChatGeneration["steps"]>[number],
): AssistantTranscriptSegment {
  const kind: AssistantTranscriptSegment["kind"] =
    step.kind === "tool"
      ? step.phase === "tool_result" ||
        step.status === "completed" ||
        step.status === "failed" ||
        step.status === "cancelled" ||
        step.ended_at !== null
        ? "tool_result"
        : "tool_call"
      : step.kind === "output"
        ? "output"
        : step.kind === "reasoning"
          ? "reasoning"
          : step.status === "failed" || step.phase === "failed"
            ? "error"
            : "status";

  const text =
    kind === "output"
      ? step.delta_text || step.safe_summary || null
      : step.safe_summary || step.delta_text || null;

  return {
    id: step.id,
    sequence: step.sequence,
    kind,
    status: step.status,
    title: step.label ?? null,
    text,
    tool_name: step.tool_name ?? null,
    tool_call_id: step.tool_call_id ?? null,
    recorded_at: step.started_at,
    updated_at: step.ended_at ?? step.started_at,
    metadata: step.metadata,
  };
}

function buildTranscriptFromGeneration(
  generation: ChatGeneration,
  assistantMessage: SessionMessage | null,
): AssistantTranscriptSegment[] {
  const segments = (generation.steps ?? [])
    .map((step) => mapGenerationStepToTranscriptSegment(step))
    .sort(compareTranscriptSegments);

  if (segments.length === 0 && generation.reasoning_summary?.trim()) {
    segments.push({
      id: `${generation.id}:reasoning-summary`,
      sequence: 1,
      kind: "reasoning",
      status: generation.status,
      title: "思路摘要",
      text: generation.reasoning_summary,
      recorded_at: generation.started_at ?? generation.created_at,
      updated_at: generation.updated_at,
    });
  }

  const assistantContent = assistantMessage?.content.trim() ?? "";
  if (
    assistantContent &&
    !segments.some(
      (segment) =>
        (segment.kind === "output" || segment.kind === "error") &&
        (segment.text ?? "").trim() === assistantContent,
    )
  ) {
    segments.push({
      id: `${generation.id}:assistant-output`,
      sequence: (segments[segments.length - 1]?.sequence ?? 0) + 1,
      kind: generation.status === "failed" ? "error" : "output",
      status: generation.status,
      title: generation.status === "failed" ? "执行异常" : "正文输出",
      text: assistantMessage?.content ?? "",
      recorded_at: assistantMessage?.created_at ?? generation.updated_at,
      updated_at: assistantMessage?.completed_at ?? generation.updated_at,
    });
  }

  if (
    generation.status !== "completed" &&
    !segments.some((segment) => segment.kind === "status" || segment.kind === "error")
  ) {
    segments.push({
      id: `${generation.id}:status`,
      sequence: (segments[segments.length - 1]?.sequence ?? 0) + 1,
      kind: generation.status === "failed" ? "error" : "status",
      status: generation.status,
      title: generation.status === "failed" ? "执行异常" : "运行状态",
      text: buildGenerationStatusText(generation),
      recorded_at: generation.started_at ?? generation.created_at,
      updated_at: generation.updated_at,
    });
  }

  if (segments.length > 0) {
    return segments.sort(compareTranscriptSegments);
  }

  return [
    {
      id: `${generation.id}:fallback-status`,
      sequence: 1,
      kind: generation.status === "failed" ? "error" : "status",
      status: generation.status,
      title: generation.status === "failed" ? "执行异常" : "运行状态",
      text: generation.error_message?.trim() || buildGenerationStatusText(generation),
      recorded_at: generation.started_at ?? generation.created_at,
      updated_at: generation.updated_at,
    },
  ];
}

function buildAssistantTranscript(
  message: SessionMessage,
  generation: ChatGeneration | null,
): AssistantTranscriptSegment[] {
  const transcript = [...message.assistant_transcript].sort(compareTranscriptSegments);
  const content = message.content.trim();

  if (
    content &&
    !transcript.some(
      (segment) =>
        (segment.kind === "output" || segment.kind === "error") &&
        (segment.text ?? "").trim() === content,
    )
  ) {
    transcript.push({
      id: `${message.id}:content`,
      sequence: (transcript[transcript.length - 1]?.sequence ?? 0) + 1,
      kind: message.status === "failed" ? "error" : "output",
      status: message.status ?? generation?.status ?? null,
      title: message.status === "failed" ? "执行异常" : "正文输出",
      text: message.content,
      recorded_at: message.created_at,
      updated_at: message.completed_at ?? message.created_at,
    });
  }

  if (transcript.length > 0) {
    return transcript.sort(compareTranscriptSegments);
  }

  if (generation) {
    return buildTranscriptFromGeneration(generation, message);
  }

  if (message.error_message?.trim()) {
    return [
      {
        id: `${message.id}:error`,
        sequence: 1,
        kind: "error",
        status: message.status ?? "failed",
        title: "执行异常",
        text: message.error_message,
        recorded_at: message.created_at,
        updated_at: message.completed_at ?? message.created_at,
      },
    ];
  }

  return [];
}

function readArtifactLabels(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.flatMap((entry) => {
    if (typeof entry === "string" && entry.trim()) {
      return [entry];
    }

    if (typeof entry === "object" && entry !== null) {
      const candidate = entry as Record<string, unknown>;
      const label =
        (typeof candidate.relative_path === "string" && candidate.relative_path.trim()) ||
        (typeof candidate.name === "string" && candidate.name.trim()) ||
        (typeof candidate.path === "string" && candidate.path.trim()) ||
        null;

      return label ? [label] : [];
    }

    return [];
  });
}

function hasStructuredMetadata(value: Record<string, unknown> | undefined): boolean {
  return Boolean(value && Object.keys(value).length > 0);
}

export function ConversationFeed(props: ConversationFeedProps) {
  const {
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
  } = props;
  const feedRef = useRef<HTMLElement | null>(null);
  const previousLastItemSignature = useRef<string | null>(null);
  const [openActionMenuId, setOpenActionMenuId] = useState<string | null>(null);

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

  function renderTranscriptDetails(segment: AssistantTranscriptSegment) {
    const metadata = segment.metadata;
    if (!hasStructuredMetadata(metadata)) {
      return null;
    }

    const stdout = typeof metadata?.stdout === "string" ? metadata.stdout : null;
    const stderr = typeof metadata?.stderr === "string" ? metadata.stderr : null;
    const result = metadata?.result;
    const artifacts = readArtifactLabels(metadata?.artifacts);
    const hasExplicitDetails =
      stdout !== null || stderr !== null || result !== undefined || artifacts.length > 0;

    return (
      <div className="assistant-transcript-details">
        {stdout !== null ? (
          <div className="assistant-transcript-detail-block">
            <span className="assistant-transcript-detail-label">stdout</span>
            <pre className="assistant-transcript-pre">{stdout || "(empty)"}</pre>
          </div>
        ) : null}
        {stderr !== null ? (
          <div className="assistant-transcript-detail-block">
            <span className="assistant-transcript-detail-label">stderr</span>
            <pre className="assistant-transcript-pre">{stderr || "(empty)"}</pre>
          </div>
        ) : null}
        {result !== undefined ? (
          <div className="assistant-transcript-detail-block">
            <span className="assistant-transcript-detail-label">result</span>
            <pre className="assistant-transcript-pre">{JSON.stringify(result, null, 2)}</pre>
          </div>
        ) : null}
        {artifacts.length > 0 ? (
          <div className="assistant-transcript-detail-block">
            <span className="assistant-transcript-detail-label">产物</span>
            <div className="chat-bubble-artifacts assistant-transcript-artifacts">
              {artifacts.map((artifact) => (
                <span key={`${segment.id}:${artifact}`} className="chat-artifact-chip">
                  {artifact}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        {!hasExplicitDetails ? (
          <div className="assistant-transcript-detail-block">
            <span className="assistant-transcript-detail-label">metadata</span>
            <pre className="assistant-transcript-pre">{JSON.stringify(metadata, null, 2)}</pre>
          </div>
        ) : null}
      </div>
    );
  }

  function renderTranscriptSegments(segments: AssistantTranscriptSegment[]) {
    if (segments.length === 0) {
      return renderMarkdownMessage("");
    }

    return (
      <div className="assistant-transcript">
        {segments.map((segment) => (
          <section
            key={segment.id}
            className={`assistant-transcript-segment assistant-transcript-segment-${segment.kind}`}
          >
            <div className="assistant-transcript-header">
              <div className="assistant-transcript-title-group">
                <span className="assistant-transcript-kind">{getTranscriptKindLabel(segment.kind)}</span>
                <strong className="assistant-transcript-title">{getTranscriptSegmentTitle(segment)}</strong>
              </div>
              <div className="assistant-transcript-side">
                {segment.tool_name ? (
                  <span className="management-token-chip">{segment.tool_name}</span>
                ) : null}
                {segment.tool_call_id ? (
                  <span className="management-token-chip">{segment.tool_call_id}</span>
                ) : null}
                {segment.status ? <StatusBadge status={segment.status} /> : null}
              </div>
            </div>
            {segment.text?.trim() ? renderMarkdownMessage(segment.text) : null}
            {renderTranscriptDetails(segment)}
          </section>
        ))}
      </div>
    );
  }

  function renderOverflowActions(message: SessionMessage) {
    const isBusy = messageActionBusyId === message.id;
    const secondaryActions: Array<{ label: string; onSelect: () => void }> = [];

    if (message.role === "user" && typeof onEditMessage === "function") {
      secondaryActions.push({
        label: "编辑",
        onSelect: () => onEditMessage(message),
      });
    }

    if (message.role === "assistant" && typeof onRegenerateMessage === "function") {
      secondaryActions.push({
        label: "重试",
        onSelect: () => onRegenerateMessage(message),
      });
    }

    if (typeof onForkMessage === "function") {
      secondaryActions.push({
        label: "分叉",
        onSelect: () => onForkMessage(message),
      });
    }

    const canRollback = typeof onRollbackMessage === "function";
    const hasVisibleActions = canRollback || secondaryActions.length > 0;

    if (!hasVisibleActions) {
      return null;
    }

    const isMenuOpen = openActionMenuId === message.id;

    return (
      <div className="chat-bubble-action-shell">
        {canRollback ? (
          <button
            className="chat-bubble-rollback-button"
            type="button"
            disabled={isBusy}
            onClick={() => onRollbackMessage?.(message)}
          >
            回溯到此
          </button>
        ) : null}
        {secondaryActions.length > 0 ? (
          <div className="chat-bubble-overflow-shell">
            <button
              className="chat-bubble-overflow-button"
              type="button"
              aria-label="打开消息操作"
              aria-expanded={isMenuOpen}
              onClick={() =>
                setOpenActionMenuId((currentValue) =>
                  currentValue === message.id ? null : message.id,
                )
              }
            >
              ···
            </button>
            {isMenuOpen ? (
              <div className="chat-bubble-overflow-menu">
                {secondaryActions.map((action) => (
                  <button
                    key={action.label}
                    type="button"
                    disabled={isBusy}
                    onClick={() => {
                      setOpenActionMenuId(null);
                      action.onSelect();
                    }}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  function renderAssistantBubble(message: SessionMessage, generation: ChatGeneration | null = null) {
    const transcript = buildAssistantTranscript(message, generation);
    const generationStatus = generation?.status ?? message.status ?? null;
    const canCancelGeneration =
      generation !== null &&
      typeof onCancelGeneration === "function" &&
      (generation.status === "queued" || generation.status === "running");

    return (
      <article key={message.id} className="chat-bubble chat-bubble-assistant chat-bubble-assistant-transcript">
        <div className="chat-bubble-meta">
          <strong className="chat-bubble-role">{formatMessageRole(message.role)}</strong>
          <div className="chat-bubble-meta-actions">
            {generation && generation.action !== "reply" ? (
              <span className="management-token-chip">{getGenerationActionLabel(generation.action)}</span>
            ) : null}
            {generation?.status === "queued" && generation.queue_position ? (
              <span className="management-token-chip">排队 #{generation.queue_position}</span>
            ) : null}
            {message.branch_id && activeBranchId && message.branch_id !== activeBranchId ? (
              <span className="management-token-chip">分支消息</span>
            ) : null}
            {generationStatus && generationStatus !== "completed" ? (
              <StatusBadge status={generationStatus} />
            ) : null}
            {canCancelGeneration ? (
              <button
                className="chat-bubble-cancel-button"
                type="button"
                disabled={cancelGenerationBusy}
                onClick={() => onCancelGeneration?.(generation.id)}
              >
                取消
              </button>
            ) : null}
          </div>
        </div>
        {renderTranscriptSegments(transcript)}
        {message.attachments.length > 0 ? (
          <div className="chat-bubble-artifacts">
            {message.attachments.map((attachment) => (
              <span key={attachment.id} className="chat-artifact-chip">
                {attachment.name} · {attachment.content_type} · {formatBytes(attachment.size_bytes)}
              </span>
            ))}
          </div>
        ) : null}
        {renderOverflowActions(message)}
      </article>
    );
  }

  function renderAssistantPlaceholder(generation: ChatGeneration) {
    const transcript = buildTranscriptFromGeneration(generation, null);
    const canCancelGeneration =
      typeof onCancelGeneration === "function" &&
      (generation.status === "queued" || generation.status === "running");

    return (
      <article
        key={generation.id}
        className="chat-bubble chat-bubble-assistant chat-bubble-assistant-transcript"
      >
        <div className="chat-bubble-meta">
          <strong className="chat-bubble-role">助手</strong>
          <div className="chat-bubble-meta-actions">
            {generation.action !== "reply" ? (
              <span className="management-token-chip">{getGenerationActionLabel(generation.action)}</span>
            ) : null}
            {generation.status === "queued" && generation.queue_position ? (
              <span className="management-token-chip">排队 #{generation.queue_position}</span>
            ) : null}
            <StatusBadge status={generation.status} />
            {canCancelGeneration ? (
              <button
                className="chat-bubble-cancel-button"
                type="button"
                disabled={cancelGenerationBusy}
                onClick={() => onCancelGeneration?.(generation.id)}
              >
                取消
              </button>
            ) : null}
          </div>
        </div>
        {renderTranscriptSegments(transcript)}
      </article>
    );
  }

  function renderMessageBubble(message: SessionMessage) {
    if (message.role === "assistant" || message.role === "system") {
      return renderAssistantBubble(message, null);
    }

    return (
      <article key={message.id} className={`chat-bubble chat-bubble-${message.role}`}>
        <div className="chat-bubble-meta">
          <strong className="chat-bubble-role">{formatMessageRole(message.role)}</strong>
          <div className="chat-bubble-meta-actions">
            {message.branch_id && activeBranchId && message.branch_id !== activeBranchId ? (
              <span className="management-token-chip">分支消息</span>
            ) : null}
            {message.status && message.status !== "completed" ? (
              <StatusBadge status={message.status} />
            ) : null}
          </div>
        </div>
        {renderUserMessage(message.content)}
        {message.attachments.length > 0 ? (
          <div className="chat-bubble-artifacts">
            {message.attachments.map((attachment) => (
              <span key={attachment.id} className="chat-artifact-chip">
                {attachment.name} · {attachment.content_type} · {formatBytes(attachment.size_bytes)}
              </span>
            ))}
          </div>
        ) : null}
        {renderOverflowActions(message)}
      </article>
    );
  }

  function renderGenerationRun(run: GenerationRun) {
    return run.assistantMessage
      ? renderAssistantBubble(run.assistantMessage, run.generation)
      : renderAssistantPlaceholder(run.generation);
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
            发送第一条提示后，这里会按对话顺序展示消息，并把助手回复整理成连续转录。
          </p>
        </div>
      </section>
    );
  }

  return (
    <section ref={feedRef} className="conversation-feed conversation-feed-threaded">
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
