import type {
  AttachmentMetadata,
  ChatGeneration,
  GenerationReasoningTraceEntry,
  SessionConversation,
  SessionDetail,
  SessionEventEntry,
  SessionMessage,
  SessionSummary,
} from "../types/sessions";

const HIDDEN_THINK_BLOCK_PATTERN = /<think>[\s\S]*?<\/think>/gi;
const MAX_SAFE_SUMMARY_LENGTH = 280;
const MAX_TIMELINE_EVENTS = 200;

export type SafeSessionSummaryEntry = {
  label: string;
  summary: string;
  tone: "neutral" | "connected" | "warning" | "success" | "error";
};

export type PersistedGenerationReasoningEntry = {
  id: string;
  identityKey: string;
  generationId: string;
  assistantMessageId: string | null;
  createdAt: string;
  cursor: number | null;
  label: string;
  summary: string;
  tone: SafeSessionSummaryEntry["tone"];
  meta: string[];
};

function toTimestamp(value: string): number {
  return new Date(value).getTime();
}

function isFiniteCursor(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function getMessageSortKey(message: SessionMessage): [number, number] {
  const sequence = typeof message.sequence === "number" ? message.sequence : Number.MAX_SAFE_INTEGER;
  return [sequence, toTimestamp(message.created_at)];
}

function isOptimisticUserMessage(message: SessionMessage): boolean {
  return message.role === "user" && message.id.startsWith("optimistic-user-");
}

export function sortSessions(sessions: SessionSummary[]): SessionSummary[] {
  return [...sessions].sort(
    (left, right) => toTimestamp(right.updated_at) - toTimestamp(left.updated_at),
  );
}

export function upsertSession(
  sessions: SessionSummary[] | undefined,
  session: SessionSummary,
): SessionSummary[] {
  const currentSessions = sessions ?? [];
  const remainingSessions = currentSessions.filter((item) => item.id !== session.id);
  return sortSessions([session, ...remainingSessions]);
}

export function mergeSessionMessage(
  detail: SessionDetail | undefined,
  message: SessionMessage,
): SessionDetail | undefined {
  if (!detail || detail.id !== message.session_id) {
    return detail;
  }

  const remainingMessages = detail.messages.filter((item) => item.id !== message.id);
  const reconciledMessages =
    message.role === "user" && !isOptimisticUserMessage(message)
      ? remainingMessages.filter(
          (item) =>
            !(isOptimisticUserMessage(item) && item.content.trim() === message.content.trim()),
        )
      : remainingMessages;
  const nextMessages = [...reconciledMessages, message].sort((left, right) => {
    const [leftSequence, leftCreatedAt] = getMessageSortKey(left);
    const [rightSequence, rightCreatedAt] = getMessageSortKey(right);
    return leftSequence - rightSequence || leftCreatedAt - rightCreatedAt;
  });

  return {
    ...detail,
    messages: nextMessages,
  };
}

export function mergeSessionMessages(
  detail: SessionDetail | undefined,
  messages: SessionMessage[],
): SessionDetail | undefined {
  return messages.reduce<SessionDetail | undefined>(
    (currentDetail, message) => mergeSessionMessage(currentDetail, message),
    detail,
  );
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readFirstNonEmptyString(
  value: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate;
    }
  }

  return null;
}

function sanitizeSafeSummaryText(value: string): string {
  const cleaned = stripHiddenThinkingBlocks(value).replace(/\s+/g, " ").trim();
  if (cleaned.length <= MAX_SAFE_SUMMARY_LENGTH) {
    return cleaned;
  }

  return `${cleaned.slice(0, MAX_SAFE_SUMMARY_LENGTH - 1).trimEnd()}…`;
}

export function stripHiddenThinkingBlocks(content: string): string {
  return content
    .replace(HIDDEN_THINK_BLOCK_PATTERN, " ")
    .replace(/<\/?think>/gi, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function buildReasoningDedupeKey({
  assistantMessageId,
  label,
  summary,
  tone,
}: {
  assistantMessageId: string | null;
  label: string;
  summary: string;
  tone: SafeSessionSummaryEntry["tone"];
}): string {
  return [assistantMessageId ?? "none", tone, label.trim(), summary.trim()].join("|");
}

function readFirstFiniteNumber(
  value: Record<string, unknown>,
  keys: readonly string[],
): number | null {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "number" && Number.isFinite(candidate)) {
      return candidate;
    }
  }

  return null;
}

function buildReasoningIdentityKey({
  generationId,
  assistantMessageId,
  label,
  summary,
  tone,
  createdAt,
  cursor,
  sequence,
}: {
  generationId: string;
  assistantMessageId: string | null;
  label: string;
  summary: string;
  tone: SafeSessionSummaryEntry["tone"];
  createdAt: string;
  cursor: number | null;
  sequence: number | null;
}): string {
  if (typeof sequence === "number" && Number.isFinite(sequence)) {
    return `sequence:${generationId}:${sequence}`;
  }

  if (typeof cursor === "number" && Number.isFinite(cursor)) {
    return `cursor:${cursor}`;
  }

  return `content:${buildReasoningDedupeKey({
    assistantMessageId,
    label,
    summary,
    tone,
  })}|${createdAt}`;
}

function toReasoningTone(status: string | null): SafeSessionSummaryEntry["tone"] {
  if (status === "error" || status?.endsWith(".failed")) {
    return "error";
  }

  if (status === "cancelled") {
    return "warning";
  }

  if (status === "done" || status === "completed") {
    return "success";
  }

  if (status === "running") {
    return "connected";
  }

  return "neutral";
}

function getReasoningPhaseLabel(phase: string | null): string | null {
  return phase ? `思路进展 · ${phase.replace(/_/g, " ")}` : null;
}

function isAssistantTraceErrorPayload(payload: Record<string, unknown>): boolean {
  return (
    payload.status === "error" ||
    typeof payload.error === "string" ||
    (typeof payload.state === "string" && payload.state.endsWith(".failed"))
  );
}

function getPersistedReasoningPayload(
  entry: Record<string, unknown>,
): Record<string, unknown> {
  if (isRecord(entry.payload)) {
    return entry.payload;
  }

  if (isRecord(entry.data)) {
    return entry.data;
  }

  return entry;
}

function getPersistedReasoningType(
  entry: Record<string, unknown>,
  payload: Record<string, unknown>,
): string {
  const explicitType = readFirstNonEmptyString(entry, ["type", "event", "kind"]);
  if (explicitType) {
    return explicitType;
  }

  return readFirstNonEmptyString(payload, ["type", "event", "kind"]) ?? "assistant.trace";
}

function getPersistedReasoningCreatedAt(
  generation: ChatGeneration,
  entry: Record<string, unknown>,
  payload: Record<string, unknown>,
): string {
  return (
    readFirstNonEmptyString(entry, [
      "created_at",
      "recorded_at",
      "updated_at",
      "timestamp",
      "emitted_at",
    ]) ??
    readFirstNonEmptyString(payload, [
      "created_at",
      "recorded_at",
      "updated_at",
      "timestamp",
      "emitted_at",
    ]) ??
    generation.started_at ??
    generation.created_at
  );
}

function getPersistedReasoningCursor(
  entry: Record<string, unknown>,
  payload: Record<string, unknown>,
): number | null {
  return readFirstFiniteNumber(entry, ["cursor"]) ?? readFirstFiniteNumber(payload, ["cursor"]);
}

function getPersistedReasoningSequence(
  entry: Record<string, unknown>,
  payload: Record<string, unknown>,
): number | null {
  return readFirstFiniteNumber(entry, ["sequence"]) ?? readFirstFiniteNumber(payload, ["sequence"]);
}

function formatAssistantTraceStateLabel(state: string): string {
  return `思路进展 · ${state.replace(/[._]/g, " ")}`;
}

function buildAssistantTraceSummary(data: Record<string, unknown>): SafeSessionSummaryEntry | null {
  const state = typeof data.state === "string" ? data.state : null;
  const status = typeof data.status === "string" ? data.status : null;
  const command = readFirstNonEmptyString(data, ["command"]);
  const errorMessage = readFirstNonEmptyString(data, ["error"]);
  const summaryText = readFirstNonEmptyString(data, [
    "safe_summary",
    "summary",
    "message",
    "status_text",
  ]);

  let summary = summaryText;
  if (!summary && state === "generation.started") {
    summary = "助手正在整理当前请求的可见推理过程。";
  } else if (!summary && state === "generation.completed") {
    summary = "助手已完成本轮推理整理。";
  } else if (!summary && state === "generation.cancelled") {
    summary = "当前推理过程已停止。";
  } else if (!summary && state === "generation.failed") {
    summary = errorMessage ? `生成失败：${errorMessage}` : "生成失败。";
  } else if (!summary && state === "tool.started") {
    summary = command ? `开始调用工具：${command}` : "开始调用工具。";
  } else if (!summary && state === "tool.finished") {
    summary = command ? `工具调用已完成：${command}` : "工具调用已完成。";
  } else if (!summary && state === "tool.failed") {
    if (command && errorMessage) {
      summary = `工具调用失败：${command}，${errorMessage}`;
    } else if (command) {
      summary = `工具调用失败：${command}`;
    } else if (errorMessage) {
      summary = `工具调用失败：${errorMessage}`;
    } else {
      summary = "工具调用失败。";
    }
  }

  if (!summary) {
    return null;
  }

  const normalizedStatus =
    status ??
    (state?.endsWith(".failed")
      ? "error"
      : state?.endsWith(".cancelled")
        ? "cancelled"
        : state?.endsWith(".completed") || state === "tool.finished"
          ? "completed"
          : state?.endsWith(".started")
            ? "running"
            : null);

  return {
    label:
      readFirstNonEmptyString(data, ["label", "title"]) ??
      (state ? formatAssistantTraceStateLabel(state) : "思路进展"),
    summary: sanitizeSafeSummaryText(summary),
    tone: toReasoningTone(normalizedStatus),
  };
}

function getPersistedReasoningAssistantMessageId(
  generation: ChatGeneration,
  entry: Record<string, unknown>,
  payload: Record<string, unknown>,
): string | null {
  return (
    readFirstNonEmptyString(entry, ["assistant_message_id", "message_id"]) ??
    readFirstNonEmptyString(payload, ["assistant_message_id", "message_id"]) ??
    generation.assistant_message_id
  );
}

function getPersistedReasoningMeta(payload: Record<string, unknown>): string[] {
  const meta: string[] = [];

  if (typeof payload.status === "string") {
    meta.push(`状态 · ${payload.status}`);
  }

  if (typeof payload.command === "string") {
    meta.push(`命令 · ${payload.command}`);
  }

  if (typeof payload.error === "string") {
    meta.push(`异常 · ${payload.error}`);
  }

  return meta;
}

export function extractGenerationReasoningEntries(
  generation: ChatGeneration,
): PersistedGenerationReasoningEntry[] {
  const entries: PersistedGenerationReasoningEntry[] = [];

  (generation.reasoning_trace ?? []).forEach((rawEntry, index) => {
    if (!isRecord(rawEntry)) {
      return;
    }

    const payload = getPersistedReasoningPayload(rawEntry);
    const type = getPersistedReasoningType(rawEntry, payload);
    const createdAt = getPersistedReasoningCreatedAt(generation, rawEntry, payload);
    const assistantMessageId = getPersistedReasoningAssistantMessageId(generation, rawEntry, payload);
    const cursor = getPersistedReasoningCursor(rawEntry, payload);
    const sequence = getPersistedReasoningSequence(rawEntry, payload);
    const persistedId = `${generation.id}:reasoning:${index}`;
    const safeSummary = extractSafeSessionSummary(type, payload);

    if (safeSummary) {
      entries.push({
        id: persistedId,
        identityKey: buildReasoningIdentityKey({
          generationId: generation.id,
          assistantMessageId,
          label: safeSummary.label,
          summary: safeSummary.summary,
          tone: safeSummary.tone,
          createdAt,
          cursor,
          sequence,
        }),
        generationId: generation.id,
        assistantMessageId,
        createdAt,
        cursor,
        label: safeSummary.label,
        summary: safeSummary.summary,
        tone: safeSummary.tone,
        meta: [],
      });
      return;
    }

    if (type !== "assistant.trace") {
      return;
    }

    const summaryText = readFirstNonEmptyString(payload, [
      "safe_summary",
      "summary",
      "message",
      "status_text",
    ]);
    const errorText = readFirstNonEmptyString(payload, ["error"]);
    const visibleText = summaryText ?? errorText;

    if (!visibleText) {
      return;
    }

    const summary = sanitizeSafeSummaryText(visibleText);
    if (!summary) {
      return;
    }

    const status = typeof payload.status === "string" ? payload.status : null;
    const isError = isAssistantTraceErrorPayload(payload);
    const label =
      readFirstNonEmptyString(payload, ["label", "title"]) ??
      getReasoningPhaseLabel(typeof payload.phase === "string" ? payload.phase : null) ??
      (isError ? "请求异常" : "思路进展");
    const tone = isError ? "error" : toReasoningTone(status);

    entries.push({
      id: persistedId,
      identityKey: buildReasoningIdentityKey({
        generationId: generation.id,
        assistantMessageId,
        label,
        summary,
        tone,
        createdAt,
        cursor,
        sequence,
      }),
      generationId: generation.id,
      assistantMessageId,
      createdAt,
      cursor,
      label,
      summary,
      tone,
      meta: getPersistedReasoningMeta(payload),
    });
  });

  if (entries.length === 0 && typeof generation.reasoning_summary === "string") {
    const summary = sanitizeSafeSummaryText(generation.reasoning_summary);
    if (summary) {
      const tone = toReasoningTone(generation.status);
      const assistantMessageId = generation.assistant_message_id;
      entries.push({
        id: `${generation.id}:reasoning-summary`,
        identityKey: buildReasoningIdentityKey({
          generationId: generation.id,
          assistantMessageId,
          label: "思路摘要",
          summary,
          tone,
          createdAt: generation.ended_at ?? generation.updated_at,
          cursor: null,
          sequence: null,
        }),
        generationId: generation.id,
        assistantMessageId,
        createdAt: generation.ended_at ?? generation.updated_at,
        cursor: null,
        label: "思路摘要",
        summary,
        tone,
        meta: [],
      });
    }
  }

  return entries;
}

function compareSessionEventEntries(left: SessionEventEntry, right: SessionEventEntry): number {
  if (isFiniteCursor(left.cursor) && isFiniteCursor(right.cursor) && left.cursor !== right.cursor) {
    return left.cursor - right.cursor;
  }

  const createdAtDifference = toTimestamp(left.createdAt) - toTimestamp(right.createdAt);
  if (createdAtDifference !== 0) {
    return createdAtDifference;
  }

  if (isFiniteCursor(left.cursor) !== isFiniteCursor(right.cursor)) {
    return isFiniteCursor(left.cursor) ? -1 : 1;
  }

  return left.id.localeCompare(right.id);
}

export function mergeSessionEventEntries(
  events: SessionEventEntry[] | undefined,
  event: SessionEventEntry,
): SessionEventEntry[] {
  const currentEvents = events ?? [];
  const nextEvents = currentEvents.filter((item) => {
    if (isFiniteCursor(event.cursor) && isFiniteCursor(item.cursor)) {
      return item.cursor !== event.cursor;
    }

    return item.id !== event.id;
  });

  return [...nextEvents, event].sort(compareSessionEventEntries).slice(-MAX_TIMELINE_EVENTS);
}

function cloneReasoningTraceEntry(
  type: string,
  data: Record<string, unknown>,
  createdAt: string,
  cursor: number | null,
  sequence: number | null,
): GenerationReasoningTraceEntry {
  return {
    ...data,
    type,
    created_at: typeof data.created_at === "string" ? data.created_at : createdAt,
    recorded_at:
      typeof data.recorded_at === "string"
        ? data.recorded_at
        : typeof data.created_at === "string"
          ? data.created_at
          : createdAt,
    cursor,
    sequence,
  };
}

function getNextReasoningSequence(generation: ChatGeneration): number {
  const maxSequence = (generation.reasoning_trace ?? []).reduce((currentMax, entry) => {
    if (!isRecord(entry)) {
      return currentMax;
    }

    const payload = getPersistedReasoningPayload(entry);
    return Math.max(currentMax, getPersistedReasoningSequence(entry, payload) ?? 0);
  }, 0);

  return maxSequence + 1;
}

function mergeGenerationReasoningTrace(
  generation: ChatGeneration,
  type: string,
  data: Record<string, unknown>,
  createdAt: string,
  cursor: number | null,
): ChatGeneration {
  const safeSummary = extractSafeSessionSummary(type, data);
  const nextSequence = getPersistedReasoningSequence(data, data) ?? getNextReasoningSequence(generation);
  const traceEntry = cloneReasoningTraceEntry(type, data, createdAt, cursor, nextSequence);

  return {
    ...generation,
    reasoning_summary:
      type === "assistant.summary" && safeSummary ? safeSummary.summary : generation.reasoning_summary,
    reasoning_trace: [...(generation.reasoning_trace ?? []), traceEntry],
    updated_at: createdAt,
  };
}

export function mergeConversationReasoningEvent(
  conversation: SessionConversation | undefined,
  type: string,
  data: unknown,
  createdAt: string,
  cursor: number | null,
): SessionConversation | undefined {
  if (!conversation || !isRecord(data) || (type !== "assistant.summary" && type !== "assistant.trace")) {
    return conversation;
  }

  const generationId = readFirstNonEmptyString(data, ["generation_id"]);
  const assistantMessageId = readFirstNonEmptyString(data, ["assistant_message_id", "message_id"]);
  const generationIndex = conversation.generations.findIndex((generation) => {
    if (generationId && generation.id === generationId) {
      return true;
    }

    return assistantMessageId ? generation.assistant_message_id === assistantMessageId : false;
  });

  if (generationIndex < 0) {
    return conversation;
  }

  const nextGenerations = conversation.generations.map((generation, index) =>
    index === generationIndex
      ? mergeGenerationReasoningTrace(generation, type, data, createdAt, cursor)
      : generation,
  );

  return {
    ...conversation,
    generations: nextGenerations,
  };
}

function buildSessionStatusSummary(value: Record<string, unknown>): SafeSessionSummaryEntry | null {
  const status = typeof value.status === "string" ? value.status : null;
  if (!status) {
    return null;
  }

  const queuedPromptCount =
    typeof value.queued_prompt_count === "number" && value.queued_prompt_count > 0
      ? value.queued_prompt_count
      : null;

  switch (status) {
    case "running":
      return {
        label: "模型进度",
        summary: queuedPromptCount
          ? `正在生成回复，后面还有 ${queuedPromptCount} 条提示排队。`
          : "正在生成回复，可继续整理下一条提示。",
        tone: "connected",
      };
    case "done":
      return {
        label: "模型进度",
        summary: "当前回复已完成，可继续发送下一条提示。",
        tone: "success",
      };
    case "cancelled":
      return {
        label: "中断反馈",
        summary: "当前回复已停止，可立即继续下一步。",
        tone: "warning",
      };
    case "error": {
      const errorMessage = readFirstNonEmptyString(value, ["error", "message"]);
      return {
        label: "模型进度",
        summary: errorMessage
          ? `生成过程中出现异常：${sanitizeSafeSummaryText(errorMessage)}`
          : "生成过程中出现异常。",
        tone: "error",
      };
    }
    default:
      return null;
  }
}

function isAssistantSummaryType(type: string): boolean {
  return (
    type === "assistant.summary" ||
    type === "assistant.status" ||
    type.startsWith("assistant.summary.") ||
    type.startsWith("assistant.progress.") ||
    type === "assistant.progress"
  );
}

export function extractSafeSessionSummary(
  type: string,
  data: unknown,
): SafeSessionSummaryEntry | null {
  if (!isRecord(data)) {
    return null;
  }

  if (type === "generation.started") {
    const queuedPromptCount =
      typeof data.queued_prompt_count === "number" && data.queued_prompt_count > 0
        ? data.queued_prompt_count
        : null;

    return {
      label: "模型进度",
      summary: queuedPromptCount
        ? `新一轮生成已开始，后面还有 ${queuedPromptCount} 条提示等待处理。`
        : "新一轮生成已开始，助手正在整理回复。",
      tone: "connected",
    };
  }

  if (type === "generation.cancelled") {
    return {
      label: "中断反馈",
      summary: "当前回复已停止，已保留到目前为止的可见输出。",
      tone: "warning",
    };
  }

  if (type === "generation.failed") {
    const errorMessage = readFirstNonEmptyString(data, ["error", "message"]);
    return {
      label: "模型进度",
      summary: errorMessage ? `生成失败：${sanitizeSafeSummaryText(errorMessage)}` : "生成失败。",
      tone: "error",
    };
  }

  if (type === "session.updated") {
    const queuedPromptCount =
      typeof data.queued_prompt_count === "number" && data.queued_prompt_count > 0
        ? data.queued_prompt_count
        : null;
    if (queuedPromptCount !== null) {
      const summaryText = readFirstNonEmptyString(data, [
        "queued_prompt_summary",
        "queued_prompt_message",
      ]);
      return {
        label: "排队提示",
        summary: summaryText
          ? sanitizeSafeSummaryText(summaryText)
          : `已有 ${queuedPromptCount} 条提示在排队区等待执行。`,
        tone: "neutral",
      };
    }

    return buildSessionStatusSummary(data);
  }

  if (type === "assistant.trace") {
    return buildAssistantTraceSummary(data);
  }

  if (!isAssistantSummaryType(type)) {
    return null;
  }

  const summaryText = readFirstNonEmptyString(data, [
    "safe_summary",
    "summary",
    "message",
    "status_text",
  ]);

  if (!summaryText) {
    return null;
  }

  const status = typeof data.status === "string" ? data.status : null;
  const phase = typeof data.phase === "string" ? data.phase : null;
  const label =
    readFirstNonEmptyString(data, ["label", "title"]) ??
    (phase ? `思路摘要 · ${phase.replace(/_/g, " ")}` : "思路摘要");

  return {
    label,
    summary: sanitizeSafeSummaryText(summaryText),
    tone:
      status === "error"
        ? "error"
        : status === "cancelled"
          ? "warning"
          : status === "done"
            ? "success"
            : status === "running"
              ? "connected"
              : "neutral",
  };
}

export function shouldStoreRealtimeEvent(type: string, data: unknown): boolean {
  if (
    type === "message.created" ||
    type === "message.updated" ||
    type === "message.delta" ||
    type === "message.completed"
  ) {
    return false;
  }

  if (type === "assistant.trace") {
    return extractSafeSessionSummary(type, data) !== null;
  }

  if (type === "session.updated") {
    return extractSafeSessionSummary(type, data) !== null;
  }

  if (isAssistantSummaryType(type)) {
    return extractSafeSessionSummary(type, data) !== null;
  }

  return true;
}

export function isSessionSummary(value: unknown): value is SessionSummary {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.status === "string" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    (typeof value.deleted_at === "string" || value.deleted_at === null)
  );
}

export function isSessionMessage(value: unknown): value is SessionMessage {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.session_id === "string" &&
    typeof value.role === "string" &&
    typeof value.content === "string" &&
    typeof value.created_at === "string"
  );
}

function isAttachmentMetadata(value: unknown): value is AttachmentMetadata {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.content_type === "string" &&
    typeof value.size_bytes === "number"
  );
}

function toAttachmentMetadataList(value: unknown): AttachmentMetadata[] {
  return Array.isArray(value) ? value.filter(isAttachmentMetadata) : [];
}

export function toSessionSummaryUpdate(
  currentSession: SessionSummary,
  value: unknown,
  createdAt: string,
): SessionSummary | null {
  if (!isRecord(value)) {
    return null;
  }

  return {
    ...currentSession,
    title: typeof value.title === "string" ? value.title : currentSession.title,
    status: typeof value.status === "string" ? value.status : currentSession.status,
    active_branch_id:
      typeof value.active_branch_id === "string" || value.active_branch_id === null
        ? value.active_branch_id
        : currentSession.active_branch_id,
    deleted_at:
      typeof value.deleted_at === "string" || value.deleted_at === null
        ? value.deleted_at
        : currentSession.deleted_at,
    updated_at: createdAt,
  };
}

export function toSessionMessageEvent(
  value: unknown,
  sessionId: string,
  createdAt: string,
): SessionMessage | null {
  if (!isRecord(value)) {
    return null;
  }

  const messageId =
    typeof value.id === "string"
      ? value.id
      : typeof value.message_id === "string"
        ? value.message_id
        : null;

  if (!messageId || typeof value.role !== "string" || typeof value.content !== "string") {
    return null;
  }

  return {
    id: messageId,
    session_id: typeof value.session_id === "string" ? value.session_id : sessionId,
    parent_message_id:
      typeof value.parent_message_id === "string" || value.parent_message_id === null
        ? value.parent_message_id
        : null,
    branch_id:
      typeof value.branch_id === "string" || value.branch_id === null ? value.branch_id : null,
    generation_id:
      typeof value.generation_id === "string" || value.generation_id === null
        ? value.generation_id
        : null,
    role: value.role,
    status: typeof value.status === "string" ? value.status : undefined,
    message_kind: typeof value.message_kind === "string" ? value.message_kind : undefined,
    sequence: typeof value.sequence === "number" ? value.sequence : undefined,
    turn_index: typeof value.turn_index === "number" ? value.turn_index : undefined,
    edited_from_message_id:
      typeof value.edited_from_message_id === "string" || value.edited_from_message_id === null
        ? value.edited_from_message_id
        : null,
    version_group_id:
      typeof value.version_group_id === "string" || value.version_group_id === null
        ? value.version_group_id
        : null,
    content: value.content,
    metadata: isRecord(value.metadata)
      ? value.metadata
      : isRecord(value.metadata_payload)
        ? value.metadata_payload
        : undefined,
    error_message:
      typeof value.error_message === "string" || value.error_message === null
        ? value.error_message
        : null,
    attachments: toAttachmentMetadataList(value.attachments),
    created_at: typeof value.created_at === "string" ? value.created_at : createdAt,
    completed_at:
      typeof value.completed_at === "string" || value.completed_at === null
        ? value.completed_at
        : null,
  };
}

export function buildEventSummary(type: string, data: unknown): string {
  const safeSummary = extractSafeSessionSummary(type, data);
  if (safeSummary) {
    return safeSummary.summary;
  }

  if (type.startsWith("session.") && isRecord(data)) {
    const title = typeof data.title === "string" ? data.title : "当前会话";
    const status = typeof data.status === "string" ? data.status : "已更新";
    const isDeleted = typeof data.deleted_at === "string";
    return `${title} 状态已更新为 ${status}${isDeleted ? "（已归档）" : "。"}`;
  }

  if (type === "assistant.trace" && isRecord(data)) {
    if (typeof data.message === "string") {
      return data.message;
    }

    return "模型请求失败。";
  }

  if (type === "tool.call.started" && isRecord(data)) {
    const command = typeof data.command === "string" ? data.command : "未命名命令";
    return `模型已触发工具调用：${command}`;
  }

  if (type === "tool.call.finished" && isRecord(data)) {
    const command = typeof data.command === "string" ? data.command : "工具调用";
    const status = typeof data.status === "string" ? data.status : "完成";
    return `${command} 执行结束，状态：${status}。`;
  }

  if (type === "tool.call.failed" && isRecord(data)) {
    const command = typeof data.command === "string" ? data.command : "工具调用";
    const error = typeof data.error === "string" ? data.error : "未知错误";
    return `${command} 执行失败：${error}`;
  }

  if (type === "message.created" && isRecord(data)) {
    const role = typeof data.role === "string" ? data.role : "message";
    const content = typeof data.content === "string" ? stripHiddenThinkingBlocks(data.content) : "";
    const preview = content.trim().replace(/\s+/g, " ").slice(0, 72);
    if (role === "assistant") {
      return `模型已回复${preview ? `：${preview}` : "。"}`;
    }
    if (role === "user") {
      return `你发送了新消息${preview ? `：${preview}` : "。"}`;
    }
    return `收到新消息${preview ? `：${preview}` : "。"}`;
  }

  if (type === "message.updated" && isRecord(data)) {
    const role = typeof data.role === "string" ? data.role : "message";
    if (role === "assistant") {
      return "模型回复正在更新。";
    }
    return "消息内容已更新。";
  }

  if (type === "message.completed" && isRecord(data)) {
    const role = typeof data.role === "string" ? data.role : "message";
    return role === "assistant" ? "模型回复已完成。" : "消息处理已完成。";
  }

  if (type === "generation.failed" && isRecord(data)) {
    const error = typeof data.error === "string" ? data.error : "未知错误";
    return `生成失败：${error}`;
  }

  return "收到新的实时事件。";
}
