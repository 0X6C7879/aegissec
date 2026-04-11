import type {
  AssistantTranscriptSegment,
  AttachmentMetadata,
  ChatGeneration,
  GenerationStep,
  GenerationReasoningTraceEntry,
  SessionConversation,
  SessionDetail,
  SessionEventEntry,
  SessionMessage,
  SessionQueue,
  SessionSummary,
} from "../types/sessions";

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
  const sequence =
    typeof message.sequence === "number" ? message.sequence : Number.MAX_SAFE_INTEGER;
  return [sequence, toTimestamp(message.created_at)];
}

function isOptimisticUserMessage(message: SessionMessage): boolean {
  return message.role === "user" && message.id.startsWith("optimistic-user-");
}

function normalizeMessageContentForStrengthCheck(content: string): string {
  return content.replace(/\s+/g, " ").trim();
}

function pickPreferredAssistantContent(existingContent: string, incomingContent: string): string {
  const normalizedExistingContent = normalizeMessageContentForStrengthCheck(existingContent);
  const normalizedIncomingContent = normalizeMessageContentForStrengthCheck(incomingContent);

  if (!normalizedIncomingContent) {
    return existingContent;
  }

  if (!normalizedExistingContent) {
    return incomingContent;
  }

  if (normalizedIncomingContent === normalizedExistingContent) {
    return incomingContent;
  }

  if (normalizedExistingContent.startsWith(normalizedIncomingContent)) {
    return existingContent;
  }

  if (normalizedIncomingContent.startsWith(normalizedExistingContent)) {
    return incomingContent;
  }

  return incomingContent;
}

const TOOL_TRANSCRIPT_KINDS = ["tool_call", "tool_result", "error"] as const;
const THIN_TOOL_RESULT_TEXT_PATTERNS = [
  /^工具调用已完成[。.]?$/i,
  /^工具执行完成[。.]?$/i,
  /^命令(?:调用)?已完成[。.]?$/i,
  /^命令执行(?:完成|结束)[。.]?$/i,
  /^工具执行完成[，,]?状态[:：]?\s*[\w-]+[。.]?$/i,
  /^命令已完成[，,]?状态[:：]?\s*[\w-]+[。.]?$/i,
  /^tool(?: execution)? completed(?:[,:]?\s*status[:：]?\s*[\w-]+)?[.]?$/i,
  /^command completed(?:[,:]?\s*status[:：]?\s*[\w-]+)?[.]?$/i,
  /^completed(?:[,:]?\s*status[:：]?\s*[\w-]+)?[.]?$/i,
] as const;

function compareAssistantTranscriptSegments(
  left: AssistantTranscriptSegment,
  right: AssistantTranscriptSegment,
): number {
  if (left.sequence !== right.sequence) {
    return left.sequence - right.sequence;
  }

  const recordedAtDifference = toTimestamp(left.recorded_at) - toTimestamp(right.recorded_at);
  if (recordedAtDifference !== 0) {
    return recordedAtDifference;
  }

  return left.id.localeCompare(right.id);
}

function isToolTranscriptKind(kind: AssistantTranscriptSegment["kind"]): boolean {
  return TOOL_TRANSCRIPT_KINDS.includes(kind as (typeof TOOL_TRANSCRIPT_KINDS)[number]);
}

function isToolTranscriptSegment(segment: AssistantTranscriptSegment): boolean {
  return isToolTranscriptKind(segment.kind);
}

function isThinToolResultText(text: string | null | undefined): boolean {
  if (!text) {
    return false;
  }

  const normalized = text.replace(/\s+/g, " ").trim().toLowerCase();
  if (normalized.length === 0) {
    return false;
  }

  return THIN_TOOL_RESULT_TEXT_PATTERNS.some((pattern) => pattern.test(normalized));
}

function getTranscriptSemanticKey(segment: AssistantTranscriptSegment): string | null {
  if (!isToolTranscriptSegment(segment) || !segment.tool_call_id) {
    return segment.id ? `id:${segment.id}` : null;
  }

  return `${segment.kind}:${segment.tool_call_id}`;
}

function preferPopulatedTranscriptString(
  preferred: string | null | undefined,
  fallback: string | null | undefined,
): string | null | undefined {
  if (typeof preferred === "string" && preferred.trim().length > 0) {
    return preferred;
  }

  if (typeof fallback === "string" && fallback.trim().length > 0) {
    return fallback;
  }

  return preferred ?? fallback;
}

function readTranscriptSegmentCommand(segment: AssistantTranscriptSegment): string | null {
  if (!segment.metadata) {
    return null;
  }

  return readFirstNonEmptyString(segment.metadata, ["command"]);
}

function scoreStandaloneTranscriptToolText(
  text: string | null | undefined,
  command: string | null,
): number {
  if (!text) {
    return 0;
  }

  const normalized = text.trim();
  if (normalized.length === 0) {
    return 0;
  }

  if (command && normalized === command.trim()) {
    return 0;
  }

  if (isThinToolResultText(normalized)) {
    return 1;
  }

  return normalized.includes("\n") ? 5 : 3;
}

function scoreTranscriptSegmentRichness(segment: AssistantTranscriptSegment): number {
  const command = readTranscriptSegmentCommand(segment);
  const textScore = scoreStandaloneTranscriptToolText(segment.text, command);
  const metadataScore = scoreShellMetadataRichness(segment.metadata);
  const hasRenderablePayload = hasRenderableShellPayload(segment.metadata);
  const statusScore =
    typeof segment.status === "string" && segment.status.trim().length > 0 ? 4 : 0;
  const toolScore = segment.tool_name ? 2 : 0;
  const titleScore = segment.title ? 1 : 0;
  const kindScore =
    segment.kind === "tool_result"
      ? 22
      : segment.kind === "error"
        ? 18
        : segment.kind === "tool_call"
          ? 6
          : 0;

  return (
    metadataScore +
    (hasRenderablePayload ? 36 : 0) +
    kindScore +
    textScore +
    statusScore +
    toolScore +
    titleScore
  );
}

function mergeTranscriptTextPreferRicher(
  existing: AssistantTranscriptSegment,
  incoming: AssistantTranscriptSegment,
): string | null | undefined {
  const existingTextScore = scoreStandaloneTranscriptToolText(
    existing.text,
    readTranscriptSegmentCommand(existing),
  );
  const incomingTextScore = scoreStandaloneTranscriptToolText(
    incoming.text,
    readTranscriptSegmentCommand(incoming),
  );

  if (incomingTextScore > existingTextScore) {
    return incoming.text ?? existing.text;
  }

  if (existingTextScore > incomingTextScore) {
    return existing.text ?? incoming.text;
  }

  return (
    (mergeScalarPreferRicher(existing.text, incoming.text) as string | null | undefined) ??
    existing.text ??
    incoming.text
  );
}

function pickEarlierTimestamp(existing: string, incoming: string): string {
  return toTimestamp(incoming) < toTimestamp(existing) ? incoming : existing;
}

function pickLaterTimestamp(existing: string, incoming: string): string {
  return toTimestamp(incoming) >= toTimestamp(existing) ? incoming : existing;
}

function mergeTranscriptSegmentPreferRicher(
  existing: AssistantTranscriptSegment,
  incoming: AssistantTranscriptSegment,
): AssistantTranscriptSegment {
  const existingRichness = scoreTranscriptSegmentRichness(existing);
  const incomingRichness = scoreTranscriptSegmentRichness(incoming);
  const preferred = incomingRichness > existingRichness ? incoming : existing;
  const fallback = preferred === existing ? incoming : existing;
  const existingSemanticKey = getTranscriptSemanticKey(existing);
  const incomingSemanticKey = getTranscriptSemanticKey(incoming);
  const semanticEquivalentByToolCall =
    existingSemanticKey !== null &&
    incomingSemanticKey !== null &&
    !existingSemanticKey.startsWith("id:") &&
    existingSemanticKey === incomingSemanticKey;
  const shouldPreserveStableIdentity = existing.id === incoming.id || semanticEquivalentByToolCall;
  const mergedMetadata = mergeMetadataPreferRicher(existing.metadata, incoming.metadata);

  return {
    ...fallback,
    ...preferred,
    id: shouldPreserveStableIdentity ? existing.id : preferred.id,
    sequence: shouldPreserveStableIdentity
      ? Math.min(existing.sequence, incoming.sequence)
      : preferred.sequence,
    recorded_at: shouldPreserveStableIdentity
      ? pickEarlierTimestamp(existing.recorded_at, incoming.recorded_at)
      : preferred.recorded_at,
    updated_at:
      preferPopulatedTranscriptString(
        pickLaterTimestamp(existing.updated_at, incoming.updated_at),
        preferred.updated_at,
      ) ?? existing.updated_at,
    status: mergeScalarPreferRicher(existing.status, incoming.status) as string | null | undefined,
    title: mergeScalarPreferRicher(existing.title, incoming.title) as string | null | undefined,
    text: mergeTranscriptTextPreferRicher(existing, incoming),
    tool_name: preferPopulatedTranscriptString(
      mergeScalarPreferRicher(existing.tool_name, incoming.tool_name) as string | null | undefined,
      preferred.tool_name ?? fallback.tool_name,
    ),
    tool_call_id: preferPopulatedTranscriptString(
      mergeScalarPreferRicher(existing.tool_call_id, incoming.tool_call_id) as
        | string
        | null
        | undefined,
      preferred.tool_call_id ?? fallback.tool_call_id,
    ),
    metadata: mergedMetadata,
  };
}

function findTranscriptMergeIndex(
  segments: AssistantTranscriptSegment[],
  candidate: AssistantTranscriptSegment,
): number {
  const exactIdMatchIndex = segments.findIndex((segment) => segment.id === candidate.id);
  if (exactIdMatchIndex >= 0) {
    return exactIdMatchIndex;
  }

  const semanticKey = getTranscriptSemanticKey(candidate);
  if (!semanticKey || semanticKey.startsWith("id:")) {
    return -1;
  }

  return segments.findIndex((segment) => getTranscriptSemanticKey(segment) === semanticKey);
}

function isChatGenerationCandidate(value: unknown): value is ChatGeneration {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.session_id === "string" &&
    typeof value.assistant_message_id === "string"
  );
}

function mapGenerationToolStepToTranscriptSegment(
  step: GenerationStep,
): AssistantTranscriptSegment | null {
  if (step.kind !== "tool") {
    return null;
  }

  const kind: AssistantTranscriptSegment["kind"] =
    step.status === "failed" || step.phase === "failed"
      ? "error"
      : step.phase === "tool_running" || step.status === "running"
        ? "tool_call"
        : "tool_result";

  return {
    id: step.id,
    sequence: step.sequence,
    kind,
    status: step.status,
    title: step.label ?? null,
    text: step.safe_summary || step.delta_text || null,
    tool_name: step.tool_name ?? null,
    tool_call_id: step.tool_call_id ?? null,
    recorded_at: step.started_at,
    updated_at: step.ended_at ?? step.started_at,
    metadata: mergeMetadataPreferRicher(
      step.metadata,
      step.command || step.status
        ? {
            ...(step.command ? { command: step.command } : {}),
            ...(step.status ? { status: step.status } : {}),
          }
        : undefined,
    ),
  };
}

function collectGenerationToolSegmentsForMessage(
  detail: SessionDetail,
  message: SessionMessage,
): AssistantTranscriptSegment[] {
  const detailRecord: Record<string, unknown> | null = isRecord(detail) ? detail : null;
  const rawGenerations = detailRecord?.["generations"];
  if (!Array.isArray(rawGenerations)) {
    return [];
  }

  return rawGenerations
    .filter(isChatGenerationCandidate)
    .filter(
      (generation) =>
        generation.id === message.generation_id || generation.assistant_message_id === message.id,
    )
    .flatMap((generation) =>
      (generation.steps ?? [])
        .map((step) => mapGenerationToolStepToTranscriptSegment(step))
        .filter(
          (segment): segment is AssistantTranscriptSegment =>
            segment !== null && isToolTranscriptSegment(segment),
        ),
    )
    .sort(compareAssistantTranscriptSegments);
}

function mergeTranscriptCollectionsPreferRicher(
  existingTranscript: AssistantTranscriptSegment[],
  incomingTranscript: AssistantTranscriptSegment[],
): AssistantTranscriptSegment[] {
  const merged: AssistantTranscriptSegment[] = [...existingTranscript];

  for (const segment of incomingTranscript) {
    const existingIndex = findTranscriptMergeIndex(merged, segment);
    if (existingIndex < 0) {
      merged.push(segment);
      continue;
    }

    const existingSegment = merged[existingIndex];
    if (!existingSegment) {
      continue;
    }

    merged[existingIndex] = mergeTranscriptSegmentPreferRicher(existingSegment, segment);
  }

  return merged.sort(compareAssistantTranscriptSegments);
}

function enrichTranscriptWithGenerationSegments(
  messageTranscript: AssistantTranscriptSegment[],
  generationSegments: AssistantTranscriptSegment[],
): AssistantTranscriptSegment[] {
  return mergeTranscriptCollectionsPreferRicher(messageTranscript, generationSegments);
}

function mergeAssistantTranscriptSegments(
  existingTranscript: AssistantTranscriptSegment[],
  incomingTranscript: AssistantTranscriptSegment[],
  enrichmentTranscript: AssistantTranscriptSegment[],
): AssistantTranscriptSegment[] {
  const mergedPersistedTranscript = mergeTranscriptCollectionsPreferRicher(
    existingTranscript,
    incomingTranscript,
  );

  return enrichTranscriptWithGenerationSegments(mergedPersistedTranscript, enrichmentTranscript);
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

  const existingMessage = detail.messages.find((item) => item.id === message.id) ?? null;
  const generationTranscriptEnrichment = collectGenerationToolSegmentsForMessage(detail, message);
  const mergedTranscript = mergeAssistantTranscriptSegments(
    existingMessage?.assistant_transcript ?? [],
    message.assistant_transcript,
    generationTranscriptEnrichment,
  );

  const nextMessage = existingMessage
    ? {
        ...existingMessage,
        ...message,
        content:
          existingMessage.role === "assistant" && message.role === "assistant"
            ? pickPreferredAssistantContent(existingMessage.content, message.content)
            : message.content,
        metadata: message.metadata ?? existingMessage.metadata,
        attachments:
          message.attachments.length > 0 || existingMessage.attachments.length === 0
            ? message.attachments
            : existingMessage.attachments,
        assistant_transcript: mergedTranscript,
      }
    : {
        ...message,
        assistant_transcript: mergedTranscript,
      };

  const remainingMessages = detail.messages.filter((item) => item.id !== message.id);
  const reconciledMessages =
    nextMessage.role === "user" && !isOptimisticUserMessage(nextMessage)
      ? remainingMessages.filter(
          (item) =>
            !(isOptimisticUserMessage(item) && item.content.trim() === nextMessage.content.trim()),
        )
      : remainingMessages;
  const nextMessages = [...reconciledMessages, nextMessage].sort((left, right) => {
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
  const cleaned = value.replace(/\s+/g, " ").trim();
  if (cleaned.length <= MAX_SAFE_SUMMARY_LENGTH) {
    return cleaned;
  }

  return `${cleaned.slice(0, MAX_SAFE_SUMMARY_LENGTH - 1).trimEnd()}…`;
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

function getPersistedReasoningPayload(entry: Record<string, unknown>): Record<string, unknown> {
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
  } else if (!summary && state === "context.compacted") {
    summary = "已压缩对话";
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
          : state === "context.compacted"
            ? "completed"
          : state?.endsWith(".started")
            ? "running"
            : null);

  return {
    label:
      readFirstNonEmptyString(data, ["label", "title"]) ??
      (state === "context.compacted"
        ? "上下文压缩"
        : state
          ? formatAssistantTraceStateLabel(state)
          : "思路进展"),
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
    const assistantMessageId = getPersistedReasoningAssistantMessageId(
      generation,
      rawEntry,
      payload,
    );
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

function compareGenerationSteps(left: GenerationStep, right: GenerationStep): number {
  if (left.sequence !== right.sequence) {
    return left.sequence - right.sequence;
  }

  const startedAtDifference = toTimestamp(left.started_at) - toTimestamp(right.started_at);
  if (startedAtDifference !== 0) {
    return startedAtDifference;
  }

  return left.id.localeCompare(right.id);
}

function compareGenerations(left: ChatGeneration, right: ChatGeneration): number {
  const createdAtDifference = toTimestamp(left.created_at) - toTimestamp(right.created_at);
  if (createdAtDifference !== 0) {
    return createdAtDifference;
  }

  return left.id.localeCompare(right.id);
}

function nextGenerationStepSequence(generation: ChatGeneration): number {
  return (
    (generation.steps ?? []).reduce((currentMax, step) => Math.max(currentMax, step.sequence), 0) +
    1
  );
}

function inferTracePhase(data: Record<string, unknown>): string | null {
  if (typeof data.phase === "string" && data.phase.trim().length > 0) {
    return data.phase;
  }

  const state = typeof data.state === "string" ? data.state : null;
  switch (state) {
    case "generation.started":
      return "planning";
    case "generation.completed":
      return "completed";
    case "generation.failed":
      return "failed";
    case "generation.cancelled":
      return "cancelled";
    case "tool.started":
      return "tool_running";
    case "tool.finished":
    case "tool.failed":
      return "tool_result";
    default:
      return "planning";
  }
}

function inferTraceStatus(data: Record<string, unknown>): string {
  if (typeof data.status === "string" && data.status.trim().length > 0) {
    return data.status;
  }

  const state = typeof data.state === "string" ? data.state : null;
  if (state === "generation.completed" || state === "tool.finished") {
    return "completed";
  }
  if (state === "generation.failed" || state === "tool.failed") {
    return "failed";
  }
  if (state === "generation.cancelled") {
    return "cancelled";
  }
  if (state === "generation.started" || state === "tool.started") {
    return "running";
  }

  return "running";
}

function inferGenerationStatusFromTrace(data: Record<string, unknown>): string | null {
  const state = typeof data.state === "string" ? data.state : null;
  switch (state) {
    case "generation.started":
      return "running";
    case "generation.completed":
      return "completed";
    case "generation.failed":
      return "failed";
    case "generation.cancelled":
      return "cancelled";
    default:
      return null;
  }
}

function buildGenerationStepId(
  type: string,
  data: Record<string, unknown>,
  createdAt: string,
  cursor: number | null,
): string {
  if (typeof data.step_id === "string" && data.step_id.trim().length > 0) {
    return data.step_id;
  }

  if (
    (type === "message.delta" || type === "message.completed") &&
    typeof data.generation_id === "string" &&
    data.generation_id.length > 0
  ) {
    return `output:${data.generation_id}`;
  }

  if (
    type.startsWith("tool.call.") &&
    typeof data.tool_call_id === "string" &&
    data.tool_call_id.length > 0
  ) {
    return `tool:${data.tool_call_id}`;
  }

  if (typeof cursor === "number" && Number.isFinite(cursor)) {
    return `event:${type}:${cursor}`;
  }

  return `event:${type}:${createdAt}`;
}

function buildToolStepSummary(type: string, data: Record<string, unknown>): string | null {
  const toolName = readFirstNonEmptyString(data, ["tool_name", "tool"]);
  const command = readFirstNonEmptyString(data, ["command"]);
  const error = readFirstNonEmptyString(data, ["error"]);
  const summary = readFirstNonEmptyString(data, ["safe_summary", "summary", "message"]);
  if (summary) {
    return sanitizeSafeSummaryText(summary);
  }

  if (type === "tool.call.started") {
    return command
      ? `开始调用工具：${command}`
      : toolName
        ? `开始调用工具：${toolName}`
        : "开始调用工具。";
  }

  if (type === "tool.call.finished") {
    return command
      ? `工具调用已完成：${command}`
      : toolName
        ? `工具调用已完成：${toolName}`
        : "工具调用已完成。";
  }

  if (type === "tool.call.failed") {
    if (command && error) {
      return `工具调用失败：${command}，${sanitizeSafeSummaryText(error)}`;
    }
    if (command) {
      return `工具调用失败：${command}`;
    }
    if (toolName && error) {
      return `工具调用失败：${toolName}，${sanitizeSafeSummaryText(error)}`;
    }
    if (toolName) {
      return `工具调用失败：${toolName}`;
    }
  }

  return error ? sanitizeSafeSummaryText(error) : null;
}

function buildOutputStepSummary(data: Record<string, unknown>): string | null {
  const delta = typeof data.delta === "string" ? data.delta.trim() : "";
  const content = typeof data.content === "string" ? data.content.trim() : "";
  const visibleText = delta || content;

  if (!visibleText) {
    return null;
  }

  return sanitizeSafeSummaryText(visibleText);
}

function buildLiveGenerationStep(
  type: string,
  data: Record<string, unknown>,
  createdAt: string,
  cursor: number | null,
  generation: ChatGeneration | null,
): GenerationStep | null {
  const generationId = readFirstNonEmptyString(data, ["generation_id"]) ?? generation?.id ?? null;
  const messageId =
    readFirstNonEmptyString(data, ["assistant_message_id", "message_id"]) ??
    generation?.assistant_message_id ??
    null;

  if (!generationId) {
    return null;
  }

  const sequence =
    readFirstFiniteNumber(data, ["sequence"]) ??
    (generation ? nextGenerationStepSequence(generation) : 1);

  const baseStep = {
    id: buildGenerationStepId(type, data, createdAt, cursor),
    generation_id: generationId,
    session_id:
      typeof data.session_id === "string" ? data.session_id : (generation?.session_id ?? ""),
    message_id: messageId,
    sequence,
    tool_name: readFirstNonEmptyString(data, ["tool_name", "tool"]),
    tool_call_id: readFirstNonEmptyString(data, ["tool_call_id"]),
    command: readFirstNonEmptyString(data, ["command"]),
    metadata: isRecord(data.metadata) ? data.metadata : undefined,
    started_at: readFirstNonEmptyString(data, ["started_at", "created_at"]) ?? createdAt,
    ended_at:
      readFirstNonEmptyString(data, ["ended_at", "completed_at"]) ??
      (type === "message.completed" ? createdAt : null),
  } satisfies Omit<GenerationStep, "kind" | "status" | "delta_text"> & {
    metadata?: Record<string, unknown>;
  };

  if (type === "assistant.summary") {
    const safeSummary = extractSafeSessionSummary(type, data)?.summary;
    return {
      ...baseStep,
      kind: "reasoning",
      phase: typeof data.phase === "string" ? data.phase : "planning",
      status: typeof data.status === "string" ? data.status : "completed",
      state: typeof data.state === "string" ? data.state : "summary.updated",
      label: readFirstNonEmptyString(data, ["label", "title"]) ?? "思路摘要",
      safe_summary: safeSummary ?? readFirstNonEmptyString(data, ["summary"]),
      delta_text: "",
    };
  }

  if (type === "assistant.trace") {
    const safeSummary = extractSafeSessionSummary(type, data)?.summary;
    return {
      ...baseStep,
      kind: "status",
      phase: inferTracePhase(data),
      status: inferTraceStatus(data),
      state: typeof data.state === "string" ? data.state : "trace",
      label: readFirstNonEmptyString(data, ["label", "title"]) ?? "思路进展",
      safe_summary: safeSummary,
      delta_text: "",
    };
  }

  if (type.startsWith("tool.call.")) {
    const toolMetadata = buildToolStepMetadata(data);
    return {
      ...baseStep,
      kind: "tool",
      phase: type === "tool.call.started" ? "tool_running" : "tool_result",
      status:
        typeof data.status === "string"
          ? data.status
          : type === "tool.call.failed"
            ? "failed"
            : type === "tool.call.finished"
              ? "completed"
              : "running",
      state:
        type === "tool.call.started"
          ? "started"
          : type === "tool.call.failed"
            ? "failed"
            : "finished",
      label: readFirstNonEmptyString(data, ["label", "tool_name", "tool", "command"]),
      safe_summary: buildToolStepSummary(type, data),
      delta_text: "",
      metadata: toolMetadata,
      ended_at: type === "tool.call.started" ? null : baseStep.ended_at,
    };
  }

  if (type === "message.delta" || type === "message.completed") {
    return {
      ...baseStep,
      kind: "output",
      phase: "synthesis",
      status: type === "message.completed" ? "completed" : "running",
      state: type === "message.completed" ? "completed" : "streaming",
      label: "正文输出",
      safe_summary: buildOutputStepSummary(data),
      delta_text:
        typeof data.delta === "string"
          ? data.delta
          : typeof data.content === "string"
            ? data.content
            : "",
    };
  }

  if (
    type === "generation.started" ||
    type === "generation.cancelled" ||
    type === "generation.failed"
  ) {
    return {
      ...baseStep,
      kind: "status",
      phase:
        type === "generation.started"
          ? "planning"
          : type === "generation.cancelled"
            ? "cancelled"
            : "failed",
      status:
        type === "generation.started"
          ? "running"
          : type === "generation.cancelled"
            ? "cancelled"
            : "failed",
      state: type.replace("generation.", ""),
      label: "Generation status",
      safe_summary: extractSafeSessionSummary(type, data)?.summary,
      delta_text: "",
    };
  }

  return null;
}

function buildToolStepMetadata(data: Record<string, unknown>): Record<string, unknown> | undefined {
  const metadata: Record<string, unknown> = isRecord(data.metadata) ? { ...data.metadata } : {};
  for (const key of [
    "arguments",
    "command",
    "status",
    "stdout",
    "stderr",
    "exit_code",
    "output",
    "execution",
    "payload",
    "data",
    "result",
    "text",
    "message",
    "summary",
    "safe_summary",
    "artifact_paths",
    "requested_timeout_seconds",
    "run_id",
    "created_at",
  ] as const) {
    if (data[key] !== undefined) {
      metadata[key] = data[key];
    }
  }

  return Object.keys(metadata).length > 0 ? metadata : undefined;
}

type ShellMetadataScoreRule = {
  path: readonly string[];
  weight: number;
};

const SHELL_METADATA_HIGH_PRIORITY_RULES: readonly ShellMetadataScoreRule[] = [
  { path: ["stdout"], weight: 72 },
  { path: ["stderr"], weight: 68 },
  { path: ["exit_code"], weight: 64 },
  { path: ["artifacts"], weight: 60 },
  { path: ["artifact_paths"], weight: 60 },
  { path: ["output", "stdout"], weight: 70 },
  { path: ["output", "stderr"], weight: 66 },
  { path: ["output", "text"], weight: 62 },
  { path: ["output", "exit_code"], weight: 60 },
  { path: ["output", "artifacts"], weight: 58 },
  { path: ["output", "artifact_paths"], weight: 58 },
  { path: ["result", "stdout"], weight: 74 },
  { path: ["result", "stderr"], weight: 70 },
  { path: ["result", "exit_code"], weight: 66 },
  { path: ["result", "artifacts"], weight: 62 },
  { path: ["result", "artifact_paths"], weight: 62 },
  { path: ["result", "output", "stdout"], weight: 78 },
  { path: ["result", "output", "stderr"], weight: 74 },
  { path: ["result", "output", "text"], weight: 70 },
  { path: ["result", "output", "exit_code"], weight: 68 },
  { path: ["execution"], weight: 60 },
  { path: ["result", "execution"], weight: 66 },
  { path: ["payload"], weight: 56 },
  { path: ["result", "payload"], weight: 62 },
  { path: ["data"], weight: 56 },
  { path: ["result", "data"], weight: 62 },
] as const;

const SHELL_METADATA_MEDIUM_PRIORITY_RULES: readonly ShellMetadataScoreRule[] = [
  { path: ["command"], weight: 20 },
  { path: ["status"], weight: 16 },
] as const;

const SHELL_METADATA_LOW_PRIORITY_RULES: readonly ShellMetadataScoreRule[] = [
  { path: ["safe_summary"], weight: 6 },
  { path: ["summary"], weight: 5 },
  { path: ["message"], weight: 4 },
  { path: ["text"], weight: 4 },
] as const;

const SHELL_RENDERABLE_PATHS = [
  ["stdout"],
  ["stderr"],
  ["exit_code"],
  ["artifacts"],
  ["artifact_paths"],
  ["output"],
  ["output", "stdout"],
  ["output", "stderr"],
  ["output", "text"],
  ["output", "exit_code"],
  ["result"],
  ["result", "stdout"],
  ["result", "stderr"],
  ["result", "exit_code"],
  ["result", "artifacts"],
  ["result", "artifact_paths"],
  ["result", "output"],
  ["result", "output", "stdout"],
  ["result", "output", "stderr"],
  ["result", "output", "text"],
  ["execution"],
  ["result", "execution"],
  ["payload"],
  ["result", "payload"],
  ["data"],
  ["result", "data"],
] as const;

function readMetadataPath(
  metadata: Record<string, unknown> | undefined,
  path: readonly string[],
): { found: boolean; value: unknown } {
  if (!metadata || path.length === 0) {
    return { found: false, value: undefined };
  }

  let current: unknown = metadata;
  for (const key of path) {
    if (!isRecord(current) || !Object.prototype.hasOwnProperty.call(current, key)) {
      return { found: false, value: undefined };
    }
    current = current[key];
  }

  return { found: true, value: current };
}

function isMeaningfulShellPayloadValue(value: unknown): boolean {
  if (value === null || value === undefined) {
    return false;
  }

  if (typeof value === "string") {
    return value.trim().length > 0;
  }

  if (typeof value === "number") {
    return Number.isFinite(value);
  }

  if (typeof value === "boolean") {
    return true;
  }

  if (Array.isArray(value)) {
    return value.length > 0 && value.some((item) => isMeaningfulShellPayloadValue(item));
  }

  if (isRecord(value)) {
    if (Object.keys(value).length === 0) {
      return false;
    }

    return Object.values(value).some((entry) => isMeaningfulShellPayloadValue(entry));
  }

  return true;
}

function scoreMetadataValueCompleteness(value: unknown): number {
  if (value === null || value === undefined) {
    return 0;
  }

  if (typeof value === "string") {
    const normalized = value.trim();
    if (!normalized) {
      return 0;
    }

    return (
      Math.min(40, Math.max(2, Math.ceil(normalized.length / 12))) +
      (normalized.includes("\n") ? 8 : 0)
    );
  }

  if (typeof value === "number") {
    return Number.isFinite(value) ? 8 : 0;
  }

  if (typeof value === "boolean") {
    return 4;
  }

  if (Array.isArray(value)) {
    return (
      value.length * 4 +
      value.reduce<number>((total, entry) => total + scoreMetadataValueCompleteness(entry), 0)
    );
  }

  if (isRecord(value)) {
    return (
      Object.keys(value).length * 3 +
      Object.values(value).reduce<number>(
        (total, entry) => total + scoreMetadataValueCompleteness(entry),
        0,
      )
    );
  }

  return 1;
}

function scoreShellMetadataRichness(metadata: Record<string, unknown> | undefined): number {
  if (!metadata) {
    return 0;
  }

  let score = scoreMetadataValueCompleteness(metadata);
  for (const rule of SHELL_METADATA_HIGH_PRIORITY_RULES) {
    const candidate = readMetadataPath(metadata, rule.path);
    if (!candidate.found || !isMeaningfulShellPayloadValue(candidate.value)) {
      continue;
    }

    score += rule.weight + scoreMetadataValueCompleteness(candidate.value);
  }

  for (const rule of SHELL_METADATA_MEDIUM_PRIORITY_RULES) {
    const candidate = readMetadataPath(metadata, rule.path);
    if (!candidate.found || !isMeaningfulShellPayloadValue(candidate.value)) {
      continue;
    }

    score += rule.weight + Math.ceil(scoreMetadataValueCompleteness(candidate.value) / 2);
  }

  for (const rule of SHELL_METADATA_LOW_PRIORITY_RULES) {
    const candidate = readMetadataPath(metadata, rule.path);
    if (!candidate.found || !isMeaningfulShellPayloadValue(candidate.value)) {
      continue;
    }

    score += rule.weight + Math.ceil(scoreMetadataValueCompleteness(candidate.value) / 4);
  }

  return score;
}

function hasRenderableShellPayload(metadata: Record<string, unknown> | undefined): boolean {
  if (!metadata) {
    return false;
  }

  return SHELL_RENDERABLE_PATHS.some((path) => {
    const candidate = readMetadataPath(metadata, path);
    return candidate.found && isMeaningfulShellPayloadValue(candidate.value);
  });
}

function buildArrayItemIdentity(value: unknown): string {
  if (typeof value === "string") {
    return `string:${value}`;
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return `${typeof value}:${String(value)}`;
  }

  if (isRecord(value)) {
    const explicitKey = readFirstNonEmptyString(value, [
      "id",
      "path",
      "relative_path",
      "name",
      "tool_call_id",
      "kind",
    ]);
    if (explicitKey) {
      return `record:${explicitKey}`;
    }

    return `record:${JSON.stringify(value)}`;
  }

  return `other:${JSON.stringify(value)}`;
}

function mergeArrayPreferComplete(
  existingValue: unknown[] | undefined,
  incomingValue: unknown[] | undefined,
): unknown[] | undefined {
  if (!existingValue) {
    return incomingValue ? [...incomingValue] : existingValue;
  }

  if (!incomingValue) {
    return [...existingValue];
  }

  const merged: unknown[] = [];
  const indexByIdentity = new Map<string, number>();

  for (const entry of [...existingValue, ...incomingValue]) {
    const identity = buildArrayItemIdentity(entry);
    const existingIndex = indexByIdentity.get(identity);

    if (existingIndex === undefined) {
      indexByIdentity.set(identity, merged.length);
      merged.push(entry);
      continue;
    }

    const currentEntry = merged[existingIndex];
    if (isRecord(currentEntry) && isRecord(entry)) {
      merged[existingIndex] = mergeMetadataPreferRicher(currentEntry, entry) ?? currentEntry;
      continue;
    }

    merged[existingIndex] = mergeScalarPreferRicher(currentEntry, entry);
  }

  return merged;
}

function mergeScalarPreferRicher(existingValue: unknown, incomingValue: unknown): unknown {
  if (incomingValue === undefined) {
    return existingValue;
  }

  if (existingValue === undefined) {
    return incomingValue;
  }

  if (existingValue === null) {
    return incomingValue;
  }

  if (incomingValue === null) {
    return existingValue;
  }

  if (isRecord(existingValue) && isRecord(incomingValue)) {
    return mergeMetadataPreferRicher(existingValue, incomingValue);
  }

  if (Array.isArray(existingValue) && Array.isArray(incomingValue)) {
    return mergeArrayPreferComplete(existingValue, incomingValue);
  }

  if (typeof existingValue === "string" && typeof incomingValue === "string") {
    const normalizedExisting = existingValue.trim();
    const normalizedIncoming = incomingValue.trim();

    if (!normalizedIncoming) {
      return existingValue;
    }

    if (!normalizedExisting) {
      return incomingValue;
    }

    const existingThin = isThinToolResultText(normalizedExisting);
    const incomingThin = isThinToolResultText(normalizedIncoming);
    if (existingThin !== incomingThin) {
      return existingThin ? incomingValue : existingValue;
    }

    if (normalizedIncoming.length > normalizedExisting.length) {
      return incomingValue;
    }

    if (normalizedExisting.length > normalizedIncoming.length) {
      return existingValue;
    }

    return incomingValue;
  }

  if (typeof existingValue === "number" && typeof incomingValue === "number") {
    if (!Number.isFinite(existingValue)) {
      return incomingValue;
    }

    if (!Number.isFinite(incomingValue)) {
      return existingValue;
    }

    return incomingValue;
  }

  if (typeof existingValue === "boolean" && typeof incomingValue === "boolean") {
    return incomingValue;
  }

  const existingScore = scoreMetadataValueCompleteness(existingValue);
  const incomingScore = scoreMetadataValueCompleteness(incomingValue);
  if (incomingScore > existingScore) {
    return incomingValue;
  }

  if (existingScore > incomingScore) {
    return existingValue;
  }

  return incomingValue;
}

function mergeMetadataPreferRicher(
  existingMetadata: Record<string, unknown> | undefined,
  incomingMetadata: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (!existingMetadata && !incomingMetadata) {
    return undefined;
  }

  if (!existingMetadata) {
    return incomingMetadata ? { ...incomingMetadata } : undefined;
  }

  if (!incomingMetadata) {
    return { ...existingMetadata };
  }

  const mergedMetadata: Record<string, unknown> = {};
  const keys = new Set([...Object.keys(existingMetadata), ...Object.keys(incomingMetadata)]);
  for (const key of keys) {
    const existingValue = existingMetadata[key];
    const incomingValue = incomingMetadata[key];

    if (isRecord(existingValue) && isRecord(incomingValue)) {
      const mergedRecord = mergeMetadataPreferRicher(existingValue, incomingValue);
      if (mergedRecord !== undefined) {
        mergedMetadata[key] = mergedRecord;
      }
      continue;
    }

    if (Array.isArray(existingValue) && Array.isArray(incomingValue)) {
      const mergedArray = mergeArrayPreferComplete(existingValue, incomingValue);
      if (mergedArray !== undefined) {
        mergedMetadata[key] = mergedArray;
      }
      continue;
    }

    const mergedValue = mergeScalarPreferRicher(existingValue, incomingValue);
    if (mergedValue !== undefined) {
      mergedMetadata[key] = mergedValue;
    }
  }

  return Object.keys(mergedMetadata).length > 0 ? mergedMetadata : undefined;
}

function mergeGenerationStepList(
  currentSteps: GenerationStep[] | undefined,
  incomingStep: GenerationStep,
): GenerationStep[] {
  const steps = currentSteps ?? [];
  const existingIndex = steps.findIndex((step) => {
    if (step.id === incomingStep.id) {
      return true;
    }

    if (
      incomingStep.kind === "tool" &&
      incomingStep.tool_call_id &&
      step.kind === "tool" &&
      step.tool_call_id === incomingStep.tool_call_id
    ) {
      return true;
    }

    return (
      incomingStep.kind === "output" &&
      step.kind === "output" &&
      step.generation_id === incomingStep.generation_id &&
      step.message_id === incomingStep.message_id
    );
  });

  if (existingIndex < 0) {
    return [...steps, incomingStep].sort(compareGenerationSteps);
  }

  const existingStep = steps[existingIndex];
  const nextMetadata = mergeMetadataPreferRicher(existingStep.metadata, incomingStep.metadata);
  const nextDeltaText =
    incomingStep.kind === "output"
      ? incomingStep.status === "completed" &&
        incomingStep.delta_text.length >= existingStep.delta_text.length
        ? incomingStep.delta_text
        : incomingStep.delta_text && !existingStep.delta_text.endsWith(incomingStep.delta_text)
          ? `${existingStep.delta_text}${incomingStep.delta_text}`
          : existingStep.delta_text || incomingStep.delta_text
      : incomingStep.delta_text || existingStep.delta_text;

  const nextStep: GenerationStep = {
    ...existingStep,
    ...incomingStep,
    delta_text: nextDeltaText,
    safe_summary:
      (mergeScalarPreferRicher(existingStep.safe_summary, incomingStep.safe_summary) as
        | string
        | undefined) ?? existingStep.safe_summary,
    command:
      (mergeScalarPreferRicher(existingStep.command, incomingStep.command) as
        | string
        | null
        | undefined) ?? existingStep.command,
    metadata: nextMetadata,
    ended_at: incomingStep.ended_at ?? existingStep.ended_at,
  };

  return steps
    .map((step, index) => (index === existingIndex ? nextStep : step))
    .sort(compareGenerationSteps);
}

function mergeGenerationState(generation: ChatGeneration, step: GenerationStep): ChatGeneration {
  const inferredStatus =
    step.kind === "status"
      ? step.status
      : step.kind === "output" && step.status === "running"
        ? "running"
        : step.kind === "output" && step.status === "completed"
          ? generation.status === "queued"
            ? "running"
            : generation.status
          : generation.status;

  return {
    ...generation,
    status: inferredStatus as ChatGeneration["status"],
    updated_at: step.ended_at ?? step.started_at,
    started_at:
      inferredStatus === "running"
        ? (generation.started_at ?? step.started_at)
        : generation.started_at,
    ended_at:
      inferredStatus === "completed" ||
      inferredStatus === "failed" ||
      inferredStatus === "cancelled"
        ? (step.ended_at ?? step.started_at)
        : generation.ended_at,
    steps: mergeGenerationStepList(generation.steps, step),
  };
}

function createLiveGeneration(
  conversation: SessionConversation,
  step: GenerationStep,
  createdAt: string,
): ChatGeneration {
  return {
    id: step.generation_id,
    session_id: conversation.session.id,
    branch_id:
      conversation.active_branch?.id ?? conversation.session.active_branch_id ?? "default-branch",
    action: "reply",
    assistant_message_id: step.message_id ?? "",
    status:
      step.status === "pending"
        ? "queued"
        : step.status === "running"
          ? "running"
          : step.status === "completed"
            ? "completed"
            : step.status === "failed"
              ? "failed"
              : "cancelled",
    reasoning_trace: [],
    steps: [step],
    created_at: createdAt,
    updated_at: createdAt,
    started_at: step.status === "running" ? step.started_at : null,
    ended_at:
      step.status === "completed" || step.status === "failed" || step.status === "cancelled"
        ? (step.ended_at ?? step.started_at)
        : null,
  };
}

export function mergeConversationGeneration(
  conversation: SessionConversation | undefined,
  generation: ChatGeneration,
): SessionConversation | undefined {
  if (!conversation || conversation.session.id !== generation.session_id) {
    return conversation;
  }

  const existingGeneration = conversation.generations.find((item) => item.id === generation.id);
  const mergedGeneration: ChatGeneration = existingGeneration
    ? {
        ...existingGeneration,
        ...generation,
        steps: (generation.steps ?? []).reduce(
          (currentSteps, step) => mergeGenerationStepList(currentSteps, step),
          existingGeneration.steps ?? [],
        ),
      }
    : generation;

  const nextGenerations = [
    ...conversation.generations.filter((item) => item.id !== generation.id),
    mergedGeneration,
  ].sort(compareGenerations);

  return {
    ...conversation,
    generations: nextGenerations,
  };
}

export function mergeConversationGenerationStep(
  conversation: SessionConversation | undefined,
  step: GenerationStep,
): SessionConversation | undefined {
  if (!conversation || conversation.session.id !== step.session_id) {
    return conversation;
  }

  const generationIndex = conversation.generations.findIndex(
    (generation) => generation.id === step.generation_id,
  );
  const nextGenerations = [...conversation.generations];

  if (generationIndex < 0) {
    nextGenerations.push(createLiveGeneration(conversation, step, step.started_at));
  } else {
    nextGenerations[generationIndex] = mergeGenerationState(nextGenerations[generationIndex], step);
  }

  return {
    ...conversation,
    generations: nextGenerations.sort(compareGenerations),
  };
}

function mergeConversationGenerationLifecycle(
  conversation: SessionConversation,
  generationId: string | null,
  nextStatus: ChatGeneration["status"] | null,
  createdAt: string,
): SessionConversation {
  if (!generationId || nextStatus === null) {
    return conversation;
  }

  return {
    ...conversation,
    generations: conversation.generations.map((generation) =>
      generation.id === generationId
        ? {
            ...generation,
            status: nextStatus,
            updated_at: createdAt,
            started_at:
              nextStatus === "running"
                ? (generation.started_at ?? createdAt)
                : generation.started_at,
            ended_at:
              nextStatus === "completed" || nextStatus === "failed" || nextStatus === "cancelled"
                ? createdAt
                : generation.ended_at,
          }
        : generation,
    ),
    active_generation_id:
      nextStatus === "running"
        ? generationId
        : conversation.active_generation_id === generationId
          ? null
          : conversation.active_generation_id,
  };
}

export function mergeConversationGenerationEvent(
  conversation: SessionConversation | undefined,
  type: string,
  data: unknown,
  createdAt: string,
  cursor: number | null,
): SessionConversation | undefined {
  if (!conversation || !isRecord(data)) {
    return conversation;
  }

  let nextConversation =
    type === "assistant.summary" || type === "assistant.trace"
      ? (mergeConversationReasoningEvent(conversation, type, data, createdAt, cursor) ??
        conversation)
      : conversation;

  const generationId = readFirstNonEmptyString(data, ["generation_id"]);
  const liveStep = buildLiveGenerationStep(
    type,
    data,
    createdAt,
    cursor,
    nextConversation.generations.find((generation) => {
      if (generationId && generation.id === generationId) {
        return true;
      }

      const assistantMessageId = readFirstNonEmptyString(data, [
        "assistant_message_id",
        "message_id",
      ]);
      return assistantMessageId ? generation.assistant_message_id === assistantMessageId : false;
    }) ?? null,
  );

  if (liveStep) {
    nextConversation =
      mergeConversationGenerationStep(nextConversation, liveStep) ?? nextConversation;
  }

  if (type === "session.updated") {
    const queuedPromptCount =
      typeof data.queued_prompt_count === "number" && Number.isFinite(data.queued_prompt_count)
        ? data.queued_prompt_count
        : null;
    if (queuedPromptCount !== null) {
      nextConversation = {
        ...nextConversation,
        queued_generation_count: queuedPromptCount,
      };
    }
    return nextConversation;
  }

  if (type === "generation.started") {
    return {
      ...mergeConversationGenerationLifecycle(nextConversation, generationId, "running", createdAt),
      queued_generation_count:
        typeof data.queued_prompt_count === "number" && Number.isFinite(data.queued_prompt_count)
          ? data.queued_prompt_count
          : nextConversation.queued_generation_count,
    };
  }

  if (type === "generation.cancelled") {
    return mergeConversationGenerationLifecycle(
      nextConversation,
      generationId,
      "cancelled",
      createdAt,
    );
  }

  if (type === "generation.failed") {
    return mergeConversationGenerationLifecycle(
      nextConversation,
      generationId,
      "failed",
      createdAt,
    );
  }

  if (type === "assistant.trace") {
    const inferredStatus = inferGenerationStatusFromTrace(data);
    return mergeConversationGenerationLifecycle(
      nextConversation,
      generationId,
      inferredStatus as ChatGeneration["status"] | null,
      createdAt,
    );
  }

  return nextConversation;
}

export function mergeQueueState(
  queue: SessionQueue | undefined,
  type: string,
  data: unknown,
): SessionQueue | undefined {
  if (!queue || !isRecord(data)) {
    return queue;
  }

  if (type === "session.updated") {
    const queuedPromptCount =
      typeof data.queued_prompt_count === "number" && Number.isFinite(data.queued_prompt_count)
        ? data.queued_prompt_count
        : null;
    if (queuedPromptCount === null) {
      return queue;
    }

    return {
      ...queue,
      queued_generation_count: queuedPromptCount,
    };
  }

  const generationTraceState =
    type === "assistant.trace" && typeof data.state === "string" ? data.state : null;
  const normalizedType = generationTraceState ?? type;
  const generationId = readFirstNonEmptyString(data, ["generation_id"]);
  if (!generationId) {
    return queue;
  }

  if (normalizedType === "generation.started") {
    const activeGeneration =
      queue.queued_generations.find((generation) => generation.id === generationId) ??
      queue.active_generation;
    return {
      ...queue,
      active_generation:
        activeGeneration && activeGeneration.id === generationId
          ? { ...activeGeneration, status: "running" }
          : queue.active_generation,
      active_generation_id: generationId,
      queued_generations: queue.queued_generations.filter(
        (generation) => generation.id !== generationId,
      ),
      queued_generation_count: Math.max(
        0,
        queue.queued_generations.filter((generation) => generation.id !== generationId).length,
      ),
    };
  }

  if (normalizedType === "generation.completed") {
    return {
      ...queue,
      active_generation:
        queue.active_generation?.id === generationId ? null : queue.active_generation,
      active_generation_id:
        queue.active_generation_id === generationId ? null : queue.active_generation_id,
      queued_generation_count: queue.queued_generations.length,
    };
  }

  if (normalizedType === "generation.cancelled" || normalizedType === "generation.failed") {
    return {
      ...queue,
      active_generation:
        queue.active_generation?.id === generationId ? null : queue.active_generation,
      active_generation_id:
        queue.active_generation_id === generationId ? null : queue.active_generation_id,
      queued_generations: queue.queued_generations.filter(
        (generation) => generation.id !== generationId,
      ),
      queued_generation_count: Math.max(
        0,
        queue.queued_generations.filter((generation) => generation.id !== generationId).length,
      ),
    };
  }

  return queue;
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
  const nextSequence =
    getPersistedReasoningSequence(data, data) ?? getNextReasoningSequence(generation);
  const traceEntry = cloneReasoningTraceEntry(type, data, createdAt, cursor, nextSequence);

  return {
    ...generation,
    reasoning_summary:
      type === "assistant.summary" && safeSummary
        ? safeSummary.summary
        : generation.reasoning_summary,
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
  if (
    !conversation ||
    !isRecord(data) ||
    (type !== "assistant.summary" && type !== "assistant.trace")
  ) {
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

  if (type === "session.compaction.completed") {
    const summaryText = readFirstNonEmptyString(data, ["summary"]);
    return {
      label: "上下文压缩",
      summary: sanitizeSafeSummaryText(summaryText ?? "已压缩对话"),
      tone: "success",
    };
  }

  if (type === "session.compaction.failed") {
    const summaryText = readFirstNonEmptyString(data, ["summary"]);
    const errorMessage = readFirstNonEmptyString(data, ["error"]);
    return {
      label: "上下文压缩",
      summary: sanitizeSafeSummaryText(
        summaryText ?? (errorMessage ? `上下文压缩失败：${errorMessage}` : "上下文压缩失败"),
      ),
      tone: "error",
    };
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
  if (type === "session.context_window.updated") {
    return false;
  }

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

  if (type === "session.compaction.completed" || type === "session.compaction.failed") {
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
    assistant_transcript: toAssistantTranscriptSegmentList(value.assistant_transcript, createdAt),
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
    const content = typeof data.content === "string" ? data.content : "";
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

function toAssistantTranscriptSegmentList(
  value: unknown,
  fallbackTimestamp: string,
): AssistantTranscriptSegment[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .flatMap((entry, index) => {
      if (!isRecord(entry)) {
        return [];
      }

      const recordedAt =
        typeof entry.recorded_at === "string" ? entry.recorded_at : fallbackTimestamp;
      const updatedAt = typeof entry.updated_at === "string" ? entry.updated_at : recordedAt;
      const metadata = isRecord(entry.metadata)
        ? { ...entry.metadata }
        : isRecord(entry.metadata_payload)
          ? { ...entry.metadata_payload }
          : {};

      for (const key of [
        "arguments",
        "command",
        "status",
        "stdout",
        "stderr",
        "exit_code",
        "output",
        "execution",
        "payload",
        "data",
        "result",
        "text",
        "message",
        "summary",
        "safe_summary",
        "error",
        "artifacts",
        "artifact_paths",
        "run_id",
        "created_at",
      ] as const) {
        if (entry[key] !== undefined && metadata[key] === undefined) {
          metadata[key] = entry[key];
        }
      }

      return [
        {
          id: typeof entry.id === "string" ? entry.id : `assistant-transcript-${index + 1}`,
          sequence: typeof entry.sequence === "number" ? entry.sequence : index + 1,
          kind: typeof entry.kind === "string" ? entry.kind : "status",
          status: typeof entry.status === "string" || entry.status === null ? entry.status : null,
          title: typeof entry.title === "string" || entry.title === null ? entry.title : null,
          text: typeof entry.text === "string" || entry.text === null ? entry.text : null,
          tool_name:
            typeof entry.tool_name === "string" || entry.tool_name === null
              ? entry.tool_name
              : null,
          tool_call_id:
            typeof entry.tool_call_id === "string" || entry.tool_call_id === null
              ? entry.tool_call_id
              : null,
          recorded_at: recordedAt,
          updated_at: updatedAt,
          metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
        } satisfies AssistantTranscriptSegment,
      ];
    })
    .sort((left, right) => {
      if (left.sequence !== right.sequence) {
        return left.sequence - right.sequence;
      }

      return toTimestamp(left.recorded_at) - toTimestamp(right.recorded_at);
    });
}
